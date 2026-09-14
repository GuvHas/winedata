"""How the retained releases reach a dashboard.

The full multi-week wine list is deliberately carried by *one* dedicated
entity rather than duplicated onto every per-kind sensor. Attributes are
recorder-written and websocket-broadcast on every update, so concentrating the
large payload in a single entity keeps the existing sensors exactly the size
they were — and lets a user exclude just that one from the recorder without
losing the small, useful ones.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_TOP_COUNT,
    DOMAIN,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

INDEX = [
    build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11"),
    build_release("t-2026-09-04", KIND_TILLFALLIGT, "2026-09-04"),
    build_release("t-2026-08-28", KIND_TILLFALLIGT, "2026-08-28"),
    build_release("h-2026-09-03", KIND_HITLISTAN, "2026-09-03"),
    build_release("h-2026-08-20", KIND_HITLISTAN, "2026-08-20"),
]


def _wines(release_id: str, n: int = 6) -> list[dict]:
    return [
        build_wine(release_id, f"Vin {i}", 17.0 - i,
                   value="fynd" if i % 2 == 0 else "prisvart", price=100.0 + i)
        for i in range(n)
    ]


async def _setup(hass: HomeAssistant, top_count: int = 3):
    entry = create_entry(
        hass,
        options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: top_count},
    )

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if rid.startswith("t-") else KIND_HITLISTAN
        wines = _wines(rid)
        return {
            "release": build_release(rid, kind, rid[2:], wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=INDEX)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, entry_id: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{key}")


async def test_a_history_sensor_carries_every_retained_release(
    hass: HomeAssistant,
) -> None:
    """One entity, all kinds, newest first — the dashboard's single source."""
    entry = await _setup(hass)
    state = hass.states.get(_entity(hass, entry.entry_id, "history"))

    assert state is not None, "no history sensor was created"
    assert state.state == "5", "state should count the retained releases"

    releases = state.attributes["releases"]
    # Strictly by date, interleaving the two categories:
    # 09-11 (T), 09-04 (T), 09-03 (H), 08-28 (T), 08-20 (H).
    assert [r["release_id"] for r in releases] == [
        "t-2026-09-11", "t-2026-09-04", "h-2026-09-03", "t-2026-08-28", "h-2026-08-20"
    ], "releases are not ordered newest first across kinds"
    assert releases[0]["kind"] == KIND_TILLFALLIGT
    assert releases[0]["kind_label"]
    assert releases[0]["wine_count"] == 6


async def test_history_wines_are_capped_per_release(hass: HomeAssistant) -> None:
    """The cap is what keeps the payload bounded as retention grows."""
    entry = await _setup(hass, top_count=3)
    state = hass.states.get(_entity(hass, entry.entry_id, "history"))

    for release in state.attributes["releases"]:
        assert len(release["wines"]) == 3, "top_count was not applied per release"
    # A lean projection, not the full wine_summary the current-release sensors
    # carry. The archive multiplies per-wine cost across every retained release
    # of every tracked type, and the full shape put it over the recorder's
    # 16 KiB attribute limit in every configuration.
    wine = state.attributes["releases"][0]["wines"][0]
    for field in ("name", "score", "price", "value", "url", "vintage",
                  "producer", "price_per_litre"):
        assert field in wine, f"{field} missing from a history wine"
    # Dropped on purpose: no history card renders these.
    for field in ("full_name", "band", "volume_ml", "color", "country", "region",
                  "grapes", "article_number", "review_url"):
        assert field not in wine, f"{field} is dead weight in the archive"


async def test_the_history_payload_stays_within_its_budget(hass: HomeAssistant) -> None:
    """Measured against Home Assistant's own limit, not a number I picked.

    This assertion previously read `< 120_000`, roughly seven times the
    recorder's actual ceiling, so it passed while every shipped configuration
    breached it. Pin it to the constant the recorder enforces.
    """
    from homeassistant.components.recorder.db_schema import MAX_STATE_ATTRS_BYTES

    entry = await _setup(hass, top_count=10)
    state = hass.states.get(_entity(hass, entry.entry_id, "history"))

    payload = json.dumps(dict(state.attributes), ensure_ascii=False, default=str)
    size = len(payload.encode())
    assert size <= MAX_STATE_ATTRS_BYTES, (
        f"history attributes are {size:,} bytes, over the recorder's "
        f"{MAX_STATE_ATTRS_BYTES:,}"
    )


async def test_the_existing_per_kind_sensors_did_not_grow(hass: HomeAssistant) -> None:
    """History must not be duplicated onto the sensors that were already small.

    They gain only a per-release summary — dates and counts, no wine lists.
    """
    entry = await _setup(hass, top_count=3)
    state = hass.states.get(_entity(hass, entry.entry_id, f"release_{KIND_TILLFALLIGT}"))

    summary = state.attributes["history"]
    assert [r["release_id"] for r in summary] == [
        "t-2026-09-11", "t-2026-09-04", "t-2026-08-28"
    ]
    assert all("wines" not in r for r in summary), (
        "the per-kind sensor is carrying full wine lists for every retained release"
    )
    assert summary[0]["wine_count"] == 6
    assert summary[0]["date"] == "2026-09-11"


async def test_a_fynd_history_sensor_counts_bargains_across_the_window(
    hass: HomeAssistant,
) -> None:
    """A badge-sized scalar, so the dashboard needs no second large payload."""
    entry = await _setup(hass, top_count=10)
    state = hass.states.get(_entity(hass, entry.entry_id, "fynd_history"))

    assert state is not None, "no fynd history sensor was created"
    # Three fynd per release (i = 0, 2, 4), five retained releases.
    assert state.state == "15"
    assert state.attributes["per_kind"][KIND_TILLFALLIGT] == 9
    assert state.attributes["per_kind"][KIND_HITLISTAN] == 6
    assert "wines" not in state.attributes, "the count sensor should carry no wine list"


async def test_history_entities_survive_an_empty_cache(hass: HomeAssistant) -> None:
    """Before the first successful poll they must exist and read zero, not break."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    async def one_release(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(rid, KIND_TILLFALLIGT, "2026-09-11", wine_count=0),
            "wines": [],
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release("t-x", KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=one_release),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(_entity(hass, entry.entry_id, "history")).state == "1"
    assert hass.states.get(_entity(hass, entry.entry_id, "fynd_history")).state == "0"
