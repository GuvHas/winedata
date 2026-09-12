"""Phase 3b — sensor tests (written before `sensor.py`).

Verifies the entities Home Assistant actually exposes: their states, unique
IDs, device grouping, and the attribute payloads a Lovelace card renders.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.munskankarna.api import CannotConnect
from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_TOP_COUNT,
    DOMAIN,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

TILLFALLIGT_ID = "tillfalligt-sortiment-11-september-2026"


def release_entity_id(hass: HomeAssistant, entry_id: str, kind: str) -> str | None:
    """Look up a release sensor by its unique_id."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_release_{kind}")


def _wines() -> list[dict]:
    return [
        build_wine(TILLFALLIGT_ID, "Toppvinet", 17.0, value="fynd", price=189.0),
        build_wine(TILLFALLIGT_ID, "Bra Fynd", 15.5, value="fynd", price=99.0),
        build_wine(TILLFALLIGT_ID, "Prisvärt Vin", 15.0, value="prisvart", price=249.0),
        build_wine(TILLFALLIGT_ID, "Medel", 13.0, value="ej-prisvart", price=320.0),
        build_wine(TILLFALLIGT_ID, "Utan Nummer", 14.0, value="fynd", price=150.0,
                   article_number=None),
    ]


def _index() -> list[dict]:
    return [
        build_release(TILLFALLIGT_ID, KIND_TILLFALLIGT, "2026-09-11"),
        build_release("hitlista-3-september-2026", KIND_HITLISTAN, "2026-09-03"),
    ]


async def _setup(hass: HomeAssistant, *, options: dict | None = None, wines=None):
    """Set the integration up with a stubbed network layer."""
    entry = create_entry(
        hass,
        options=options
        or {CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: 3},
    )
    payload = _wines() if wines is None else wines

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if release_id.startswith("tillfalligt") else KIND_HITLISTAN
        return {
            "release": build_release(release_id, kind, "2026-09-11", wine_count=len(payload)),
            "wines": list(payload),
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_entities_are_created_per_tracked_kind(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    for kind in (KIND_TILLFALLIGT, KIND_HITLISTAN):
        entity_id = release_entity_id(hass, entry.entry_id, kind)
        assert entity_id is not None, f"no sensor registered for {kind}"
        assert hass.states.get(entity_id)


async def test_release_sensor_state_is_the_wine_count(hass: HomeAssistant) -> None:
    """State stays a small scalar; the list lives in attributes."""
    entry = await _setup(hass)
    state = hass.states.get(release_entity_id(hass, entry.entry_id, KIND_TILLFALLIGT))
    assert state.state == "5"
    assert state.attributes["unit_of_measurement"] == "viner"


async def test_release_sensor_attributes(hass: HomeAssistant) -> None:
    """Everything a dashboard card needs, and nothing it does not."""
    entry = await _setup(hass)
    state = hass.states.get(release_entity_id(hass, entry.entry_id, KIND_TILLFALLIGT))
    attrs = state.attributes

    assert attrs["release_id"] == TILLFALLIGT_ID
    assert attrs["release_date"] == "2026-09-11"
    assert attrs["release_url"].endswith(TILLFALLIGT_ID)
    assert attrs["kind"] == KIND_TILLFALLIGT

    wines = attrs["wines"]
    assert len(wines) == 3  # capped by CONF_TOP_COUNT
    assert [w["name"] for w in wines] == ["Toppvinet", "Bra Fynd", "Prisvärt Vin"]

    top = wines[0]
    assert top["score"] == 17.0
    assert top["value"] == "fynd"
    assert top["price"] == 189.0
    assert top["url"] == "https://www.systembolaget.se/produkt/vin/9049001/"
    assert top["article_number"] == "9049001"
    # Tasting notes are the bulk of the payload and are deliberately excluded.
    assert "tasting_note" not in top


async def test_attributes_stay_small(hass: HomeAssistant) -> None:
    """Attributes are recorded and broadcast on every update, so size matters."""
    import json

    entry = await _setup(hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_TOP_COUNT: 10})
    state = hass.states.get(release_entity_id(hass, entry.entry_id, KIND_TILLFALLIGT))
    size = len(json.dumps(dict(state.attributes), default=str).encode())
    assert size < 8192, f"attribute payload is {size} bytes"


async def test_state_respects_the_255_character_limit(hass: HomeAssistant) -> None:
    """Home Assistant truncates states longer than 255 characters."""
    await _setup(hass)
    for entity_id in hass.states.async_entity_ids("sensor"):
        assert len(hass.states.get(entity_id).state) <= 255


async def test_top_pick_sensor_names_the_best_wine(hass: HomeAssistant) -> None:
    await _setup(hass)
    state = hass.states.get("sensor.munskankarna_top_pick")
    assert state.state == "Toppvinet"
    assert state.attributes["score"] == 17.0
    assert state.attributes["url"] == "https://www.systembolaget.se/produkt/vin/9049001/"


async def test_fynd_sensor_counts_only_bargains(hass: HomeAssistant) -> None:
    await _setup(hass)
    state = hass.states.get("sensor.munskankarna_fynd")
    # Three 'fynd' wines per release across two releases.
    assert state.state == "6"
    assert all(w["value"] == "fynd" for w in state.attributes["wines"])


async def test_latest_release_date_sensor(hass: HomeAssistant) -> None:
    """A timestamp-ish sensor for 'when did the newest tasting land'."""
    await _setup(hass)
    state = hass.states.get("sensor.munskankarna_latest_release")
    assert state.state == "2026-09-11"


async def test_unique_ids_are_stable_and_distinct(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)

    unique_ids = [e.unique_id for e in entities]
    assert len(set(unique_ids)) == len(unique_ids)
    assert all(uid.startswith(entry.entry_id) for uid in unique_ids)
    assert f"{entry.entry_id}_release_{KIND_TILLFALLIGT}" in unique_ids


async def test_entities_share_one_device(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].manufacturer == "Munskänkarna"


async def test_sensors_go_unavailable_when_every_fetch_fails(hass: HomeAssistant) -> None:
    """Network failure must mark entities unavailable, not report stale zeros."""
    from custom_components.munskankarna.api import CannotConnect

    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    with patch.object(
        MunskankarnaCoordinator,
        "_async_fetch_index",
        new=AsyncMock(side_effect=CannotConnect("offline")),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # A first-refresh failure raises ConfigEntryNotReady, so no stale entity
    # is ever published with misleading data.
    assert entry.state is not None
    assert not hass.states.async_entity_ids("sensor")


async def test_unload_removes_entities(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entity_id = release_entity_id(hass, entry.entry_id, KIND_TILLFALLIGT)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is None or state.state == "unavailable"


async def test_missing_release_leaves_that_sensor_absent(hass: HomeAssistant) -> None:
    """A kind that returned nothing should not fabricate a zeroed sensor."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        if release_id.startswith("hitlista"):
            from custom_components.munskankarna.api import CannotConnect

            raise CannotConnect("down")
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [build_wine(release_id, "Enda", 15.0)],
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(release_entity_id(hass, entry.entry_id, KIND_TILLFALLIGT))
    hitlista_id = release_entity_id(hass, entry.entry_id, KIND_HITLISTAN)
    hitlista = hass.states.get(hitlista_id) if hitlista_id else None
    assert hitlista is None or hitlista.state in ("unknown", "unavailable")


async def test_trigger_sync_service_refreshes(hass: HomeAssistant) -> None:
    """`munskankarna.trigger_sync` forces an immediate poll."""
    await _setup(hass)
    assert hass.services.has_service(DOMAIN, "trigger_sync")

    with patch.object(
        MunskankarnaCoordinator, "async_request_refresh", new=AsyncMock()
    ) as refresh:
        await hass.services.async_call(DOMAIN, "trigger_sync", {}, blocking=True)
    refresh.assert_awaited()


# ---------------------------------------------------------------------------
# A kind that fails during startup must still get an entity
# ---------------------------------------------------------------------------


async def test_a_kind_failing_at_startup_still_gets_a_sensor(hass: HomeAssistant) -> None:
    """Entities must exist for every configured kind, not every loaded one.

    Platform setup runs once. Creating entities only for the kinds present in
    the first successful update meant a category whose page was down during
    startup had no sensor at all — and no later success could create one,
    because `async_setup_entry` never runs again. Reloading the integration
    was the only cure, which is exactly what `available` exists to avoid.
    """
    entry = create_entry(
        hass,
        options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: 3},
    )
    payload = _wines()
    remaining_failures = {KIND_HITLISTAN: 1}

    async def flaky_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if release_id.startswith("tillfalligt") else KIND_HITLISTAN
        if remaining_failures.get(kind):
            remaining_failures[kind] -= 1
            raise CannotConnect(f"{kind} is down")
        return {
            "release": build_release(release_id, kind, "2026-09-11", wine_count=len(payload)),
            "wines": list(payload),
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=flaky_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = release_entity_id(hass, entry.entry_id, KIND_HITLISTAN)
        assert entity_id is not None, "the kind that failed at startup never got a sensor"
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

        # The one that loaded is unaffected.
        working = hass.states.get(release_entity_id(hass, entry.entry_id, KIND_TILLFALLIGT))
        assert working.state == "5"

        # Next poll succeeds — no reload, no reconfiguration.
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    recovered = hass.states.get(entity_id)
    assert recovered.state == "5", "the recovered kind did not populate"
    assert recovered.attributes["kind"] == KIND_HITLISTAN
    assert len(recovered.attributes["wines"]) == 3


async def test_unavailable_release_sensor_exposes_no_stale_attributes(
    hass: HomeAssistant,
) -> None:
    """An entity that exists but has no data must not serve a half-payload."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})

    async def only_tillfalligt(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        if not release_id.startswith("tillfalligt"):
            raise CannotConnect("down")
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [build_wine(release_id, "Enda Vinet")],
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=only_tillfalligt),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(release_entity_id(hass, entry.entry_id, KIND_HITLISTAN))
    assert state.state == STATE_UNAVAILABLE
    assert "wines" not in state.attributes
