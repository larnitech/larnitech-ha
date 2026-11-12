# Larnitech for Home Assistant

Local integration of the [Larnitech](https://larnitech.com) smart home controller with Home Assistant.

- **Local push**: Home Assistant talks to the controller over its API2 WebSocket (port 2041). No cloud, no MQTT broker, changes arrive instantly in both directions.
- **Everything from the project**: lights, dimmers, RGB and tunable white, relays and water valves, blinds and gates, climate controllers (modes, heat/cool setpoints, automations as presets), air conditioners, fancoils, heating valves, ventilation, temperature / humidity / illuminance / CO₂ / voltage / current / power / frequency / consumption / percent / number sensors, motion, door and leak sensors, light scenes, scripts, wall switches (as events for automations), virtual sensors and texts of scripts and plugins, the weather plugin (weather with forecast, sunrise and sunset).
- **Values as the Larnitech app shows them**:
  - the illumination sensor is a relative level (0–100 %); Home Assistant shows it in lux with the app's own formula (the same as the app with *Lux measuring* on, at most 2000 lx) and keeps the level as the `level` attribute;
  - a motion sensor reports a motion level (0–100 %); it is *on* when the level is above the threshold in the options (15 % by default). Optionally each motion sensor also gets a sensor with the level in %;
  - voltage, power, frequency, consumption, percent and number sensors are 8.8 fixed-point values (V, kW, Hz, kWh, %, number); current sensors are in mA;
  - a virtual sensor takes its unit from its `dim` attribute.
- **Not added**, because Home Assistant cannot use them through API2: IR transmitters and receivers, remote controls, cameras (rtsp), intercoms, speakers and microphones, COM ports, card readers and the app's formatted text panels (`prf`). Entities of earlier versions for these are removed from the registry on update.
- **Rooms**: every element becomes a Home Assistant device in the area named after its Larnitech room.
- **Home Assistant devices in Larnitech**: pick any lights, switches, covers, climate, locks, fans and sensors in the integration options and they become real Larnitech elements, visible in the Larnitech app and usable in Larnitech scripts and Blockly.

  | Home Assistant entity | Larnitech element |
  |---|---|
  | light | lamp, dimmer-lamp (with colour temperature for tunable-white lights) or rgb-lamp |
  | switch, input_boolean, lock, siren, script, automation | lamp |
  | fan | lamp or dimmer-lamp |
  | cover | blinds |
  | climate | conditioner or valve-heating (the conditioner setpoint is in whole degrees: 25.5 shows as 26) |
  | binary_sensor | motion-sensor, leak-sensor or door-sensor |
  | sensor: temperature, humidity, CO₂ | temperature-, humidity-, co2-sensor |
  | sensor: illuminance (lx) | virtual text, for example "850 lx" (the illumination sensor is a relative level in %, at most 127.99) |
  | sensor: power, voltage, current, frequency | power-sensor (kW), voltage-sensor (V), current-sensor (mA), freq-sensor (Hz); a value above 255.99 kW / V / Hz (or below 0) is shown as text |
  | sensor: battery or unit % | percent-sensor (0–100 %) |
  | other numeric sensor, input_number, number | virtual sensor with the unit (−128…127.99), else text |
  | energy, non-numeric sensor, input_text, input_select, select, device_tracker, person | virtual text (value and unit) |

  For numeric sensors the options let you choose how they appear in Larnitech: the dedicated sensor, percent sensor, number sensor (0–255.99), virtual sensor with unit, or text. Entities that cannot be exported (for example, an entity that no longer exists) are listed in the options and in *Settings → Repairs*.
- Remote access through `SERIAL.in.larnitech.com:8443` works too.

## Install

1. Open HACS, search for **Larnitech**, install it and restart Home Assistant. The search finds it once it is listed in the HACS default repositories. Until then use the button or the custom repository steps below.
2. *Settings → Devices & services → Add integration → Larnitech*.
3. Enter the controller address, port `2041` and the API key. LT Setup shows all three on *Plugins → Home Assistant*.

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=larnitech_official)

### Install as a custom repository

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=larnitech&repository=larnitech-ha&category=integration)

In HACS open ⋮ → *Custom repositories*, add `https://github.com/larnitech/larnitech-ha` with type *Integration*, install **Larnitech** and restart Home Assistant. Then continue from step 2 above.

### Migrating from 1.1.x

Version 1.2.0 changes the integration domain from `larnitech` to `larnitech_official`, so it can be installed next to the other Larnitech integration in HACS.

1. Remove the old Larnitech integration in *Settings → Devices & services*.
2. Update to 1.2.0 in HACS and restart Home Assistant. Delete the old `custom_components/larnitech` folder if it remains.
3. Add the integration again.

Automations and scripts need updating: the services are now `larnitech_official.*` and the event is `larnitech_official_event`.

## Options

- **Home Assistant devices in Larnitech**: entities that are registered on the controller as Larnitech elements.
- **Show in Larnitech as**: the next step, shown when a selected entity is a number, sets the Larnitech element type for each such entity. You can change it later; the element keeps its address.
- **Hide element types**: Larnitech element types that are not added to Home Assistant.
- **Motion threshold**: a motion level above this value counts as motion (default 15 %).
- **Motion level sensors**: also add a sensor with the motion level in % for each motion sensor (off by default).
- **Add system sensors**: also add the sensors marked `system="yes"` in the project: module temperatures and supply voltages, motor currents, CPU usage and load, weather plugin items (off by default). Controllable system elements (lamps, scripts) are always added.

## Services

- `larnitech_official.set_status`: send any status to an element, e.g. `addr: "585:17"`, `status: {"state": "on", "level": 50}`.
- `larnitech_official.reload_devices`: reload the element list after the project was changed in LT Setup.

Wall switches fire `larnitech_official_event` events and appear as event entities.

---

# Larnitech для Home Assistant

Локальная интеграция контроллера Larnitech с Home Assistant: все элементы проекта появляются в HA по комнатам, обратная связь мгновенная в обе стороны, без облака и MQTT. В настройках интеграции можно выбрать устройства Home Assistant, которые станут элементами Larnitech и будут доступны в приложении Larnitech, сценариях и Blockly. Числовые датчики (батарея, загрузка процессора, давление, мощность и т. д.) можно показать как датчик процентов, числовой датчик, виртуальный датчик с единицей измерения или текст; тип выбирается на следующем шаге настроек.

Датчик освещённости Larnitech хранит относительный уровень (0–100 %); в HA он показывается в люксах по формуле приложения Larnitech (как в приложении с включённым «Lux measuring»), уровень в % остаётся в атрибуте `level`. Датчик движения сообщает уровень движения (0–100 %): движением считается уровень выше порога из настроек (по умолчанию 15 %); по желанию для каждого датчика движения добавляется датчик уровня в %. ИК-передатчики и приёмники, пульты, камеры (rtsp), домофоны, колонки и микрофоны в HA не добавляются. Системные датчики (отмеченные в проекте `system="yes"`: температуры и напряжения модулей, токи моторов, загрузка процессора, элементы плагина погоды) добавляются только при включённой настройке «Добавить системные датчики».

Установка: HACS → поиск **Larnitech** (когда интеграция появится в списке HACS) → установить → перезапустить HA → Настройки → Устройства и службы → Добавить интеграцию → Larnitech. Адрес, порт и ключ API показаны в LT Setup: Плагины → Home Assistant.

Пока интеграции нет в списке: HACS → ⋮ → Пользовательские репозитории → `https://github.com/larnitech/larnitech-ha` (Integration) → установить **Larnitech**, дальше так же.

Переход с 1.1.x: в версии 1.2.0 домен изменился на `larnitech_official`. Удалите старую интеграцию Larnitech, обновитесь в HACS, перезапустите HA, удалите папку `custom_components/larnitech`, если она осталась, и добавьте интеграцию заново. В автоматизациях и сценариях замените службы на `larnitech_official.*` и событие на `larnitech_official_event`.
