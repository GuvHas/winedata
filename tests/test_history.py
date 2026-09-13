"""Multi-release retention.

The integration used to keep only the newest release per tasting type. It now
retains the most recent few, so a dashboard can show several weeks at once.

Retention is deliberately **count-based, not age-based**. Measured from the
real release index, the categories publish on very different cadences —
Tillfälligt sortiment every 7 days, Hitlistan every 14, Fast sortiment and
Lokalt och småskaligt every 28–62. A 21-day cutoff would leave the slower
categories empty for most of every month, which is worse than the problem it
would solve.
"""

from __future__ import annotations

import pytest

from custom_components.munskankarna.const import (
    DEFAULT_HISTORY_COUNT,
    MAX_HISTORY_COUNT,
)
from custom_components.munskankarna.coordinator import merge_history
from tests.helpers import build_release, build_wine


def _result(release_id: str, date: str | None, wines: int = 1) -> dict:
    """A ParseResult-shaped dict for one release."""
    return {
        "release": build_release(release_id, "tillfalligt-sortiment", date,
                                 wine_count=wines),
        "wines": [build_wine(release_id, f"Vin {n}") for n in range(wines)],
        "warnings": [],
        "page_valid": True,
    }


def test_the_default_keeps_three_releases() -> None:
    """Three is the shipped depth; the ceiling bounds a misconfigured entry."""
    assert DEFAULT_HISTORY_COUNT == 3
    assert MAX_HISTORY_COUNT >= DEFAULT_HISTORY_COUNT


def test_a_new_release_is_added_without_displacing_the_old_ones() -> None:
    """The point of the feature: appending, not overwriting."""
    existing = [_result("r-2026-09-04", "2026-09-04")]
    merged = merge_history(existing, [_result("r-2026-09-11", "2026-09-11")], limit=3)

    assert [r["release"]["id"] for r in merged] == ["r-2026-09-11", "r-2026-09-04"]


def test_releases_are_ordered_newest_first() -> None:
    """Order is the dashboard's reading order, so it is the cache's order."""
    merged = merge_history(
        [],
        [
            _result("b", "2026-08-28"),
            _result("c", "2026-09-11"),
            _result("a", "2026-09-04"),
        ],
        limit=3,
    )
    assert [r["release"]["date"] for r in merged] == [
        "2026-09-11", "2026-09-04", "2026-08-28"
    ]


def test_a_refetched_release_replaces_its_cached_copy() -> None:
    """Re-polling the newest release must update it, not duplicate it."""
    existing = [_result("r-2026-09-11", "2026-09-11", wines=5)]
    merged = merge_history(existing, [_result("r-2026-09-11", "2026-09-11", wines=9)],
                           limit=3)

    assert len(merged) == 1, "the same release id was kept twice"
    assert merged[0]["release"]["wine_count"] == 9, "the fresher copy did not win"


def test_the_oldest_release_falls_off_the_end() -> None:
    """Retention is what bounds memory and attribute size; it must actually bite."""
    existing = [
        _result("r4", "2026-09-04"),
        _result("r3", "2026-08-28"),
        _result("r2", "2026-08-21"),
    ]
    merged = merge_history(existing, [_result("r5", "2026-09-11")], limit=3)

    assert len(merged) == 3
    assert [r["release"]["id"] for r in merged] == ["r5", "r4", "r3"]
    assert "r2" not in [r["release"]["id"] for r in merged]


@pytest.mark.parametrize("limit", [0, -1])
def test_a_nonsense_limit_still_keeps_the_current_release(limit: int) -> None:
    """Losing the current release is never an acceptable outcome."""
    merged = merge_history([], [_result("r", "2026-09-11")], limit=limit)
    assert len(merged) == 1


def test_an_undated_release_sorts_last_but_is_not_dropped() -> None:
    """Undated releases must not displace dated ones — nor vanish silently.

    `newest_per_kind` already treats an undated release as older than any dated
    one, so a stray missing date cannot mask the current week. The same rule
    applies here.
    """
    merged = merge_history(
        [], [_result("undated", None), _result("dated", "2026-09-11")], limit=3
    )
    assert [r["release"]["id"] for r in merged] == ["dated", "undated"]


def test_merging_does_not_mutate_the_list_it_was_given() -> None:
    """The coordinator's previous snapshot is shared; merging must copy."""
    existing = [_result("old", "2026-09-04")]
    before = list(existing)
    merge_history(existing, [_result("new", "2026-09-11")], limit=3)
    assert existing == before, "merge_history mutated its input"


# ---------------------------------------------------------------------------
# Coordinator: backfill from the index, then stop re-fetching what cannot change
# ---------------------------------------------------------------------------


from unittest.mock import AsyncMock, patch  # noqa: E402

from homeassistant.core import HomeAssistant  # noqa: E402

from custom_components.munskankarna.const import (  # noqa: E402
    CONF_HISTORY_COUNT,
    CONF_KINDS,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator  # noqa: E402
from tests.helpers import create_entry  # noqa: E402

#: Four weekly releases, newest first — the real Tillfälligt sortiment cadence.
WEEKLY_INDEX = [
    build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11"),
    build_release("t-2026-09-04", KIND_TILLFALLIGT, "2026-09-04"),
    build_release("t-2026-08-28", KIND_TILLFALLIGT, "2026-08-28"),
    build_release("t-2026-08-21", KIND_TILLFALLIGT, "2026-08-21"),
]


def _history_coordinator(hass: HomeAssistant, count: int = 3):
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_HISTORY_COUNT: count}
    )
    return MunskankarnaCoordinator(hass, entry), entry


async def test_the_index_backfills_history_on_the_first_cycle(
    hass: HomeAssistant,
) -> None:
    """History must not take three weeks to appear.

    The release index lists several past releases per category, so the depth
    can be filled on the first poll instead of accumulating from install day.
    """
    coordinator, _ = _history_coordinator(hass)
    fetched: list[str] = []

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        fetched.append(rid)
        return _result(rid, rid.removeprefix("t-"))

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=list(WEEKLY_INDEX)),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    history = coordinator.data["history"][KIND_TILLFALLIGT]
    assert [r["release"]["id"] for r in history] == [
        "t-2026-09-11", "t-2026-09-04", "t-2026-08-28"
    ], "the first cycle did not backfill three releases"
    # The fourth is beyond the retention depth and must not even be requested.
    assert "t-2026-08-21" not in fetched, "fetched a release beyond the retention depth"


async def test_a_cached_historical_release_is_never_refetched(
    hass: HomeAssistant,
) -> None:
    """Published release pages do not change, so re-reading them is pure waste.

    Only the newest release per kind can still gain corrections. Without this,
    every cycle would re-fetch every retained release: 13 requests instead of 5
    against a small volunteer-run site.
    """
    coordinator, _ = _history_coordinator(hass)
    fetched: list[str] = []

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        fetched.append(rid)
        return _result(rid, rid.removeprefix("t-"))

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=list(WEEKLY_INDEX)),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        await coordinator.async_refresh()
        first_cycle = list(fetched)
        fetched.clear()
        await coordinator.async_refresh()

    assert len(first_cycle) == 3, f"first cycle fetched {first_cycle}"
    assert fetched == ["t-2026-09-11"], (
        f"second cycle should re-read only the newest release, read {fetched}"
    )
    # History is intact after the cheap cycle.
    assert len(coordinator.data["history"][KIND_TILLFALLIGT]) == 3


async def test_the_retention_depth_is_clamped_like_the_other_options(
    hass: HomeAssistant,
) -> None:
    """A stored value above the ceiling must not be honoured on upgrade."""
    coordinator, _ = _history_coordinator(hass, count=999)
    assert coordinator.history_count == MAX_HISTORY_COUNT

    nonsense, _ = _history_coordinator(hass, count=0)
    assert nonsense.history_count == DEFAULT_HISTORY_COUNT


async def test_the_current_release_still_drives_the_existing_sensors(
    hass: HomeAssistant,
) -> None:
    """History is additive: `releases` keeps meaning the current release."""
    coordinator, _ = _history_coordinator(hass)

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return _result(rid, rid.removeprefix("t-"))

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=list(WEEKLY_INDEX)),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        await coordinator.async_refresh()

    current = coordinator.data["releases"][KIND_TILLFALLIGT]
    assert current["release"]["id"] == "t-2026-09-11"
