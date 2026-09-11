"""Release rollover: does a sensor follow next week's tasting on its own?

This is the behaviour the integration exists for - Munskänkarna publishes a new
Tillfälligt sortiment most weeks, and the sensor must track it without anyone
reconfiguring anything.
"""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.munskankarna.const import CONF_KINDS, DOMAIN, KIND_TILLFALLIGT
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

WEEK1 = "tillfalligt-sortiment-11-september-2026"
WEEK2 = "tillfalligt-sortiment-18-september-2026"   # published a week later

async def test_sensor_rolls_over_to_next_weeks_release(hass: HomeAssistant) -> None:
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    index = [build_release(WEEK1, KIND_TILLFALLIGT, "2026-09-11")]

    async def fake_index(self):
        return list(index)

    async def fake_release(self, release_id, title):
        return {"release": build_release(release_id, KIND_TILLFALLIGT,
                    "2026-09-18" if release_id == WEEK2 else "2026-09-11", wine_count=1),
                "wines": [build_wine(release_id, f"Wine-{release_id[-12:]}", 15.0)],
                "warnings": []}

    with (patch.object(MunskankarnaCoordinator, "_async_fetch_index", new=fake_index),
          patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_release)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        eid = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_release_{KIND_TILLFALLIGT}")
        before = hass.states.get(eid)
        assert before.attributes["release_id"] == WEEK1

        # Munskänkarna publishes next week's tasting; it appears on the index.
        index.insert(0, build_release(WEEK2, KIND_TILLFALLIGT, "2026-09-18"))

        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        after = hass.states.get(eid)
        assert after.attributes["release_id"] == WEEK2, "sensor did not roll over"
        assert after.attributes["release_date"] == "2026-09-18"
        # Crucially the entity_id is unchanged, so dashboards keep working.
        assert after.entity_id == before.entity_id


async def test_undated_release_does_not_displace_a_dated_one(hass: HomeAssistant) -> None:
    """A title we cannot date must not win over a real dated release."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})
    index = [build_release(WEEK1, KIND_TILLFALLIGT, "2026-09-11"),
             build_release("tillfalligt-sortiment-hostspecial", KIND_TILLFALLIGT, None)]

    async def fake_index(self): return list(index)
    async def fake_release(self, release_id, title):
        return {"release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
                "wines": [build_wine(release_id, "X", 15.0)], "warnings": []}

    with (patch.object(MunskankarnaCoordinator, "_async_fetch_index", new=fake_index),
          patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_release)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        chosen = coordinator.release_for(KIND_TILLFALLIGT)["release"]["id"]
        assert chosen == WEEK1
