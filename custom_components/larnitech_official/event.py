"""Wall switches: every status change is an HA event."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import EVENT_LARNITECH, EVENT_TYPES
from .entity import LarnitechEntity, devices_of

EVENT_NAMES = ["press", "release", "double_press", "long_press", "on", "off", "code", "change"]


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechEvent(entry, d) for d in devices_of(entry, EVENT_TYPES))


class LarnitechEvent(LarnitechEntity, EventEntity):
    _attr_event_types = EVENT_NAMES

    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        self._attr_device_class = EventDeviceClass.BUTTON

    def _handle_status(self, status: dict[str, Any], source: str) -> None:
        # only real presses: not the re-read after a reconnect
        if source != "push":
            return
        raw = str(status.get("event", status.get("state", "change"))).lower().replace("-", "_")
        name = raw if raw in EVENT_NAMES else ("code" if "code" in status else "change")
        data = {k.replace("-", "_"): v for k, v in status.items()}
        self._trigger_event(name, data)
        self.hass.bus.async_fire(EVENT_LARNITECH, {"addr": self.addr, "name": self._dev.get("name"), "event": name, **data})
