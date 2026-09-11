"""Phase 3a — coordinator tests (written before `coordinator.py`).

The coordinator turns "a list of releases" into "the newest release per tasting
type, with its wines sorted", which is the shape every sensor consumes.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.munskankarna.api import CannotConnect
from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TOP_COUNT,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry


def _index() -> list[dict]:
    """Two Tillfälligt releases (newest first is NOT the source order) + one Hitlista."""
    return [
        build_release("tillfalligt-sortiment-4-september-2026", KIND_TILLFALLIGT, "2026-09-04"),
        build_release("tillfalligt-sortiment-11-september-2026", KIND_TILLFALLIGT, "2026-09-11"),
        build_release("hitlista-3-september-2026", KIND_HITLISTAN, "2026-09-03"),
        build_release("webbviner-oktober-2026", "webbviner", "2026-10-01"),
    ]


def _page(release_id: str, wines: list[dict]) -> dict:
    return {
        "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=len(wines)),
        "wines": wines,
        "warnings": [],
    }


@pytest.fixture
def coordinator(hass: HomeAssistant) -> MunskankarnaCoordinator:
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: 3}
    )
    return MunskankarnaCoordinator(hass, entry)


async def test_selects_only_the_newest_release_per_kind(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator
) -> None:
    """One sensor per tasting type tracks the current release, not the archive."""
    fetched: list[str] = []

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        fetched.append(release_id)
        return _page(release_id, [build_wine(release_id, "A", 15.0)])

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        data = await coordinator._async_update_data()

    # Newest per configured kind; the older Tillfälligt and the unconfigured
    # webbviner release are both skipped.
    assert sorted(fetched) == [
        "hitlista-3-september-2026",
        "tillfalligt-sortiment-11-september-2026",
    ]
    assert set(data["releases"]) == {KIND_TILLFALLIGT, KIND_HITLISTAN}
    assert data["releases"][KIND_TILLFALLIGT]["release"]["id"] == (
        "tillfalligt-sortiment-11-september-2026"
    )


async def test_wines_are_sorted_by_score_then_value(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator
) -> None:
    """Sensors show the best picks first, so ordering belongs in the coordinator."""
    wines = [
        build_wine("r", "Low", 12.0, value="fynd"),
        build_wine("r", "High", 17.0, value="prisvart"),
        build_wine("r", "Mid-fynd", 15.0, value="fynd"),
        build_wine("r", "Mid-plain", 15.0, value="ej-prisvart"),
    ]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return _page(release_id, wines)

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release("r", KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        data = await coordinator._async_update_data()

    names = [w["name"] for w in data["releases"][KIND_TILLFALLIGT]["wines"]]
    # Score descending; equal scores break toward the better value verdict.
    assert names == ["High", "Mid-fynd", "Mid-plain", "Low"]


async def test_network_failure_raises_update_failed(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator
) -> None:
    """A failed index fetch must surface as UpdateFailed, not a raw exception."""
    with patch.object(
        MunskankarnaCoordinator,
        "_async_fetch_index",
        new=AsyncMock(side_effect=CannotConnect("down")),
    ), pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_one_failing_release_does_not_lose_the_others(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator
) -> None:
    """Partial failure degrades gracefully rather than blanking every sensor."""

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        if release_id.startswith("hitlista"):
            raise CannotConnect("that one is down")
        return _page(release_id, [build_wine(release_id, "A", 15.0)])

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        data = await coordinator._async_update_data()

    assert KIND_TILLFALLIGT in data["releases"]
    assert KIND_HITLISTAN not in data["releases"]
    assert any("hitlista" in warning for warning in data["warnings"])


async def test_every_release_failing_raises_update_failed(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator
) -> None:
    """If nothing could be fetched, the sensors should go unavailable."""

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        raise CannotConnect("all down")

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=_index())
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()


async def test_scan_interval_comes_from_options(hass: HomeAssistant) -> None:
    entry = create_entry(hass, options={CONF_SCAN_INTERVAL_HOURS: 12})
    coordinator = MunskankarnaCoordinator(hass, entry)
    assert coordinator.update_interval == timedelta(hours=12)


async def test_scan_interval_falls_back_to_the_default(hass: HomeAssistant) -> None:
    coordinator = MunskankarnaCoordinator(hass, create_entry(hass))
    assert coordinator.update_interval == timedelta(hours=6)


async def test_top_count_limits_the_exposed_wines(hass: HomeAssistant) -> None:
    """Attributes are recorded and broadcast, so the list is capped."""
    entry = create_entry(hass, options={CONF_TOP_COUNT: 2, CONF_KINDS: [KIND_TILLFALLIGT]})
    coordinator = MunskankarnaCoordinator(hass, entry)
    wines = [build_wine("r", f"W{i}", float(10 + i)) for i in range(10)]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return _page(release_id, wines)

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release("r", KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        data = await coordinator._async_update_data()

    assert coordinator.top_count == 2
    # The coordinator keeps the full release; the cap is applied when building
    # attributes, so counts stay truthful.
    assert data["releases"][KIND_TILLFALLIGT]["release"]["wine_count"] == 10


async def test_coordinator_passes_config_entry_when_core_supports_it(
    hass: HomeAssistant,
) -> None:
    """Newer cores want the entry passed explicitly rather than read from a ContextVar.

    Home Assistant 2024.12+ accepts `config_entry=` on DataUpdateCoordinator and
    reports reliance on the ContextVar fallback. It is only a warning for custom
    integrations, but passing it is correct and future-proofs the integration.
    """
    import inspect

    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    entry = create_entry(hass)
    coordinator = MunskankarnaCoordinator(hass, entry)

    supported = "config_entry" in inspect.signature(DataUpdateCoordinator.__init__).parameters
    if supported:
        assert coordinator.config_entry is entry
    else:
        # On older cores the attribute simply does not exist; the integration
        # keeps its own reference either way.
        assert coordinator.entry is entry


def test_coordinator_init_kwargs_adapts_to_the_running_core(hass: HomeAssistant) -> None:
    """The compat probe must yield the right kwargs for either core generation."""
    import inspect

    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from custom_components.munskankarna.coordinator import coordinator_init_kwargs

    entry = create_entry(hass)
    kwargs = coordinator_init_kwargs(entry)
    supported = "config_entry" in inspect.signature(DataUpdateCoordinator.__init__).parameters

    if supported:
        assert kwargs == {"config_entry": entry}
    else:
        assert kwargs == {}

    # Whatever the core accepts, the kwargs must be usable verbatim.
    signature = inspect.signature(DataUpdateCoordinator.__init__)
    for key in kwargs:
        assert key in signature.parameters
