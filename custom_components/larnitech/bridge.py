"""Home Assistant entities as Larnitech elements (the controller's plugin API).

The integration announces itself as a plugin ({"request": "setup", "appId": ...}), then registers
the chosen entities with she-register-pnp. The controller creates real elements for them, so they
show up in the Larnitech app and can be used in Larnitech scripts and Blockly. State goes to the
controller as a byte-level hex status (she-device-status request); commands from the Larnitech
side arrive as she-device-status events and are executed as Home Assistant service calls.
Byte layouts follow the Larnitech wiki and the Larnitech Matter bridge, which uses the same API.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CoreState, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .api import LarnitechClient, LarnitechError
from .const import APP_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

CREATED_TIMEOUT = 8
STORE_VERSION = 1


def _hex(values: list[int]) -> str:
    return "0x" + "".join(f"{max(0, min(255, int(v))):02x}" for v in values)


def _parse_hex(text: str) -> list[int]:
    text = str(text).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = re.sub(r"[^0-9a-f]", "", text)
    if len(text) % 2:
        text = "0" + text
    return [int(text[i:i + 2], 16) for i in range(0, len(text), 2)]


def _temp(value: float | None) -> list[int]:
    """Larnitech sensor value: two bytes, fraction (1/256) first, then the signed integral part."""
    if value is None:
        return [0x00, 0x80]  # undefined
    v = max(-0x7FFF, min(0x7FFF, round(value * 256))) & 0xFFFF
    return [v & 0xFF, v >> 8]


def _parse_temp(lo: int, hi: int) -> float | None:
    v = lo | hi << 8
    if v == 0x8000:
        return None
    if v & 0x8000:
        v -= 0x10000
    return v / 256


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def larnitech_type(state: State) -> str | None:
    """Which Larnitech element type an entity becomes, or None if it is not supported."""
    domain = state.domain
    attrs = state.attributes
    dclass = attrs.get(ATTR_DEVICE_CLASS)
    if domain == "light":
        modes = set(attrs.get("supported_color_modes") or [])
        if modes & {"hs", "rgb", "rgbw", "rgbww", "xy"}:
            return "rgb-lamp"
        if modes - {"onoff"}:
            return "dimmer-lamp"
        return "lamp"
    if domain in ("switch", "input_boolean", "lock", "siren", "automation", "script"):
        return "lamp"
    if domain == "fan":
        return "dimmer-lamp" if attrs.get("percentage") is not None or attrs.get("percentage_step") else "lamp"
    if domain == "cover":
        return "blinds"
    if domain == "climate":
        modes = set(attrs.get("hvac_modes") or [])
        return "conditioner" if modes & {"cool", "heat_cool", "auto", "dry", "fan_only"} else "valve-heating"
    if domain == "sensor":
        return {
            "temperature": "temperature-sensor",
            "humidity": "humidity-sensor",
            "illuminance": "illumination-sensor",
            "carbon_dioxide": "co2-sensor",
        }.get(dclass)
    if domain == "binary_sensor":
        if dclass in ("motion", "occupancy", "presence", "moving"):
            return "motion-sensor"
        if dclass in ("moisture",):
            return "leak-sensor"
        return "door-sensor"
    return None


# conditioner byte 0, bits 4-7
COND_MODES = {"fan_only": 0, "cool": 1, "dry": 2, "heat": 3, "heat_cool": 4, "auto": 4}
COND_T_MIN = 16  # byte 1 is the setpoint minus t-min; elements created through the plugin API use 16
# conditioner byte 4, bits 0-3: airflow
AIRFLOW = {"auto": 0, "low": 1, "medium": 2, "high": 3}


def _airflow(fan_mode: Any) -> int:
    """Larnitech airflow code for a Home Assistant fan mode (also for names like on_high)."""
    f = str(fan_mode or "auto").lower()
    if f.startswith("auto"):
        return 0
    for word, code in (("high", 3), ("max", 3), ("strong", 3), ("med", 2), ("mid", 2), ("low", 1), ("min", 1), ("quiet", 1)):
        if word in f:
            return code
    return 0


def _blind_level(pos: float | None, s: str) -> int:
    """Blinds level 0..250, 0 = open (Home Assistant counts how far it is open)."""
    if pos is None:
        return 250 if s == "closed" else 0
    return round((100 - max(0, min(100, pos))) * 2.5)


def encode(kind: str, state: State, prev: list[int] | None = None) -> str | None:
    """Current entity state as the Larnitech hex status for that element type.

    prev is the status sent last time: an element that is off keeps its brightness and colour.
    """
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    a = state.attributes
    s = state.state
    prev = prev or []
    on = s in ("on", "open", "opening", "home", "playing", "heat", "cool", "heat_cool", "auto", "dry", "fan_only")
    if kind == "lamp":
        if state.domain == "lock":
            return _hex([1 if s == "locked" else 0])
        return _hex([1 if on else 0])
    if kind == "dimmer-lamp":
        if state.domain == "fan":
            pct = _num(a.get("percentage"))
            level = round(pct * 2.5) if pct else None
        else:
            bri = _num(a.get("brightness"))
            level = round(bri * 250 / 255) if bri else None
        if level is None:
            level = prev[1] if len(prev) >= 2 and not on else 0
        return _hex([1 if on else 0, level])
    if kind == "rgb-lamp":
        # bytes: state, V (brightness), S (saturation), H (hue), each 0..250
        bri = _num(a.get("brightness"))
        hs = a.get("hs_color")
        if not on and bri is None and len(prev) >= 4:
            return _hex([0, *prev[1:4]])
        hs = hs or (0, 0)
        return _hex([1 if on else 0, round((bri or 0) * 250 / 255), round(float(hs[1]) / 100 * 250), round(float(hs[0]) / 360 * 250)])
    if kind == "blinds":
        # bytes: state (0 closed, 1 opened, 2 closing, 3 opening, 4 middle), target level, current level
        pos = _num(a.get("current_position"))
        level = _blind_level(pos, s)
        if s == "opening":
            return _hex([3, 0, level])
        if s == "closing":
            return _hex([2, 250, level])
        if pos is None:
            code = 1 if s == "open" else 0
        else:
            code = 1 if pos >= 100 else (0 if pos <= 0 else 4)
        return _hex([code, level, level])
    if kind == "valve-heating":
        # bytes: state, setpoint (2), current temperature (2), 0xFF = manual mode
        target = _num(a.get("temperature")) or _num(a.get("target_temp_low"))
        return _hex([0 if s == "off" else 1, *_temp(target), *_temp(_num(a.get("current_temperature"))), 0xFF])
    if kind == "conditioner":
        # byte 0: bit 0 on, bits 4-7 mode; byte 1: setpoint - t-min; byte 3: vanes; byte 4: airflow
        target = _num(a.get("temperature")) or _num(a.get("target_temp_low")) or _num(a.get("target_temp_high"))
        if s in COND_MODES:
            mode = COND_MODES[s]
        else:
            mode = prev[0] >> 4 if prev else 4
        t = max(0, min(255, round(target) - COND_T_MIN)) if target is not None else (prev[1] if len(prev) >= 2 else 8)
        vanes = prev[3] if len(prev) >= 4 else 0
        return _hex([(0 if s == "off" else 1) | mode << 4, t, 0, vanes, _airflow(a.get("fan_mode")), 0])
    value = _num(s)
    if kind in ("temperature-sensor", "humidity-sensor", "illumination-sensor"):
        if kind == "temperature-sensor" and value is not None and a.get("unit_of_measurement") == "°F":
            value = (value - 32) * 5 / 9
        if kind == "illumination-sensor" and value is not None:
            # the 2-byte sensor format holds at most 127.99 (checked on a controller): brighter reads as that
            value = max(0, min(127.99, value))
        return _hex(_temp(value))
    if kind == "co2-sensor":
        v = 0xFFFF if value is None else max(0, min(0xFFFE, round(value)))
        return _hex([v & 0xFF, v >> 8])
    if kind == "motion-sensor":
        # a motion level, two bytes like the other sensors
        return _hex(_temp(100 if s == "on" else 0))
    if kind in ("door-sensor", "leak-sensor"):
        return _hex([1 if s == "on" else 0])
    return None


def decode(kind: str, state: State, hexstatus: str) -> list[tuple[str, str, dict[str, Any]]]:
    """A command from Larnitech as Home Assistant service calls: (domain, service, data)."""
    b = _parse_hex(hexstatus)
    if not b:
        return []
    eid = {"entity_id": state.entity_id}
    domain = state.domain
    if kind == "lamp":
        on = b[0] & 1 != 0
        if domain == "lock":
            return [("lock", "lock" if on else "unlock", eid)]
        if domain in ("automation",):
            return [("automation", "turn_on" if on else "turn_off", eid)]
        if domain == "script":
            return [("script", "turn_on" if on else "turn_off", eid)]
        if domain == "siren":
            return [("siren", "turn_on" if on else "turn_off", eid)]
        return [(domain if domain in ("light", "switch", "input_boolean", "fan") else "homeassistant", "turn_on" if on else "turn_off", eid)]
    if kind == "dimmer-lamp" and len(b) >= 2:
        if b[0] & 1 == 0:
            return [(domain, "turn_off", eid)]
        if domain == "fan":
            return [("fan", "turn_on", {**eid, "percentage": max(1, round(b[1] / 2.5))})]
        return [("light", "turn_on", {**eid, "brightness": max(1, round(b[1] * 255 / 250))})]
    if kind == "rgb-lamp" and len(b) >= 4:
        if b[0] & 1 == 0:
            return [("light", "turn_off", eid)]
        return [("light", "turn_on", {**eid, "brightness": max(1, round(b[1] * 255 / 250)),
                                      "hs_color": [round(b[3] / 250 * 360, 1), round(b[2] / 250 * 100, 1)]})]
    if kind == "blinds":
        has_pos = state.attributes.get("current_position") is not None
        if len(b) == 1:
            # single byte: 2 = level 0 (open), 3 = level 100 % (closed), 4 = stop
            service = {0: "open_cover", 1: "close_cover", 2: "open_cover", 3: "close_cover", 4: "stop_cover"}.get(b[0])
            return [("cover", service, eid)] if service else []
        # the controller sends the wanted status: state, target level (0..250, 0 = open) and current level
        code, target = b[0] & 7, min(b[1], 250)
        if len(b) >= 3 and code not in (2, 3) and target == min(b[2], 250):
            return [("cover", "stop_cover", eid)]
        if target == 0:
            return [("cover", "open_cover", eid)]
        if target == 250:
            return [("cover", "close_cover", eid)]
        if not has_pos:
            return [("cover", "open_cover" if target < 125 else "close_cover", eid)]
        return [("cover", "set_cover_position", {**eid, "position": 100 - round(target / 2.5)})]
    if kind == "valve-heating":
        if b[0] & 1 == 0:
            return [("climate", "set_hvac_mode", {**eid, "hvac_mode": "off"})]
        modes = state.attributes.get("hvac_modes") or []
        mode = "heat" if "heat" in modes or not modes else next((m for m in modes if m != "off"), "heat")
        target = _parse_temp(b[1], b[2]) if len(b) >= 3 else None
        current = _num(state.attributes.get("temperature"))
        if target is None or (current is not None and abs(target - current) < 0.25):
            return [("climate", "set_hvac_mode", {**eid, "hvac_mode": mode})]
        return [("climate", "set_temperature", {**eid, "hvac_mode": mode, "temperature": target})]
    if kind == "conditioner" and len(b) >= 2:
        if b[0] & 1 == 0:
            return [("climate", "set_hvac_mode", {**eid, "hvac_mode": "off"})]
        modes = state.attributes.get("hvac_modes") or []
        wanted = {0: "fan_only", 1: "cool", 2: "dry", 3: "heat", 4: "heat_cool"}.get(b[0] >> 4, "heat_cool")
        if wanted == "heat_cool" and wanted not in modes:
            wanted = "auto"
        mode = wanted if wanted in modes or not modes else next((m for m in modes if m != "off"), wanted)
        target = b[1] + COND_T_MIN
        if mode == "heat_cool" and state.attributes.get("target_temp_low") is not None:
            low = _num(state.attributes.get("target_temp_low")) or target
            high = _num(state.attributes.get("target_temp_high")) or target
            span = max(1, high - low)
            temps = {"target_temp_low": target, "target_temp_high": target + span}
        else:
            temps = {"temperature": target}
        calls = [("climate", "set_temperature", {**eid, "hvac_mode": mode, **temps})]
        if mode in ("fan_only", "dry"):
            # not every climate takes a setpoint in these modes
            calls = [("climate", "set_hvac_mode", {**eid, "hvac_mode": mode})]
        fan_modes = state.attributes.get("fan_modes") or []
        if len(b) >= 5 and fan_modes:
            code = b[4] & 0x0F
            current = state.attributes.get("fan_mode")
            if _airflow(current) != code:
                # prefer a fan mode that keeps the current on/auto prefix (on_low -> on_high)
                prefix = str(current).split("_")[0] if current and "_" in str(current) else ""
                if code == 0:
                    # automatic airflow: a mode named auto (auto_low, auto_high...), only then one with code 0
                    candidates = [f for f in fan_modes if str(f).lower() == "auto"] or [
                        f for f in fan_modes if str(f).lower().startswith("auto")] or [
                        f for f in fan_modes if _airflow(f) == 0 and str(f).lower() != "off"]
                    prefix = ""
                else:
                    candidates = [f for f in fan_modes if _airflow(f) == code]
                fan = next((f for f in candidates if prefix and str(f).startswith(prefix)), candidates[0] if candidates else None)
                if fan is not None:
                    calls.append(("climate", "set_fan_mode", {**eid, "fan_mode": fan}))
        return calls
    return []


def addr_key(entity_id: str) -> str:
    return "ha_" + re.sub(r"[^a-z0-9_]", "_", entity_id.lower())


class EntityBridge:
    """Registers selected HA entities on the controller and keeps them in sync."""

    def __init__(self, hass: HomeAssistant, client: LarnitechClient, entry_id: str, entity_ids: list[str]) -> None:
        self.hass = hass
        self.client = client
        self.entity_ids = list(entity_ids)
        # what each entity was registered as. The registration is always the complete set, so an
        # entity that is missing for a moment (its integration still loading) must still be sent,
        # or the controller would delete its element and break the scripts that use it.
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.exported")
        self._known: dict[str, dict[str, str]] = {}  # entity_id -> {"type": ..., "name": ...}
        self._started = asyncio.Event()
        self._unsub_started = None
        self.kinds: dict[str, str] = {}  # addr-key -> larnitech type
        self.entities: dict[str, str] = {}  # addr-key -> entity_id
        self.addrs: dict[str, str] = {}  # addr-key -> controller address
        self.errors: dict[str, str] = {}
        self._created: set[str] = set()
        self._last: dict[str, str] = {}
        self._unsubs: list = []

    @staticmethod
    async def async_forget(hass: HomeAssistant, entry_id: str) -> None:
        await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.exported").async_remove()

    async def async_start(self) -> None:
        self._known = (await self._store.async_load() or {}).get("entities", {})
        if self.hass.state is CoreState.running:
            self._started.set()
        else:
            @callback
            def _on_started(_event: Event) -> None:
                self._unsub_started = None
                self._started.set()
                # entities of integrations loaded after us exist now: register them with real names and states
                if self.client.connected and self.entity_ids:
                    self.hass.async_create_task(self._on_session())

            self._unsub_started = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
        self._unsubs.append(self.client.add_plugin_listener(self._on_plugin_message))
        self._unsubs.append(self.client.add_session_listener(self._on_session))
        if self.entity_ids:
            self._unsubs.append(async_track_state_change_event(self.hass, self.entity_ids, self._on_state))

    async def async_stop(self) -> None:
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @property
    def owned_addrs(self) -> set[str]:
        return set(self.addrs.values())

    async def _on_session(self) -> None:
        """After every (re)authorization: announce the plugin and register the full set."""
        if not self._started.is_set():
            # during Home Assistant start-up other integrations may not have created their entities
            # yet; registering now could delete their elements. _on_started registers instead.
            return
        self._created.clear()
        self._last.clear()
        entries = []
        self.kinds.clear()
        self.entities.clear()
        changed = False
        for eid in self.entity_ids:
            state = self.hass.states.get(eid)
            known = self._known.get(eid)
            usable = state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            # keep the type an element was created with: changing it would recreate the element
            kind = known["type"] if known else (larnitech_type(state) if state is not None else None)
            if kind is None:
                if state is not None:
                    if self.errors.get(eid) != "unsupported":
                        _LOGGER.warning("%s cannot become a Larnitech element (unsupported kind of entity)", eid)
                    self.errors[eid] = "unsupported"
                    _LOGGER.debug("%s cannot become a Larnitech element (unsupported kind of entity)", eid)
                elif not self._started.is_set():
                    pass  # not loaded yet; registered once Home Assistant has started
                else:
                    self.errors[eid] = "not found"
                continue
            name = str(state.attributes.get(ATTR_FRIENDLY_NAME) or eid) if usable else (known or {}).get("name", eid)
            name = name[:60]
            if known != {"type": kind, "name": name}:
                self._known[eid] = {"type": kind, "name": name}
                changed = True
            self.errors.pop(eid, None)
            key = addr_key(eid)
            self.kinds[key] = kind
            self.entities[key] = eid
            entries.append({"addr-key": key, "type": kind, "name": name})
        for eid in [e for e in self._known if e not in self.entity_ids]:
            del self._known[eid]
            changed = True
        if changed:
            await self._store.async_save({"entities": self._known})
        if not entries and not self._started.is_set() and self.entity_ids:
            # nothing known yet and nothing loaded: do not wipe the controller's elements, wait for start
            return
        try:
            await self.client.request({"request": "setup", "appId": APP_ID})
            # the list is the complete set: elements registered earlier and missing now are removed
            await self.client.request({"request": "she-register-pnp", "pnp": entries})
        except LarnitechError as err:
            _LOGGER.warning("Larnitech plugin registration failed: %s", err)
            return
        if entries:
            self.hass.async_create_task(self._push_all_later())

    async def _push_all_later(self) -> None:
        # normally each element is pushed on she-device-is-created; this covers elements that already existed
        await asyncio.sleep(CREATED_TIMEOUT)
        for key in list(self.kinds):
            await self._push(key, force=key not in self._created)

    @callback
    def _on_plugin_message(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "she-device-is-created":
            key = str(msg.get("addr-key", ""))
            if msg.get("addr"):
                self.addrs[key] = str(msg["addr"])
                self._mark_owned()
            self._created.add(key)
            self.hass.async_create_task(self._push(key, force=True))
        elif event == "she-device-status":
            key = str(msg.get("addr-key", ""))
            self.hass.async_create_task(self._command(key, str(msg.get("status", ""))))
        elif msg.get("response") == "she-register-pnp":
            for reg in msg.get("register") or []:
                self.addrs[str(reg.get("addr-key"))] = str(reg.get("addr"))
            for err in msg.get("error") or []:
                key = str(err.get("addr-key"))
                eid = self.entities.get(key, key)
                self.errors[eid] = str(err.get("description") or err.get("code"))
                _LOGGER.warning("Controller refused %s as %s: %s", eid, self.kinds.get(key), self.errors[eid])
                # leak sensors are not known to every firmware: retry as a door sensor
                if self.kinds.get(key) == "leak-sensor":
                    self.kinds[key] = "door-sensor"
                    self.hass.async_create_task(self._reregister())
            self._mark_owned()

    def _mark_owned(self) -> None:
        for addr in self.addrs.values():
            self.client.owned.add(addr)
            dev = self.client.devices.get(addr)
            if dev is not None:
                dev["ha-owned"] = True

    async def _reregister(self) -> None:
        entries = []
        for key, kind in self.kinds.items():
            eid = self.entities[key]
            name = self._known.get(eid, {}).get("name", eid)
            self._known[eid] = {"type": kind, "name": name}
            entries.append({"addr-key": key, "type": kind, "name": name})
        await self._store.async_save({"entities": self._known})
        try:
            await self.client.request({"request": "she-register-pnp", "pnp": entries})
        except LarnitechError as err:
            _LOGGER.warning("Larnitech plugin re-registration failed: %s", err)

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        eid = event.data["entity_id"]
        key = addr_key(eid)
        if key in self.kinds:
            self.hass.async_create_task(self._push(key))

    async def _push(self, key: str, force: bool = False) -> None:
        if not self.client.connected or key not in self.kinds:
            return
        state = self.hass.states.get(self.entities[key])
        if state is None:
            return
        status = encode(self.kinds[key], state, _parse_hex(self._last.get(key, "")))
        if status is None or (not force and self._last.get(key) == status):
            return
        self._last[key] = status
        try:
            await self.client.request({"request": "she-device-status", "addr-key": key, "status": status})
        except LarnitechError as err:
            _LOGGER.debug("Push %s failed: %s", key, err)

    async def _command(self, key: str, status: str) -> None:
        if key not in self.kinds:
            return
        state = self.hass.states.get(self.entities[key])
        if state is None:
            return
        calls = decode(self.kinds[key], state, status)
        _LOGGER.debug("Larnitech command for %s: %s -> %s", state.entity_id, status, calls)
        for domain, service, data in calls:
            try:
                await self.hass.services.async_call(domain, service, data, blocking=True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Larnitech command %s.%s for %s failed: %s", domain, service, state.entity_id, err)
        # confirm: the controller expects the resulting status back even if nothing changed
        await asyncio.sleep(0.3)
        await self._push(key, force=True)
