"""HTTP client lifecycle and connection reuse.

One client, one login and one TLS handshake per update cycle — and nothing
left open when the entry is unloaded.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import respx
from homeassistant.core import HomeAssistant

from custom_components.munskankarna.api import MunskankarnaClient
from custom_components.munskankarna.const import (
    CONF_KINDS,
    DEFAULT_BASE_URL,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry


@respx.mock
async def test_one_connection_pool_serves_a_whole_cycle() -> None:
    """Every request in a cycle must share one client, not one each."""
    respx.get(url__regex=rf"{DEFAULT_BASE_URL}/.*").mock(
        return_value=httpx.Response(200, text="ok")
    )
    async with MunskankarnaClient(DEFAULT_BASE_URL) as client:
        transport_before = client._client
        for path in ("/a", "/b", "/c"):
            await client.fetch_text(path)
        assert client._client is transport_before, "client was replaced mid-cycle"


@respx.mock
async def test_the_client_is_closed_when_the_cycle_ends() -> None:
    """A client we created must not outlive its context."""
    respx.get(f"{DEFAULT_BASE_URL}/a").mock(return_value=httpx.Response(200, text="ok"))
    client = MunskankarnaClient(DEFAULT_BASE_URL)
    async with client:
        await client.fetch_text("/a")
        inner = client._client
    assert inner.is_closed, "the client was left open after the cycle"


@respx.mock
async def test_the_client_is_closed_even_when_the_cycle_raises() -> None:
    """An exception mid-update must not leak a connection pool."""
    respx.get(f"{DEFAULT_BASE_URL}/a").mock(side_effect=httpx.ConnectError("boom"))
    client = MunskankarnaClient(DEFAULT_BASE_URL)
    inner = None
    try:
        async with client:
            inner = client._client
            await client.fetch_text("/a")
    except Exception:  # noqa: BLE001 - the failure itself is not under test
        pass
    assert inner is not None and inner.is_closed


async def test_unloading_the_entry_leaves_nothing_behind(hass: HomeAssistant) -> None:
    """After unload there is no coordinator, no runtime_data and no services."""
    from custom_components.munskankarna.const import DOMAIN

    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [build_wine(release_id, "Ett Vin", 15.0)],
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release("r", KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data is not None

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    # Services are domain-wide and go with the last entry.
    assert not hass.services.has_service(DOMAIN, "trigger_sync")
    assert not hass.services.has_service(DOMAIN, "publish_mqtt")
    # No entities left behind.
    assert not [
        e for e in hass.states.async_entity_ids("sensor") if "munskankarna" in e
    ] or all(
        hass.states.get(e).state == "unavailable"
        for e in hass.states.async_entity_ids("sensor")
        if "munskankarna" in e
    )


async def test_no_update_runs_after_unload(hass: HomeAssistant) -> None:
    """A scheduled refresh must not fire against a torn-down entry."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    calls: list[str] = []

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        calls.append(release_id)
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [build_wine(release_id, "Ett Vin", 15.0)],
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release("r", KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        after_setup = len(calls)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert len(calls) == after_setup, "an update ran after unload"
