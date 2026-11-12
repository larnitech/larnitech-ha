"""Regression tests for the export bridge (encode/decode), no controller needed.

Run inside a Home Assistant environment (it imports homeassistant):
    python3 -m pytest tests/test_bridge.py      or      python3 tests/test_bridge.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from homeassistant.core import State  # noqa: E402

from custom_components.larnitech_official.bridge import (  # noqa: E402
    VIRTUAL_TEXT, _parse_hex, complete_status, decode, encode, larnitech_type, pnp_fields, type_choices,
)

PANEL = {"hvac_modes": ["auto", "heat", "cool", "off"], "fan_modes": ["auto", "low", "medium", "high"],
         "temperature": 22.0, "fan_mode": "auto", "min_temp": 16, "max_temp": 30}


def panel(mode="cool", **attrs):
    return State("climate.living_room_panel", mode, {**PANEL, **attrs})


def services(calls):
    return [(d, s, {k: v for k, v in data.items() if k != "entity_id"}) for d, s, data in calls]


# --- conditioner: mode and power from the Larnitech app (1.2.3) ---

def test_mode_change_is_its_own_call():
    # the app sends mode heat: 0x31 (on, mode 3), setpoint unchanged
    calls = services(decode("conditioner", panel("cool"), "0x310600000000"))
    assert calls == [("climate", "set_hvac_mode", {"hvac_mode": "heat"})]
    # never hvac_mode inside set_temperature: Home Assistant only passes it to the entity, many ignore it
    assert not any("hvac_mode" in d for _, s, d in calls if s == "set_temperature")


def test_mode_auto_and_cool():
    assert services(decode("conditioner", panel("heat"), "0x410600000000")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "auto"})]
    assert services(decode("conditioner", panel("auto"), "0x110600000000")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "cool"})]


def test_turn_on_and_off():
    assert services(decode("conditioner", panel("off"), "0x110600000000")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "cool"})]
    assert services(decode("conditioner", panel("cool"), "0x100600000000")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "off"})]


def test_setpoint_and_fan_unchanged_mode():
    assert services(decode("conditioner", panel("cool"), "0x110800000300")) == [
        ("climate", "set_temperature", {"temperature": 24}),
        ("climate", "set_fan_mode", {"fan_mode": "high"})]


def test_on_with_mode_setpoint_fan():
    assert services(decode("conditioner", panel("off"), "0x310800000300")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "heat"}),
        ("climate", "set_temperature", {"temperature": 24}),
        ("climate", "set_fan_mode", {"fan_mode": "high"})]


def test_nothing_changed_no_calls():
    assert decode("conditioner", panel("cool"), "0x110600000000") == []


def test_partial_hex_status_is_completed():
    assert complete_status("0x31", "0x110800000300") == "0x310800000300"
    assert complete_status("0x310900000300", "0x1108") == "0x310900000300"
    assert services(decode("conditioner", panel("cool", temperature=24.0, fan_mode="high"),
                           complete_status("0x31", "0x110800000300"))) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "heat"})]


def test_half_degree_setpoint():
    # the conditioner status holds whole degrees: 25.5 is shown as 26 (half up), 24.5 as 25
    assert _parse_hex(encode("conditioner", panel("cool", temperature=25.5)))[1] == 26 - 16
    assert _parse_hex(encode("conditioner", panel("cool", temperature=24.5)))[1] == 25 - 16
    # a mode change from the app carries 26 back: HA keeps 25.5
    assert services(decode("conditioner", panel("cool", temperature=25.5), "0x310a00000000")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "heat"})]
    # a real setpoint change still goes through
    assert services(decode("conditioner", panel("cool", temperature=25.5), "0x110b00000000")) == [
        ("climate", "set_temperature", {"temperature": 27})]


def test_valve_heating_turn_on_with_setpoint():
    st = State("climate.radiator", "off", {"hvac_modes": ["heat", "off"], "temperature": 20.0})
    # on + setpoint 22.0 (8.8: 0x00 0x16)
    assert services(decode("valve-heating", st, "0x010016")) == [
        ("climate", "set_hvac_mode", {"hvac_mode": "heat"}),
        ("climate", "set_temperature", {"temperature": 22.0})]


# --- tunable white (1.2.3) ---

CT = {"supported_color_modes": ["color_temp"], "color_mode": "color_temp", "min_color_temp_kelvin": 2700,
      "max_color_temp_kelvin": 6500, "brightness": 128, "color_temp_kelvin": 4600}


def test_ct_light_is_two_channel_dimmer():
    st = State("light.ct", "on", CT)
    assert larnitech_type(st) == "dimmer-lamp"
    assert pnp_fields("dimmer-lamp", st) == {"type": "dimmer-lamp", "channels": "2"}
    # entity unavailable at start-up: the stored record keeps the channels
    assert pnp_fields("dimmer-lamp", None, {"type": "dimmer-lamp", "channels": "2"})["channels"] == "2"
    plain = State("light.dim", "on", {"supported_color_modes": ["brightness"], "brightness": 10})
    assert pnp_fields("dimmer-lamp", plain) == {"type": "dimmer-lamp"}


def test_ct_encode():
    b = _parse_hex(encode("dimmer-lamp", State("light.ct", "on", CT)))
    assert b == [1, 125, 125]  # 4600 K is the middle of 2700..6500
    assert _parse_hex(encode("dimmer-lamp", State("light.ct", "on", {**CT, "color_temp_kelvin": 6500})))[2] == 0
    assert _parse_hex(encode("dimmer-lamp", State("light.ct", "on", {**CT, "color_temp_kelvin": 2700})))[2] == 250
    # off: no kelvin in HA, the last value is kept
    off = State("light.ct", "off", {**CT, "brightness": None, "color_temp_kelvin": None})
    assert _parse_hex(encode("dimmer-lamp", off, [1, 125, 40])) == [0, 125, 40]


def test_ct_decode():
    st = State("light.ct", "on", CT)
    assert services(decode("dimmer-lamp", st, "0x017d00")) == [
        ("light", "turn_on", {"brightness": 128, "color_temp_kelvin": 6500})]
    # level only: the kelvin is not touched
    assert services(decode("dimmer-lamp", st, "0x01c87d")) == [("light", "turn_on", {"brightness": 204})]
    assert services(decode("dimmer-lamp", st, "0x007d7d")) == [("light", "turn_off", {})]


def test_rgb_light_stays_rgb():
    st = State("light.rgb", "on", {"supported_color_modes": ["hs", "color_temp"]})
    assert larnitech_type(st) == "rgb-lamp"
    assert pnp_fields("rgb-lamp", st) == {"type": "rgb-lamp"}


# --- illuminance (1.2.3) ---

def test_lux_is_text_by_default():
    st = State("sensor.lux", "850", {"device_class": "illuminance", "unit_of_measurement": "lx"})
    assert larnitech_type(st) == VIRTUAL_TEXT
    assert encode(VIRTUAL_TEXT, st) == "850 lx"
    assert "illumination-sensor" in type_choices(st)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as err:
                failed += 1
                print("FAIL", name, err)
    sys.exit(1 if failed else 0)
