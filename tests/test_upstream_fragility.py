"""Graceful degradation when the source site changes or misbehaves.

The integration scrapes a site it does not control. Every one of these inputs
must produce a warning and a usable sensor, never an unhandled exception.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.munskankarna.const import (
    CONF_KINDS,
    DOMAIN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from custom_components.munskankarna.parser import parse_release_page, parse_score
from tests.helpers import build_release, create_entry

MALFORMED_PAGES = [
    "",
    "   ",
    "<html>",
    "<html><body><p>Sidan är flyttad</p></body></html>",
    "<ul id='wine-bottles-list'></ul>",
    # The container renamed - the most likely real redesign.
    "<ul id='wines'><li class='card'><div class='wine'>x</div></li></ul>",
    # Truncated mid-tag.
    "<ul id='wine-bottles-list'><li class='medium-3 groupedlist'><div class='c-wine-inf",
    # Not HTML at all.
    '{"error": "gone"}',
    "\x00\x01\x02 binary garbage",
]


@pytest.mark.parametrize("html", MALFORMED_PAGES)
def test_malformed_pages_never_raise(html: str) -> None:
    result = parse_release_page(html, release_id="r", title="R")
    assert result["wines"] == []
    assert result["warnings"], "a silent empty result hides a broken scrape"
    assert result["release"]["wine_count"] == 0


def test_a_card_missing_every_optional_field_still_parses() -> None:
    """Only the name is genuinely required."""
    html = """
    <ul id="wine-bottles-list"><li class="medium-3 groupedlist">
      <div class="c-wine-info"><div class="c-wine-info__headings"><h3>
        <a href="/sv/vinlocus/a/b"><span>Namnlöst Vin</span></a></h3></div>
      </div></li></ul>
    """
    wine = parse_release_page(html, release_id="r", title="R")["wines"][0]
    assert wine["name"] == "Namnlöst Vin"
    for field in ("score", "price_sek", "vintage", "producer", "country", "article_number"):
        assert wine[field] is None, f"{field} should be None, got {wine[field]!r}"
    assert wine["grapes"] == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14,5", 14.5),
        ("15", 15.0),
        # Formats the panel does not currently use, but might.
        ("14.5", 14.5),
        ("14,5/20", 14.5),
        ("14,5 p", 14.5),
        # Nonsense must not become a score.
        ("inget betyg", None),
        ("-", None),
        ("", None),
        ("★★★★", None),
        # Out of range on a 20-point scale.
        ("92", None),
        ("100", None),
        ("21", None),
    ],
)
def test_unexpected_rating_formats(raw: str, expected: float | None) -> None:
    assert parse_score(raw) == expected


def test_a_negative_score_is_rejected() -> None:
    """A leading minus must not be dropped, turning -5 into 5."""
    assert parse_score("-5") is None


async def test_a_holiday_week_with_zero_wines_is_not_an_error(
    hass: HomeAssistant,
) -> None:
    """An empty release is normal in a holiday week; the sensor reports 0."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-12-24", wine_count=0),
            "wines": [],
            "warnings": ["no wines this week"],
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release("r", KIND_TILLFALLIGT, "2026-12-24")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_release_{KIND_TILLFALLIGT}"
    )
    state = hass.states.get(entity_id)
    assert state is not None, "an empty release should still produce a sensor"
    assert state.state == "0"
    assert state.attributes["wines"] == []


async def test_an_empty_index_fails_loudly(hass: HomeAssistant) -> None:
    """No releases at all means the scrape is broken, not that wine ended."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    coordinator = MunskankarnaCoordinator(hass, entry)

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=[])
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()


def test_a_release_with_no_wines_warns_about_the_right_thing() -> None:
    """The warning should not blame a markup change for a quiet week."""
    result = parse_release_page(
        "<ul id='wine-bottles-list'></ul>", release_id="r", title="R"
    )
    warning = " ".join(result["warnings"]).lower()
    assert "no wines" in warning
