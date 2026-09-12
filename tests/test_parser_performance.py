"""Parser performance on hostile input.

These functions run on the event loop against text scraped from a site we do
not control. Quadratic backtracking there is an availability problem: Home
Assistant stalls for the duration.
"""

from __future__ import annotations

import time

import pytest

from custom_components.munskankarna.parser import (
    parse_alcohol,
    parse_price,
    parse_release_page,
    parse_score,
    parse_volume_ml,
    split_vintage,
)

#: Long runs of digits are the trigger: each start position matches the digit
#: run, fails the suffix, and backtracks.
ADVERSARIAL = ("9" * 20_000) + "x" + ("9" * 20_000)

#: Generous — the real cost should be microseconds. This catches quadratic
#: blowup without being flaky on a loaded CI runner.
BUDGET_SECONDS = 0.25


@pytest.mark.parametrize(
    "func",
    [parse_score, parse_price, parse_volume_ml, parse_alcohol],
)
def test_field_parsers_are_linear_on_hostile_input(func) -> None:
    started = time.perf_counter()
    func(ADVERSARIAL)
    elapsed = time.perf_counter() - started
    assert elapsed < BUDGET_SECONDS, (
        f"{func.__name__} took {elapsed:.2f}s on a 40k input — backtracking"
    )


def test_split_vintage_is_linear_on_hostile_input() -> None:
    started = time.perf_counter()
    split_vintage(("a " * 10_000) + "2020")
    elapsed = time.perf_counter() - started
    assert elapsed < BUDGET_SECONDS, f"split_vintage took {elapsed:.2f}s"


def test_a_page_of_hostile_cards_parses_promptly() -> None:
    """End to end: a page engineered to be slow must still parse quickly."""
    card = f"""
    <li class="medium-3 groupedlist"><div class="c-wine-info">
      <div class="wine-points">{ADVERSARIAL[:5000]}</div>
      <div class="c-wine-info__price">{ADVERSARIAL[:5000]}</div>
      <div class="c-wine-info__headings"><h3><a href="/sv/vinlocus/a/b">
        <span>{"Vin " * 2000}2020</span></a></h3></div>
      <div class="c-wine-info__volumeAlocoholValues">
        <span>{ADVERSARIAL[:5000]}</span></div>
    </div></li>
    """
    html = f"<ul id='wine-bottles-list'>{card * 5}</ul>"

    started = time.perf_counter()
    parse_release_page(html, release_id="r", title="R")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"parsing a hostile page took {elapsed:.2f}s"


def test_bounding_does_not_break_real_values() -> None:
    """The guard must not change any legitimate parse."""
    assert parse_score("14,5") == 14.5
    assert parse_price("1 250 kr") == 1250.0
    assert parse_volume_ml("75 cl") == 750
    assert parse_alcohol("13,5 %") == 13.5
    assert split_vintage("Brut Nature Gran Reserva 2017") == ("Brut Nature Gran Reserva", 2017)
    assert split_vintage("Saint-Saveur Cuvée Frédéric T Brut NV")[1] is None
