"""Repair entity ids left behind by a renamed device.

Pinning entity ids (1.1.1) fixed *new* registrations only. Home Assistant
never renames an entity it has already registered, so an instance whose device
was called "Virtual Munskänkarna" when the sensors first appeared keeps
`sensor.virtual_munskankarna_*` for ever, and the shipped dashboard points at
entities that do not exist.

Those entities are not missing — they are registered under the wrong id, so
adding "empty" ones is impossible: the unique_id is already taken and a second
entity would only get a suffixed id. The fix is to rename what is there.

What may be renamed unasked is exactly what Home Assistant can be *shown* to
have produced: the device's current name followed by the entity name. Nothing
else qualifies, however much it looks the part. `sensor.cellar_munskankarna_history`
is what a device called "Cellar Munskänkarna" would have produced and equally
what somebody would type to name this sensor in their cellar — the two are
indistinguishable, so renaming it is a guess, and a guess here silently breaks
whatever referenced the old id.

The rest is therefore offered rather than taken: every id that does not match
is raised as a fixable repair issue naming it and its target, which renames
them on confirmation. That keeps the decision with the person who made it
while costing them one click, and it replaces the silence that made a
mismatched dashboard so hard to account for — the entity exists, it holds
data, and nothing said why the card could not find it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import slugify

from .const import DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

ISSUE_MISMATCHED_IDS = "mismatched_entity_ids"

#: The README section explaining why an id can end up mismatched.
LEARN_MORE_URL = "https://github.com/GuvHas/winedata#entities"


def canonical_object_id(name: str) -> str:
    """The object_id an entity gets under the integration's own name.

    Shared with the sensor platform so the id a fresh install registers and
    the id this migration repairs towards can never drift apart.
    """
    return slugify(f"{DEFAULT_NAME} {name}")


@dataclass(frozen=True, slots=True)
class Mismatch:
    """An entity whose id is not the one the shipped dashboard references."""

    entity_id: str
    #: The id it would have on a fresh install.
    wanted: str
    #: Home Assistant can be shown to have produced `entity_id` — it is exactly
    #: what it derives from the device's current name plus the entity name.
    generated: bool
    #: Nothing else holds `wanted`, so the rename has somewhere to go.
    free: bool

    def __str__(self) -> str:
        return f"{self.entity_id} → {self.wanted}" + ("" if self.free else " (taken)")


def issue_id(entry: ConfigEntry) -> str:
    """One issue per config entry, so two entries cannot overwrite each other."""
    return f"{ISSUE_MISMATCHED_IDS}_{entry.entry_id}"


@callback
def async_plan(hass: HomeAssistant, entry: ConfigEntry) -> list[Mismatch]:
    """Every entity of this entry whose id is not its canonical one."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    plan: list[Mismatch] = []

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
        plan.append(
            Mismatch(
                entity_id=existing.entity_id,
                wanted=wanted,
                generated=current == slugify(f"{device_name} {name}"),
                free=registry.async_get(wanted) is None
                and hass.states.get(wanted) is None,
            )
        )

    return plan


@callback
def async_apply(
    hass: HomeAssistant, entry: ConfigEntry, *, consented: bool
) -> list[Mismatch]:
    """Rename what may be renamed and return what is left.

    Without consent only a provably generated id moves. With it — the repair
    issue was confirmed — every id whose target is free moves, because the
    person who might have chosen it has just said to.
    """
    registry = er.async_get(hass)
    left: list[Mismatch] = []

    for mismatch in async_plan(hass, entry):
        if not mismatch.free:
            _LOGGER.warning(
                "Leaving %s alone: %s is already taken, so the dashboard needs "
                "editing or the other entity renaming",
                mismatch.entity_id,
                mismatch.wanted,
            )
            left.append(mismatch)
            continue

        if not (mismatch.generated or consented):
            left.append(mismatch)
            continue

        _LOGGER.info(
            "Renaming %s to %s so it matches the documented dashboard",
            mismatch.entity_id,
            mismatch.wanted,
        )
        registry.async_update_entity(mismatch.entity_id, new_entity_id=mismatch.wanted)

    return left


@callback
def async_report(hass: HomeAssistant, entry: ConfigEntry, left: list[Mismatch]) -> None:
    """Raise, refresh, or withdraw the repair issue for what is left."""
    if not left:
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry))
        return

    listing = ", ".join(str(mismatch) for mismatch in left)
    _LOGGER.warning(
        "These entity ids do not match the ids the example dashboard uses: %s. "
        "Settings > Repairs offers to rename them",
        listing,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry),
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MISMATCHED_IDS,
        translation_placeholders={"count": str(len(left))},
        learn_more_url=LEARN_MORE_URL,
        data={"entry_id": entry.entry_id},
    )


@callback
def async_rename_on_request(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename everything still movable. Called only from the repair flow."""
    async_report(hass, entry, async_apply(hass, entry, consented=True))


@callback
def async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move provably generated ids, and keep the report on the rest current."""
    async_report(hass, entry, async_apply(hass, entry, consented=False))

    @callback
    def _registry_changed(event: Event) -> None:
        """Reconcile after a rename made outside this integration.

        Setup is not the only moment an id changes: somebody following the
        repair's instructions renames entities while the entry stays loaded,
        and nothing reloads it. Reporting only — renaming here would chase
        the events the renames themselves raise.
        """
        data = event.data
        if data["action"] == "update" and "entity_id" not in data.get("changes", {}):
            return
        async_report(hass, entry, async_plan(hass, entry))

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_changed)
    )
