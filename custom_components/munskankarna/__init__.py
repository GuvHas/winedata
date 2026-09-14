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
from .migrate import async_migrate_entity_ids

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

#: The coordinator lives on `entry.runtime_data`, so the entry type carries it.
#: Platforms annotate their entry with this and get a typed coordinator for free.
type MunskankarnaConfigEntry = ConfigEntry[MunskankarnaCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the domain-level service handlers (registered once)."""
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MunskankarnaConfigEntry) -> bool:
    """Set up Munskänkarna from a config entry."""
    # Before the platforms register anything: Home Assistant keeps the id an
    # entity already has, so a repair has to happen while none are loaded.
    async_migrate_entity_ids(hass, entry)

    coordinator = MunskankarnaCoordinator(hass, entry)

    # Before the first poll, so a restart reuses the retained releases instead
    # of re-reading release pages that cannot have changed.
    await coordinator.async_load_history()

    # Raises ConfigEntryNotReady on failure, so Home Assistant retries with
    # backoff instead of publishing entities full of misleading zeros.
    await coordinator.async_config_entry_first_refresh()

    # Stored on the entry itself rather than in hass.data: Home Assistant
    # clears it automatically on unload, and platforms get it typed.
    entry.runtime_data = coordinator

    _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MunskankarnaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # `entry` is still reported as loaded while its own unload is in progress,
    # so it is excluded explicitly. Without that the "last entry" test never
    # fired and the domain services outlived the integration, leaving
    # munskankarna.trigger_sync registered but wired to nothing.
    if unloaded and not _loaded_entries(hass, exclude=entry.entry_id):
        for service in (SERVICE_TRIGGER_SYNC, SERVICE_PUBLISH_MQTT):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MunskankarnaConfigEntry) -> None:
    """Delete the entry's history cache when it is removed.

    Unload leaves it in place on purpose — a reload or a restart should reuse
    it. Only removing the integration should discard it.
    """
    await MunskankarnaCoordinator(hass, entry).async_remove_storage()


async def async_reload_entry(hass: HomeAssistant, entry: MunskankarnaConfigEntry) -> None:
    """Reload when the options change (interval, tracked kinds, list size)."""
    await hass.config_entries.async_reload(entry.entry_id)


def _loaded_entries(
    hass: HomeAssistant, exclude: str | None = None
) -> list[MunskankarnaConfigEntry]:
    """Entries with a live coordinator, optionally skipping one entry id."""
    return [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.entry_id != exclude
        and isinstance(getattr(entry, "runtime_data", None), MunskankarnaCoordinator)
    ]


def _coordinators(hass: HomeAssistant) -> list[MunskankarnaCoordinator]:
    return [entry.runtime_data for entry in _loaded_entries(hass)]


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
