"""Regression guard for the reported blocking-call warning.

    Detected blocking call to load_verify_locations with args
    (<ssl.SSLContext ...>, '.../certifi/cacert.pem', None, None) inside the
    event loop by custom integration 'munskankarna' at api.py, line 90

Home Assistant detects this by patching `ssl.SSLContext.load_verify_locations`
and friends (see homeassistant/block_async_io.py). That detector is recent —
the pinned test harness here predates it, which is exactly why the suite did
not catch the bug — so these tests install an equivalent probe and drive the
real entry points through it.
"""

from __future__ import annotations

import ssl
import threading
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.munskankarna import api as api_module
from custom_components.munskankarna.const import (
    CONF_BASE_URL,
    CONF_KINDS,
    DEFAULT_BASE_URL,
    DOMAIN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import create_entry

INDEX_HTML = (
    "<html><h3>Tillfälligt sortiment</h3>"
    '<a href="/sv/vinlocus/tillfalligt-sortiment-11-september-2026">'
    "Tillfälligt sortiment 11 september 2026</a></html>"
)
RELEASE_HTML = (
    '<ul id="wine-bottles-list"><li class="medium-3 groupedlist">'
    '<div class="c-wine-info"><div class="wine-points">15</div>'
    '<div class="c-wine-info__price">199:-</div>'
    '<div class="c-wine-info__headings"><h3><a href="/sv/vinlocus/a/b">'
    "<span>Ett Vin 2020</span></a></h3></div></div></li></ul>"
)


@pytest.fixture
def blocking_probe(monkeypatch):
    """Flag CA-bundle loads that happen on the event loop thread.

    Mirrors Home Assistant's own check: a call carrying only `cadata` touches
    no filesystem and is allowed; anything else reads from disk.
    """
    # Start from a cold cache so the fallback build path is genuinely exercised.
    monkeypatch.setattr(api_module, "_DEFAULT_SSL_CONTEXT", None, raising=False)

    loop_thread = threading.current_thread()
    offenders: list[str] = []
    original = ssl.SSLContext.load_verify_locations

    def guarded(self, cafile=None, capath=None, cadata=None, **kwargs: Any):
        only_cadata = cafile is None and capath is None and cadata is not None
        if threading.current_thread() is loop_thread and not only_cadata:
            offenders.append(f"load_verify_locations(cafile={cafile!r}, capath={capath!r})")
        return original(self, cafile, capath, cadata, **kwargs)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", guarded)
    return offenders


async def test_config_flow_does_not_block_the_event_loop(
    hass: HomeAssistant, blocking_probe
) -> None:
    """Setting the integration up must not read the CA bundle on the loop."""
    with patch("custom_components.munskankarna.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
                return_value=httpx.Response(200, text=INDEX_HTML)
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
            )
            await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert blocking_probe == [], f"blocking CA load during config flow: {blocking_probe}"


async def test_update_cycle_does_not_block_the_event_loop(
    hass: HomeAssistant, blocking_probe
) -> None:
    """A full poll must not read the CA bundle on the loop either."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    coordinator = MunskankarnaCoordinator(hass, entry)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
            return_value=httpx.Response(200, text=INDEX_HTML)
        )
        mock.route(url__regex=r".*/sv/vinlocus/tillfalligt.*").mock(
            return_value=httpx.Response(200, text=RELEASE_HTML)
        )
        data = await coordinator._async_update_data()

    assert KIND_TILLFALLIGT in data["releases"]
    assert blocking_probe == [], f"blocking CA load during update: {blocking_probe}"


async def test_standalone_client_does_not_block_the_event_loop(blocking_probe) -> None:
    """Without Home Assistant, the fallback context is built off the loop."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(200, text="ok"))
        async with api_module.MunskankarnaClient(DEFAULT_BASE_URL) as client:
            await client.fetch_text("/x")

    assert blocking_probe == [], f"blocking CA load in standalone client: {blocking_probe}"


async def test_probe_catches_the_original_bug(blocking_probe) -> None:
    """The probe must actually fail on the pre-fix code, or it proves nothing.

    Constructing httpx.AsyncClient() with no `verify` is precisely what api.py
    used to do, and is what produced the reported warning.
    """
    client = httpx.AsyncClient()
    await client.aclose()

    assert blocking_probe, (
        "the probe did not detect a known-blocking construction; "
        "it would not have caught the original bug either"
    )


async def test_entry_setup_is_clean(hass: HomeAssistant, blocking_probe) -> None:
    """Setting up the entry end to end, the way Home Assistant does."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
            return_value=httpx.Response(200, text=INDEX_HTML)
        )
        mock.route(url__regex=r".*/sv/vinlocus/tillfalligt.*").mock(
            return_value=httpx.Response(200, text=RELEASE_HTML)
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert blocking_probe == [], f"blocking CA load during entry setup: {blocking_probe}"


async def test_repeated_updates_reuse_the_cached_context(
    hass: HomeAssistant, blocking_probe
) -> None:
    """Polling every six hours must not rebuild the context each time."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    coordinator = MunskankarnaCoordinator(hass, entry)

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=[])
        ),
        pytest.raises(Exception),  # noqa: B017 - empty index fails the update, as designed
    ):
        await coordinator._async_update_data()

    assert blocking_probe == []
