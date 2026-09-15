"""The repair flow behind the mismatched-entity-id issue.

The issue exists because the migration cannot prove who chose an id. This
flow resolves that the only way that is actually sound: it asks, lists what
it would rename, and renames on confirmation.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .migrate import async_plan, async_rename_on_request


class MismatchedIdsRepairFlow(RepairsFlow):
    """Confirm, then rename every entity whose canonical id is free."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            # The integration was removed while the issue stood. Finishing the
            # flow is what withdraws it, and there is nothing left to rename.
            return self.async_create_entry(data={})

        if user_input is not None:
            async_rename_on_request(self.hass, entry)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "entities": "\n".join(
                    f"- {mismatch}" for mismatch in async_plan(self.hass, entry)
                )
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Build the flow for an issue raised by `migrate.async_report`."""
    entry_id = str((data or {}).get("entry_id", ""))
    return MismatchedIdsRepairFlow(entry_id)
