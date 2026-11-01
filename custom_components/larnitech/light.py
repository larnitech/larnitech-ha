"""Lights: lamp, dimmer, RGB."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LarnitechConfigEntry
from .const import LIGHT_TYPES
from .entity import LarnitechEntity, devices_of, is_on_value, kind_of, to_float

# Larnitech API2: level, hue, saturation and colour temperature are all 0..100.
# Colour temperature ("color-white", older firmware "color-temp"): 0 = cold, 100 = warm.
MIN_K = 2000
MAX_K = 6500
CT_KEYS = ("color-white", "color-temp")


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechLight(entry, d) for d in devices_of(entry, LIGHT_TYPES))


class LarnitechLight(LarnitechEntity, LightEntity):
    _attr_min_color_temp_kelvin = MIN_K
    _attr_max_color_temp_kelvin = MAX_K

    def __init__(self, entry, device) -> None:
        super().__init__(entry, device)
        kind = kind_of(device)
        st = device.get("status") or {}
        modes: set[ColorMode] = set()
        if kind == "rgb-lamp":
            if "r" in st or "rgb" in st or "color" in st:
                modes.add(ColorMode.RGB)
            else:
                modes.add(ColorMode.HS)
        self._ct_key = next((k for k in CT_KEYS if k in st), None)
        if self._ct_key is None and device.get("color-temp"):
            self._ct_key = "color-temp"
        if self._ct_key:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes and kind in ("dimer-lamp", "dimmer-lamp", "rgb-lamp"):
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)
        self._attr_supported_color_modes = modes
        if kind in ("dimer-lamp", "dimmer-lamp", "rgb-lamp"):
            self._attr_supported_features = LightEntityFeature.TRANSITION
        self._last_mode = next(iter(modes))

    @property
    def is_on(self) -> bool | None:
        on = is_on_value(self.status.get("state"))
        if on is None and "level" in self.status:
            lvl = to_float(self.status.get("level"))
            return None if lvl is None else lvl > 0
        return on

    @property
    def brightness(self) -> int | None:
        lvl = to_float(self.status.get("level"))
        if lvl is None:
            return None
        return max(0, min(255, round(lvl * 255 / 100)))

    @property
    def color_mode(self) -> ColorMode:
        return self._last_mode

    @property
    def hs_color(self) -> tuple[float, float] | None:
        h, s = to_float(self.status.get("hue")), to_float(self.status.get("saturation"))
        if h is None or s is None:
            return None
        return round(h * 3.6, 1), s

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        st = self.status
        if all(k in st for k in ("r", "g", "b")):
            return int(st["r"]), int(st["g"]), int(st["b"])
        color = st.get("color") or st.get("rgb")
        if isinstance(color, str) and color.lstrip("#").__len__() == 6:
            c = color.lstrip("#")
            return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        ct = to_float(self.status.get(self._ct_key)) if self._ct_key else None
        if ct is None:
            return None
        return round(MAX_K - (MAX_K - MIN_K) * ct / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        cmd: dict[str, Any] = {"state": "on"}
        if ATTR_BRIGHTNESS in kwargs:
            cmd["level"] = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
        if ATTR_HS_COLOR in kwargs:
            cmd["hue"] = round(kwargs[ATTR_HS_COLOR][0] / 3.6)
            cmd["saturation"] = round(kwargs[ATTR_HS_COLOR][1])
            self._last_mode = ColorMode.HS
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            if "color" in self.status:
                cmd["color"] = f"#{r:02x}{g:02x}{b:02x}"
            else:
                cmd.update(r=r, g=g, b=b)
            self._last_mode = ColorMode.RGB
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            k = kwargs[ATTR_COLOR_TEMP_KELVIN]
            cmd[self._ct_key or "color-white"] = max(0, min(100, round((MAX_K - k) * 100 / (MAX_K - MIN_K))))
            self._last_mode = ColorMode.COLOR_TEMP
        if ATTR_TRANSITION in kwargs:
            cmd["auto-period"] = round(kwargs[ATTR_TRANSITION])
        await self._send(cmd)

    async def async_turn_off(self, **kwargs: Any) -> None:
        cmd: dict[str, Any] = {"state": "off"}
        if ATTR_TRANSITION in kwargs:
            cmd["auto-period"] = round(kwargs[ATTR_TRANSITION])
        await self._send(cmd)
