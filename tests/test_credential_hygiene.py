"""Credentials must not escape into logs, states, attributes or diagnostics.

The password is posted to the login form on every cycle that uses one, so the
interesting question is whether it can surface anywhere a user or a support
log would see it.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from homeassistant.core import HomeAssistant

from custom_components.munskankarna.api import (
    CannotConnect,
    InvalidAuth,
    MunskankarnaClient,
)
from custom_components.munskankarna.const import (
    CONF_BASE_URL,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

SECRET = "Sup3rS3cret-Pa55phrase"  # noqa: S105 - a canary, not a real credential
USER = "member@example.com"

LOGIN_PAGE = (
    '<html><form class="js-login">'
    '<input name="__RequestVerificationToken" value="T"/>'
    '<input name="ufprt" value="U"/>'
    '<input name="Password" type="password"/></form></html>'
)


@respx.mock
async def test_failed_login_never_logs_the_password(caplog) -> None:
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))

    with caplog.at_level(logging.DEBUG):
        async with MunskankarnaClient(DEFAULT_BASE_URL, USER, SECRET) as client:
            with pytest.raises(InvalidAuth):
                await client.async_login()

    assert SECRET not in caplog.text
    assert SECRET not in str(caplog.records)


@respx.mock
async def test_transport_failure_never_logs_the_password(caplog) -> None:
    """An httpx error carries the request; it must not carry the body."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(side_effect=httpx.ConnectError("boom"))

    with caplog.at_level(logging.DEBUG):
        async with MunskankarnaClient(DEFAULT_BASE_URL, USER, SECRET) as client:
            with pytest.raises(CannotConnect):
                await client.async_login()

    assert SECRET not in caplog.text


@respx.mock
async def test_exception_messages_do_not_carry_credentials() -> None:
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))

    async with MunskankarnaClient(DEFAULT_BASE_URL, USER, SECRET) as client:
        with pytest.raises(InvalidAuth) as excinfo:
            await client.async_login()

    assert SECRET not in str(excinfo.value)
    assert SECRET not in repr(excinfo.value)


@respx.mock
async def test_the_client_does_not_expose_credentials_via_repr() -> None:
    """A client rendered in a traceback frame must not print the password."""
    async with MunskankarnaClient(DEFAULT_BASE_URL, USER, SECRET) as client:
        assert SECRET not in repr(client)
        assert SECRET not in str(client)
        # It is held privately for the login POST, and nothing renders it.
        assert client.has_credentials is True


async def _setup_with_credentials(hass: HomeAssistant):
    entry = create_entry(
        hass,
        data={CONF_BASE_URL: DEFAULT_BASE_URL, CONF_USERNAME: USER, CONF_PASSWORD: SECRET},
        options={CONF_KINDS: [KIND_TILLFALLIGT]},
    )

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
        patch.object(MunskankarnaClient, "async_login", new=AsyncMock(return_value=True)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_credentials_never_appear_in_any_entity_state_or_attribute(
    hass: HomeAssistant,
) -> None:
    """A state dump is the most commonly shared artefact in a bug report."""
    await _setup_with_credentials(hass)

    dump = json.dumps(
        [
            {"entity_id": s.entity_id, "state": s.state, "attributes": dict(s.attributes)}
            for s in hass.states.async_all()
        ],
        default=str,
    )
    assert SECRET not in dump
    assert USER not in dump


async def test_credentials_never_appear_in_diagnostics(hass: HomeAssistant) -> None:
    from custom_components.munskankarna.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await _setup_with_credentials(hass)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    dump = json.dumps(diagnostics, default=str)

    assert SECRET not in dump
    assert USER not in dump
    assert "**REDACTED**" in dump


async def test_setup_logs_do_not_contain_credentials(
    hass: HomeAssistant, caplog
) -> None:
    with caplog.at_level(logging.DEBUG):
        await _setup_with_credentials(hass)
    assert SECRET not in caplog.text
