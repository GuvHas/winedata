"""History survives a restart without re-reading the site.

History lives in `.storage`, not the recorder. That is the point: the recorder
is for time-series state, and a 3-week wine archive is a document cache. Home
Assistant restarts often — updates, reboots, reloads — and re-fetching a dozen
release pages on each one would be rude to a small volunteer-run site for data
that cannot have changed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.munskankarna.const import (
    CONF_KINDS,
    DOMAIN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

INDEX = [
    build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11"),
    build_release("t-2026-09-04", KIND_TILLFALLIGT, "2026-09-04"),
    build_release("t-2026-08-28", KIND_TILLFALLIGT, "2026-08-28"),
]


def _result(release_id: str) -> dict:
    return {
        "release": build_release(release_id, KIND_TILLFALLIGT,
                                 release_id.removeprefix("t-"), wine_count=1),
        "wines": [build_wine(release_id, "Ett Vin")],
        "warnings": [],
        "page_valid": True,
    }


async def test_history_is_written_to_storage_not_the_recorder(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """A poll persists the retained releases under the integration's own key."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return _result(rid)

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=INDEX)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    key = f"{DOMAIN}.history_{entry.entry_id}"
    assert key in hass_storage, f"nothing stored under {key}; saw {list(hass_storage)}"
    stored = hass_storage[key]["data"]
    assert [r["release"]["id"] for r in stored["history"][KIND_TILLFALLIGT]] == [
        "t-2026-09-11", "t-2026-09-04", "t-2026-08-28"
    ]


async def test_a_restart_restores_history_without_refetching(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The whole reason for persisting: a restart costs one request, not three."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    hass_storage[f"{DOMAIN}.history_{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.history_{entry.entry_id}",
        "data": {"history": {KIND_TILLFALLIGT: [_result(r["id"]) for r in INDEX]}},
    }

    fetched: list[str] = []

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        fetched.append(rid)
        return _result(rid)

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=INDEX)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert fetched == ["t-2026-09-11"], (
        f"a restart should re-read only the current release, read {fetched}"
    )
    coordinator = entry.runtime_data
    assert len(coordinator.data["history"][KIND_TILLFALLIGT]) == 3


async def test_a_corrupt_store_does_not_prevent_setup(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """A cache is an optimisation; a broken one must never block startup."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    hass_storage[f"{DOMAIN}.history_{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.history_{entry.entry_id}",
        "data": {"history": "this is not a dict of lists"},
    }

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return _result(rid)

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=INDEX)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.last_update_success is True
    assert len(coordinator.data["history"][KIND_TILLFALLIGT]) == 3


async def test_removing_the_entry_removes_its_stored_history(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """Uninstalling must not leave an orphaned cache behind."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return _result(rid)

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=INDEX)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    key = f"{DOMAIN}.history_{entry.entry_id}"
    assert key in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage.get(key, {}).get("data") in (None, {}), "the cache outlived the entry"
