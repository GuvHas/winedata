"""The Munskänkarna integration.

Brings Munskänkarna's weekly wine reviews into Home Assistant, each matched to
its Systembolaget catalog entry.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_PUBLISH_MQTT, SERVICE_TRIGGER_SYNC
from .coordinator import MunskankarnaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the domain-level service handlers (registered once)."""
    hass.data.setdefault(DOMAIN, {})
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Munskänkarna from a config entry."""
    coordinator = MunskankarnaCoordinator(hass, entry)

    # Raises ConfigEntryNotReady on failure, so Home Assistant retries with
    # backoff instead of publishing entities full of misleading zeros.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove the services along with the last entry.
        if not hass.data[DOMAIN]:
            for service in (SERVICE_TRIGGER_SYNC, SERVICE_PUBLISH_MQTT):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options change (interval, tracked kinds, list size)."""
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinators(hass: HomeAssistant) -> list[MunskankarnaCoordinator]:
    return [
        value
        for value in hass.data.get(DOMAIN, {}).values()
        if isinstance(value, MunskankarnaCoordinator)
    ]


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services, once per Home Assistant instance."""
    if hass.services.has_service(DOMAIN, SERVICE_TRIGGER_SYNC):
        return

    async def handle_trigger_sync(call: ServiceCall) -> None:
        """Force an immediate poll of every configured entry."""
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    async def handle_publish_mqtt(call: ServiceCall) -> None:
        """Publish the current snapshot to MQTT on demand."""
        # Imported lazily: the bridge is optional and pulls in the MQTT
        # integration, which many installations do not have.
        from .mqtt_bridge import async_publish_snapshot

        for coordinator in _coordinators(hass):
            await async_publish_snapshot(
                hass,
                coordinator,
                topic=call.data.get("topic"),
                retain=call.data.get("retain", True),
            )

    hass.services.async_register(DOMAIN, SERVICE_TRIGGER_SYNC, handle_trigger_sync)
    hass.services.async_register(DOMAIN, SERVICE_PUBLISH_MQTT, handle_publish_mqtt)
