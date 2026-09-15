"""Repair entity ids left behind by a renamed device.

Pinning entity ids (1.1.1) fixed *new* registrations only. Home Assistant
never renames an entity it has already registered, so an instance whose device
was called "Virtual Munskänkarna" when the sensors first appeared keeps
`sensor.virtual_munskankarna_*` for ever, and the shipped dashboard points at
entities that do not exist.

Those entities are not missing — they are registered under the wrong id, so
adding "empty" ones is impossible: the unique_id is already taken and a second
entity would only get a suffixed id. The fix is to rename what is there.

The whole risk is overriding an id the *user* chose, so an id is moved only
when it can be placed as one Home Assistant produced — see `_is_auto_derived`
— and never onto an id something else already owns. Whatever is left over is
raised as a repair issue rather than skipped in silence, because silence is
what makes a mismatched dashboard so hard to account for: the entity exists,
it holds data, and nothing says why the card cannot find it.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import slugify

from .const import DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

#: How the integration's own name slugs. Every device this integration creates
#: is named `DEFAULT_NAME`, so this is the tail of every id Home Assistant has
#: derived for it under an unrenamed device.
_OWN_SLUG = slugify(DEFAULT_NAME)

ISSUE_MISMATCHED_IDS = "mismatched_entity_ids"


def canonical_object_id(name: str) -> str:
    """The object_id an entity gets under the integration's own name.

    Shared with the sensor platform so the id a fresh install registers and
    the id this migration repairs towards can never drift apart.
    """
    return slugify(f"{DEFAULT_NAME} {name}")


def _is_auto_derived(current: str, name: str, device_name: str) -> bool:
    """Could Home Assistant itself have produced `current` for this entity?

    Two shapes qualify, and a hand-picked id is neither.

    The first is what Home Assistant derives *now*: the device's current name
    followed by the entity name. The second covers a device renamed more than
    once — Home Assistant keeps no record of previous device names, so the id
    can no longer be re-derived, but it still ends in this integration's own
    slug followed by the entity name, because the device is created as
    "Munskänkarna" and every rename that keeps the word keeps that tail. An id
    spelling out `…munskankarna_history` denotes this entity under any
    reading, so moving it takes away no meaningful choice.

    What stays out is the point: `sensor.min_vinkallare` matches neither, and
    neither does a bare `sensor.history` — the integration has set
    `has_entity_name` since the commit that introduced the sensor platform, so
    no released version ever registered an id without a device name in front.
    """
    suffix = slugify(name)
    return current == slugify(f"{device_name} {name}") or current.endswith(
        f"{_OWN_SLUG}_{suffix}"
    )


@callback
def async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move this entry's auto-derived entity ids onto their canonical form."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    stragglers: list[str] = []

    for existing in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not (name := existing.original_name):
            continue

        current = existing.entity_id.partition(".")[2]
        canonical = canonical_object_id(name)
        if current == canonical:
            continue

        wanted = f"{existing.domain}.{canonical}"
        device = devices.async_get(existing.device_id) if existing.device_id else None
        device_name = (device.name_by_user or device.name or "") if device else ""

        if not _is_auto_derived(current, name, device_name):
            # Home Assistant cannot be shown to have produced this id, so a
            # person may well have. Their choice outranks the dashboard's
            # convenience — but say so, rather than leaving them to guess.
            stragglers.append(f"{existing.entity_id} → {wanted}")
            continue

        if registry.async_get(wanted) is not None or hass.states.get(wanted) is not None:
            _LOGGER.warning(
                "Leaving %s alone: %s is already taken, so the dashboard needs "
                "editing or the other entity renaming",
                existing.entity_id,
                wanted,
            )
            stragglers.append(f"{existing.entity_id} → {wanted} (taken)")
            continue

        _LOGGER.info(
            "Renaming %s to %s so it matches the documented dashboard",
            existing.entity_id,
            wanted,
        )
        registry.async_update_entity(existing.entity_id, new_entity_id=wanted)

    _async_report(hass, entry, stragglers)


@callback
def _async_report(hass: HomeAssistant, entry: ConfigEntry, stragglers: list[str]) -> None:
    """Surface the ids that could not be placed, and clear the notice once none are."""
    issue_id = f"{ISSUE_MISMATCHED_IDS}_{entry.entry_id}"
    if not stragglers:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    _LOGGER.warning(
        "These entity ids do not match the ids the example dashboard uses: %s. "
        "Rename them under Settings > Devices & Services > Entities, or point "
        "the dashboard at the ids you have",
        ", ".join(stragglers),
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MISMATCHED_IDS,
        translation_placeholders={"entities": ", ".join(stragglers)},
    )
