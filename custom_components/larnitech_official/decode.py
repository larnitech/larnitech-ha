"""Values of Larnitech elements as API2 reports them, decoded the way the Larnitech app shows them.

No Home Assistant imports: the tests run these directly.

API2 (tcp-api-devices-status-get.cpp) prints a 2-byte sensor ("sensor word") in one of two ways:
- temperature, motion, illumination, humidity and a virtual sensor: the signed 8.8 value (1/256 units)
  as a number, e.g. 21.5;
- co2 and every type after it (current, freq, voltage, power, consumption, percent, float): the raw
  unsigned 16-bit number, e.g. 3200 for 12.5 V.
The modules and the app use 8.8 for these too (a module's supply voltage is volts x 256), so the raw
number is divided by 256 here, except co2 (ppm) and current (the app shows the raw number as mA).
Checked on a controller with elements of each type created through the plugin API.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

# raw 16-bit number / 256 -> value; and the unit the Larnitech app shows
FIXED88_RAW = {"voltage-sensor", "power-sensor", "freq-sensor", "consumption-sensor", "percent-sensor", "float-sensor"}
# 8.8 values the controller already divided; -128 (0x8000), -127 and -125 mean "no value"
UNDEFINED_BELOW = -124.9

WEATHER_ADDR = "999:9"


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def word_value(kind: str, status: dict[str, Any]) -> float | None:
    """The value of a 2-byte sensor element as the Larnitech app shows it."""
    raw = number(status.get("state"))
    if raw is None:
        return None
    if kind in FIXED88_RAW:
        return round(raw / 256, 2)
    if kind in ("current-sensor", "co2-sensor"):
        return None if raw == 0xFFFF else raw
    # signed 8.8, already divided by the controller
    return None if raw < UNDEFINED_BELOW else raw


def virtual_sensor_value(status: dict[str, Any], vtype: str | None) -> float | None:
    """A virtual sensor (sub-type sensor): API2 prints its first two bytes as signed 8.8.

    The app shows vtype="int"/"uint" elements as a plain 16-bit number, so the bytes are rebuilt from the
    8.8 value. vtype="long" (4 bytes) cannot be rebuilt from two bytes: no value.
    """
    value = number(status.get("state"))
    if value is None:
        return None
    vtype = (vtype or "float").lower()
    if vtype == "float":
        return value
    raw = round(value * 256) & 0xFFFF
    if vtype == "uint":
        return raw
    if vtype == "int":
        return raw - 0x10000 if raw & 0x8000 else raw
    return None


def status_bytes(status: dict[str, Any]) -> bytes | None:
    """The raw bytes of an element API2 reports only as {"hex": "0x..."} (generic virtual elements)."""
    text = status.get("hex")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def sensor32_value(status: dict[str, Any]) -> float | None:
    """A virtual sensor32: signed 32-bit value in 1/256 units, little endian (as the app decodes it)."""
    b = status_bytes(status)
    if b is None or len(b) < 4:
        return None
    return int.from_bytes(b[:4], "little", signed=True) / 256


def text_value(status: dict[str, Any]) -> str | None:
    """A virtual text or long-text element: API2 sends the text itself as "state"."""
    st = status.get("state")
    if isinstance(st, str):
        return st
    b = status_bytes(status)
    if b is not None:
        return b.split(b"\0", 1)[0].decode("utf-8", "replace")
    return None


def sun_times(status: dict[str, Any]) -> tuple[int | None, int | None] | None:
    """A virtual sunrise element (weather plugin): two 16-bit numbers, minutes after midnight of
    sunrise and of sunset. Values beyond a day (the sun does not rise or set) give None."""
    b = status_bytes(status)
    if b is None or len(b) < 4:
        return None

    def minutes(lo: int, hi: int) -> int | None:
        v = lo | hi << 8
        return v if v < 24 * 60 else None

    return minutes(b[0], b[1]), minutes(b[2], b[3])


def at_minutes(minutes: int | None, today: dt.datetime) -> dt.datetime | None:
    """Today (in the time zone of `today`) at this many minutes after midnight."""
    if minutes is None:
        return None
    return today.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)


# Weather icons of the Larnitech app (weather/<code>.png) follow the Yahoo weather condition codes
YAHOO_CONDITIONS = {
    0: "exceptional", 1: "exceptional", 2: "exceptional", 3: "lightning-rainy", 4: "lightning-rainy",
    5: "snowy-rainy", 6: "snowy-rainy", 7: "snowy-rainy", 8: "rainy", 9: "rainy", 10: "rainy", 11: "rainy",
    12: "rainy", 13: "snowy", 14: "snowy", 15: "snowy", 16: "snowy", 17: "hail", 18: "snowy-rainy",
    19: "fog", 20: "fog", 21: "fog", 22: "fog", 23: "windy", 24: "windy", 25: "exceptional", 26: "cloudy",
    27: "cloudy", 28: "cloudy", 29: "partlycloudy", 30: "partlycloudy", 31: "clear-night", 32: "sunny",
    33: "clear-night", 34: "sunny", 35: "hail", 36: "sunny", 37: "lightning", 38: "lightning",
    39: "lightning", 40: "rainy", 41: "snowy", 42: "snowy", 43: "snowy", 44: "partlycloudy",
    45: "lightning-rainy", 46: "snowy", 47: "lightning-rainy",
}


def _s8(v: int) -> int:
    return v - 256 if v > 127 else v


def weather(status: dict[str, Any], today: dt.date) -> dict[str, Any] | None:
    """The weather element 999:9 of the weather plugin, decoded as the app does (style-sensor.xml):

    byte 0 condition code, 1 humidity %, 2 temperature °C (signed), 3-4 pressure mmHg, 5-6 wind km/h
    (6 whole, 5 the fraction x 250), 7-8 wind direction °, then 4 bytes per forecast day:
    condition code, weekday (0 = Monday), lowest and highest temperature °C (signed).
    """
    b = status_bytes(status)
    if b is None or len(b) < 9:
        return None
    result: dict[str, Any] = {
        "code": b[0],
        "condition": YAHOO_CONDITIONS.get(b[0]),
        "humidity": b[1],
        "temperature": _s8(b[2]),
        "pressure": b[3] | b[4] << 8,
        "wind_speed": round(b[6] + b[5] / 250, 2),
        "wind_bearing": (b[7] | b[8] << 8) % 360,
        "forecast": [],
    }
    day = today
    for o in range(9, len(b) - 3, 4):
        weekday = b[o + 1]
        if weekday > 6:
            break
        # the next date with that weekday after the previous one (the first forecast day is tomorrow)
        day = day + dt.timedelta(days=(weekday - day.weekday() - 1) % 7 + 1)
        result["forecast"].append({
            "date": day,
            "code": b[o],
            "condition": YAHOO_CONDITIONS.get(b[o]),
            "templow": _s8(b[o + 2]),
            "temperature": _s8(b[o + 3]),
        })
    return result


def is_weather(device: dict[str, Any]) -> bool:
    return device.get("type") == "virtual" and (
        device.get("sub-type") == "weather" or (str(device.get("addr")) == WEATHER_ADDR and not device.get("sub-type"))
    )


def motion_detected(status: dict[str, Any], threshold: float) -> bool | None:
    """A motion sensor reports a level (0-100 %): motion is a level above the threshold."""
    level = word_value("motion-sensor", status)
    if level is None:
        return None
    return level > threshold
