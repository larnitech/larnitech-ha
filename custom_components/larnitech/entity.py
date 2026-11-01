"""Base entity for Larnitech elements."""
from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import LarnitechConfigEntry
from .api import LarnitechClient, LarnitechError
from .const import APP_ID, CONF_IGNORED_TYPES, DOMAIN, MANUFACTURER, SKIP_TYPES

# Home Assistant 2026.10 links a device to its hub by device id; "via_device" is removed in 2027.8
VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__

ON_VALUES = {"on", "1", "true", "open", "opened", "alarm", "detected", "motion", "leak", "yes", "middle", "opening", "closing"}


def is_on_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("undefined", "unknown", ""):
        return None
    return text in ON_VALUES


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


VIRTUAL_ALIASES = {"jalousie120": "jalousie", "gate120": "gate", "ventilation": "vent"}


def kind_of(device: dict[str, Any]) -> str:
    """Element type; a virtual element counts as its sub-type (type="virtual" sub-type="lamp")."""
    kind = str(device.get("type") or "")
    if kind == "lamp" and device.get("sub-type") == "lock":
        return "lock"
    if kind == "virtual":
        sub = str(device.get("sub-type") or "")
        return VIRTUAL_ALIASES.get(sub, sub) or kind
    return kind


def devices_of(entry: LarnitechConfigEntry, types: set[str]) -> list[dict[str, Any]]:
    ignored = set(entry.options.get(CONF_IGNORED_TYPES, [])) | SKIP_TYPES
    return [
        d
        for d in entry.runtime_data.client.devices.values()
        if kind_of(d) in types and kind_of(d) not in ignored and not is_own(d)
    ]


def is_own(device: dict[str, Any]) -> bool:
    """Elements this integration created for Home Assistant entities: never mirror them back."""
    return bool(device.get("ha-owned")) or device.get("plugin") == APP_ID


class LarnitechEntity(Entity):
    """One Larnitech element (addr) shown as one HA device with suggested area."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: LarnitechConfigEntry, device: dict[str, Any]) -> None:
        self._client: LarnitechClient = entry.runtime_data.client
        self._dev = device
        self.addr = str(device["addr"])
        hub = entry.runtime_data.hub_id
        self._attr_unique_id = f"{hub}_{self.addr}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{hub}_{self.addr}")},
            name=device.get("name") or self.addr,
            manufacturer=MANUFACTURER,
            model=kind_of(device),
            suggested_area=device.get("area") or None,
            serial_number=self.addr,
        )
        if VIA_DEVICE_ID:
            self._attr_device_info["via_device_id"] = entry.runtime_data.hub_device_id
        else:
            self._attr_device_info["via_device"] = (DOMAIN, hub)

    @property
    def status(self) -> dict[str, Any]:
        return self._dev.get("status") or {}

    @property
    def available(self) -> bool:
        return self._client.connected and self.addr in self._client.devices

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"larnitech_addr": self.addr, "larnitech_type": self._dev.get("type")}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_status_listener(self._on_status))
        self.async_on_remove(self._client.add_connection_listener(self._on_connection))

    @callback
    def _on_status(self, addr: str, status: dict[str, Any], source: str) -> None:
        if addr == self.addr:
            self._handle_status(status, source)
            self.async_write_ha_state()

    def _handle_status(self, status: dict[str, Any], source: str) -> None:
        """Hook for subclasses (events)."""

    @callback
    def _on_connection(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def _send(self, status: dict[str, Any] | str) -> None:
        try:
            await self._client.set_status(self.addr, status)
        except LarnitechError as err:
            raise HomeAssistantError(f"Larnitech {self.addr}: {err}") from err
