"""Scalar sensors, so the recorder can graph what the attributes cannot.

Everything interesting about a release lives in attributes, and attributes are
not recorder time-series: `apexcharts-card` and the built-in history graph plot
*state* history, so today there is nothing to plot. These three sensors are
small numbers that change when a release lands, which is exactly what a trend
chart needs — and they need no HACS card to be useful.

The rule they must not break: a figure over nothing is unknown, never zero. A
zero would draw a real trough on the chart for a week when the data simply had
not arrived.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from custom_components.munskankarna.const import (
    CONF_KINDS,
    KIND_TILLFALLIGT,
    VALUE_FYND,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

INDEX = [build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11")]

MEDIAN_SCORE = "sensor.munskankarna_median_score"
MEDIAN_PRICE = "sensor.munskankarna_median_price_per_litre"
FYND_SHARE = "sensor.munskankarna_fynd_share"


async def _setup(hass: HomeAssistant, wines: list[dict]):
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT]})

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(rid, KIND_TILLFALLIGT, "2026-09-11",
                                     wine_count=len(wines)),
            "wines": list(wines),
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=list(INDEX)),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _wine(name: str, score: float | None, price: float | None, value: str) -> dict:
    return build_wine(name, name, score=score, price=price, value=value)


async def test_the_median_score_is_the_middle_of_an_odd_sample(
    hass: HomeAssistant,
) -> None:
    await _setup(hass, [
        _wine("a", 13.0, 100.0, "prisvart"),
        _wine("b", 16.0, 100.0, "prisvart"),
        _wine("c", 19.0, 100.0, "prisvart"),
    ])

    state = hass.states.get(MEDIAN_SCORE)
    assert state is not None, "the median score sensor was never created"
    assert float(state.state) == 16.0
    assert state.attributes["sample_size"] == 3
    # Recorded as a measurement, or long-term statistics never accumulate.
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT


async def test_an_even_sample_averages_the_two_middles(hass: HomeAssistant) -> None:
    await _setup(hass, [
        _wine("a", 14.0, 100.0, "prisvart"),
        _wine("b", 15.0, 100.0, "prisvart"),
        _wine("c", 16.0, 100.0, "prisvart"),
        _wine("d", 17.0, 100.0, "prisvart"),
    ])

    assert float(hass.states.get(MEDIAN_SCORE).state) == 15.5


async def test_an_unrated_wine_is_left_out_of_the_score_but_not_the_price(
    hass: HomeAssistant,
) -> None:
    """The parser allows either field to be missing, independently."""
    await _setup(hass, [
        _wine("a", 14.0, 75.0, "prisvart"),     # 100 kr/l
        _wine("b", None, 150.0, "prisvart"),    # 200 kr/l, no score
        _wine("c", 18.0, 225.0, "prisvart"),    # 300 kr/l
    ])

    score = hass.states.get(MEDIAN_SCORE)
    assert float(score.state) == 16.0, "an unrated wine reached the score median"
    assert score.attributes["sample_size"] == 2

    price = hass.states.get(MEDIAN_PRICE)
    assert float(price.state) == 200.0, "the unrated wine was dropped from the price too"
    assert price.attributes["sample_size"] == 3


async def test_the_fynd_share_is_a_percentage_of_the_release(
    hass: HomeAssistant,
) -> None:
    await _setup(hass, [
        _wine("a", 14.0, 100.0, VALUE_FYND),
        _wine("b", 15.0, 100.0, VALUE_FYND),
        _wine("c", 16.0, 100.0, "prisvart"),
        _wine("d", 17.0, 100.0, "ej-prisvart"),
    ])

    state = hass.states.get(FYND_SHARE)
    assert float(state.state) == 50.0
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["sample_size"] == 4


async def test_a_figure_over_nothing_is_unknown_not_zero(hass: HomeAssistant) -> None:
    """Zero would draw a real trough on the chart for a week with no data."""
    await _setup(hass, [_wine("a", None, None, "prisvart")])

    for entity_id in (MEDIAN_SCORE, MEDIAN_PRICE):
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNKNOWN, f"{entity_id} published {state.state}"
        assert state.attributes["sample_size"] == 0

    # A wine with no score is still a wine, so the share is a real 0%.
    assert float(hass.states.get(FYND_SHARE).state) == 0.0


async def test_the_trend_sensors_stay_cheap_to_record(hass: HomeAssistant) -> None:
    """Their whole point is being recorder-friendly; a payload would undo that."""
    await _setup(hass, [_wine("a", 15.0, 100.0, VALUE_FYND)])

    for entity_id in (MEDIAN_SCORE, MEDIAN_PRICE, FYND_SHARE):
        attributes = dict(hass.states.get(entity_id).attributes)
        for managed in ("friendly_name", "unit_of_measurement", "state_class",
                        "icon", "device_class"):
            attributes.pop(managed, None)
        assert set(attributes) == {"sample_size"}, (
            f"{entity_id} carries more than a sample size: {sorted(attributes)}"
        )
