"""The Larnitech integration: Larnitech smart home controller over API2, local push."""
from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LarnitechAuthError, LarnitechClient, LarnitechError
from .bridge import EntityBridge
from .const import CONF_EXPORT_ENTITIES, CONF_KEY, CONF_SERIAL, DOMAIN, MANUFACTURER

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.EVENT,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_SET_STATUS = "set_status"
SERVICE_RELOAD_DEVICES = "reload_devices"


@dataclass
class LarnitechData:
    client: LarnitechClient
    hub_id: str
    bridge: EntityBridge


type LarnitechConfigEntry = ConfigEntry[LarnitechData]


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry) -> bool:
    client = LarnitechClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_KEY]
    )
    bridge = EntityBridge(hass, client, entry.options.get(CONF_EXPORT_ENTITIES, []))
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
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_id)},
        manufacturer=MANUFACTURER,
        model="Smart home controller",
        name=entry.title,
        configuration_url=f"http://{entry.data[CONF_HOST]}/",
    )
    entry.runtime_data = LarnitechData(client, hub_id, bridge)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LarnitechConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.bridge.async_stop()
        await entry.runtime_data.client.close()
    return ok


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
