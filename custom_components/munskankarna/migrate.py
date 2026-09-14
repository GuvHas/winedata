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

That guarantee costs coverage, deliberately. The legacy id is recognised by
re-deriving it from the device's *current* name, and Home Assistant keeps no
record of a device's previous names — so an entity left on
`sensor.virtual_munskankarna_history` by a device since renamed again is
indistinguishable from an id a person picked, and is left alone. The README
documents that case and the manual fix; guessing at it is the one thing worse
than leaving it.
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


def _derived_object_id(entry: er.RegistryEntry, device_name: str) -> str:
    """The one object_id Home Assistant itself could have derived for `entry`.

    Every sensor this integration has ever registered set `has_entity_name`,
    from the commit that introduced the platform onwards, so Home Assistant
    always prefixed the device name. A bare `sensor.history` was therefore
    never produced by any released version — only by a person — and treating
    it as auto-derived would rewrite a deliberate id while covering no install
    that exists.
    """
    return slugify(f"{device_name} {entry.original_name or ''}")


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
        if current != _derived_object_id(existing, device_name):
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
