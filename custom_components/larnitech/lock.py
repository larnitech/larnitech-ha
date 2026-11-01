"""Locks: Larnitech lamp elements with sub-type "lock".

Such an output drives an electric lock: "on" opens it (a short pulse, channel type "Lock (short press)",
the controller switches it back off by itself), "off" is the idle, locked state.
"""
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
        on = is_on_value(self.status.get("state"))
        return None if on is None else not on

    async def async_lock(self, **kwargs: Any) -> None:
        await self._send({"state": "off"})

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._send({"state": "on"})
