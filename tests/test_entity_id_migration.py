"""Repair entity ids left behind by a renamed device.

Pinning entity ids in 1.1.1 fixed *new* registrations. Home Assistant never
renames an existing entity, so an instance that registered entities while its
device was called "Virtual Munskänkarna" keeps `sensor.virtual_munskankarna_*`
for ever, and the shipped dashboard points at entities that do not exist.

The entities are not missing — that is the trap in the error message. They are
registered under the wrong id, so creating "empty" ones is impossible: the
unique_id is already taken, and a second entity would only get a suffixed id.
The fix is to rename what is there.

The whole risk is renaming an id the *user* chose. The migration therefore only
touches an id that is exactly what Home Assistant would auto-derive from the
current device name — a hand-picked id never matches that.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.munskankarna.const import (
    CONF_KINDS,
    DOMAIN,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

INDEX = [
    build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11"),
    build_release("h-2026-09-03", KIND_HITLISTAN, "2026-09-03"),
]

#: unique_id suffix -> (original_name, canonical object_id)
OWNED = {
    "history": ("History", "munskankarna_history"),
    "fynd_history": ("Fynd history", "munskankarna_fynd_history"),
    "top_pick": ("Top pick", "munskankarna_top_pick"),
}


async def _run_setup(hass: HomeAssistant, entry) -> None:
    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if release_id.startswith("t-") else KIND_HITLISTAN
        return {
            "release": build_release(release_id, kind, "2026-09-11", wine_count=1),
            "wines": [build_wine(release_id, "Ett Vin")],
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=list(INDEX))
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _legacy_install(hass: HomeAssistant, device_name: str, prefix: str):
    """An entry whose entities were registered under a renamed device."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})

    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Munskänkarna",
    )
    devices.async_update_device(device.id, name_by_user=device_name)

    registry = er.async_get(hass)
    for suffix, (original_name, _) in OWNED.items():
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_{suffix}",
            suggested_object_id=f"{prefix}_{suffix}",
            config_entry=entry,
            device_id=device.id,
            original_name=original_name,
            has_entity_name=True,
        )
    return entry, registry


async def test_ids_left_by_a_renamed_device_are_repaired(hass: HomeAssistant) -> None:
    """The reported instance, reproduced and then fixed."""
    entry, registry = _legacy_install(hass, "Virtual Munskänkarna", "virtual_munskankarna")

    # Precondition: the dashboard's ids genuinely do not exist yet.
    before = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert "sensor.virtual_munskankarna_history" in before
    assert "sensor.munskankarna_history" not in before

    await _run_setup(hass, entry)

    after = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    for _suffix, (_name, canonical) in OWNED.items():
        assert f"sensor.{canonical}" in after, f"{canonical} was not repaired"
    assert not [e for e in after if "virtual" in e], f"prefixed ids survived: {after}"


async def test_the_repaired_entities_keep_working(hass: HomeAssistant) -> None:
    """A rename must not orphan the entity from its coordinator."""
    entry, _ = _legacy_install(hass, "Virtual Munskänkarna", "virtual_munskankarna")
    await _run_setup(hass, entry)

    state = hass.states.get("sensor.munskankarna_history")
    assert state is not None
    assert state.state not in (None, "unavailable", "unknown")
    assert "releases" in state.attributes


async def test_an_id_the_user_chose_is_left_alone(hass: HomeAssistant) -> None:
    """The one thing worse than a mismatched id is overriding a deliberate one.

    `sensor.min_vinkallare` is not what Home Assistant would derive from any
    device name, so it was hand-picked and must survive.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Munskänkarna",
    )
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_history",
        suggested_object_id="min_vinkallare",
        config_entry=entry, device_id=device.id,
        original_name="History", has_entity_name=True,
    )

    await _run_setup(hass, entry)

    ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert "sensor.min_vinkallare" in ids, "a deliberately chosen entity id was renamed"
    assert "sensor.munskankarna_history" not in ids


async def test_a_bare_name_id_is_left_alone(hass: HomeAssistant) -> None:
    """`sensor.history` can only have been chosen by a person.

    The integration has set `has_entity_name` since the commit that introduced
    the sensor platform, so no released version ever registered an id without
    the device name in front of it. Treating a bare entity name as
    auto-derived would therefore rewrite an id somebody picked deliberately,
    while covering no install that actually exists.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Munskänkarna",
    )
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_history",
        suggested_object_id="history",
        config_entry=entry, device_id=device.id,
        original_name="History", has_entity_name=True,
    )

    await _run_setup(hass, entry)

    ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert "sensor.history" in ids, "a deliberately chosen bare-name id was renamed"
    assert "sensor.munskankarna_history" not in ids


async def test_a_taken_canonical_id_is_not_stolen(hass: HomeAssistant) -> None:
    """Never rename onto an id something else already holds."""
    entry, registry = _legacy_install(hass, "Virtual Munskänkarna", "virtual_munskankarna")
    # Something unrelated already owns the canonical id.
    registry.async_get_or_create(
        "sensor", "template", "unrelated-thing",
        suggested_object_id="munskankarna_history",
    )

    await _run_setup(hass, entry)

    ours = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_history")
    assert ours == "sensor.virtual_munskankarna_history", (
        "the migration stole an id another integration owns"
    )
    assert registry.async_get("sensor.munskankarna_history").platform == "template"


async def test_already_canonical_ids_are_untouched(hass: HomeAssistant) -> None:
    """No churn on a healthy install."""
    entry, registry = _legacy_install(hass, "Munskänkarna", "munskankarna")
    await _run_setup(hass, entry)

    ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert "sensor.munskankarna_history" in ids
    assert not any(e.endswith("_2") for e in ids), f"the migration churned ids: {ids}"


async def test_an_id_from_a_device_renamed_twice_is_repaired(hass: HomeAssistant) -> None:
    """The gap 1.1.2 left: recognising the id needed the *old* device name.

    Entities registered while the device was "Virtual Munskänkarna", then the
    device renamed again. Home Assistant keeps no record of previous device
    names, so re-deriving from the current one no longer matches — but the id
    still spells out the integration's own slug followed by the entity name,
    which no other entity could mean.
    """
    entry, registry = _legacy_install(hass, "Virtual Munskänkarna", "virtual_munskankarna")
    dr.async_get(hass).async_update_device(
        dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)}).id,
        name_by_user="Vinkällaren",
    )

    await _run_setup(hass, entry)

    after = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    for _suffix, (_name, canonical) in OWNED.items():
        assert f"sensor.{canonical}" in after, f"{canonical} was not repaired"


async def test_an_id_it_cannot_place_is_raised_as_a_repair(hass: HomeAssistant) -> None:
    """Silence is what left the reporter stuck twice: say so in the UI."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Munskänkarna",
    )
    devices.async_update_device(device.id, name_by_user="Vinlager")
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_history",
        suggested_object_id="vinkallare_history",
        config_entry=entry, device_id=device.id,
        original_name="History", has_entity_name=True,
    )

    await _run_setup(hass, entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"mismatched_entity_ids_{entry.entry_id}")
    assert issue is not None, "an unplaceable id was skipped silently"
    placeholders = issue.translation_placeholders or {}
    assert "sensor.vinkallare_history" in placeholders.get("entities", "")
    assert "sensor.munskankarna_history" in placeholders.get("entities", "")


async def test_a_healthy_install_raises_no_repair(hass: HomeAssistant) -> None:
    """And clears one left over from a previous run."""
    entry, _ = _legacy_install(hass, "Munskänkarna", "munskankarna")
    await _run_setup(hass, entry)

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"mismatched_entity_ids_{entry.entry_id}") is None
