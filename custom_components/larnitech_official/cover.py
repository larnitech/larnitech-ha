"""Blinds (jalousie) and gates."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import COVER_TYPES
from .entity import LarnitechEntity, devices_of, kind_of, to_float

# Larnitech reports "position" as how far the blind is CLOSED (0 = open, 100 = closed)
# and takes the same scale in "target"; Home Assistant counts how far it is open.
POSITION_KEYS = ("position",)


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechCover(entry, d) for d in devices_of(entry, COVER_TYPES))


class LarnitechCover(LarnitechEntity, CoverEntity):
    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        kind = kind_of(device)
        self._attr_device_class = CoverDeviceClass.GATE if kind == "gate" else (
            CoverDeviceClass.CURTAIN if kind == "curtain" else CoverDeviceClass.BLIND
        )
        feats = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        st = device.get("status") or {}
        self._pos_key = next((k for k in POSITION_KEYS if k in st), None)
        if self._pos_key:
            feats |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = feats

    @property
    def _state(self) -> str:
        return str(self.status.get("state", "")).lower()

    @property
    def current_cover_position(self) -> int | None:
        if not self._pos_key:
            return None
        pos = to_float(self.status.get(self._pos_key))
        return None if pos is None else round(100 - max(0, min(100, pos)))

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is not None:
            return pos == 0
        if self._state in ("close", "closed", "off"):
            return True
        if self._state in ("open", "opened", "on", "middle"):
            return False
        return None

    @property
    def is_opening(self) -> bool:
        return self._state in ("opening", "open-in-progress", "up")

    @property
    def is_closing(self) -> bool:
        return self._state in ("closing", "close-in-progress", "down")

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send({"state": "open"})

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send({"state": "close"})

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._send({"state": "stop"})

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._send({"target": 100 - int(kwargs[ATTR_POSITION])})
