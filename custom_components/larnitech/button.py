"""Scripts run as buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import BUTTON_TYPES
from .entity import LarnitechEntity, devices_of


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechScriptButton(entry, d) for d in devices_of(entry, BUTTON_TYPES))


class LarnitechScriptButton(LarnitechEntity, ButtonEntity):
    _attr_icon = "mdi:script-text-play"

    async def async_press(self) -> None:
        await self._send({"state": "on"})
