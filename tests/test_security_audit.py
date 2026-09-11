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


# ---------------------------------------------------------------------------
# Markdown injection
#
# Every text field is interpolated into a Lovelace markdown card, inside a
# table cell and often inside a link label. Markdown metacharacters in scraped
# text can break out of both.
# ---------------------------------------------------------------------------

MARKDOWN_PAYLOADS = [
    # Close the link label early and open an attacker-controlled link.
    "Vin](javascript:alert(1))[x",
    # Inject extra table cells / columns.
    "Vin | 0 | [pwn](https://evil.example) |",
    # Raw HTML, in case the card ever renders it.
    "Vin<img src=x onerror=alert(1)>",
    # Break the table structure entirely.
    "Vin\n\n# Injected heading\n\n| a | b |",
    # Reference-style link definition.
    "Vin][evil]: https://evil.example",
]


def _markdown_cell(name: str) -> str:
    """Reproduce how the shipped dashboard interpolates a name."""
    return f"| **15** | [{name}](https://example/x) | 199 kr |"


@pytest.mark.parametrize("payload", MARKDOWN_PAYLOADS)
def test_scraped_text_cannot_break_out_of_a_markdown_cell(payload: str) -> None:
    """Names must be inert once placed in a markdown table cell."""
    from custom_components.munskankarna.sensor import wine_summary

    wine = _first_wine(_card(name=payload))
    name = wine_summary(wine)["name"]
    cell = _markdown_cell(name)

    # The cell must contain exactly the three delimiters it was built with.
    assert cell.count("|") == 4, f"table structure broken: {cell!r}"
    # No second link may be introduced inside the label.
    assert cell.count("](") == 1, f"link syntax escaped: {cell!r}"
    assert "\n" not in name, "a newline would end the table row"
    assert "<" not in name and ">" not in name, f"raw HTML survived: {name!r}"


def test_escaping_preserves_ordinary_names() -> None:
    """Real wine names must survive unchanged."""
    from custom_components.munskankarna.sensor import wine_summary

    for name in (
        "Brut Nature Gran Reserva",
        "Château Tour Grand Faurie",
        "Côté Mas Brut Blanc de Blancs",
        "Domaine Amélie Guillot",
        "Spätburgunder",
    ):
        wine = _first_wine(_card(name=f"{name} 2020"))
        assert wine_summary(wine)["name"] == name


def test_tasting_note_and_producer_are_escaped_too() -> None:
    """Every free-text field reaches the card, not just the name."""
    from custom_components.munskankarna.sensor import wine_summary

    html = """
    <ul id="wine-bottles-list"><li class="medium-3 groupedlist">
      <div class="c-wine-info">
        <div class="wine-points">15</div>
        <div class="c-wine-info__price">199:-</div>
        <div class="c-wine-info__headings"><h3><a href="/sv/vinlocus/a/b">
          <span>Vin 2020</span></a></h3></div>
        <div class="c-wine-info__producer"><span>Prod | ](javascript:alert(1))[</span></div>
      </div>
    </li></ul>
    """
    producer = wine_summary(_first_wine(html))["producer"]
    assert "|" not in producer or producer.count("\\|") == producer.count("|")
    assert "](" not in producer, f"link syntax survived in producer: {producer!r}"
