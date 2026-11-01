"""Locks: Larnitech lamp elements with sub-type "lock" (on = locked)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import LOCK_TYPES
from .entity import LarnitechEntity, devices_of, is_on_value


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechLock(entry, d) for d in devices_of(entry, LOCK_TYPES))


class LarnitechLock(LarnitechEntity, LockEntity):
    @property
    def is_locked(self) -> bool | None:
        return is_on_value(self.status.get("state"))

    async def async_lock(self, **kwargs: Any) -> None:
        await self._send({"state": "on"})

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._send({"state": "off"})
