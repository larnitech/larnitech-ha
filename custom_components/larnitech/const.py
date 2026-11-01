"""Constants for the Larnitech integration."""
from __future__ import annotations

DOMAIN = "larnitech"
DEFAULT_PORT = 2041
MANUFACTURER = "Larnitech"

CONF_KEY = "key"
CONF_SERIAL = "serial"
CONF_IGNORED_TYPES = "ignored_types"
CONF_EXPORT_ENTITIES = "export_entities"

EVENT_LARNITECH = "larnitech_event"

LIGHT_TYPES = {"lamp", "dimer-lamp", "dimmer-lamp", "rgb-lamp"}
SWITCH_TYPES = {"valve", "speaker", "switch-relay", "socket"}
COVER_TYPES = {"jalousie", "gate", "blinds", "curtain"}
CLIMATE_TYPES = {"climate-control", "conditioner", "fancoil", "valve-heating"}
FAN_TYPES = {"vent"}
SENSOR_TYPES = {
    "temperature-sensor",
    "humidity-sensor",
    "illumination-sensor",
    "co2-sensor",
    "voltage-sensor",
    "current-sensor",
    "power-sensor",
    "energy-sensor",
    "pressure-sensor",
    "noise-sensor",
}
BINARY_SENSOR_TYPES = {"motion-sensor", "door-sensor", "leak-sensor", "smoke-sensor", "window-sensor", "presence-sensor"}
SCENE_TYPES = {"light-scheme"}
APP_ID = "home_assistant"
BUTTON_TYPES = {"script"}
EVENT_TYPES = {"remote-control", "ir-receiver", "switch"}
SECURITY_TYPES = {"security"}

# element types that make no sense in Home Assistant
SKIP_TYPES = {"com-port", "goog-control"}
