"""The Larnitech integration: Larnitech smart home controller over API2, local push."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import LarnitechAuthError, LarnitechClient, LarnitechError
from .bridge import EntityBridge
from .const import APP_ID, CONF_EXPORT_ENTITIES, CONF_EXPORT_TYPES, CONF_KEY, CONF_SERIAL, DOMAIN, MANUFACTURER
from .handover import async_setup_hand_over

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.EVENT,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WEATHER,
]

SERVICE_SET_STATUS = "set_status"
SERVICE_RELOAD_DEVICES = "reload_devices"


@dataclass
class LarnitechData:
    client: LarnitechClient
    hub_id: str
    bridge: EntityBridge
    hub_device_id: str
    # (platform, unique id) of the entities created in this set-up
    unique_ids: set[tuple[str, str]] = field(default_factory=set)


type LarnitechConfigEntry = ConfigEntry[LarnitechData]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """When version 2.0.0 (domain larnitech) is installed next to this one, hand the controllers over to it."""
    try:
        async_setup_hand_over(hass)
    except Exception:  # noqa: BLE001 - never stop the set-up
        _LOGGER.exception("Could not schedule the check for Larnitech 2.0.0")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry) -> bool:
    client = LarnitechClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_KEY]
    )
    bridge = EntityBridge(
        hass, client, entry.entry_id, entry.options.get(CONF_EXPORT_ENTITIES, []), entry.options.get(CONF_EXPORT_TYPES, {})
    )
    await bridge.async_start()
    try:
        await client.start()
    except LarnitechAuthError as err:
        await bridge.async_stop()
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except LarnitechError as err:
        await bridge.async_stop()
        await client.close()
        raise ConfigEntryNotReady(str(err)) from err

    hub_id = entry.data.get(CONF_SERIAL) or entry.data[CONF_HOST]
    hub_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_id)},
        manufacturer=MANUFACTURER,
        model="Smart home controller",
        name=entry.title,
        configuration_url=f"http://{entry.data[CONF_HOST]}/",
    )
    entry.runtime_data = LarnitechData(client, hub_id, bridge, hub_device.id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale_entities(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    def _on_new_devices(addrs: set[str]) -> None:
        # the project was changed in LT Setup: reload so new elements get entities
        _LOGGER.info("New Larnitech elements %s, reloading", sorted(addrs))
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(client.add_new_devices_listener(_on_new_devices))
    # the key was changed on the controller: ask the user for the new one
    client.on_auth_failed = lambda: entry.async_start_reauth(hass)
    _register_services(hass)
    return True


def _remove_stale_entities(hass: HomeAssistant, entry: LarnitechConfigEntry) -> None:
    """Remove registry entries this version no longer creates (element types that cannot work in Home
    Assistant, motion levels switched off in the options, hidden types, deleted elements), and devices
    left without entities."""
    ent_reg = er.async_get(hass)
    keep = entry.runtime_data.unique_ids
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.platform == DOMAIN and (ent.domain, ent.unique_id) not in keep:
            _LOGGER.debug("Removing %s: no such Larnitech entity any more", ent.entity_id)
            ent_reg.async_remove(ent.entity_id)
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if device.id == entry.runtime_data.hub_device_id:
            continue
        if not er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True):
            dev_reg.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: LarnitechConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.bridge.async_stop()
        await entry.runtime_data.client.close()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: LarnitechConfigEntry) -> None:
    """The integration is deleted: remove the elements it created for Home Assistant entities."""
    if not entry.options.get(CONF_EXPORT_ENTITIES):
        return
    client = LarnitechClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_KEY]
    )
    try:
        await client.connect()
        await client.request({"request": "setup", "appId": APP_ID})
        await client.request({"request": "she-register-pnp", "pnp": []})
    except LarnitechError as err:
        _LOGGER.warning("Could not remove Home Assistant elements from the controller: %s", err)
    finally:
        await client.close()
    await EntityBridge.async_forget(hass, entry.entry_id)


async def _async_reload(hass: HomeAssistant, entry: LarnitechConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _clients(hass: HomeAssistant) -> list[LarnitechClient]:
    return [
        e.runtime_data.client
        for e in hass.config_entries.async_loaded_entries(DOMAIN)
    ]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_STATUS):
        return

    async def set_status(call: ServiceCall) -> None:
        addr = call.data["addr"]
        for client in _clients(hass):
            if addr in client.devices:
                try:
                    await client.set_status(addr, call.data["status"])
                except LarnitechError as err:
                    raise HomeAssistantError(str(err)) from err
                return
        raise HomeAssistantError(f"Larnitech element {addr} not found")

    async def reload_devices(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            hass.config_entries.async_schedule_reload(entry.entry_id)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_STATUS,
        set_status,
        schema=vol.Schema({vol.Required("addr"): cv.string, vol.Required("status"): vol.Any(dict, cv.string)}),
    )
    hass.services.async_register(DOMAIN, SERVICE_RELOAD_DEVICES, reload_devices)
