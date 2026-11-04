# Larnitech for Home Assistant

Local integration of the [Larnitech](https://larnitech.com) smart home controller with Home Assistant.

- **Local push**: Home Assistant talks to the controller over its API2 WebSocket (port 2041). No cloud, no MQTT broker, changes arrive instantly in both directions.
- **Everything from the project**: lights, dimmers, RGB and tunable white, relays and water valves, blinds and gates, climate controllers (modes, heat/cool setpoints, automations as presets), air conditioners, fancoils, heating valves, ventilation, temperature / humidity / illuminance / CO₂ sensors, motion, door and leak sensors, light scenes, scripts, wall switches and remotes (as events for automations).
- **Rooms**: every element becomes a Home Assistant device in the area named after its Larnitech room.
- **Home Assistant devices in Larnitech**: pick any lights, switches, covers, climate, locks, fans and sensors in the integration options and they become real Larnitech elements, visible in the Larnitech app and usable in Larnitech scripts and Blockly.
- Remote access through `SERIAL.in.larnitech.com:8443` works too.

## Install

1. In HACS open ⋮ → *Custom repositories*, add `https://github.com/larnitech/larnitech-ha` with type *Integration*, install **Larnitech** and restart Home Assistant.
2. *Settings → Devices & services → Add integration → Larnitech*.
3. Enter the controller address, port `2041` and the API key. LT Setup shows all three on *Plugins → Home Assistant*.

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=larnitech)

## Options

- **Home Assistant devices in Larnitech**: entities that are registered on the controller as Larnitech elements.
- **Hide element types**: Larnitech element types that are not added to Home Assistant.

## Services

- `larnitech.set_status`: send any status to an element, e.g. `addr: "585:17"`, `status: {"state": "on", "level": 50}`.
- `larnitech.reload_devices`: reload the element list after the project was changed in LT Setup.

Wall switches, remotes and IR receivers fire `larnitech_event` events and appear as event entities.

---

# Larnitech для Home Assistant

Локальная интеграция контроллера Larnitech с Home Assistant: все элементы проекта появляются в HA по комнатам, обратная связь мгновенная в обе стороны, без облака и MQTT. В настройках интеграции можно выбрать устройства Home Assistant, которые станут элементами Larnitech и будут доступны в приложении Larnitech, сценариях и Blockly.

Установка: HACS → ⋮ → Пользовательские репозитории → `https://github.com/larnitech/larnitech-ha` (Integration) → установить **Larnitech** → перезапустить HA → Настройки → Устройства и службы → Добавить интеграцию → Larnitech. Адрес, порт и ключ API показаны в LT Setup: Плагины → Home Assistant.
