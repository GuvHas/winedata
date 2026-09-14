"""Entity ids must be predictable, because the shipped dashboard hardcodes them.

Home Assistant derives an entity_id from `device.name_by_user or device.name`
plus the entity name when `has_entity_name` is set. So renaming the device in
the UI changes the ids of every entity registered *after* the rename, while
entities registered before it keep the old ones.

That is how an instance ends up with both `sensor.munskankarna_hitlista` and
`sensor.virtual_munskankarna_history`: the release sensors were registered
first, the device was renamed, and 1.1.0's two new sensors then picked up the
new prefix. Nothing in this integration ever produced a "virtual" prefix.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.munskankarna.const import (
    CONF_KINDS,
    DOMAIN,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

#: Exactly what dashboard/munskankarna-lovelace.yaml references.
EXPECTED_ENTITY_IDS = {
    "sensor.munskankarna_top_pick",
    "sensor.munskankarna_fynd",
    "sensor.munskankarna_latest_release",
    "sensor.munskankarna_wines_tested",
    "sensor.munskankarna_history",
    "sensor.munskankarna_fynd_history",
    "sensor.munskankarna_tillfalligt_sortiment",
    "sensor.munskankarna_hitlista",
}

INDEX = [
    build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11"),
    build_release("h-2026-09-03", KIND_HITLISTAN, "2026-09-03"),
]


async def _setup(hass: HomeAssistant, entry) -> None:
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


def _registered(hass: HomeAssistant, entry_id: str) -> set[str]:
    registry = er.async_get(hass)
    return {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry_id)
    }


async def test_entity_ids_are_exactly_what_the_dashboard_expects(
    hass: HomeAssistant,
) -> None:
    """The canonical set, on a clean install."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    await _setup(hass, entry)

    assert _registered(hass, entry.entry_id) == EXPECTED_ENTITY_IDS


async def test_a_renamed_device_does_not_change_entity_ids(
    hass: HomeAssistant,
) -> None:
    """The reported bug, reproduced.

    The device is renamed before the entities are created, exactly as it is
    for a user who renamed it and then upgraded to a version adding sensors.
    Without pinning, every id here gains a `virtual_` prefix and the shipped
    dashboard points at entities that do not exist.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})

    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Munskänkarna",
    )
    devices.async_update_device(device.id, name_by_user="Virtual Munskänkarna")

    await _setup(hass, entry)

    registered = _registered(hass, entry.entry_id)
    prefixed = {eid for eid in registered if "virtual" in eid}
    assert not prefixed, f"a renamed device leaked into entity ids: {sorted(prefixed)}"
    assert registered == EXPECTED_ENTITY_IDS


async def test_the_friendly_name_still_follows_the_device(
    hass: HomeAssistant,
) -> None:
    """Pinning the id must not pin the display name.

    A user renaming the device should still see that name in the UI; only the
    id — which automations and the dashboard depend on — is held stable.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Munskänkarna",
    )
    devices.async_update_device(device.id, name_by_user="Vinkällaren")

    await _setup(hass, entry)

    state = hass.states.get("sensor.munskankarna_history")
    assert state is not None
    assert "Vinkällaren" in state.attributes["friendly_name"], (
        "the device's user-chosen name no longer reaches the friendly name"
    )


async def test_a_second_config_entry_does_not_steal_the_ids(
    hass: HomeAssistant,
) -> None:
    """Pinned ids must still be collision-safe.

    Two entries cannot both own sensor.munskankarna_history; the second must
    take a suffixed id rather than overwrite the first.
    """
    first = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    await _setup(hass, first)

    second = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    second.add_to_hass(hass)
    await _setup(hass, second)

    first_ids = _registered(hass, first.entry_id)
    second_ids = _registered(hass, second.entry_id)

    assert "sensor.munskankarna_history" in first_ids
    assert not (first_ids & second_ids), "the two entries share an entity id"


def test_the_dashboard_references_only_canonical_ids() -> None:
    """The YAML and the integration must not drift apart again."""
    import pathlib
    import re

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "dashboard"
        / "munskankarna-lovelace.yaml"
    )
    active = path.read_text(encoding="utf-8").split("# OPTIONAL: custom cards")[0]
    referenced = set(re.findall(r"sensor\.munskankarna_[a-z0-9_]+", active))

    assert referenced, "the dashboard names no sensors"
    unknown = referenced - EXPECTED_ENTITY_IDS
    assert not unknown, f"dashboard references ids the integration never creates: {unknown}"
