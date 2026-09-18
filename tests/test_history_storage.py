"""History survives a restart without re-reading the site.

History lives in `.storage`, not the recorder. That is the point: the recorder
is for time-series state, and a 3-week wine archive is a document cache. Home
Assistant restarts often — updates, reboots, reloads — and re-fetching a dozen
release pages on each one would be rude to a small volunteer-run site for data
that cannot have changed.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
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


@contextmanager
def _writes(coordinator: MunskankarnaCoordinator):
    """Count the times the cache is actually handed to the store."""
    store = coordinator._store
    counter = SimpleNamespace(count=0)
    real_delay, real_save = store.async_delay_save, store.async_save

    def delayed(data_func, delay=0):  # noqa: ANN001
        counter.count += 1
        return real_delay(data_func, delay)

    async def immediate(data):  # noqa: ANN001
        counter.count += 1
        return await real_save(data)

    with (
        patch.object(store, "async_delay_save", delayed),
        patch.object(store, "async_save", immediate),
    ):
        yield counter


@contextmanager
def _site(pages: dict[str, dict]):
    """A site whose release pages can be swapped between polls."""

    async def fake_release(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return pages[rid]

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=list(INDEX)),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_release),
    ):
        yield


async def test_an_unchanged_cycle_is_not_written_again(hass: HomeAssistant) -> None:
    """Published pages are immutable, so most polls change nothing at all.

    Rewriting the whole file regardless costs 257 kB every six hours at the
    defaults — about 1 MB a day of byte-identical writes, usually onto an SD
    card. Roughly 99% of them are avoidable.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    coordinator = MunskankarnaCoordinator(hass, entry)
    pages = {r["id"]: _result(r["id"]) for r in INDEX}

    with _site(pages), _writes(coordinator) as writes:
        await coordinator.async_load_history()
        await coordinator.async_refresh()
        after_first = writes.count
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert after_first == 1, f"the first cycle wrote {after_first} time(s)"
    assert writes.count == 1, (
        f"two unchanged cycles wrote {writes.count - after_first} more time(s)"
    )


async def test_a_changed_release_is_written(hass: HomeAssistant) -> None:
    """Skipping a write must depend on the content, not on the clock."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    coordinator = MunskankarnaCoordinator(hass, entry)
    pages = {r["id"]: _result(r["id"]) for r in INDEX}

    with _site(pages), _writes(coordinator) as writes:
        await coordinator.async_load_history()
        await coordinator.async_refresh()
        baseline = writes.count

        newest = "t-2026-09-11"
        pages[newest] = {
            **pages[newest],
            "wines": [
                build_wine(newest, "Ett Vin"),
                build_wine(newest, "Sent Tillagt", score=16.0),
            ],
        }
        await coordinator.async_refresh()

    assert writes.count == baseline + 1, "a changed release was not persisted"


async def test_an_upgrade_persists_the_announcement_record(
    hass: HomeAssistant,
) -> None:
    """The record must be written even when the history itself is unchanged.

    1.1.3 stored history and nothing else. On upgrade the first cycle seeds
    the record of what has been announced while the releases stay byte for
    byte the same — so a check that looked only at the history would skip the
    write, and every restart would seed again and re-announce.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    seeding = MunskankarnaCoordinator(hass, entry)
    pages = {r["id"]: _result(r["id"]) for r in INDEX}
    with _site(pages):
        await seeding.async_load_history()
        await seeding.async_refresh()
    stored_history = (await seeding._store.async_load())["history"]

    # Exactly what 1.1.3 left behind: history, no record.
    await seeding._store.async_save({"history": stored_history})

    upgraded = MunskankarnaCoordinator(hass, entry)
    with _site(pages), _writes(upgraded) as writes:
        await upgraded.async_load_history()
        await upgraded.async_refresh()

    assert writes.count == 1, "the seeded announcement record was never persisted"
    assert (await upgraded._store.async_load()).get("seen"), "the record is missing"
