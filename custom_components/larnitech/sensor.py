"""Numeric sensors, plus a diagnostic text sensor for element types without a better platform."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSoundPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import (
    BINARY_SENSOR_TYPES, BUTTON_TYPES, CLIMATE_TYPES, COVER_TYPES, EVENT_TYPES, FAN_TYPES,
    LIGHT_TYPES, SCENE_TYPES, SECURITY_TYPES, SENSOR_TYPES, SKIP_TYPES, SWITCH_TYPES, CONF_IGNORED_TYPES,
)
from .entity import LarnitechEntity, kind_of, to_float

SPECS: dict[str, tuple[SensorDeviceClass | None, str | None, tuple[str, ...]]] = {
    "temperature-sensor": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, ("temperature", "value", "state")),
    "humidity-sensor": (SensorDeviceClass.HUMIDITY, PERCENTAGE, ("humidity", "value", "state")),
    "illumination-sensor": (SensorDeviceClass.ILLUMINANCE, LIGHT_LUX, ("illumination", "illuminance", "value", "state")),
    "co2-sensor": (SensorDeviceClass.CO2, CONCENTRATION_PARTS_PER_MILLION, ("co2", "value", "state")),
    "voltage-sensor": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, ("voltage", "value", "state")),
    "current-sensor": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, ("current", "value", "state")),
    "power-sensor": (SensorDeviceClass.POWER, UnitOfPower.WATT, ("power", "value", "state")),
    "energy-sensor": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR, ("energy", "value", "state")),
    "pressure-sensor": (SensorDeviceClass.PRESSURE, UnitOfPressure.HPA, ("pressure", "value", "state")),
    "noise-sensor": (SensorDeviceClass.SOUND_PRESSURE, UnitOfSoundPressure.DECIBEL, ("noise", "value", "state")),
}
HANDLED = (
    LIGHT_TYPES | SWITCH_TYPES | COVER_TYPES | CLIMATE_TYPES | FAN_TYPES | SENSOR_TYPES
    | BINARY_SENSOR_TYPES | SCENE_TYPES | BUTTON_TYPES | EVENT_TYPES | SECURITY_TYPES | SKIP_TYPES
)


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    ignored = set(entry.options.get(CONF_IGNORED_TYPES, []))
    entities: list[SensorEntity] = []
    for dev in entry.runtime_data.client.devices.values():
        kind = kind_of(dev)
        if dev.get("ha-owned"):
            continue
        if kind in ignored:
            continue
        if kind in SPECS:
            entities.append(LarnitechSensor(entry, dev))
        elif kind in SECURITY_TYPES or (kind and kind not in HANDLED):
            entities.append(LarnitechStatusSensor(entry, dev, enabled=kind in SECURITY_TYPES))
    async_add(entities)


class LarnitechSensor(LarnitechEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        self._attr_device_class, self._attr_native_unit_of_measurement, self._keys = SPECS[kind_of(device)]

    @property
    def native_value(self) -> float | None:
        for key in self._keys:
            if key in self.status:
                return to_float(self.status[key])
        return None


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
