"""Phase 2b — config flow tests (written before `config_flow.py`)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
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


async def test_validation_supplies_an_ssl_context(hass: HomeAssistant, mock_setup_entry) -> None:
    """Setup validation must not let httpx build an SSL context on the loop."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    validate = AsyncMock(return_value={"authenticated": False, "release_count": 34})
    with patch(VALIDATE, new=validate):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )
        await hass.async_block_till_done()

    _, kwargs = validate.await_args
    assert kwargs.get("verify") is not None, (
        "the config flow did not pass an SSL context; httpx would build one "
        "on the event loop and Home Assistant would flag a blocking call"
    )


async def test_config_flow_builds_no_ssl_context_on_the_loop(
    hass: HomeAssistant, monkeypatch
) -> None:
    """End to end: walking the flow must never call ssl.create_default_context here."""
    import ssl as ssl_module
    import threading

    import httpx
    import respx

    from custom_components.munskankarna import api as api_module

    monkeypatch.setattr(api_module, "_DEFAULT_SSL_CONTEXT", None, raising=False)
    loop_thread = threading.current_thread()
    offenders: list[str] = []
    real = ssl_module.create_default_context

    def recording(*args, **kwargs):
        if threading.current_thread() is loop_thread:
            offenders.append("create_default_context on the event loop")
        return real(*args, **kwargs)

    monkeypatch.setattr(ssl_module, "create_default_context", recording)

    index_html = (
        "<html><h3>Hitlista</h3>"
        '<a href="/sv/vinlocus/hitlista-3-september-2026">Hitlista 3 september 2026</a></html>'
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
            return_value=httpx.Response(200, text=index_html)
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: DEFAULT_BASE_URL}
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# Reauthentication
#
# The coordinator raises ConfigEntryAuthFailed when a login is rejected, which
# makes Home Assistant start a reauth flow. Without these steps that flow dies
# with "Handler doesn't support step reauth" and the user gets no way to fix
# their password.
# ---------------------------------------------------------------------------


async def test_rejected_login_starts_a_reauth_form(hass: HomeAssistant) -> None:
    from custom_components.munskankarna.api import InvalidAuth, MunskankarnaClient
    from custom_components.munskankarna.const import CONF_KINDS, KIND_TILLFALLIGT
    from tests.helpers import create_entry

    entry = create_entry(
        hass,
        data={
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_USERNAME: "member",
            CONF_PASSWORD: "stale",
        },
        options={CONF_KINDS: [KIND_TILLFALLIGT]},
    )
    with patch.object(
        MunskankarnaClient, "async_login", new=AsyncMock(side_effect=InvalidAuth)
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth"
    ]
    assert flows, "a rejected login should start a reauth flow"
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_reauth_accepts_corrected_credentials(hass: HomeAssistant) -> None:
    from tests.helpers import create_entry

    entry = create_entry(
        hass,
        data={
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_USERNAME: "member",
            CONF_PASSWORD: "stale",
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with (
        patch(VALIDATE, new=AsyncMock(return_value={"authenticated": True, "release_count": 34})),
        patch("custom_components.munskankarna.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "member", CONF_PASSWORD: "fresh"}
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "fresh"
    # The base URL must survive a reauth that only changes credentials.
    assert entry.data[CONF_BASE_URL] == DEFAULT_BASE_URL


async def test_reauth_rejects_still_bad_credentials(hass: HomeAssistant) -> None:
    from tests.helpers import create_entry

    entry = create_entry(
        hass,
        data={
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_USERNAME: "member",
            CONF_PASSWORD: "stale",
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    with patch(VALIDATE, new=AsyncMock(side_effect=InvalidAuth)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "member", CONF_PASSWORD: "also-wrong"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "stale"


async def test_options_flow_configures_the_retention_depth(hass: HomeAssistant) -> None:
    """How many releases to keep is a user choice, so it belongs in Options."""
    from custom_components.munskankarna.const import (
        CONF_HISTORY_COUNT,
        DEFAULT_HISTORY_COUNT,
        MAX_HISTORY_COUNT,
    )

    entry = create_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert CONF_HISTORY_COUNT in schema_keys, "retention depth is not configurable"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL_HOURS: 12,
            CONF_TOP_COUNT: 5,
            CONF_HISTORY_COUNT: 2,
            CONF_KINDS: [KIND_TILLFALLIGT],
        },
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HISTORY_COUNT] == 2

    # The form itself must not offer a depth the coordinator would clamp away.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        result["data_schema"]({
            CONF_SCAN_INTERVAL_HOURS: 12,
            CONF_TOP_COUNT: 5,
            CONF_HISTORY_COUNT: MAX_HISTORY_COUNT + 1,
            CONF_KINDS: [KIND_TILLFALLIGT],
        })
    assert DEFAULT_HISTORY_COUNT <= MAX_HISTORY_COUNT
