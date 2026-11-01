"""Relays, sockets, valves."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import SWITCH_TYPES
from .entity import LarnitechEntity, devices_of, is_on_value, kind_of

ICONS = {"valve": "mdi:pipe-valve", "speaker": "mdi:speaker"}


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechSwitch(entry, d) for d in devices_of(entry, SWITCH_TYPES))


class LarnitechSwitch(LarnitechEntity, SwitchEntity):
    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        kind = kind_of(device)
        self._attr_icon = ICONS.get(kind)
        self._attr_device_class = SwitchDeviceClass.OUTLET if kind == "socket" else SwitchDeviceClass.SWITCH

    @property
    def is_on(self) -> bool | None:
        return is_on_value(self.status.get("state"))

    @property
    def _valve(self) -> bool:
        return self._dev.get("type") == "valve"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send({"state": "open" if self._valve else "on"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send({"state": "close" if self._valve else "off"})
