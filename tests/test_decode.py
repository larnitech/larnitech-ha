"""Tests for decoding Larnitech element statuses (decode.py, api.parse_logic_attrs), no controller needed.

The statuses are what API2 get-devices returned on a controller for elements created through the
plugin API with known bytes (1.2.5). Run inside a Home Assistant environment:
    python3 -m pytest tests/test_decode.py      or      python3 tests/test_decode.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.larnitech_official.api import parse_logic_attrs  # noqa: E402
from custom_components.larnitech_official.bridge import level_to_lux  # noqa: E402
from custom_components.larnitech_official.decode import (  # noqa: E402
    at_minutes, is_weather, motion_detected, sensor32_value, sun_times, text_value, virtual_sensor_value,
    weather, word_value,
)
from custom_components.larnitech_official.entity import is_hidden_system, is_system, kind_of  # noqa: E402


# --- 2-byte sensors: API2 gives the raw number for co2 and the types after it ---

def test_voltage_is_volts_times_256():
    # bytes 80 0C (12.5 V, as a module reports its supply voltage) -> API2 {"state": 3200}
    assert word_value("voltage-sensor", {"state": 3200}) == 12.5
    assert word_value("voltage-sensor", {"state": 6144}) == 24.0


def test_power_freq_consumption_percent_float():
    assert word_value("power-sensor", {"state": 3200}) == 12.5
    assert word_value("freq-sensor", {"state": 12800}) == 50.0
    assert word_value("consumption-sensor", {"state": 3200}) == 12.5
    # percent-sensor bytes 80 25 -> 9600 -> 37.5 % (the app shows the high byte, 37 %)
    assert word_value("percent-sensor", {"state": 9600}) == 37.5
    # float-sensor bytes 40 03 -> 832 -> 3.25
    assert word_value("float-sensor", {"state": 832}) == 3.25


def test_current_is_raw_milliamperes_and_co2_ppm():
    assert word_value("current-sensor", {"state": 384}) == 384
    assert word_value("co2-sensor", {"state": 650}) == 650
    assert word_value("co2-sensor", {"state": 65535}) is None


def test_signed_88_types_come_divided():
    assert word_value("motion-sensor", {"state": 40.0}) == 40.0
    assert word_value("illumination-sensor", {"state": 50.0}) == 50.0
    assert word_value("humidity-sensor", {"state": 55.5}) == 55.5
    assert word_value("temperature-sensor", {"state": -20.68}) == -20.68
    # 0x8000 and the "no value" markers
    assert word_value("temperature-sensor", {"state": -128.0}) is None
    assert word_value("temperature-sensor", {"state": -125.0}) is None
    assert word_value("temperature-sensor", {"state": "undefined"}) is None
    assert word_value("voltage-sensor", {}) is None


# --- motion ---

def test_motion_threshold():
    assert motion_detected({"state": 0.0}, 15) is False
    assert motion_detected({"state": 15.0}, 15) is False
    assert motion_detected({"state": 15.5}, 15) is True
    assert motion_detected({"state": 40.0}, 50) is False
    assert motion_detected({"state": "undefined"}, 15) is None


# --- illumination in lux with the app's formula ---

def test_illumination_lux():
    assert round(level_to_lux(0)) == 0
    assert round(level_to_lux(50)) == round(0.0027 * 50 ** 3 - 0.0966 * 50 ** 2 + 2.8386 * 50 - 5.6581)
    assert level_to_lux(100) == 2000


# --- virtual elements ---

def test_virtual_sensor():
    # bytes 80 F5 -> -10.5
    assert virtual_sensor_value({"state": -10.5}, None) == -10.5
    assert virtual_sensor_value({"state": -10.5}, "float") == -10.5
    # vtype int / uint: the app shows the 16-bit number
    assert virtual_sensor_value({"state": 1.0}, "int") == 256
    assert virtual_sensor_value({"state": -10.5}, "int") == -2688
    assert virtual_sensor_value({"state": -10.5}, "uint") == 0xF580
    assert virtual_sensor_value({"state": 1.0}, "long") is None
    assert virtual_sensor_value({"state": "undefined"}, None) is None


def test_sensor32():
    assert sensor32_value({"hex": "0x80800100"}) == 384.5
    assert sensor32_value({"hex": "0x00FFFFFF"}) == -1.0
    assert sensor32_value({"hex": "0x01"}) is None


def test_text_and_long_text():
    assert text_value({"state": "Привет, мир"}) == "Привет, мир"
    assert text_value({"state": "Line one\nВторая строка"}) == "Line one\nВторая строка"
    # a text that only arrives as bytes
    assert text_value({"hex": "0x" + "Тест".encode().hex()}) == "Тест"
    assert text_value({}) is None


def test_sunrise():
    # 95 01 1A 04: sunrise 405 min (06:45), sunset 1050 min (17:30)
    assert sun_times({"hex": "0x95011A04"}) == (405, 1050)
    assert sun_times({"hex": "0xFFFF1A04"}) == (None, 1050)
    assert sun_times({"state": "undefined"}) is None
    today = dt.datetime(2026, 11, 12, 13, 5, 7, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    assert at_minutes(405, today) == dt.datetime(2026, 11, 12, 6, 45, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    assert at_minutes(None, today) is None


WX = "0x0341F6E4026405B4000405F8030A0600050B00FB060C01FA080D02FC07"


def test_weather():
    w = weather({"hex": WX}, dt.date(2026, 11, 12))  # a Thursday
    assert w["code"] == 3 and w["condition"] == "lightning-rainy"
    assert w["humidity"] == 0x41
    assert w["temperature"] == -10
    assert w["pressure"] == 0x02E4  # 740 mmHg
    assert w["wind_speed"] == 5.4  # 5 + 100/250
    assert w["wind_bearing"] == 180
    days = w["forecast"]
    assert len(days) == 5
    # weekday 5 (Saturday): the next Saturday after Thursday
    assert days[0] == {"date": dt.date(2026, 11, 14), "code": 4, "condition": "lightning-rainy", "templow": -8, "temperature": 3}
    assert days[1]["date"] == dt.date(2026, 11, 15) and days[1]["templow"] == 0 and days[1]["temperature"] == 5
    assert [d["date"].weekday() for d in days] == [5, 6, 0, 1, 2]
    assert weather({"hex": "0x0341"}, dt.date(2026, 11, 12)) is None


def test_weather_kind():
    assert is_weather({"addr": "999:9", "type": "virtual"})
    assert is_weather({"addr": "905:15", "type": "virtual", "sub-type": "weather"})
    assert not is_weather({"addr": "999:9", "type": "virtual", "sub-type": "text"})
    assert kind_of({"addr": "999:9", "type": "virtual"}) == "weather"
    assert kind_of({"addr": "999:10", "type": "virtual", "sub-type": "sunrise"}) == "sunrise"
    assert kind_of({"addr": "999:5", "type": "virtual", "sub-type": "long-text"}) == "long-text"


def test_logic_attrs():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><smart-house><area name="A">'
        '<item addr="905:11" type="virtual" sub-type="sensor" dim="hPa" vtype="uint" name="P"/>'
        '<item addr="313:11" type="dimmer-lamp" name="D"/></area></smart-house>'
    )
    assert parse_logic_attrs(xml) == {"905:11": {"dim": "hPa", "vtype": "uint"}}
    assert parse_logic_attrs("not xml") == {}
    assert parse_logic_attrs("") == {}


class _Entry:
    def __init__(self, **options):
        self.options = options


def test_system_sensors_hidden_by_default():
    temp = {"addr": "313:95", "type": "temperature-sensor", "system": "yes"}
    wx = {"addr": "999:9", "type": "virtual", "system": "yes"}
    lamp = {"addr": "999:20", "type": "virtual", "sub-type": "lamp", "system": "yes"}
    script = {"addr": "313:250", "type": "script", "system": "yes"}
    plain = {"addr": "313:15", "type": "temperature-sensor"}
    assert is_system(temp) and not is_system(plain)
    off, on = _Entry(), _Entry(system_sensors=True)
    assert is_hidden_system(off, temp) and is_hidden_system(off, wx)
    assert not is_hidden_system(on, temp) and not is_hidden_system(on, wx)
    # controllable system elements stay
    assert not is_hidden_system(off, lamp) and not is_hidden_system(off, script)
    assert not is_hidden_system(off, plain)


def test_logic_attrs_system():
    xml = '<smart-house><item addr="313:95" system="yes" type="temperature-sensor"/></smart-house>'
    assert parse_logic_attrs(xml) == {"313:95": {"system": "yes"}}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
