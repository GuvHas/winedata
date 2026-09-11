"""Phase 2b — config flow tests (written before `config_flow.py`)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.munskankarna.api import CannotConnect, InvalidAuth
from custom_components.munskankarna.const import (
    CONF_BASE_URL,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TOP_COUNT,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DOMAIN,
    KIND_TILLFALLIGT,
)
from tests.helpers import create_entry

VALIDATE = "custom_components.munskankarna.config_flow.async_validate_credentials"


@pytest.fixture
def mock_setup_entry():
    """Stop the flow from actually setting the integration up."""
    with patch(
        "custom_components.munskankarna.async_setup_entry", return_value=True
    ) as mocked:
        yield mocked


async def test_form_shows_and_creates_entry(hass: HomeAssistant, mock_setup_entry) -> None:
    """The happy path: anonymous setup, which is the supported default."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    with patch(VALIDATE, new=AsyncMock(return_value={"authenticated": False, "release_count": 34})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Munskänkarna"
    assert result["data"][CONF_BASE_URL] == DEFAULT_BASE_URL
    assert result["data"].get(CONF_USERNAME) is None
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_accepts_member_credentials(hass: HomeAssistant, mock_setup_entry) -> None:
    """Credentials are optional, but honoured when given."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        VALIDATE, new=AsyncMock(return_value={"authenticated": True, "release_count": 34})
    ) as validate:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BASE_URL: DEFAULT_BASE_URL,
                CONF_USERNAME: "member@example.com",
                CONF_PASSWORD: "hunter2",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == "member@example.com"
    assert result["data"][CONF_PASSWORD] == "hunter2"
    validate.assert_awaited_once()


async def test_form_invalid_auth(hass: HomeAssistant) -> None:
    """Bad credentials surface as `invalid_auth` on the password field's form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(side_effect=InvalidAuth)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BASE_URL: DEFAULT_BASE_URL, CONF_USERNAME: "u", CONF_PASSWORD: "bad"},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(side_effect=CannotConnect)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_form_unknown_error(hass: HomeAssistant) -> None:
    """An unexpected exception must not leak a traceback into the UI."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_form_recovers_after_an_error(hass: HomeAssistant, mock_setup_entry) -> None:
    """A failed attempt must leave the flow usable, not wedged."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(side_effect=CannotConnect)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )
    assert result["errors"] == {"base": "cannot_connect"}

    with patch(VALIDATE, new=AsyncMock(return_value={"authenticated": False, "release_count": 34})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )
        await hass.async_block_till_done()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY


async def test_duplicate_entry_is_rejected(hass: HomeAssistant) -> None:
    """One entry per base URL."""
    create_entry(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(return_value={"authenticated": False, "release_count": 34})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_settings(hass: HomeAssistant) -> None:
    """Polling interval, attribute size and tracked kinds are reconfigurable."""
    entry = create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL_HOURS: 12,
            CONF_TOP_COUNT: 5,
            CONF_KINDS: [KIND_TILLFALLIGT],
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SCAN_INTERVAL_HOURS] == 12
    assert result["data"][CONF_TOP_COUNT] == 5
    assert result["data"][CONF_KINDS] == [KIND_TILLFALLIGT]
