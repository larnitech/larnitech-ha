"""Motion, door, leak and similar sensors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import BINARY_SENSOR_TYPES, CONF_MOTION_THRESHOLD, DEFAULT_MOTION_THRESHOLD
from .decode import motion_detected, word_value
from .entity import LarnitechEntity, devices_of, is_on_value, kind_of

CLASSES = {
    "motion-sensor": BinarySensorDeviceClass.MOTION,
    "presence-sensor": BinarySensorDeviceClass.OCCUPANCY,
    "door-sensor": BinarySensorDeviceClass.DOOR,
    "window-sensor": BinarySensorDeviceClass.WINDOW,
    "leak-sensor": BinarySensorDeviceClass.MOISTURE,
    "smoke-sensor": BinarySensorDeviceClass.SMOKE,
}
# a door-sensor (two-state input) with a sub-type, as the Larnitech app shows it
SUB_CLASSES = {
    "motion": BinarySensorDeviceClass.MOTION,
    "fire": BinarySensorDeviceClass.HEAT,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "gas": BinarySensorDeviceClass.GAS,
    "co2": BinarySensorDeviceClass.GAS,
    "leak": BinarySensorDeviceClass.MOISTURE,
    "lock": BinarySensorDeviceClass.LOCK,
    "alarm": BinarySensorDeviceClass.SAFETY,
    "contact": BinarySensorDeviceClass.OPENING,
    "glass": BinarySensorDeviceClass.TAMPER,
}


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    threshold = float(entry.options.get(CONF_MOTION_THRESHOLD, DEFAULT_MOTION_THRESHOLD))
    async_add(LarnitechBinarySensor(entry, d, threshold) for d in devices_of(entry, BINARY_SENSOR_TYPES))


class LarnitechBinarySensor(LarnitechEntity, BinarySensorEntity):
    def __init__(self, entry, device, threshold: float = DEFAULT_MOTION_THRESHOLD) -> None:
        super().__init__(entry, device)
        self._kind = kind_of(device)
        self._threshold = threshold
        self._attr_device_class = CLASSES.get(self._kind)
        if self._kind == "door-sensor" and device.get("sub-type") in SUB_CLASSES:
            self._attr_device_class = SUB_CLASSES[device["sub-type"]]

    @property
    def is_on(self) -> bool | None:
        if self._kind == "motion-sensor":
            # a level 0..100 %, not on/off: any level above 0 would always read as motion
            return motion_detected(self.status, self._threshold)
        st = self.status.get("state")
        if isinstance(st, str):
            text = st.strip().lower()
            if text in ("closed", "close", "ok", "normal", "no"):
                return False
            if text in ("opened", "open", "leak", "leakage", "alarm", "fire"):
                return True
        return is_on_value(st)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        if self._kind == "motion-sensor":
            attrs["level"] = word_value("motion-sensor", self.status)
            attrs["threshold"] = self._threshold
        return attrs
