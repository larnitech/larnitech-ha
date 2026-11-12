"""Hand the controllers over to version 2.0.0, which uses the domain larnitech again.

HACS installs 2.0.0 into custom_components/larnitech and leaves this folder in place, so after the
restart both versions are there and only this one has config entries. Once Home Assistant has
started, this version checks whether the larnitech folder is ours (domain larnitech, version 2.0.0
or newer, this repository) and, if so, starts an import flow of the new domain for each entry. The
2.0.0 code then moves the entities, devices and exported elements and removes this entry and folder.

Nothing happens when the larnitech folder is missing or belongs to another integration. Errors are
logged and never stop this version from working.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from awesomeversion import AwesomeVersion

from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

NEW_DOMAIN = "larnitech"
NEW_VERSION = AwesomeVersion("2.0.0")
REPOSITORY = "github.com/larnitech/larnitech-ha"


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        info = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def is_our_new_version(info: dict[str, Any] | None) -> bool:
    """The manifest of 2.0.0 or newer of this integration (not the other Larnitech integration)."""
    if not info or info.get("domain") != NEW_DOMAIN:
        return False
    try:
        if AwesomeVersion(str(info.get("version", ""))) < NEW_VERSION:
            return False
    except Exception:  # noqa: BLE001 - not a version
        return False
    urls = f"{info.get('documentation', '')} {info.get('issue_tracker', '')}"
    return REPOSITORY in urls or "@larnitech" in (info.get("codeowners") or [])


async def _async_hand_over(hass: HomeAssistant) -> None:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return
    folder = Path(hass.config.path("custom_components", NEW_DOMAIN))
    info = await hass.async_add_executor_job(_read_manifest, folder)
    if not is_our_new_version(info):
        return
    _LOGGER.info(
        "Larnitech %s is installed: moving %d controller(s) from %s to %s",
        info.get("version"), len(entries), DOMAIN, NEW_DOMAIN,
    )
    for entry in entries:
        try:
            result = await hass.config_entries.flow.async_init(
                NEW_DOMAIN, context={"source": SOURCE_IMPORT}, data={"entry_id": entry.entry_id}
            )
        except Exception:  # noqa: BLE001 - the move must never break this version
            _LOGGER.exception("Could not move %s to %s; it keeps working with this version", entry.title, NEW_DOMAIN)
            continue
        _LOGGER.debug("Move of %s: %s", entry.title, result.get("type"))


async def _async_safe_hand_over(hass: HomeAssistant) -> None:
    try:
        await _async_hand_over(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Checking for Larnitech 2.0.0 failed")


def async_setup_hand_over(hass: HomeAssistant) -> None:
    """Check once Home Assistant has started (or now, if it is running already)."""
    if hass.is_running:
        hass.async_create_background_task(_async_safe_hand_over(hass), f"{DOMAIN} hand-over")
        return

    async def _on_started(_event: Event) -> None:
        await _async_safe_hand_over(hass)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
