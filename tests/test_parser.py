"""Phase 1 — parser tests.

Written before `parser.py` exists. These run against HTML captured from the
live site (the same fixtures the TypeScript parser is held to), so an upstream
markup change fails here rather than silently producing an empty sensor.
"""

from __future__ import annotations

import pytest

from custom_components.munskankarna.parser import (
    DEFAULT_BASE_URL,
    build_product_url,
    build_search_url,
    normalize_article_number,
    parse_alcohol,
    parse_assortment_kind,
    parse_color,
    parse_price,
    parse_release_date,
    parse_release_index,
    parse_release_page,
    parse_score,
    parse_value_rating,
    parse_volume_ml,
    parse_wine_detail,
    price_per_litre,
    score_band,
    slugify,
    split_vintage,
)

# --------------------------------------------------------------------------
# Field-level normalisers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("14,5", 14.5), ("15", 15.0), (" 17,0 ", 17.0), ("", None), ("abc", None), ("92", None)],
)
def test_parse_score(raw: str, expected: float | None) -> None:
    """Scores use a Swedish decimal comma and live on a 0-20 scale."""
    assert parse_score(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("199:-", 199.0), ("1 250 kr", 1250.0), ("159:50", 159.5), ("37:-", 37.0), ("", None)],
)
def test_parse_price(raw: str, expected: float | None) -> None:
    assert parse_price(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("75 cl", 750), ("750 ml", 750), ("1,5 l", 1500), ("62 cl", 620), ("okänd", None)],
)
def test_parse_volume_ml(raw: str, expected: int | None) -> None:
    assert parse_volume_ml(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12% vol.", 12.0), ("13,5 %", 13.5), ("14 %", 14.0), ("inget", None)],
)
def test_parse_alcohol(raw: str, expected: float | None) -> None:
    assert parse_alcohol(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Brut Nature Gran Reserva 2017", ("Brut Nature Gran Reserva", 2017)),
        # Non-vintage bottlings keep their full name.
        ("Saint-Saveur Cuvée Frédéric T Brut NV", ("Saint-Saveur Cuvée Frédéric T Brut NV", None)),
        # A trailing number that is not a plausible year must not be consumed.
        ("Cuvée 21", ("Cuvée 21", None)),
        ("2020", ("2020", None)),
    ],
)
def test_split_vintage(raw: str, expected: tuple[str, int | None]) -> None:
    assert split_vintage(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rött vin", "red"),
        ("Vitt vin", "white"),
        ("Mousserande vin", "sparkling"),
        ("Rosévin", "rose"),
        ("Specialvin", "fortified"),
        ("", "other"),
    ],
)
def test_parse_color(raw: str, expected: str) -> None:
    assert parse_color(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fynd i sin prisklass", "fynd"),
        ("Mer än prisvärt", "mer-an-prisvart"),
        ("Prisvärt", "prisvart"),
        # "Ej prisvärt" contains "prisvärt": the negative form must win.
        ("Ej prisvärt", "ej-prisvart"),
        ("", None),
    ],
)
def test_parse_value_rating(raw: str, expected: str | None) -> None:
    assert parse_value_rating(raw) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (18, "exceptionellt"),
        (17.5, "hogklassigt"),
        (15, "hogklassigt"),
        (14.5, "bra"),
        (12, "bra"),
        (11.5, "medelbra"),
        (8.5, "enkelt"),
        (None, None),
    ],
)
def test_score_band(score: float | None, expected: str | None) -> None:
    """Munskänkarna's published bands for the 20-point scale."""
    assert score_band(score) == expected


def test_price_per_litre() -> None:
    assert price_per_litre(199, 750) == 265.33
    assert price_per_litre(599, 620) == 966.13
    assert price_per_litre(None, 750) is None
    assert price_per_litre(199, None) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Tillfälligt sortiment 11 september 2026", "2026-09-11"),
        ("Ordervaror oktober 2026", "2026-10-01"),
        ("Hitlista 3 september 2026", "2026-09-03"),
        ("Temaprovning champagne", None),
        # Impossible dates are rejected rather than rolled over.
        ("31 februari 2026", None),
    ],
)
def test_parse_release_date(title: str, expected: str | None) -> None:
    assert parse_release_date(title) == expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("tillfalligt-sortiment-11-september-2026", "tillfalligt-sortiment"),
        ("fast-sortiment-nya-viner-1-september-2026", "fast-sortiment"),
        ("hitlista-3-september-2026", "hitlistan"),
        ("webbviner-oktober-2026", "webbviner"),
        # Upstream really does publish this misspelling.
        ("odervaror-oktober-2026", "bestallningssortimentet"),
        ("nagot-helt-annat", "ovrigt"),
    ],
)
def test_parse_assortment_kind(slug: str, expected: str) -> None:
    assert parse_assortment_kind(slug)[0] == expected


def test_slugify_folds_swedish_characters() -> None:
    assert slugify("Tillfälligt sortiment") == "tillfalligt-sortiment"
    assert slugify("Château Mas Tinell") == "chateau-mas-tinell"
    assert slugify("Rosé & Co.") == "rose-co"


# --------------------------------------------------------------------------
# Systembolaget linking
# --------------------------------------------------------------------------


def test_normalize_article_number() -> None:
    assert normalize_article_number("9049001") == "9049001"
    assert normalize_article_number(" 258701 ") == "258701"
    assert normalize_article_number("ab") is None
    assert normalize_article_number("") is None
    assert normalize_article_number(None) is None


def test_build_product_url() -> None:
    assert build_product_url("9049001") == "https://www.systembolaget.se/produkt/vin/9049001/"
    # No article number means no product link - never a broken URL.
    assert build_product_url(None) is None
    assert build_product_url("") is None


def test_build_search_url_is_the_fallback() -> None:
    """Wines with no article number fall back to a catalog search."""
    url = build_search_url("Brut Nature Gran Reserva Heretat Mas Tinell")
    assert url.startswith("https://www.systembolaget.se/sortiment/?q=")
    assert "Brut+Nature" in url or "Brut%20Nature" in url


# --------------------------------------------------------------------------
# Release index
# --------------------------------------------------------------------------


def test_parse_release_index(load_fixture_html) -> None:
    releases = parse_release_index(load_fixture_html("release-index.html"))
    assert len(releases) >= 20

    weekly = next(r for r in releases if r["id"] == "tillfalligt-sortiment-11-september-2026")
    assert weekly["kind"] == "tillfalligt-sortiment"
    assert weekly["date"] == "2026-09-11"
    assert weekly["url"] == (
        f"{DEFAULT_BASE_URL}/sv/vinlocus/tillfalligt-sortiment-11-september-2026"
    )

    # Facet pages are not releases.
    ids = {r["id"] for r in releases}
    assert ids.isdisjoint({"land", "druva", "importor", "provningstyp"})


def test_parse_release_index_handles_empty_html() -> None:
    assert parse_release_index("") == []
    assert parse_release_index("<html><body><p>inget</p></body></html>") == []


# --------------------------------------------------------------------------
# Release pages
# --------------------------------------------------------------------------


def test_parse_release_page_extracts_every_field(load_fixture_html) -> None:
    result = parse_release_page(
        load_fixture_html("release-tillfalligt-sortiment.html"),
        release_id="tillfalligt-sortiment-11-september-2026",
        title="Tillfälligt sortiment 11 september 2026",
    )

    # The fixture is trimmed to the first ten cards.
    assert len(result["wines"]) == 10
    assert result["release"]["date"] == "2026-09-11"
    assert "urval" in (result["release"]["summary"] or "")

    cava = next(w for w in result["wines"] if w["name"] == "Brut Nature Gran Reserva")
    assert cava["vintage"] == 2017
    assert cava["score"] == 14.0
    assert cava["score_label"] == "14"
    assert cava["band"] == "bra"
    assert cava["value_rating"] == "mer-an-prisvart"
    assert cava["typical"] is True
    assert cava["color"] == "sparkling"
    assert cava["color_label"] == "Mousserande vin"
    assert cava["producer"] == "Heretat Mas Tinell, S.L."
    assert cava["country"] == "Spanien"
    assert cava["region"] == "Navarra"
    assert cava["appellation"] == "Cava"
    assert cava["grapes"] == ["xarel-lo", "macabeo", "parellada"]
    assert cava["price_sek"] == 199.0
    assert cava["volume_ml"] == 750
    assert cava["alcohol_percent"] == 12.0
    assert cava["price_per_litre"] == 265.33
    assert cava["tasting_note"].startswith("Utvecklad doft")

    # The Systembolaget join is the point of the integration.
    assert cava["article_number"] == "9049001"
    assert cava["product_url"] == "https://www.systembolaget.se/produkt/vin/9049001/"

    # Every wine in this release is stocked, scored and uniquely identified.
    assert all(w["article_number"] for w in result["wines"])
    assert all(w["score"] is not None for w in result["wines"])
    ids = [w["id"] for w in result["wines"]]
    assert len(set(ids)) == len(ids)


def test_parse_release_page_smaller_layout(load_fixture_html) -> None:
    result = parse_release_page(
        load_fixture_html("release-hitlista.html"),
        release_id="hitlista-3-september-2026",
        title="Hitlista 3 september 2026",
    )
    assert len(result["wines"]) == 5
    assert result["release"]["summary"] == "Fem fina fynd."
    assert result["warnings"] == []

    wine = next(w for w in result["wines"] if w["name"] == "Temjanika Luda Mara")
    assert wine["score"] == 13.5
    assert wine["value_rating"] == "fynd"
    assert wine["country"] == "Nordmakedonien"
    assert wine["article_number"] == "9339801"


def test_parsing_is_identical_authenticated_and_anonymous(load_fixture_html) -> None:
    """A member session changes the chrome, never the wine data.

    The integration accepts optional credentials, so the parser must produce
    byte-identical results whether or not the page was fetched while logged in.
    """
    kwargs = {"release_id": "hitlista-3-september-2026", "title": "Hitlista 3 september 2026"}
    anonymous = parse_release_page(load_fixture_html("release-hitlista.html"), **kwargs)
    authenticated = parse_release_page(
        load_fixture_html("release-hitlista-authenticated.html"), **kwargs
    )
    assert authenticated["wines"] == anonymous["wines"]
    assert authenticated["release"]["summary"] == anonymous["release"]["summary"]


def test_parse_release_page_warns_on_unrecognised_markup() -> None:
    """A layout change must degrade to a warning, never an exception."""
    result = parse_release_page(
        "<html><body><h1>Tom provning</h1></body></html>",
        release_id="tom-provning",
        title="Tom provning",
    )
    assert result["wines"] == []
    assert result["release"]["wine_count"] == 0
    assert result["warnings"]


@pytest.mark.parametrize("html", ["", "   ", "<html></html>", "not html at all"])
def test_parse_release_page_survives_degenerate_input(html: str) -> None:
    result = parse_release_page(html, release_id="x", title="X")
    assert result["wines"] == []
    assert result["warnings"]


def test_parse_release_page_tolerates_a_card_missing_its_article_number() -> None:
    """Webbviner are reviewed but not sold at Systembolaget."""
    html = """
    <ul id="wine-bottles-list">
      <li class="medium-3 groupedlist">
        <div class="c-wine-info">
          <div class="wine-points">15</div>
          <div class="c-wine-info__price">380:-</div>
          <div class="c-wine-info__headings"><h3>
          <a href="/sv/vinlocus/x/y"><span>Test Brut NV</span></a></h3></div>
        </div>
      </li>
    </ul>
    """
    result = parse_release_page(html, release_id="webbviner-test", title="Webbviner test")
    assert len(result["wines"]) == 1
    wine = result["wines"][0]
    assert wine["article_number"] is None
    assert wine["product_url"] is None
    assert wine["score"] == 15.0
    assert wine["vintage"] is None


# --------------------------------------------------------------------------
# Wine detail pages
# --------------------------------------------------------------------------


def test_parse_wine_detail_adds_the_importer(load_fixture_html) -> None:
    """The importer is the one field listing pages omit."""
    detail = parse_wine_detail(load_fixture_html("wine-detail.html"))
    assert detail["importer"] == "Verissima AB"
    assert detail["producer"] == "Az. Ferruccio Deiana"
    assert detail["grapes"] == ["cannonau"]
    assert detail["volume_ml"] == 750
    assert detail["alcohol_percent"] == 14.0


def test_parse_wine_detail_handles_empty_html() -> None:
    assert parse_wine_detail("") == {}


def test_parser_uses_only_stdlib_html_parsing() -> None:
    """No compiled parser dependency.

    BeautifulSoup's "lxml" backend needs a compiled wheel, which is a poor fit
    for Home Assistant OS/Alpine installs. The stdlib "html.parser" backend
    produces identical results on this site (verified against full live release
    pages), so the integration ships without lxml.
    """
    import pathlib
    import re

    component = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "munskankarna"
    # Look for real usage - a quoted backend name or an import - rather than
    # the bare word, which legitimately appears in explanatory comments.
    usage = re.compile(r"""["']lxml["']|\bimport\s+lxml|\bfrom\s+lxml\b""")
    for path in component.glob("*.py"):
        assert not usage.search(path.read_text(encoding="utf-8")), f"{path.name} uses lxml"

    manifest = (component / "manifest.json").read_text(encoding="utf-8")
    assert "lxml" not in manifest, "manifest.json still requires lxml"
