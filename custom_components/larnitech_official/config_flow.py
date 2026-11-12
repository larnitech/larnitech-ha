"""Config flow for Larnitech."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LarnitechAuthError, LarnitechClient, LarnitechError
from .entity import is_own, kind_of
from .bridge import type_choices
from .const import (
    CONF_EXPORT_ENTITIES, CONF_EXPORT_TYPES, CONF_IGNORED_TYPES, CONF_KEY, CONF_MOTION_LEVEL, CONF_MOTION_THRESHOLD,
    CONF_SERIAL, CONF_SYSTEM_SENSORS, DEFAULT_MOTION_THRESHOLD, DEFAULT_PORT, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _probe(hass, host: str, port: int, key: str) -> tuple[str, int, list[str]]:
    """Connect, authorize, read devices. Returns (serial, device count, types)."""
    session = async_get_clientsession(hass)
    client = LarnitechClient(session, host, port, key)
    try:
        await client.connect()
        devices = await client.load_devices()
    finally:
        await client.close()
    serial = ""
    m = re.match(r"^([0-9a-f]{8})\.in\.larnitech\.com$", host, re.I)
    if m:
        serial = m.group(1).lower()
    else:
        try:
            async with asyncio.timeout(5):
                resp = await session.get(f"http://{host}/sn.php")
                text = await resp.text()
            m = re.search(r"sn:\s*([0-9a-fA-F]{8})", text)
            if m:
                serial = m.group(1).lower()
        except (aiohttp.ClientError, TimeoutError, OSError):
            pass
    types = sorted({str(d.get("type", "")) for d in devices.values() if d.get("type")})
    return serial, len(devices), types


class LarnitechConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            key = user_input[CONF_KEY].strip()
            try:
                serial, count, _ = await _probe(self.hass, host, port, key)
            except LarnitechAuthError:
                errors["base"] = "invalid_auth"
            except LarnitechError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(serial or f"{host}:{port}")
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port, CONF_KEY: key})
                title = f"Larnitech {serial}" if serial else f"Larnitech {host}"
                return self.async_create_entry(
                    title=title,
                    data={CONF_HOST: host, CONF_PORT: port, CONF_KEY: key, CONF_SERIAL: serial},
                    description_placeholders={"count": str(count)},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=(user_input or {}).get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Required(CONF_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None
        if user_input is not None:
            try:
                await _probe(self.hass, entry.data[CONF_HOST], entry.data[CONF_PORT], user_input[CONF_KEY])
            except LarnitechAuthError:
                errors["base"] = "invalid_auth"
            except LarnitechError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_KEY: user_input[CONF_KEY]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_KEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LarnitechOptionsFlow()


EXPORT_DOMAINS = [
    "light", "switch", "input_boolean", "fan", "cover", "climate", "lock", "sensor", "binary_sensor",
    "siren", "script", "automation", "input_number", "number", "input_text", "text", "input_select",
    "select", "device_tracker", "person",
]


class LarnitechOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options = {**self.config_entry.options, **user_input}
            if self._choosable():
                return await self.async_step_types()
            return self._finish({})
        client = self.config_entry.runtime_data.client
        types = sorted({kind_of(d) for d in client.devices.values() if d.get("type") and not is_own(d)})
        current = self.config_entry.options.get(CONF_IGNORED_TYPES, [])
        exported = self.config_entry.options.get(CONF_EXPORT_ENTITIES, [])
        schema = vol.Schema(
            {
                vol.Optional(CONF_EXPORT_ENTITIES, default=exported): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=EXPORT_DOMAINS,
                        multiple=True,
                        exclude_entities=self._own_entities(),
                    )
                ),
                vol.Optional(CONF_IGNORED_TYPES, default=current): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=sorted(set(types) | set(current)), multiple=True)
                ),
                vol.Optional(
                    CONF_MOTION_THRESHOLD,
                    default=self.config_entry.options.get(CONF_MOTION_THRESHOLD, DEFAULT_MOTION_THRESHOLD),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=99, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_MOTION_LEVEL, default=self.config_entry.options.get(CONF_MOTION_LEVEL, False)
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_SYSTEM_SENSORS, default=self.config_entry.options.get(CONF_SYSTEM_SENSORS, False)
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, description_placeholders={"problems": self._problems()}
        )

    async def async_step_types(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """For each exported entity that can be shown in several ways, the Larnitech element type."""
        choosable = self._choosable()
        if user_input is not None:
            return self._finish({eid: kind for eid, kind in user_input.items() if eid in choosable})
        saved = self.config_entry.options.get(CONF_EXPORT_TYPES, {})
        bridge = self.config_entry.runtime_data.bridge
        fields: dict[Any, Any] = {}
        lines = []
        for eid, choices in choosable.items():
            state = self.hass.states.get(eid)
            current = bridge.kind_for(eid, state)
            default = saved.get(eid) if saved.get(eid) in choices else (current if current in choices else choices[0])
            fields[vol.Required(eid, default=default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=choices,
                    translation_key="export_type",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            if state is not None:
                name = state.attributes.get("friendly_name") or eid
                unit = state.attributes.get("unit_of_measurement") or ""
                lines.append(f"- {eid}: {name}, {state.state} {unit}".rstrip())
        return self.async_show_form(
            step_id="types",
            data_schema=vol.Schema(fields),
            description_placeholders={"entities": "\n".join(lines)},
        )

    def _choosable(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for eid in self._options.get(CONF_EXPORT_ENTITIES, []):
            state = self.hass.states.get(eid)
            choices = type_choices(state) if state is not None else []
            if len(choices) > 1:
                result[eid] = choices
        return result

    def _finish(self, chosen: dict[str, str]) -> ConfigFlowResult:
        exported = set(self._options.get(CONF_EXPORT_ENTITIES, []))
        saved = self.config_entry.options.get(CONF_EXPORT_TYPES, {})
        # keep the choice of an entity that is still exported but has no state right now
        kept = {eid: kind for eid, kind in saved.items() if eid in exported and eid not in chosen
                and self.hass.states.get(eid) is None}
        self._options[CONF_EXPORT_TYPES] = {**kept, **chosen}
        return self.async_create_entry(data=self._options)

    def _problems(self) -> str:
        """Exported entities that are not Larnitech elements now, one per line."""
        bridge = self.config_entry.runtime_data.bridge
        if not bridge.errors:
            return ""
        return "\n\n" + "\n".join(f"⚠ {eid}: {err}" for eid, err in sorted(bridge.errors.items()))
    def _own_entities(self) -> list[str]:
        """Entities of this integration: sending them back to the controller would loop."""
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        return [e.entity_id for e in er.async_entries_for_config_entry(reg, self.config_entry.entry_id)]
