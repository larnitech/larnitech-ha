"""Motion, door, leak and similar sensors."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import BINARY_SENSOR_TYPES
from .entity import LarnitechEntity, devices_of, is_on_value, kind_of

CLASSES = {
    "motion-sensor": BinarySensorDeviceClass.MOTION,
    "presence-sensor": BinarySensorDeviceClass.OCCUPANCY,
    "door-sensor": BinarySensorDeviceClass.DOOR,
    "window-sensor": BinarySensorDeviceClass.WINDOW,
    "leak-sensor": BinarySensorDeviceClass.MOISTURE,
    "smoke-sensor": BinarySensorDeviceClass.SMOKE,
}


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechBinarySensor(entry, d) for d in devices_of(entry, BINARY_SENSOR_TYPES))


class LarnitechBinarySensor(LarnitechEntity, BinarySensorEntity):
    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        self._attr_device_class = CLASSES.get(kind_of(device))

    @property
    def is_on(self) -> bool | None:
        st = self.status.get("state")
        if isinstance(st, str):
            text = st.strip().lower()
            if text in ("closed", "close", "ok", "normal", "no"):
                return False
            if text in ("opened", "open", "leak", "alarm", "fire"):
                return True
        return is_on_value(st)
