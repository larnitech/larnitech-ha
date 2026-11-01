"""Ventilation."""
from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import FAN_TYPES
from .entity import LarnitechEntity, devices_of, is_on_value, to_float


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechFan(entry, d) for d in devices_of(entry, FAN_TYPES))


class LarnitechFan(LarnitechEntity, FanEntity):
    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        feats = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if "level" in (device.get("status") or {}):
            feats |= FanEntityFeature.SET_SPEED
        self._attr_supported_features = feats

    @property
    def is_on(self) -> bool | None:
        return is_on_value(self.status.get("state"))

    @property
    def percentage(self) -> int | None:
        lvl = to_float(self.status.get("level"))
        return None if lvl is None else int(lvl)

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        cmd: dict[str, Any] = {"state": "on"}
        if percentage is not None:
            cmd["level"] = percentage
        await self._send(cmd)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send({"state": "off"})

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self.async_turn_off()
        else:
            await self._send({"state": "on", "level": percentage})
