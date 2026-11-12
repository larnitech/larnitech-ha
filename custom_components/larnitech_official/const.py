"""Constants for the Larnitech integration."""
from __future__ import annotations

DOMAIN = "larnitech_official"
DEFAULT_PORT = 2041
MANUFACTURER = "Larnitech"

CONF_KEY = "key"
CONF_SERIAL = "serial"
CONF_IGNORED_TYPES = "ignored_types"
CONF_EXPORT_ENTITIES = "export_entities"
# entity_id -> Larnitech element type chosen in the options (for entities that can be shown several ways)
CONF_EXPORT_TYPES = "export_types"
# a motion sensor reports a level 0..100 %: above this many % counts as motion
CONF_MOTION_THRESHOLD = "motion_threshold"
DEFAULT_MOTION_THRESHOLD = 15
# also show the motion level of each motion sensor as a sensor in %
CONF_MOTION_LEVEL = "motion_level"
# also add sensors marked system="yes" in the project (module temperatures and supply voltages, CPU usage
# and load, motor currents, weather plugin items); off by default
CONF_SYSTEM_SENSORS = "system_sensors"

EVENT_LARNITECH = "larnitech_official_event"

LOCK_TYPES = {"lock"}
LIGHT_TYPES = {"lamp", "dimer-lamp", "dimmer-lamp", "rgb-lamp"}
SWITCH_TYPES = {"valve", "switch-relay", "socket", "air-fan"}
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
    "freq-sensor",
    "consumption-sensor",
    "percent-sensor",
    "float-sensor",
    "energy-sensor",
    "pressure-sensor",
    "noise-sensor",
}
# virtual elements by sub-type (kind_of gives the sub-type): numeric values, texts, sunrise and sunset
VIRTUAL_SENSOR_TYPES = {"sensor", "sensor32"}
TEXT_TYPES = {"text", "long-text"}
SUN_TYPES = {"sunrise"}
WEATHER_TYPES = {"weather"}
BINARY_SENSOR_TYPES = {"motion-sensor", "door-sensor", "leak-sensor", "smoke-sensor", "window-sensor", "presence-sensor"}
SCENE_TYPES = {"light-scheme"}
APP_ID = "home_assistant"
BUTTON_TYPES = {"script"}
EVENT_TYPES = {"switch"}
SECURITY_TYPES = {"security"}

# element types that cannot work in Home Assistant: no entity. Checked against the controller's API2:
# - ir-transmitter, com-port, security-card-reader: raw byte channels for scripts;
# - ir-receiver: its codes go to Larnitech scripts, API2 has no event for them;
# - remote-control: an IR remote that sends codes (an output), no state;
# - rtsp: a camera stream address, API2 gives no stream;
# - intercom, speaker, mic: SIP and media of the Larnitech app;
# - prf: a formatted text panel of the app built from other elements;
# - panel1, low-level, import-script, goog-control: configuration items.
SKIP_TYPES = {
    "com-port", "goog-control", "ir-transmitter", "ir-receiver", "remote-control", "rtsp", "intercom",
    "speaker", "mic", "security-card-reader", "prf", "panel1", "low-level", "import-script",
}

# kinds that only show values: hidden when the element is a system element (CONF_SYSTEM_SENSORS)
SYSTEM_SENSOR_KINDS = (
    SENSOR_TYPES | BINARY_SENSOR_TYPES | VIRTUAL_SENSOR_TYPES | TEXT_TYPES | SUN_TYPES | WEATHER_TYPES
    | {"motion-sensor", "security", "json"}
)
