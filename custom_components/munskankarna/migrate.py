"""Repair entity ids left behind by a renamed device.

Pinning entity ids (1.1.1) fixed *new* registrations only. Home Assistant
never renames an entity it has already registered, so an instance whose device
was called "Virtual Munskänkarna" when the sensors first appeared keeps
`sensor.virtual_munskankarna_*` for ever, and the shipped dashboard points at
entities that do not exist.

Those entities are not missing — they are registered under the wrong id, so
adding "empty" ones is impossible: the unique_id is already taken and a second
entity would only get a suffixed id. The fix is to rename what is there.

The whole risk is overriding an id the *user* chose, so the migration only
touches an id that is exactly what Home Assistant would have auto-derived when
the entity was registered. A hand-picked id never matches that, and neither
does an id another integration already owns.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)


def canonical_object_id(name: str) -> str:
    """The object_id an entity gets under the integration's own name.

    Shared with the sensor platform so the id a fresh install registers and
    the id this migration repairs towards can never drift apart.
    """
    return slugify(f"{DEFAULT_NAME} {name}")


def _derived_object_ids(entry: er.RegistryEntry, device_name: str) -> set[str]:
    """Every object_id Home Assistant itself could have derived for `entry`.

    With `has_entity_name` the object_id is the device name plus the entity
    name; without it, the entity name alone. Both are listed because an
    install may predate the switch, and neither can collide with a
    deliberately chosen id.
    """
    name = entry.original_name or ""
    return {slugify(f"{device_name} {name}"), slugify(name)}


@callback
def async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move this entry's auto-derived entity ids onto their canonical form."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)

    for existing in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not existing.original_name:
            continue

        current = existing.entity_id.partition(".")[2]
        canonical = canonical_object_id(existing.original_name)
        if current == canonical:
            continue

        device = devices.async_get(existing.device_id) if existing.device_id else None
        device_name = (device.name_by_user or device.name or "") if device else ""
        if current not in _derived_object_ids(existing, device_name):
            # Home Assistant would never have produced this id, so a person
            # did. Their choice outranks the dashboard's convenience.
            continue

        wanted = f"{existing.domain}.{canonical}"
        if registry.async_get(wanted) is not None or hass.states.get(wanted) is not None:
            _LOGGER.warning(
                "Leaving %s alone: %s is already taken, so the dashboard needs "
                "editing or the other entity renaming",
                existing.entity_id,
                wanted,
            )
            continue

        _LOGGER.info(
            "Renaming %s to %s so it matches the documented dashboard",
            existing.entity_id,
            wanted,
        )
        registry.async_update_entity(existing.entity_id, new_entity_id=wanted)
