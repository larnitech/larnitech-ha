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

from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .api import LarnitechClient, LarnitechError
from .const import APP_ID

_LOGGER = logging.getLogger(__name__)

CREATED_TIMEOUT = 8


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
        fan = {"off": 0, "low": 1, "medium": 2, "middle": 2, "high": 3, "on": 4, "auto": 5}.get(str(a.get("fan_mode", "auto")).lower(), 5)
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
        calls = [("climate", "set_temperature", {**eid, "hvac_mode": mode, "temperature": b[2]})]
        fan_modes = state.attributes.get("fan_modes") or []
        fan = ["off", "low", "medium", "high", "on", "auto"][min(b[5], 5)]
        if fan == "medium" and fan not in fan_modes and "middle" in fan_modes:
            fan = "middle"
        if fan in fan_modes:
            calls.append(("climate", "set_fan_mode", {**eid, "fan_mode": fan}))
        return calls
    return []


def addr_key(entity_id: str) -> str:
    return "ha_" + re.sub(r"[^a-z0-9_]", "_", entity_id.lower())


class EntityBridge:
    """Registers selected HA entities on the controller and keeps them in sync."""

    def __init__(self, hass: HomeAssistant, client: LarnitechClient, entity_ids: list[str]) -> None:
        self.hass = hass
        self.client = client
        self.entity_ids = list(entity_ids)
        self.kinds: dict[str, str] = {}  # addr-key -> larnitech type
        self.entities: dict[str, str] = {}  # addr-key -> entity_id
        self.addrs: dict[str, str] = {}  # addr-key -> controller address
        self.errors: dict[str, str] = {}
        self._created: set[str] = set()
        self._last: dict[str, str] = {}
        self._unsubs: list = []

    async def async_start(self) -> None:
        self._unsubs.append(self.client.add_plugin_listener(self._on_plugin_message))
        self._unsubs.append(self.client.add_session_listener(self._on_session))
        if self.entity_ids:
            self._unsubs.append(async_track_state_change_event(self.hass, self.entity_ids, self._on_state))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @property
    def owned_addrs(self) -> set[str]:
        return set(self.addrs.values())

    async def _on_session(self) -> None:
        """After every (re)authorization: announce the plugin and register the full set."""
        self._created.clear()
        self._last.clear()
        entries = []
        self.kinds.clear()
        self.entities.clear()
        for eid in self.entity_ids:
            state = self.hass.states.get(eid)
            if state is None:
                continue
            kind = larnitech_type(state)
            if kind is None:
                self.errors[eid] = "unsupported"
                continue
            key = addr_key(eid)
            self.kinds[key] = kind
            self.entities[key] = eid
            entries.append({"addr-key": key, "type": kind, "name": str(state.attributes.get(ATTR_FRIENDLY_NAME) or eid)[:60]})
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
            state = self.hass.states.get(self.entities[key])
            name = str(state.attributes.get(ATTR_FRIENDLY_NAME) or self.entities[key]) if state else self.entities[key]
            entries.append({"addr-key": key, "type": kind, "name": name[:60]})
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
