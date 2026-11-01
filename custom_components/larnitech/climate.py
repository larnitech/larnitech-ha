"""Climate controllers, air conditioners, fancoils."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import CLIMATE_TYPES
from .entity import LarnitechEntity, devices_of, is_on_value, to_float

LT_TO_HVAC = {
    "heat": HVACMode.HEAT,
    "heating": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "cooling": HVACMode.COOL,
    "auto": HVACMode.HEAT_COOL,
    "dry": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "fan-only": HVACMode.FAN_ONLY,
    "vent": HVACMode.FAN_ONLY,
}
HVAC_TO_LT_DEFAULT = {v: k for k, v in reversed(list(LT_TO_HVAC.items()))}
FAN_KEYS = ("fan", "fan-speed")
FAN_PERCENT = {"low": 33, "middle": 66, "high": 100}


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechClimate(entry, d) for d in devices_of(entry, CLIMATE_TYPES))


class LarnitechClimate(LarnitechEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5
    _attr_max_temp = 35
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        st = device.get("status") or {}
        kind = device.get("type")
        lt_modes = [str(m) for m in (device.get("modes") or [])]
        if not lt_modes and kind in ("conditioner", "fancoil"):
            # the controller lists modes only for some elements
            lt_modes = ["cool", "heat", "fan", "auto"]
        if "mode" in st and str(st["mode"]) not in lt_modes:
            lt_modes.append(str(st["mode"]))
        if device.get("type") == "climate-control" and "auto" not in lt_modes and "setpoint-heat" in st and "setpoint-cool" in st:
            lt_modes.append("auto")
        self._mode_map: dict[HVACMode, str] = {}
        for m in lt_modes:
            hv = LT_TO_HVAC.get(m)
            if hv and hv not in self._mode_map:
                self._mode_map[hv] = m
        if kind == "valve-heating":
            # a heating valve only heats: on/off, no mode field
            self._mode_map = {}
        self._send_mode = bool(self._mode_map)
        if not self._mode_map:
            self._mode_map[HVACMode.HEAT] = "heat"
        self._attr_hvac_modes = [HVACMode.OFF, *self._mode_map]

        feats = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        # conditioner, fancoil, heating valve: "target"; climate-control: "setpoint"/"setpoint-heat"/"setpoint-cool"
        self._target_key = "target" if "target" in st or device.get("type") in ("conditioner", "fancoil", "valve-heating") else "setpoint"
        if self._target_key == "target" or any(k in st for k in ("setpoint", "setpoint-heat", "setpoint-cool")):
            feats |= ClimateEntityFeature.TARGET_TEMPERATURE
        if "setpoint-heat" in st and "setpoint-cool" in st:
            feats |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        self._presets = [str(a) for a in (device.get("automations") or [])]
        if self._presets:
            feats |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = self._presets
        self._fan_key = next((k for k in FAN_KEYS if k in st), None)
        fans = device.get("fan-modes") or device.get("fans")
        self._fan_numeric = self._fan_key is not None and isinstance(st.get(self._fan_key), (int, float))
        if self._fan_key:
            feats |= ClimateEntityFeature.FAN_MODE
            if self._fan_numeric:
                self._attr_fan_modes = list(FAN_PERCENT)
            else:
                self._attr_fan_modes = [str(f) for f in fans] if fans else ["auto", "low", "middle", "high"]
        if "target-humidity" in st:
            feats |= ClimateEntityFeature.TARGET_HUMIDITY
        self._attr_supported_features = feats

    # ---- state -----------------------------------------------------------
    @property
    def hvac_mode(self) -> HVACMode | None:
        if is_on_value(self.status.get("state")) is False:
            return HVACMode.OFF
        mode = str(self.status.get("mode", ""))
        hv = LT_TO_HVAC.get(mode)
        if hv in self._mode_map:
            return hv
        return next(iter(self._mode_map))

    @property
    def hvac_action(self) -> HVACAction | None:
        act = str(self.status.get("action", self.status.get("current-mode", ""))).lower()
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return {"heat": HVACAction.HEATING, "heating": HVACAction.HEATING, "cool": HVACAction.COOLING,
                "cooling": HVACAction.COOLING, "idle": HVACAction.IDLE, "fan": HVACAction.FAN}.get(act)

    @property
    def current_temperature(self) -> float | None:
        # the controller reports 0.0 when no room sensor is linked
        t = to_float(self.status.get("current-temperature", self.status.get("current", self.status.get("temperature"))))
        return None if not t else t

    @property
    def current_humidity(self) -> float | None:
        h = to_float(self.status.get("current-humidity"))
        return None if not h else h

    @property
    def target_temperature(self) -> float | None:
        mode = self.hvac_mode
        if mode == HVACMode.HEAT and "setpoint-heat" in self.status:
            return to_float(self.status["setpoint-heat"])
        if mode == HVACMode.COOL and "setpoint-cool" in self.status:
            return to_float(self.status["setpoint-cool"])
        if mode == HVACMode.HEAT_COOL and self.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE:
            return None
        return to_float(self.status.get(self._target_key))

    @property
    def target_temperature_low(self) -> float | None:
        return to_float(self.status.get("setpoint-heat"))

    @property
    def target_temperature_high(self) -> float | None:
        return to_float(self.status.get("setpoint-cool"))

    @property
    def target_humidity(self) -> float | None:
        return to_float(self.status.get("target-humidity"))

    @property
    def preset_mode(self) -> str | None:
        p = self.status.get("automation", self._dev.get("automation"))
        return str(p) if p is not None else None

    @property
    def fan_mode(self) -> str | None:
        if not self._fan_key:
            return None
        val = self.status.get(self._fan_key)
        if self._fan_numeric:
            pct = to_float(val) or 0
            return "low" if pct < 40 else ("middle" if pct < 80 else "high")
        return None if val is None else str(val)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        for k in ("current-co2", "pid-temperature", "time-interval"):
            if k in self.status:
                attrs[k.replace("-", "_")] = self.status[k]
        return attrs

    # ---- commands --------------------------------------------------------
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._send({"state": "off"})
            return
        cmd: dict[str, Any] = {"state": "on"}
        if self._send_mode:
            cmd["mode"] = self._mode_map.get(hvac_mode, HVAC_TO_LT_DEFAULT.get(hvac_mode, "auto"))
        await self._send(cmd)

    async def async_turn_on(self) -> None:
        await self._send({"state": "on"})

    async def async_turn_off(self) -> None:
        await self._send({"state": "off"})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        cmd: dict[str, Any] = {}
        if ATTR_HVAC_MODE in kwargs and kwargs[ATTR_HVAC_MODE] != HVACMode.OFF:
            cmd["state"] = "on"
            if self._send_mode:
                cmd["mode"] = self._mode_map.get(kwargs[ATTR_HVAC_MODE], "auto")
        if ATTR_TARGET_TEMP_LOW in kwargs:
            cmd["setpoint-heat"] = float(kwargs[ATTR_TARGET_TEMP_LOW])
        if ATTR_TARGET_TEMP_HIGH in kwargs:
            cmd["setpoint-cool"] = float(kwargs[ATTR_TARGET_TEMP_HIGH])
        if ATTR_TEMPERATURE in kwargs:
            t = float(kwargs[ATTR_TEMPERATURE])
            mode = self.hvac_mode
            if mode == HVACMode.HEAT and "setpoint-heat" in self.status:
                cmd["setpoint-heat"] = t
            elif mode == HVACMode.COOL and "setpoint-cool" in self.status:
                cmd["setpoint-cool"] = t
            else:
                cmd[self._target_key] = t
        if cmd:
            await self._send(cmd)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._send({"automation": preset_mode})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        value: Any = FAN_PERCENT.get(fan_mode, 66) if self._fan_numeric else fan_mode
        await self._send({self._fan_key or "fan": value})

    async def async_set_humidity(self, humidity: int) -> None:
        await self._send({"target-humidity": humidity})
