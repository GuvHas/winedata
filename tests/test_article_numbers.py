"""Systembolaget article number handling.

A wrong article number is worse than a missing one: the link resolves, just to
somebody else's wine. These pin the edge cases the source site actually emits.
"""

from __future__ import annotations

import pytest

from custom_components.munskankarna.parser import (
    build_product_url,
    normalize_article_number,
    parse_release_page,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9049001", "9049001"),
        (" 258701 ", "258701"),
        ("nr 9049001", "9049001"),
        ("Systembolaget: 9049001", "9049001"),
    ],
)
def test_plain_article_numbers(raw: str, expected: str) -> None:
    assert normalize_article_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # Digits from a package suffix must never be welded onto the number.
        "9049001 (2-pack)",
        "9049001 x6",
        "9049001, 3-pack",
        "9049001 / 12",
    ],
)
def test_package_suffixes_do_not_corrupt_the_number(raw: str) -> None:
    """Stripping every non-digit silently produced a different product.

    "9049001 (2-pack)" became "90490012", a valid-looking seven-plus-digit
    number pointing at an unrelated product page.
    """
    assert normalize_article_number(raw) == "9049001"


@pytest.mark.parametrize("raw", ["0049001", "0258701", "0000258701"])
def test_leading_zeros_are_stripped(raw: str) -> None:
    """Systembolaget URLs carry no leading zeros; keeping them 404s."""
    normalized = normalize_article_number(raw)
    assert normalized is not None
    assert not normalized.startswith("0"), f"leading zero survived: {normalized!r}"


@pytest.mark.parametrize(
    "raw",
    [
        "", "   ", None, "ab", "0", "00000", "-", "12",
        # "007" normalises to "7", too short to be an article number.
        "007",
    ],
)
def test_implausible_values_are_rejected(raw: str | None) -> None:
    """Better no link than a link to the wrong bottle."""
    assert normalize_article_number(raw) is None


def test_rejected_numbers_produce_no_url() -> None:
    assert build_product_url("00000") is None
    assert build_product_url(None) is None
    assert build_product_url("nonsense") is None


def test_a_year_in_a_href_is_not_mistaken_for_an_article_number() -> None:
    """The href fallback must not scrape a vintage out of a URL path."""
    html = """
    <ul id="wine-bottles-list"><li class="medium-3 groupedlist">
      <div class="c-wine-info">
        <div class="wine-points">15</div>
        <div class="c-wine-info__price">199:-</div>
        <div class="c-wine-info__headings"><h3><a href="/sv/vinlocus/a/b">
          <span>Ett Vin 2020</span></a></h3></div>
        <a href="https://www.systembolaget.se/sortiment/2020">Systembolaget</a>
      </div>
    </li></ul>
    """
    wine = parse_release_page(html, release_id="r", title="R")["wines"][0]
    assert wine["article_number"] is None, (
        f"scraped {wine['article_number']!r} out of a non-product URL"
    )


def test_real_systembolaget_link_still_parses() -> None:
    html = """
    <ul id="wine-bottles-list"><li class="medium-3 groupedlist">
      <div class="c-wine-info">
        <div class="wine-points">15</div>
        <div class="c-wine-info__price">199:-</div>
        <div class="c-wine-info__headings"><h3><a href="/sv/vinlocus/a/b">
          <span>Ett Vin 2020</span></a></h3></div>
        <a href="https://www.systembolaget.se/9049001"><span>9049001</span></a>
      </div>
    </li></ul>
    """
    wine = parse_release_page(html, release_id="r", title="R")["wines"][0]
    assert wine["article_number"] == "9049001"
    assert wine["product_url"] == "https://www.systembolaget.se/produkt/vin/9049001/"
