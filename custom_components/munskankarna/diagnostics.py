"""Diagnostics support for the Munskänkarna integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import MunskankarnaCoordinator

#: Credentials must never reach a downloaded diagnostics file.
TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: MunskankarnaCoordinator = hass.data[DOMAIN][entry.entry_id]

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "tracked_kinds": coordinator.kinds,
            "top_count": coordinator.top_count,
        },
        # Bounded on purpose: a diagnostics dump should stay readable.
        "snapshot": coordinator.as_payload(limit=3),
        "warnings": (coordinator.data or {}).get("warnings", [])[:10],
    }
