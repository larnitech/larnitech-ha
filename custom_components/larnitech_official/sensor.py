"""Numeric and text sensors, sunrise and sunset, motion levels; plus a diagnostic text sensor for
element types without a better platform."""
from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSoundPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

try:  # Home Assistant 2026.10+
    from homeassistant.const import UnitOfRatio

    PPM = UnitOfRatio.PARTS_PER_MILLION
except ImportError:
    from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION as PPM
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .bridge import level_to_lux
from .const import (
    BINARY_SENSOR_TYPES, BUTTON_TYPES, CLIMATE_TYPES, CONF_IGNORED_TYPES, CONF_MOTION_LEVEL, COVER_TYPES,
    EVENT_TYPES, FAN_TYPES, LIGHT_TYPES, LOCK_TYPES, SCENE_TYPES, SECURITY_TYPES, SENSOR_TYPES, SKIP_TYPES,
    SUN_TYPES, SWITCH_TYPES, TEXT_TYPES, VIRTUAL_SENSOR_TYPES, WEATHER_TYPES,
)
from .decode import at_minutes, sensor32_value, sun_times, text_value, virtual_sensor_value, word_value
from .entity import LarnitechEntity, is_own, kind_of

# kind -> (device class, unit); the value comes from decode.word_value
SPECS: dict[str, tuple[SensorDeviceClass | None, str | None]] = {
    "temperature-sensor": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    "humidity-sensor": (SensorDeviceClass.HUMIDITY, PERCENTAGE),
    # the level (0..100 %) converted to lux with the Larnitech app's own formula (bridge.level_to_lux)
    "illumination-sensor": (SensorDeviceClass.ILLUMINANCE, LIGHT_LUX),
    "co2-sensor": (SensorDeviceClass.CO2, PPM),
    "voltage-sensor": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    # the app shows the raw number as mA (blind motor current)
    "current-sensor": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE),
    "power-sensor": (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT),
    "freq-sensor": (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    "consumption-sensor": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "percent-sensor": (None, PERCENTAGE),
    "float-sensor": (None, None),
    "energy-sensor": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "pressure-sensor": (SensorDeviceClass.PRESSURE, UnitOfPressure.HPA),
    "noise-sensor": (SensorDeviceClass.SOUND_PRESSURE, UnitOfSoundPressure.DECIBEL),
}
# the "dim" of a virtual sensor -> HA unit and device class
DIM_UNITS: dict[str, tuple[str, SensorDeviceClass | None]] = {
    "TS": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "°C": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "°F": (UnitOfTemperature.FAHRENHEIT, SensorDeviceClass.TEMPERATURE),
    "%": (PERCENTAGE, None),
    "V": (UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    "A": (UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
    "W": (UnitOfPower.WATT, SensorDeviceClass.POWER),
    "kW": (UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
    "kWh": (UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
    "Hz": (UnitOfFrequency.HERTZ, SensorDeviceClass.FREQUENCY),
    "hPa": (UnitOfPressure.HPA, SensorDeviceClass.PRESSURE),
    "mmHg": (UnitOfPressure.MMHG, SensorDeviceClass.PRESSURE),
    "lx": (LIGHT_LUX, SensorDeviceClass.ILLUMINANCE),
    "ppm": (PPM, SensorDeviceClass.CO2),
}
HANDLED = (
    LIGHT_TYPES | LOCK_TYPES | SWITCH_TYPES | COVER_TYPES | CLIMATE_TYPES | FAN_TYPES | SENSOR_TYPES
    | BINARY_SENSOR_TYPES | SCENE_TYPES | BUTTON_TYPES | EVENT_TYPES | SECURITY_TYPES | SKIP_TYPES
    | VIRTUAL_SENSOR_TYPES | TEXT_TYPES | SUN_TYPES | WEATHER_TYPES
)
# the state of an HA entity holds at most 255 characters
MAX_STATE = 255


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    ignored = set(entry.options.get(CONF_IGNORED_TYPES, []))
    motion_level = bool(entry.options.get(CONF_MOTION_LEVEL, False))
    entities: list[SensorEntity] = []
    for dev in entry.runtime_data.client.devices.values():
        kind = kind_of(dev)
        if is_own(dev) or kind in ignored or kind in SKIP_TYPES:
            continue
        if kind in SPECS:
            entities.append(LarnitechSensor(entry, dev))
        elif kind in VIRTUAL_SENSOR_TYPES:
            entities.append(LarnitechVirtualSensor(entry, dev))
        elif kind in TEXT_TYPES:
            entities.append(LarnitechTextSensor(entry, dev))
        elif kind in SUN_TYPES:
            entities.append(LarnitechSunSensor(entry, dev, 0))
            entities.append(LarnitechSunSensor(entry, dev, 1))
        elif kind == "motion-sensor":
            if motion_level:
                entities.append(LarnitechMotionLevel(entry, dev))
        elif kind in SECURITY_TYPES or (kind and kind not in HANDLED):
            entities.append(LarnitechStatusSensor(entry, dev, enabled=kind in SECURITY_TYPES))
    async_add(entities)


class LarnitechSensor(LarnitechEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        self._kind = kind_of(device)
        self._attr_device_class, self._attr_native_unit_of_measurement = SPECS[self._kind]
        if self._kind in ("consumption-sensor", "energy-sensor"):
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def _level(self) -> float | None:
        return word_value(self._kind, self.status)

    @property
    def native_value(self) -> float | None:
        value = self._level
        if value is not None and self._kind == "illumination-sensor":
            return round(level_to_lux(value))
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        if self._kind == "illumination-sensor":
            # the level the Larnitech app shows in % (and its automations compare with)
            attrs["level"] = self._level
        return attrs


class LarnitechMotionLevel(LarnitechEntity, SensorEntity):
    """The motion level of a motion sensor (0..100 %), next to its binary sensor."""

    _attr_translation_key = "motion_level"
    _unique_suffix = "_level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:motion-sensor"

    @property
    def native_value(self) -> float | None:
        return word_value("motion-sensor", self.status)


class LarnitechVirtualSensor(LarnitechEntity, SensorEntity):
    """A virtual sensor of a Larnitech script or plugin, with the unit from its "dim" attribute."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        dim = str(device.get("dim") or "").strip()
        unit, dclass = DIM_UNITS.get(dim, (dim or None, None))
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dclass
        self._sensor32 = kind_of(device) == "sensor32"

    @property
    def native_value(self) -> float | None:
        if self._sensor32:
            return sensor32_value(self.status)
        return virtual_sensor_value(self.status, self._dev.get("vtype"))


class LarnitechTextSensor(LarnitechEntity, SensorEntity):
    """A virtual text or long-text element (weather texts, messages of scripts)."""

    _attr_icon = "mdi:text-box-outline"

    @property
    def native_value(self) -> str | None:
        text = text_value(self.status)
        if text is None:
            return None
        return text if len(text) <= MAX_STATE else text[: MAX_STATE - 1] + "…"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        text = text_value(self.status)
        if text is not None and len(text) > MAX_STATE:
            attrs["text"] = text
        return attrs


class LarnitechSunSensor(LarnitechEntity, SensorEntity):
    """Sunrise (index 0) or sunset (1) of today from the weather plugin's sunrise element."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry, device, index: int) -> None:
        self._index = index
        self._attr_translation_key = ("sunrise", "sunset")[index]
        self._unique_suffix = ("_sunrise", "_sunset")[index]
        self._attr_icon = ("mdi:weather-sunset-up", "mdi:weather-sunset-down")[index]
        super().__init__(entry, device)

    @property
    def native_value(self) -> dt.datetime | None:
        times = sun_times(self.status)
        if times is None:
            return None
        return at_minutes(times[self._index], dt_util.now())


class LarnitechStatusSensor(LarnitechEntity, SensorEntity):
    """Raw state of an element the integration has no dedicated platform for."""

    def __init__(self, entry, device, enabled: bool) -> None:
        super().__init__(entry, device)
        self._attr_entity_registry_enabled_default = enabled
        self._attr_icon = "mdi:shield-home" if device.get("type") in SECURITY_TYPES else "mdi:information-outline"

    @property
    def native_value(self) -> str | None:
        st = self.status.get("state")
        return None if st is None else str(st)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        attrs.update({k.replace("-", "_"): v for k, v in self.status.items() if k != "state"})
        return attrs
