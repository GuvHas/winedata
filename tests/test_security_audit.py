"""Security audit: untrusted upstream content reaching Home Assistant.

Everything this integration shows comes from scraped HTML. If Munskänkarna
were compromised — or simply changed — that markup must never become an
executable link, break out of a Lovelace markdown table, or point a user at
the wrong Systembolaget product.
"""

from __future__ import annotations

import pytest

from custom_components.munskankarna.parser import parse_release_page

DANGEROUS_HREFS = [
    "javascript:alert(document.cookie)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
]


def _card(href: str = "/sv/vinlocus/a/b", name: str = "Ett Vin 2020") -> str:
    """One minimal wine card with a controllable link and name."""
    return f"""
    <ul id="wine-bottles-list"><li class="medium-3 groupedlist">
      <div class="c-wine-info">
        <div class="wine-points">15</div>
        <div class="c-wine-info__price">199:-</div>
        <div class="c-wine-info__headings"><h3>
          <a href="{href}"><span>{name}</span></a></h3></div>
      </div>
    </li></ul>
    """


def _first_wine(html: str) -> dict:
    result = parse_release_page(html, release_id="r", title="R")
    assert result["wines"], "fixture should yield one wine"
    return result["wines"][0]


@pytest.mark.parametrize("href", DANGEROUS_HREFS)
def test_dangerous_url_schemes_are_rejected(href: str) -> None:
    """A scraped href must never survive as a non-http(s) URL.

    `urljoin` passes `javascript:` and `data:` URLs through untouched, and the
    dashboard renders `[{{ w.name }}]({{ w.url or w.review_url }})` — so an
    unfiltered href becomes a clickable script link in a markdown card.
    """
    wine = _first_wine(_card(href=href))
    assert wine["review_url"] is None, (
        f"{href!r} survived as review_url={wine['review_url']!r}"
    )


def test_safe_relative_and_absolute_urls_still_work() -> None:
    """The filter must not break ordinary links."""
    assert _first_wine(_card(href="/sv/vinlocus/a/b"))["review_url"] == (
        "https://www.munskankarna.se/sv/vinlocus/a/b"
    )
    assert _first_wine(_card(href="https://www.munskankarna.se/x"))["review_url"] == (
        "https://www.munskankarna.se/x"
    )


def test_offsite_links_are_rejected() -> None:
    """An href pointing somewhere else entirely is not a Munskänkarna review."""
    assert _first_wine(_card(href="https://evil.example/phish"))["review_url"] is None
