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
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from custom_components.munskankarna.parser import parse_release_page, parse_score
from tests.helpers import build_release, build_wine, create_entry

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


# ---------------------------------------------------------------------------
# "Zero wines" must mean a recognised release, not an unrecognised page
# ---------------------------------------------------------------------------


MAINTENANCE_PAGE = """
<html><body><h1>Underhåll pågår</h1>
<p>Vi är snart tillbaka.</p></body></html>
"""

LOGIN_WALL_PAGE = """
<html><body><h1>Logga in</h1>
<form class="js-login"><input name="Password" type="password" /></form>
</body></html>
"""

RECOGNISED_BUT_EMPTY_PAGE = """
<html><body><h1>Tillfälligt sortiment 24 december 2026</h1>
<div class="c-wine-contentdescription">Om provningen: inga viner denna vecka.</div>
<ul id="wine-bottles-list"></ul></body></html>
"""


@pytest.mark.parametrize(
    "html", [MAINTENANCE_PAGE, LOGIN_WALL_PAGE, "", '{"error": "gone"}', "<html>"]
)
def test_unrecognised_pages_are_reported_as_invalid(html: str) -> None:
    """The parser must say whether it recognised a release page at all.

    Without that signal a maintenance page is indistinguishable from a quiet
    week: both parse to zero wines, so the coordinator accepted the former as
    a legitimate result and overwrote good cached data with a 0-wine state.
    """
    result = parse_release_page(html, release_id="r", title="R")
    assert result["page_valid"] is False
    assert result["wines"] == []


def test_a_recognised_release_with_no_wines_is_valid() -> None:
    """An authentically empty release is a real result, not a broken page."""
    result = parse_release_page(RECOGNISED_BUT_EMPTY_PAGE, release_id="r", title="R")
    assert result["page_valid"] is True
    assert result["wines"] == []
    assert result["release"]["wine_count"] == 0


def test_real_release_fixtures_are_recognised(load_fixture_html) -> None:  # noqa: ANN001
    """The marker must match the pages the site actually serves."""
    for name in ("release-tillfalligt-sortiment.html", "release-hitlista.html"):
        result = parse_release_page(load_fixture_html(name), release_id="r", title="R")
        assert result["page_valid"] is True, f"{name} was not recognised"
        assert result["wines"], f"{name} parsed no wines"


async def test_a_maintenance_page_preserves_cached_wines(hass: HomeAssistant) -> None:
    """A broken page must fail the update, not blank the sensor.

    UpdateFailed keeps the coordinator's previous data and marks the entity
    unavailable; accepting the page would replace last week's wines with 0.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    release_id = "tillfalligt-sortiment-11-september-2026"
    serve_maintenance = {"now": False}

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        if serve_maintenance["now"]:
            return parse_release_page(MAINTENANCE_PAGE, release_id=rid, title=title)
        return {
            "release": build_release(rid, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [build_wine(rid, "Ett Vin")],
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release(release_id, KIND_TILLFALLIGT)]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert coordinator.data["releases"][KIND_TILLFALLIGT]["release"]["wine_count"] == 1

        # The site starts serving a maintenance page.
        serve_maintenance["now"] = True
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is False, "a maintenance page was accepted"
    # The good data is still there rather than replaced by a 0-wine state.
    assert coordinator.data["releases"][KIND_TILLFALLIGT]["release"]["wine_count"] == 1


async def test_a_recognised_empty_release_still_updates(hass: HomeAssistant) -> None:
    """The opposite case: a real quiet week must succeed with zero wines."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    release_id = "tillfalligt-sortiment-24-december-2026"

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return parse_release_page(RECOGNISED_BUT_EMPTY_PAGE, release_id=rid, title=title)

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release(release_id, KIND_TILLFALLIGT)]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.last_update_success is True
    assert coordinator.data["releases"][KIND_TILLFALLIGT]["release"]["wine_count"] == 0


# ---------------------------------------------------------------------------
# Review round: a partial failure must not drop the failed kind's data
# ---------------------------------------------------------------------------


DESCRIPTION_BUT_NO_CARD_LIST = """
<html><body><h1>Tillfälligt sortiment 11 september 2026</h1>
<div class="c-wine-contentdescription">Om provningen: veckans viner.</div>
<ul id="wines"><li class="card"><div class="wine">Ett Vin</div></li></ul>
</body></html>
"""

CARD_CLASS_RENAMED = """
<html><body><h1>Tillfälligt sortiment 11 september 2026</h1>
<div class="c-wine-contentdescription">Om provningen: veckans viner.</div>
<ul id="wine-bottles-list">
  <li class="medium-3 groupedlist"><div class="c-wine-card"><h3>
    <a href="/sv/vinlocus/a/b"><span>Ett Vin</span></a></h3></div></li>
  <li class="medium-3 groupedlist"><div class="c-wine-card"><h3>
    <a href="/sv/vinlocus/a/c"><span>Ett Till</span></a></h3></div></li>
</ul></body></html>
"""


@pytest.mark.parametrize(
    "html",
    [
        # The outer description survives a redesign that renames the list.
        DESCRIPTION_BUT_NO_CARD_LIST,
        # The list survives but every card inside it is unreadable.
        CARD_CLASS_RENAMED,
    ],
)
def test_a_redesign_that_breaks_the_cards_is_not_an_empty_release(html: str) -> None:
    """An outer marker is not enough to call zero wines authentic.

    Both of these keep a marker the flag originally trusted while the cards
    themselves became unreadable, so the page passed as a genuine quiet week
    and the zero propagated — the exact corruption page_valid exists to stop.
    """
    result = parse_release_page(html, release_id="r", title="R")
    assert result["wines"] == []
    assert result["page_valid"] is False


def test_an_empty_card_list_is_still_an_authentic_empty_release() -> None:
    """The container present and genuinely empty stays valid."""
    result = parse_release_page(RECOGNISED_BUT_EMPTY_PAGE, release_id="r", title="R")
    assert result["page_valid"] is True
    assert result["wines"] == []


async def test_one_broken_kind_does_not_discard_its_cached_wines(
    hass: HomeAssistant,
) -> None:
    """A partial failure replaced the whole snapshot, dropping the failed kind.

    `_async_collect` skipped the broken kind and returned a successful result
    containing only the others. `DataUpdateCoordinator` replaces `data`
    wholesale, so the skipped kind's wines vanished and its sensor went
    unavailable — the same data loss, reached by a different route.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    broken = {"now": False}

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if rid.startswith("tillfalligt") else KIND_HITLISTAN
        if broken["now"] and kind == KIND_HITLISTAN:
            return parse_release_page(MAINTENANCE_PAGE, release_id=rid, title=title)
        return {
            "release": build_release(rid, kind, "2026-09-11", wine_count=1),
            "wines": [build_wine(rid, f"Vin {kind}")],
            "warnings": [],
            "page_valid": True,
        }

    index = [
        build_release("tillfalligt-x", KIND_TILLFALLIGT, "2026-09-11"),
        build_release("hitlista-x", KIND_HITLISTAN, "2026-09-03"),
    ]

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=index)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert coordinator.data["releases"][KIND_HITLISTAN]["release"]["wine_count"] == 1

        broken["now"] = True
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    # The healthy kind refreshed ...
    assert coordinator.last_update_success is True
    assert coordinator.data["releases"][KIND_TILLFALLIGT]["release"]["wine_count"] == 1
    # ... and the broken one kept what it had, flagged as not freshly confirmed.
    kept = coordinator.data["releases"].get(KIND_HITLISTAN)
    assert kept is not None, "the broken kind's cached wines were discarded"
    assert kept["release"]["wine_count"] == 1
    assert kept.get("stale") is True

    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_release_{KIND_HITLISTAN}"
    )
    state = hass.states.get(entity_id)
    assert state.state == "1", "the sensor lost its reading on a partial failure"
    assert state.attributes["stale"] is True


async def test_a_kind_skipped_by_a_rate_limit_keeps_its_data(hass: HomeAssistant) -> None:
    """The 429 `break` leaves later kinds unfetched; they must not be dropped."""
    from custom_components.munskankarna.api import RateLimited

    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    limited = {"now": False}

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if rid.startswith("tillfalligt") else KIND_HITLISTAN
        if limited["now"]:
            raise RateLimited("slow down", retry_after=1800)
        return {
            "release": build_release(rid, kind, "2026-09-11", wine_count=1),
            "wines": [build_wine(rid, f"Vin {kind}")],
            "warnings": [],
            "page_valid": True,
        }

    index = [
        build_release("tillfalligt-x", KIND_TILLFALLIGT, "2026-09-11"),
        build_release("hitlista-x", KIND_HITLISTAN, "2026-09-03"),
    ]

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=index)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        limited["now"] = True
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    # Nothing loaded this cycle, so the update fails and HA keeps the snapshot.
    assert coordinator.last_update_success is False
    for kind in (KIND_TILLFALLIGT, KIND_HITLISTAN):
        assert coordinator.data["releases"][kind]["release"]["wine_count"] == 1
