"""Diagnostics must never leak credentials."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.munskankarna.api import MunskankarnaClient
from custom_components.munskankarna.const import (
    CONF_BASE_URL,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from custom_components.munskankarna.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.helpers import build_release, build_wine, create_entry

RELEASE_ID = "tillfalligt-sortiment-11-september-2026"


async def test_diagnostics_redacts_credentials(hass: HomeAssistant) -> None:
    entry = create_entry(
        hass,
        data={
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_USERNAME: "member@example.com",
            CONF_PASSWORD: "hunter2",
        },
        options={CONF_KINDS: [KIND_TILLFALLIGT]},
    )

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [build_wine(release_id, "Toppvinet", 17.0)],
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
        # Credentials are configured here, so the cycle's single login would
        # otherwise reach the network.
        patch.object(MunskankarnaClient, "async_login", new=AsyncMock(return_value=True)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    serialised = json.dumps(diagnostics, default=str)
    assert "hunter2" not in serialised
    assert "member@example.com" not in serialised
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["snapshot"]["releases"]
