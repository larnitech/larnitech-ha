"""Light schemes."""
from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import SCENE_TYPES
from .entity import LarnitechEntity, devices_of


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechScene(entry, d) for d in devices_of(entry, SCENE_TYPES))


class LarnitechScene(LarnitechEntity, Scene):
    async def async_activate(self, **kwargs: Any) -> None:
        await self._send({"state": "on"})
