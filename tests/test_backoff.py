"""Rate limiting, retries and backoff.

The integration polls a small volunteer-run site. Being a good citizen when it
pushes back is a correctness requirement, not a nicety.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from custom_components.munskankarna.api import (
    CannotConnect,
    MunskankarnaClient,
    RateLimited,
)
from custom_components.munskankarna.const import DEFAULT_BASE_URL

INDEX = (
    "<html><h3>T</h3>"
    '<a href="/sv/vinlocus/r1">R1 1 september 2026</a>'
    '<a href="/sv/vinlocus/r2">R2 2 september 2026</a>'
    '<a href="/sv/vinlocus/r3">R3 3 september 2026</a></html>'
)


@respx.mock
async def test_a_rate_limit_stops_the_whole_cycle() -> None:
    """After a 429 the cycle must stop, not walk the remaining releases.

    Previously every release was attempted in turn, so one rate limit produced
    N further requests against a server that had just asked us to slow down.
    """
    respx.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
        return_value=httpx.Response(200, text=INDEX)
    )
    limited = respx.route(url__regex=r".*/sv/vinlocus/r\d$").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "120"})
    )

    async with MunskankarnaClient(DEFAULT_BASE_URL) as client:
        releases = await client.async_fetch_releases()
        results, errors = await client.async_fetch_many(releases)

    assert limited.call_count == 1, (
        f"kept requesting after a 429: {limited.call_count} requests"
    )
    assert results == []
    assert errors and "rate" in errors[0].lower()


@respx.mock
async def test_rate_limit_raises_a_distinct_error_carrying_retry_after() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/x").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "90"})
    )
    async with MunskankarnaClient(DEFAULT_BASE_URL) as client:
        with pytest.raises(RateLimited) as excinfo:
            await client.fetch_text("/x")
    assert excinfo.value.retry_after == 90


@respx.mock
async def test_retry_after_http_date_is_understood() -> None:
    """Retry-After may be an HTTP date rather than a number of seconds."""
    respx.get(f"{DEFAULT_BASE_URL}/x").mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "Wed, 11 Sep 2030 19:00:00 GMT"}
        )
    )
    async with MunskankarnaClient(DEFAULT_BASE_URL) as client:
        with pytest.raises(RateLimited) as excinfo:
            await client.fetch_text("/x")
    assert excinfo.value.retry_after is not None
    assert excinfo.value.retry_after > 0


@respx.mock
async def test_a_transient_503_is_retried_then_succeeds() -> None:
    """A single 503 should not fail a six-hourly poll."""
    route = respx.get(f"{DEFAULT_BASE_URL}/x").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, text="ok"),
        ]
    )
    async with MunskankarnaClient(DEFAULT_BASE_URL, retry_backoff=0) as client:
        assert await client.fetch_text("/x") == "ok"
    assert route.call_count == 2


@respx.mock
async def test_retries_are_bounded() -> None:
    """Persistent 5xx must give up rather than loop."""
    route = respx.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(503))
    async with MunskankarnaClient(DEFAULT_BASE_URL, retry_backoff=0) as client:
        with pytest.raises(CannotConnect):
            await client.fetch_text("/x")
    assert route.call_count <= 3, f"{route.call_count} attempts is too many"


@respx.mock
async def test_a_rate_limit_is_never_retried_immediately() -> None:
    """429 means stop, not try again."""
    route = respx.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(429))
    async with MunskankarnaClient(DEFAULT_BASE_URL, retry_backoff=0) as client:
        with pytest.raises(RateLimited):
            await client.fetch_text("/x")
    assert route.call_count == 1


@respx.mock
async def test_client_errors_are_not_retried() -> None:
    """A 404 will not become a 200 on the second attempt."""
    route = respx.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(404))
    async with MunskankarnaClient(DEFAULT_BASE_URL, retry_backoff=0) as client:
        with pytest.raises(CannotConnect):
            await client.fetch_text("/x")
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# The coordinator walks releases itself, so it needs the same restraint.
# ---------------------------------------------------------------------------


async def test_coordinator_abandons_the_cycle_when_rate_limited(hass) -> None:
    """A 429 on one release must not trigger requests for the rest."""
    from unittest.mock import AsyncMock, patch

    from custom_components.munskankarna.const import (
        CONF_KINDS,
        KIND_HITLISTAN,
        KIND_TILLFALLIGT,
    )
    from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
    from tests.helpers import build_release, create_entry

    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN]})
    coordinator = MunskankarnaCoordinator(hass, entry)

    attempted: list[str] = []

    async def fake_fetch(self, release_id: str, title: str):  # noqa: ANN001, ANN202
        attempted.append(release_id)
        raise RateLimited("Rate limited", retry_after=120)

    index = [
        build_release("tillfalligt-x", KIND_TILLFALLIGT, "2026-09-11"),
        build_release("hitlista-x", KIND_HITLISTAN, "2026-09-03"),
    ]

    from homeassistant.helpers.update_coordinator import UpdateFailed

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=index)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()

    assert len(attempted) == 1, f"kept fetching after a 429: {attempted}"


async def test_a_rate_limit_during_setup_is_shown_as_cannot_connect(hass) -> None:
    """RateLimited must not fall through to the generic `unknown` error.

    It is a sibling of CannotConnect, not a subclass, so the config flow's
    `except CannotConnect` missed it: the user saw `unknown` and the log got a
    traceback for an ordinary, expected condition.
    """
    from unittest.mock import AsyncMock, patch

    from homeassistant import config_entries, data_entry_flow

    from custom_components.munskankarna.const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.munskankarna.config_flow.async_validate_credentials",
        new=AsyncMock(side_effect=RateLimited("slow down", retry_after=60)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
