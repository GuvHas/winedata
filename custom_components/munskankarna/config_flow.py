"""Config and options flow for the Munskänkarna integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback

try:  # Home Assistant >= 2024.4
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover - older cores
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, InvalidAuth, async_validate_credentials
from .const import (
    ALL_KINDS,
    CONF_BASE_URL,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TOP_COUNT,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_KINDS,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOP_COUNT,
    DOMAIN,
    KIND_LABELS,
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
                info = await async_validate_credentials(base_url, username, password)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MunskankarnaOptionsFlow:
        """Return the options flow handler."""
        return MunskankarnaOptionsFlow(config_entry)


class MunskankarnaOptionsFlow(OptionsFlow):
    """Adjust polling cadence, attribute size and which tastings are tracked."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Keep the entry on a private attribute.

        Home Assistant >= 2024.11 injects `self.config_entry` and deprecates
        assigning it, while older cores never set it at all. Storing it under a
        private name works on every version and trips no deprecation warning.
        """
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and persist the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options
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
