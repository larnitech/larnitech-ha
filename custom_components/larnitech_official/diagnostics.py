"""Diagnostics download."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LarnitechConfigEntry
from .const import CONF_KEY


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: LarnitechConfigEntry) -> dict[str, Any]:
    client = entry.runtime_data.client
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_KEY}),
        "options": dict(entry.options),
        "connected": client.connected,
        "devices": list(client.devices.values()),
    }
