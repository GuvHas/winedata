"""Following the index's own "Visa fler" links, and saying so when it cannot.

The release index carries exactly five releases per tasting type and then a
link to that type's own page. `MAX_HISTORY_COUNT` is six, so the options UI
offers a retention depth the index alone cannot satisfy — and until now the
sixth release was simply invisible, with nothing said about it.

Following costs nothing at the default depth of three, because five is
already enough; a request is made only for a tracked kind that is actually
short. Whatever is still short after that is reported rather than silently
truncated, which also covers the case where the per-type page does not parse.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.munskankarna.const import (
    CONF_HISTORY_COUNT,
    CONF_KINDS,
    DEFAULT_BASE_URL,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from custom_components.munskankarna.parser import parse_more_links
from tests.helpers import build_release, build_wine, create_entry


def test_the_index_offers_a_page_per_tasting_type(load_fixture_html) -> None:
    """Read off the real capture, not off an assumption about the markup."""
    links = parse_more_links(load_fixture_html("release-index.html"), DEFAULT_BASE_URL)

    assert links[KIND_TILLFALLIGT] == (
        f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp/tillfalligt-sortiment"
    )
    # The kind comes from the heading, never the href: "Fast sortiment" is
    # reached at /provningstyp/ordinarie-sortimentet.
    assert links["fast-sortiment"] == (
        f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp/ordinarie-sortimentet"
    )
    assert len(links) == 7, f"expected one per tasting type, got {sorted(links)}"


def _index(n: int) -> list[dict[str, Any]]:
    """`n` releases of one kind, newest first, as the index lists them."""
    return [
        build_release(f"t-2026-09-{11 - 7 * i:02d}", KIND_TILLFALLIGT,
                      f"2026-09-{11 - 7 * i:02d}")
        for i in range(n)
    ]


async def _run(hass: HomeAssistant, *, depth: int, on_page_one: int, behind: int):
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_HISTORY_COUNT: depth}
    )
    coordinator = MunskankarnaCoordinator(hass, entry)
    followed: list[str] = []
    everything = _index(on_page_one + behind)

    async def fake_more(self, kind: str, url: str) -> list[dict]:  # noqa: ANN001
        followed.append(url)
        return everything[on_page_one:]

    async def fake_release(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(rid, KIND_TILLFALLIGT, rid.removeprefix("t-"),
                                     wine_count=1),
            "wines": [build_wine(rid, "Ett Vin")],
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=everything[:on_page_one]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_more_links",
                     new=lambda self: {KIND_TILLFALLIGT: "https://example.test/more"}),
        patch.object(MunskankarnaCoordinator, "_async_fetch_more", new=fake_more),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_release),
    ):
        await coordinator.async_refresh()
    return coordinator, followed


async def test_a_deep_enough_index_is_never_followed(hass: HomeAssistant) -> None:
    """At the default depth the five on the page are already enough."""
    coordinator, followed = await _run(hass, depth=3, on_page_one=5, behind=3)

    assert followed == [], f"fetched {followed} without needing to"
    assert len(coordinator.data["history"][KIND_TILLFALLIGT]) == 3


async def test_a_short_kind_follows_its_own_page(hass: HomeAssistant) -> None:
    """Depth six against an index that lists five: the sixth is reachable."""
    coordinator, followed = await _run(hass, depth=6, on_page_one=5, behind=3)

    assert followed == ["https://example.test/more"], "the type's page was not read"
    retained = coordinator.data["history"][KIND_TILLFALLIGT]
    assert len(retained) == 6, f"retained {len(retained)} of the 6 configured"
    assert not [w for w in coordinator.data["warnings"] if "release" in w]


async def test_a_kind_that_is_still_short_is_reported(hass: HomeAssistant) -> None:
    """Silence is the bug. A depth the site cannot fill has to be visible."""
    coordinator, _ = await _run(hass, depth=6, on_page_one=2, behind=1)

    warnings = coordinator.data["warnings"]
    assert any(KIND_TILLFALLIGT in w and "6" in w for w in warnings), (
        f"nothing reported a short index: {warnings}"
    )
    # Short is not broken: whatever was found is still retained and published.
    assert len(coordinator.data["history"][KIND_TILLFALLIGT]) == 3


def test_a_more_link_off_the_configured_site_is_dropped() -> None:
    """These URLs get fetched, so the index must not be able to redirect us.

    An absolute href in scraped markup would otherwise send `fetch_text` at
    whatever host it names — including the Home Assistant host's own network.
    """
    hostile = (
        '<h3>Hitlista</h3>'
        '<a href="http://127.0.0.1:8123/sv/vinlocus/private">Visa fler från Hitlista</a>'
    )
    assert parse_more_links(hostile, DEFAULT_BASE_URL) == {}

    scripted = (
        '<h3>Hitlista</h3>'
        '<a href="javascript:alert(1)/sv/vinlocus/x">Visa fler från Hitlista</a>'
    )
    assert parse_more_links(scripted, DEFAULT_BASE_URL) == {}


def test_a_relative_more_link_still_resolves() -> None:
    """The guard must not throw out the links that actually exist."""
    ok = (
        '<h3>Hitlista</h3>'
        '<a href="/sv/vinlocus/provningstyp/hitlistan">Visa fler från Hitlista</a>'
    )
    assert parse_more_links(ok, DEFAULT_BASE_URL) == {
        "hitlistan": f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp/hitlistan"
    }
