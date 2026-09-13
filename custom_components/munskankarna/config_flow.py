"""Config and options flow for the Munskänkarna integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.ssl import get_default_context

from .api import CannotConnect, InvalidAuth, RateLimited, async_validate_credentials
from .const import (
    ALL_KINDS,
    CONF_BASE_URL,
    CONF_HISTORY_COUNT,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TOP_COUNT,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_HISTORY_COUNT,
    DEFAULT_KINDS,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOP_COUNT,
    DOMAIN,
    KIND_LABELS,
    MAX_HISTORY_COUNT,
    MAX_SCAN_INTERVAL_HOURS,
    MAX_TOP_COUNT,
    MIN_SCAN_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)

# Credentials are optional: the Vinlocus review pages are public, and the
# integration is fully functional anonymously. They are offered for members who
# want access to anything gated behind a login.
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Optional(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
        ),
        vol.Optional(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


def _kind_options() -> list[SelectOptionDict]:
    return [SelectOptionDict(value=kind, label=KIND_LABELS[kind]) for kind in ALL_KINDS]


class MunskankarnaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial UI setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the base URL and optional member credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = (user_input.get(CONF_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")

            # One entry per site; re-running the flow reconfigures rather than
            # creating a duplicate set of sensors.
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()

            username = user_input.get(CONF_USERNAME) or None
            password = user_input.get(CONF_PASSWORD) or None

            try:
                # Hand over Home Assistant's cached SSL context: letting httpx
                # build its own would read the CA bundle from disk on the event
                # loop, which Home Assistant reports as a blocking call.
                info = await async_validate_credentials(
                    base_url,
                    username,
                    password,
                    verify=get_default_context(),
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except (CannotConnect, RateLimited):
                # RateLimited is a sibling of CannotConnect, not a subclass;
                # without it here an ordinary 429 showed the user "unknown"
                # and logged a traceback.
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - never leak a traceback into the UI
                _LOGGER.exception("Unexpected error validating Munskänkarna setup")
                errors["base"] = "unknown"
            else:
                _LOGGER.debug(
                    "Munskänkarna validated: %s releases, authenticated=%s",
                    info["release_count"],
                    info["authenticated"],
                )
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after Munskänkarna rejected a stored login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect fresh credentials for an existing entry."""
        # `_get_reauth_entry()` only exists on newer cores; resolving through
        # the flow context works across the whole supported range.
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:  # pragma: no cover - entry removed mid-flow
            return self.async_abort(reason="reauth_failed")

        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL
            try:
                await async_validate_credentials(
                    base_url,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    verify=get_default_context(),
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except (CannotConnect, RateLimited):
                # RateLimited is a sibling of CannotConnect, not a subclass;
                # without it here an ordinary 429 showed the user "unknown"
                # and logged a traceback.
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - never leak a traceback into the UI
                _LOGGER.exception("Unexpected error during Munskänkarna reauthentication")
                errors["base"] = "unknown"
            else:
                # Merge rather than replace, so the base URL survives.
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **user_input}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"username": entry.data.get(CONF_USERNAME) or ""},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MunskankarnaOptionsFlow:
        """Return the options flow handler."""
        return MunskankarnaOptionsFlow()


class MunskankarnaOptionsFlow(OptionsFlow):
    """Adjust polling cadence, attribute size and which tastings are tracked."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and persist the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL_HOURS,
                    default=options.get(
                        CONF_SCAN_INTERVAL_HOURS,
                        int(DEFAULT_SCAN_INTERVAL.total_seconds() // 3600),
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL_HOURS, max=MAX_SCAN_INTERVAL_HOURS),
                ),
                vol.Optional(
                    CONF_TOP_COUNT,
                    default=options.get(CONF_TOP_COUNT, DEFAULT_TOP_COUNT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_TOP_COUNT)),
                vol.Optional(
                    CONF_HISTORY_COUNT,
                    default=options.get(CONF_HISTORY_COUNT, DEFAULT_HISTORY_COUNT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_HISTORY_COUNT)),
                vol.Optional(
                    CONF_KINDS,
                    default=list(options.get(CONF_KINDS, DEFAULT_KINDS)),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=_kind_options(),
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
