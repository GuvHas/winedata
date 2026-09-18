"""Announce new releases and new wines on the Home Assistant event bus.

The integration published nothing to the bus before this. Every downstream
ask — a Companion App alert when a Fynd lands, an LED rack that reacts to a
high score — is the same primitive, so it is built once here and the routing
is left to the user's automations.

The hazard is announcing history. A fresh install backfills three releases per
kind from the index, and an upgrade restores them from `.storage`; firing for
those would notify ninety times for wines published weeks ago. So the first
cycle that has nothing to compare against seeds the record silently, and only
what appears *after* that is news.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.munskankarna.const import (
    CONF_HISTORY_COUNT,
    CONF_KINDS,
    EVENT_RELEASE_PUBLISHED,
    EVENT_WINE_RELEASED,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

WEEK_2 = build_release("t-2026-09-04", KIND_TILLFALLIGT, "2026-09-04")
WEEK_3 = build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11")


def _result(release_id: str, names: list[str]) -> dict[str, Any]:
    """A release page. Scores descend with position, so order is decidable."""
    return {
        "release": build_release(
            release_id, KIND_TILLFALLIGT, release_id.removeprefix("t-"),
            wine_count=len(names),
        ),
        "wines": [
            build_wine(release_id, name, score=17.0 - i) for i, name in enumerate(names)
        ],
        "warnings": [],
        "page_valid": True,
    }


def _coordinator(hass: HomeAssistant, count: int = 3):
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_HISTORY_COUNT: count}
    )
    return MunskankarnaCoordinator(hass, entry), entry


class _Site:
    """A fake Munskänkarna whose index and pages can change between polls."""

    def __init__(self, index: list[dict[str, Any]], pages: dict[str, list[str]]) -> None:
        self.index = index
        self.pages = pages

    def patches(self):
        site = self

        async def fake_index(self) -> list[dict[str, Any]]:  # noqa: ANN001
            return list(site.index)

        async def fake_release(self, rid: str, title: str) -> dict:  # noqa: ANN001
            return _result(rid, site.pages[rid])

        return (
            patch.object(MunskankarnaCoordinator, "_async_fetch_index", new=fake_index),
            patch.object(
                MunskankarnaCoordinator, "_async_fetch_release", new=fake_release
            ),
        )


async def test_the_first_cycle_announces_nothing(hass: HomeAssistant) -> None:
    """A fresh install backfills weeks of history. None of it is news."""
    coordinator, _ = _coordinator(hass)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)
    releases = async_capture_events(hass, EVENT_RELEASE_PUBLISHED)

    site = _Site([WEEK_3, WEEK_2], {"t-2026-09-11": ["Ett Vin"], "t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await coordinator.async_load_history()
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert wines == [], f"the backfill announced {len(wines)} wine(s)"
    assert releases == [], f"the backfill announced {len(releases)} release(s)"


async def test_a_release_that_arrives_later_is_announced(hass: HomeAssistant) -> None:
    """The whole point: what appears after the record is seeded is news."""
    coordinator, _ = _coordinator(hass)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)
    releases = async_capture_events(hass, EVENT_RELEASE_PUBLISHED)

    site = _Site([WEEK_2], {"t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await coordinator.async_load_history()
        await coordinator.async_refresh()
        assert wines == [] and releases == []

        site.index = [WEEK_3, WEEK_2]
        site.pages["t-2026-09-11"] = ["Nytt Vin", "Andra Vinet"]
        await coordinator.async_refresh()

    assert [e.data["release_id"] for e in releases] == ["t-2026-09-11"]
    assert releases[0].data["kind"] == KIND_TILLFALLIGT
    assert releases[0].data["date"] == "2026-09-11"

    # Best first within a release: the coordinator sorts before publishing, and
    # an automation that acts on the first event should get the best wine.
    assert [e.data["name"] for e in wines] == ["Nytt Vin", "Andra Vinet"]
    assert wines[0].data["score"] > wines[1].data["score"]
    announced = wines[0].data
    assert announced["kind"] == KIND_TILLFALLIGT
    assert announced["release_id"] == "t-2026-09-11"
    assert announced["release_date"] == "2026-09-11"
    # The payload carries what an automation actually branches on.
    for field in ("score", "value", "price", "color", "article_number", "url"):
        assert field in announced, f"{field} missing from the wine payload"


async def test_nothing_is_announced_twice(hass: HomeAssistant) -> None:
    """Polling every six hours must not re-announce the same week all week."""
    coordinator, _ = _coordinator(hass)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)

    site = _Site([WEEK_2], {"t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await coordinator.async_load_history()
        await coordinator.async_refresh()
        site.index = [WEEK_3, WEEK_2]
        site.pages["t-2026-09-11"] = ["Nytt Vin"]
        await coordinator.async_refresh()
        assert len(wines) == 1
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert len(wines) == 1, f"announced {len(wines)} times for one wine"


async def test_a_wine_added_to_the_current_release_is_announced(
    hass: HomeAssistant,
) -> None:
    """The newest page is re-read every cycle precisely because it can change."""
    coordinator, _ = _coordinator(hass)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)

    site = _Site([WEEK_2], {"t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await coordinator.async_load_history()
        await coordinator.async_refresh()
        site.pages["t-2026-09-04"] = ["Ett Till", "Sent Tillagt"]
        await coordinator.async_refresh()

    assert [e.data["name"] for e in wines] == ["Sent Tillagt"], (
        "a wine added to a known release should be the only announcement"
    )


async def test_an_upgrade_announces_nothing(hass: HomeAssistant) -> None:
    """1.1.3 stored history but no record of what had been announced.

    Treating that as "nothing announced yet" would notify for every retained
    wine the moment the user updates.
    """
    coordinator, entry = _coordinator(hass)
    await coordinator._store.async_save(
        {"history": {KIND_TILLFALLIGT: [_result("t-2026-09-04", ["Ett Till"])]}}
    )

    fresh = MunskankarnaCoordinator(hass, entry)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)
    site = _Site([WEEK_2], {"t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await fresh.async_load_history()
        await fresh.async_refresh()

    assert wines == [], f"upgrading announced {len(wines)} wine(s)"


async def test_the_record_survives_a_restart(hass: HomeAssistant) -> None:
    """Otherwise every restart re-announces the current week."""
    coordinator, entry = _coordinator(hass)
    site = _Site([WEEK_2], {"t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await coordinator.async_load_history()
        await coordinator.async_refresh()
        site.index = [WEEK_3, WEEK_2]
        site.pages["t-2026-09-11"] = ["Nytt Vin"]
        await coordinator.async_refresh()

    restarted = MunskankarnaCoordinator(hass, entry)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)
    with site.patches()[0], site.patches()[1]:
        await restarted.async_load_history()
        await restarted.async_refresh()

    assert wines == [], "a restart re-announced wines already seen"


async def test_one_category_cannot_silence_another(hass: HomeAssistant) -> None:
    """Categories publish on different schedules; a shared high-water mark breaks that.

    A weekly release dated later than a monthly one is processed first, and a
    single global mark then rules the monthly release out as history — while
    still recording its ids, so it is never announced at all.
    """
    from custom_components.munskankarna.const import KIND_HITLISTAN

    entry = create_entry(
        hass,
        options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_HISTORY_COUNT: 2},
    )
    coordinator = MunskankarnaCoordinator(hass, entry)

    weekly_old = build_release("t-2026-09-04", KIND_TILLFALLIGT, "2026-09-04")
    monthly_old = build_release("h-2026-08-20", KIND_HITLISTAN, "2026-08-20")
    weekly_new = build_release("t-2026-09-18", KIND_TILLFALLIGT, "2026-09-18")
    monthly_new = build_release("h-2026-09-15", KIND_HITLISTAN, "2026-09-15")

    index = [weekly_old, monthly_old]

    async def fake_index(self) -> list[dict[str, Any]]:  # noqa: ANN001
        return list(index)

    async def fake_release(self, rid: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if rid.startswith("t-") else KIND_HITLISTAN
        return {
            "release": build_release(rid, kind, rid[2:], wine_count=1),
            "wines": [build_wine(rid, f"Vin {rid}")],
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(MunskankarnaCoordinator, "_async_fetch_index", new=fake_index),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_release),
    ):
        await coordinator.async_load_history()
        await coordinator.async_refresh()

        releases = async_capture_events(hass, EVENT_RELEASE_PUBLISHED)
        index[:] = [weekly_new, weekly_old, monthly_new, monthly_old]
        await coordinator.async_refresh()

    announced = {e.data["release_id"] for e in releases}
    assert announced == {"t-2026-09-18", "h-2026-09-15"}, (
        f"the later weekly release silenced the monthly one: {announced}"
    )


async def test_nothing_is_announced_if_the_record_cannot_be_persisted(
    hass: HomeAssistant,
) -> None:
    """Announcing before the record is durable means announcing twice.

    The save helper swallows write failures on purpose, so a failed write is
    silent — and after a restart the old record would make these wines news
    all over again.
    """
    coordinator, _ = _coordinator(hass)
    wines = async_capture_events(hass, EVENT_WINE_RELEASED)

    site = _Site([WEEK_2], {"t-2026-09-04": ["Ett Till"]})
    with site.patches()[0], site.patches()[1]:
        await coordinator.async_load_history()
        await coordinator.async_refresh()

        site.index = [WEEK_3, WEEK_2]
        site.pages["t-2026-09-11"] = ["Nytt Vin"]
        with patch.object(
            coordinator._store, "async_save", side_effect=OSError("disk full")
        ):
            await coordinator.async_refresh()
        assert wines == [], "announced a wine whose record was never written"

        # The next cycle writes, so the wine is announced then — deferred, not lost.
        await coordinator.async_refresh()

    assert [e.data["name"] for e in wines] == ["Nytt Vin"]
