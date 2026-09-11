"""Attribute payload size.

Attributes are written to the recorder (SQLite/MariaDB) and pushed over the
websocket to every connected client on each update. Anything unbounded here
grows the database forever and slows the UI.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_TOP_COUNT,
    DOMAIN,
    KIND_TILLFALLIGT,
    MAX_SUMMARY_LENGTH,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

RELEASE_ID = "tillfalligt-sortiment-11-september-2026"

# The real Webbviner release publishes an editorial dump of ~1500 characters
# in this field; Tillfälligt sortiment publishes ~120.
HUGE_SUMMARY = "Månadens bästa och mest prisvärda viner. " * 60


async def _setup(hass: HomeAssistant, *, summary: str, wines: int, top_count: int = 10):
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_TOP_COUNT: top_count}
    )
    payload = [build_wine(RELEASE_ID, f"Vin {i}", 15.0) for i in range(wines)]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        release = build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=wines)
        release["summary"] = summary
        return {"release": release, "wines": list(payload), "warnings": []}

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _attrs(hass: HomeAssistant, entry) -> dict:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_release_{KIND_TILLFALLIGT}"
    )
    return dict(hass.states.get(entity_id).attributes)


async def test_long_summary_is_truncated(hass: HomeAssistant) -> None:
    """An editorial dump must not be recorded verbatim on every update."""
    entry = await _setup(hass, summary=HUGE_SUMMARY, wines=3)
    summary = _attrs(hass, entry)["summary"]

    assert len(summary) <= MAX_SUMMARY_LENGTH, f"summary is {len(summary)} chars"
    assert summary.endswith("…"), "truncation should be visible to the reader"


async def test_short_summary_is_left_alone(hass: HomeAssistant) -> None:
    entry = await _setup(hass, summary="Fem fina fynd.", wines=3)
    assert _attrs(hass, entry)["summary"] == "Fem fina fynd."


async def test_total_attribute_payload_stays_bounded(hass: HomeAssistant) -> None:
    """Worst case: a huge summary and the maximum configurable wine list."""
    from custom_components.munskankarna.const import MAX_TOP_COUNT

    entry = await _setup(
        hass, summary=HUGE_SUMMARY, wines=MAX_TOP_COUNT * 2, top_count=MAX_TOP_COUNT
    )
    size = len(json.dumps(_attrs(hass, entry), default=str).encode())
    assert size < 16_384, f"worst-case attribute payload is {size} bytes"


async def test_default_payload_is_small(hass: HomeAssistant) -> None:
    """The realistic case — default options — should be a few kilobytes."""
    from custom_components.munskankarna.const import DEFAULT_TOP_COUNT

    entry = await _setup(hass, summary="Fem fina fynd.", wines=40, top_count=DEFAULT_TOP_COUNT)
    size = len(json.dumps(_attrs(hass, entry), default=str).encode())
    assert size < 8_192, f"default attribute payload is {size} bytes"


async def test_option_cannot_exceed_the_ceiling(hass: HomeAssistant) -> None:
    """The options schema must not let a user configure an unbounded list."""
    import voluptuous as vol

    from custom_components.munskankarna.config_flow import MunskankarnaOptionsFlow
    from custom_components.munskankarna.const import CONF_TOP_COUNT, MAX_TOP_COUNT

    entry = create_entry(hass)
    flow = MunskankarnaOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    result = await flow.async_step_init()
    schema = result["data_schema"]

    with pytest.raises(vol.Invalid):
        schema({CONF_TOP_COUNT: MAX_TOP_COUNT + 1})


async def test_wine_list_is_capped_by_the_option(hass: HomeAssistant) -> None:
    entry = await _setup(hass, summary="kort", wines=40, top_count=5)
    assert len(_attrs(hass, entry)["wines"]) == 5


async def test_state_never_exceeds_the_255_character_limit(hass: HomeAssistant) -> None:
    await _setup(hass, summary=HUGE_SUMMARY, wines=40, top_count=40)
    for entity_id in hass.states.async_entity_ids("sensor"):
        assert len(hass.states.get(entity_id).state) <= 255


async def test_a_previously_saved_oversized_limit_is_clamped(hass: HomeAssistant) -> None:
    """Upgrades keep their stored option; the new ceiling must still apply.

    Lowering the schema bound only constrains newly submitted forms, so an
    entry saved with the old maximum of 40 would keep recording 40 wines.
    """
    from custom_components.munskankarna.const import CONF_TOP_COUNT, MAX_TOP_COUNT
    from custom_components.munskankarna.coordinator import MunskankarnaCoordinator

    entry = create_entry(hass, options={CONF_TOP_COUNT: 40})
    coordinator = MunskankarnaCoordinator(hass, entry)
    assert coordinator.top_count == MAX_TOP_COUNT


async def test_a_nonsense_saved_limit_falls_back(hass: HomeAssistant) -> None:
    from custom_components.munskankarna.const import CONF_TOP_COUNT, DEFAULT_TOP_COUNT
    from custom_components.munskankarna.coordinator import MunskankarnaCoordinator

    for stored in (0, -5, "abc", None):
        entry = create_entry(hass, options={CONF_TOP_COUNT: stored})
        assert MunskankarnaCoordinator(hass, entry).top_count == DEFAULT_TOP_COUNT
