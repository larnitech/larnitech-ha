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


def _u16(value: float | None, scale: float = 1, none: int = 0xFFFF) -> list[int]:
    if value is None:
        return [none >> 8, none & 0xFF]
    u = max(0, min(0xFFFE, round(value * scale)))
    return [u >> 8, u & 0xFF]


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


def _fan_code(fan_mode: Any) -> int:
    """Larnitech fan byte: off 0, low 1, medium 2, high 3, on 4, auto 5 (also for names like on_high)."""
    f = str(fan_mode or "auto").lower()
    exact = {"off": 0, "low": 1, "medium": 2, "middle": 2, "mid": 2, "high": 3, "on": 4, "auto": 5}
    if f in exact:
        return exact[f]
    for word, code in (("high", 3), ("max", 3), ("strong", 3), ("med", 2), ("mid", 2), ("low", 1), ("min", 1), ("quiet", 1), ("auto", 5)):
        if word in f:
            return code
    return 5


def encode(kind: str, state: State) -> str | None:
    """Current entity state as the Larnitech hex status for that element type."""
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    a = state.attributes
    s = state.state
    on = s in ("on", "open", "opening", "home", "playing", "heat", "cool", "heat_cool", "auto", "dry", "fan_only")
    if kind == "lamp":
        if state.domain == "lock":
            return _hex([1 if s == "locked" else 0])
        return _hex([1 if on else 0])
    if kind == "dimmer-lamp":
        if state.domain == "fan":
            pct = _num(a.get("percentage")) or 0
            return _hex([1 if on else 0, round(pct * 2.5)])
        bri = _num(a.get("brightness")) or 0
        return _hex([1 if on else 0, round(bri * 250 / 255)])
    if kind == "rgb-lamp":
        bri = _num(a.get("brightness")) or 0
        hs = a.get("hs_color") or (0, 0)
        return _hex([1 if on else 0, round(bri * 250 / 255), round(float(hs[0]) / 360 * 250), round(float(hs[1]) / 100 * 250)])
    if kind == "blinds":
        # state codes: 0 closed, 1 opened, 2 closing, 3 opening, 4 middle
        if s == "opening":
            return _hex([3])
        if s == "closing":
            return _hex([2])
        pos = _num(a.get("current_position"))
        if pos is None:
            return _hex([1 if s == "open" else 0])
        return _hex([1 if pos >= 100 else (0 if pos <= 0 else 4)])
    if kind in ("valve-heating", "conditioner"):
        target = _num(a.get("temperature")) or _num(a.get("target_temp_low")) or 0
        if kind == "valve-heating":
            return _hex([0 if s == "off" else 1, round(target) if s != "off" else 0])
        cur = _num(a.get("current_temperature")) or 0
        cool = _num(a.get("target_temp_high")) or target
        mode = {"off": 0, "heat": 1, "cool": 2}.get(s, 3)
        fan = _fan_code(a.get("fan_mode"))
        return _hex([0 if s == "off" else 1, mode, round(target), round(cur), round(cool), fan])
    value = _num(s)
    if kind == "temperature-sensor":
        if value is None:
            return "0x8000"
        if a.get("unit_of_measurement") == "°F":
            value = (value - 32) * 5 / 9
        v = round(value * 100) & 0xFFFF
        return _hex([v >> 8, v & 0xFF])
    if kind == "humidity-sensor":
        return _hex(_u16(value, 100))
    if kind in ("illumination-sensor", "co2-sensor"):
        return _hex(_u16(value))
    if kind in ("door-sensor", "leak-sensor", "motion-sensor"):
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
        on = b[0] != 0
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
        if b[0] == 0:
            return [(domain, "turn_off", eid)]
        if domain == "fan":
            return [("fan", "turn_on", {**eid, "percentage": max(1, round(b[1] / 2.5))})]
        return [("light", "turn_on", {**eid, "brightness": max(1, round(b[1] * 255 / 250))})]
    if kind == "rgb-lamp" and len(b) >= 4:
        if b[0] == 0:
            return [("light", "turn_off", eid)]
        return [("light", "turn_on", {**eid, "brightness": max(1, round(b[1] * 255 / 250)),
                                      "hs_color": [round(b[2] / 250 * 360, 1), round(b[3] / 250 * 100, 1)]})]
    if kind == "blinds":
        # target position 0..120, 0 = fully open
        pos = 100 - round(min(b[0], 120) / 120 * 100)
        if state.attributes.get("current_position") is None:
            return [("cover", "open_cover" if pos >= 50 else "close_cover", eid)]
        return [("cover", "set_cover_position", {**eid, "position": pos})]
    if kind == "valve-heating" and len(b) >= 2:
        if b[0] == 0:
            return [("climate", "set_hvac_mode", {**eid, "hvac_mode": "off"})]
        return [("climate", "set_temperature", {**eid, "hvac_mode": "heat", "temperature": b[1]})]
    if kind == "conditioner" and len(b) >= 6:
        if b[0] == 0:
            return [("climate", "set_hvac_mode", {**eid, "hvac_mode": "off"})]
        modes = state.attributes.get("hvac_modes") or []
        mode = {1: "heat", 2: "cool"}.get(b[1]) or ("heat_cool" if "heat_cool" in modes else "auto")
        if mode not in modes and modes:
            mode = next((m for m in modes if m != "off"), mode)
        if mode == "heat_cool" and state.attributes.get("target_temp_low") is not None:
            temps = {"target_temp_low": b[2], "target_temp_high": max(b[4], b[2])}
        else:
            temps = {"temperature": b[4] if mode == "cool" else b[2]}
        calls = [("climate", "set_temperature", {**eid, "hvac_mode": mode, **temps})]
        fan_modes = state.attributes.get("fan_modes") or []
        wanted = min(b[5], 5)
        fan = next((f for f in fan_modes if str(f).lower() == ["off", "low", "medium", "high", "on", "auto"][wanted]), None)
        if fan is None and wanted == 2:
            fan = next((f for f in fan_modes if str(f).lower() == "middle"), None)
        if fan is None:
            fan = next((f for f in fan_modes if _fan_code(f) == wanted), None)
        if fan is not None and fan != state.attributes.get("fan_mode"):
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
        status = encode(self.kinds[key], state)
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
