"""Attribute payloads must fit Home Assistant's recorder limit.

The recorder refuses to store a state whose attributes exceed
`MAX_STATE_ATTRS_BYTES` (16 KiB), logging

    State attributes for sensor.x exceed maximum size of 16384 bytes.
    This can cause database performance issues; Attributes will not be stored

and dropping them. The entity keeps working live, but its history is silently
lost — which is worse than an error, because nothing in the UI says so.

1.1.0 shipped over that limit in *every* configuration, including a single
tasting type at defaults. These tests pin the ceiling against Home Assistant's
own constant rather than a number chosen by hand.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.db_schema import MAX_STATE_ATTRS_BYTES
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.munskankarna.const import (
    ALL_KINDS,
    CONF_HISTORY_COUNT,
    CONF_KINDS,
    CONF_TOP_COUNT,
    DEFAULT_HISTORY_COUNT,
    DEFAULT_KINDS,
    DEFAULT_TOP_COUNT,
    DOMAIN,
    MAX_HISTORY_COUNT,
    MAX_TOP_COUNT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

#: A realistic release: long Swedish names, producers and full URLs, which is
#: what actually drives the byte count.
WINES_PER_RELEASE = 46


def _wines(release_id: str, count: int = WINES_PER_RELEASE) -> list[dict]:
    return [
        build_wine(
            release_id,
            f"Château Grand Cru Classé Réserve Spéciale {i}",
            17.0 - (i % 40) / 10,
            value="fynd" if i % 3 == 0 else "prisvart",
            price=99.0 + i,
        )
        for i in range(count)
    ]


async def _setup(hass: HomeAssistant, kinds: list[str], history: int, top: int):
    entry = create_entry(
        hass,
        options={CONF_KINDS: kinds, CONF_TOP_COUNT: top, CONF_HISTORY_COUNT: history},
    )
    index = [
        build_release(f"{kind}-{week}", kind, f"2026-09-{11 - week * 7:02d}")
        for kind in kinds
        for week in range(history)
    ]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = release_id.rsplit("-", 1)[0]
        wines = _wines(release_id)
        return {
            "release": build_release(release_id, kind, "2026-09-11",
                                     wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=index)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _recorded_bytes(state: State) -> int:
    """The size the recorder measures: the full attribute dict as JSON."""
    return len(json.dumps(dict(state.attributes), ensure_ascii=False,
                          default=str).encode())


def _all_states(hass: HomeAssistant, entry_id: str) -> list[State]:
    registry = er.async_get(hass)
    states = []
    for entity in er.async_entries_for_config_entry(registry, entry_id):
        if (state := hass.states.get(entity.entity_id)) is not None:
            states.append(state)
    return states


@pytest.mark.parametrize(
    ("label", "kinds", "history", "top"),
    [
        ("shipped defaults", list(DEFAULT_KINDS), DEFAULT_HISTORY_COUNT,
         DEFAULT_TOP_COUNT),
        ("a single tasting type", [DEFAULT_KINDS[0]], DEFAULT_HISTORY_COUNT,
         DEFAULT_TOP_COUNT),
        ("maximum configurable", list(DEFAULT_KINDS), MAX_HISTORY_COUNT,
         MAX_TOP_COUNT),
        ("every tasting type at maximum", list(ALL_KINDS), MAX_HISTORY_COUNT,
         MAX_TOP_COUNT),
    ],
)
async def test_no_entity_exceeds_the_recorder_limit(
    hass: HomeAssistant, label: str, kinds: list[str], history: int, top: int
) -> None:
    """No reachable configuration may produce an unrecordable entity.

    Parameterised over the corners rather than the default alone: the option
    ceilings are reachable from the UI, so they are part of the contract.
    """
    entry = await _setup(hass, kinds, history, top)

    oversized = [
        (state.entity_id, _recorded_bytes(state))
        for state in _all_states(hass, entry.entry_id)
        if _recorded_bytes(state) > MAX_STATE_ATTRS_BYTES
    ]
    assert not oversized, (
        f"{label}: attributes exceed the recorder limit of "
        f"{MAX_STATE_ATTRS_BYTES:,} bytes: "
        + ", ".join(f"{eid} at {size:,}" for eid, size in oversized)
    )


async def test_the_history_sensor_stays_well_under_the_limit(
    hass: HomeAssistant,
) -> None:
    """Headroom matters: Home Assistant adds its own attributes on top.

    friendly_name, icon, unit_of_measurement and device_class are injected by
    the framework after the integration builds its payload, so a design that
    lands exactly on the limit breaches it in practice.
    """
    entry = await _setup(hass, list(DEFAULT_KINDS), MAX_HISTORY_COUNT, MAX_TOP_COUNT)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_history")
    state = hass.states.get(entity_id)

    size = _recorded_bytes(state)
    assert size <= MAX_STATE_ATTRS_BYTES * 0.9, (
        f"history attributes are {size:,} bytes, over 90% of the "
        f"{MAX_STATE_ATTRS_BYTES:,} byte limit — no headroom"
    )


async def test_the_history_sensor_says_when_it_trimmed(hass: HomeAssistant) -> None:
    """Silent truncation is the failure mode this whole change exists to avoid."""
    entry = await _setup(hass, list(DEFAULT_KINDS), MAX_HISTORY_COUNT, MAX_TOP_COUNT)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_history")
    attrs = hass.states.get(entity_id).attributes

    assert attrs["truncated"] is True, "a trimmed payload did not say so"
    assert attrs["wines_per_release"] < MAX_TOP_COUNT
    assert attrs["wines_per_release"] >= 1, "trimming removed every wine"
    for release in attrs["releases"]:
        assert len(release["wines"]) <= attrs["wines_per_release"]


async def test_a_small_configuration_is_not_trimmed(hass: HomeAssistant) -> None:
    """Trimming must only engage when it has to."""
    entry = await _setup(hass, [DEFAULT_KINDS[0]], 2, 5)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_history")
    attrs = hass.states.get(entity_id).attributes

    assert attrs["truncated"] is False
    assert attrs["wines_per_release"] == 5
    assert all(len(r["wines"]) == 5 for r in attrs["releases"])


async def test_every_release_keeps_its_metadata_even_when_trimmed(
    hass: HomeAssistant,
) -> None:
    """The timeline is the cheap part and must survive: dates, counts, links.

    Trimming takes wines, never releases — losing a week entirely would make
    the archive lie about what was published.
    """
    entry = await _setup(hass, list(ALL_KINDS), MAX_HISTORY_COUNT, MAX_TOP_COUNT)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_history")
    attrs = hass.states.get(entity_id).attributes

    coordinator = entry.runtime_data
    assert len(attrs["releases"]) == len(coordinator.retained_releases()), (
        "trimming dropped whole releases instead of wines"
    )
    for release in attrs["releases"]:
        assert release["date"]
        assert release["kind_label"]
        assert release["wine_count"] == WINES_PER_RELEASE
        assert release["url"]


# ---------------------------------------------------------------------------
# Review round: metadata alone can exceed the budget
# ---------------------------------------------------------------------------


async def test_an_oversized_metadata_payload_is_still_trimmed(
    hass: HomeAssistant,
) -> None:
    """Zero wines is not automatically small enough.

    The binary search bottoms out at zero wines and returned that payload
    unchecked. Release *metadata* is not free: the parser takes the release
    title straight from the page's <h1> without bounding it, so enough
    retained releases with long enough titles push the payload over the limit
    with every wine already removed.
    """
    entry = create_entry(
        hass,
        options={
            CONF_KINDS: list(ALL_KINDS),
            CONF_TOP_COUNT: MAX_TOP_COUNT,
            CONF_HISTORY_COUNT: MAX_HISTORY_COUNT,
        },
    )
    # A pathological but reachable page: the <h1> is unbounded upstream.
    huge_title = "Tillfälligt sortiment " + "mycket långt namn " * 300

    index = [
        build_release(f"{kind}-{week}", kind, f"2026-09-{11 - week:02d}",
                      title=huge_title)
        for kind in ALL_KINDS
        for week in range(MAX_HISTORY_COUNT)
    ]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = release_id.rsplit("-", 1)[0]
        wines = _wines(release_id, 5)
        return {
            "release": build_release(release_id, kind, "2026-09-11",
                                     title=huge_title, wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=index)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    oversized = [
        (state.entity_id, _recorded_bytes(state))
        for state in _all_states(hass, entry.entry_id)
        if _recorded_bytes(state) > MAX_STATE_ATTRS_BYTES
    ]
    assert not oversized, (
        "metadata alone exceeded the recorder limit: "
        + ", ".join(f"{eid} at {size:,}" for eid, size in oversized)
    )


async def test_release_titles_are_bounded(hass: HomeAssistant) -> None:
    """The cheapest root fix: an unbounded scraped field has no business here."""
    entry = create_entry(hass, options={CONF_KINDS: [DEFAULT_KINDS[0]]})
    huge_title = "Provning " + "x" * 5000

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(release_id, DEFAULT_KINDS[0], "2026-09-11",
                                     title=huge_title, wine_count=1),
            "wines": _wines(release_id, 1),
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=[
                build_release("r", DEFAULT_KINDS[0], "2026-09-11", title=huge_title)
            ]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_history")
    for release in hass.states.get(entity_id).attributes["releases"]:
        assert len(release["title"]) <= 200, (
            f"an unbounded release title reached the attributes: {len(release['title'])}"
        )
