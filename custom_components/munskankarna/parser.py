"""Parser for Munskänkarna's Vinlocus wine reviews.

Vinlocus is server-rendered and each release listing page embeds the *complete*
record for every wine it covers — score, price, value verdict, grapes,
producer, origin, tasting note and the Systembolaget article number. One
request per release is therefore enough; the per-wine detail pages are only
fetched for the importer, which listings omit.

This module is deliberately pure: it takes HTML strings and returns plain
dicts. No I/O, no Home Assistant imports — which keeps it fast to test and safe
to call from the event loop.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any, Final, TypedDict
from urllib.parse import quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

DEFAULT_BASE_URL: Final = "https://www.munskankarna.se"
PRODUCT_URL_BASE: Final = "https://www.systembolaget.se/produkt/vin"
SEARCH_URL_BASE: Final = "https://www.systembolaget.se/sortiment/"

#: Schemes a scraped link may use. An allowlist, not a blocklist: `urljoin`
#: passes `javascript:` and `data:` URLs through unchanged, and these URLs are
#: rendered as clickable markdown links on a Lovelace card, so anything else
#: would be an execution vector if the source site were ever compromised.
_SAFE_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

#: Paths under /sv/vinlocus/ that are facets, not releases.
_NON_RELEASE_SEGMENTS: Final[frozenset[str]] = frozenset(
    {"land", "druva", "importor", "provningstyp", "producent", "argang", "sok"}
)

_MONTHS: Final[dict[str, int]] = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}

#: Ordered so more specific slugs are tested before generic ones.
_ASSORTMENT_RULES: Final[tuple[tuple[str, str, str], ...]] = (
    (r"tillfalligt-sortiment|tillfälligt sortiment", "tillfalligt-sortiment",
     "Tillfälligt sortiment"),
    (r"fast-sortiment|fast sortiment|ordinarie", "fast-sortiment", "Fast sortiment"),
    (r"hitlist", "hitlistan", "Hitlista"),
    (r"lokalt-och-smaskaligt|lokalt och småskaligt", "lokalt-och-smaskaligt",
     "Lokalt och småskaligt"),
    (r"ordervaror|odervaror|bestallningssortiment|beställningssortiment",
     "bestallningssortimentet", "Ordervaror"),
    (r"webbviner|webbhandlare", "webbviner", "Webbviner"),
    (r"temaprovning|tema", "temaprovning", "Temaprovning"),
)


class ReleaseDict(TypedDict):
    """A published tasting ("provning") — the weekly/monthly release batch."""

    id: str
    title: str
    kind: str
    kind_label: str
    date: str | None
    url: str
    summary: str | None
    wine_count: int


class WineDict(TypedDict):
    """One reviewed wine, joined to its Systembolaget catalog entry."""

    id: str
    name: str
    full_name: str
    vintage: int | None
    producer: str | None
    importer: str | None
    color: str
    color_label: str
    country: str | None
    region: str | None
    appellation: str | None
    grapes: list[str]
    price_sek: float | None
    volume_ml: int | None
    alcohol_percent: float | None
    price_per_litre: float | None
    score: float | None
    score_label: str | None
    band: str | None
    value_rating: str | None
    value_label: str | None
    typical: bool
    tasting_note: str | None
    review_url: str | None
    article_number: str | None
    product_url: str | None
    release_id: str


class ParseResult(TypedDict):
    """Outcome of parsing one release page."""

    release: ReleaseDict
    wines: list[WineDict]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def clean(value: str | None) -> str:
    """Collapse whitespace (including non-breaking spaces) and trim."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace(" ", " ").replace(" ", " ")).strip()


def clean_or_none(value: str | None) -> str | None:
    """`clean`, but empty strings become ``None``."""
    return clean(value) or None


# ---------------------------------------------------------------------------
# Field normalisers
# ---------------------------------------------------------------------------


def parse_score(value: str | None) -> float | None:
    """Parse a Swedish-formatted score: ``14,5`` -> 14.5. Only 0-20 is valid."""
    match = re.search(r"\d+(?:\.\d+)?", clean(value).replace(",", "."))
    if not match:
        return None
    score = float(match.group())
    return score if 0 <= score <= 20 else None


def parse_price(value: str | None) -> float | None:
    """Parse ``199:-``, ``1 250 kr``, ``159:50`` into SEK."""
    text = clean(value)
    if not text:
        return None
    match = re.search(r"(\d[\d\s]*)(?:[,:](\d{1,2}))?", text)
    if not match:
        return None
    whole = int(match.group(1).replace(" ", ""))
    # `159:-` uses `-` as the öre placeholder; only digits count as decimals.
    fraction = int(match.group(2).ljust(2, "0")) / 100 if match.group(2) else 0.0
    total = round(whole + fraction, 2)
    return total or None


def parse_volume_ml(value: str | None) -> int | None:
    """Parse ``75 cl`` / ``750 ml`` / ``1,5 l`` into millilitres."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(cl|ml|l)\b", clean(value).lower())
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    factor = {"cl": 10, "ml": 1, "l": 1000}[match.group(2)]
    millilitres = round(amount * factor)
    return millilitres or None


def parse_alcohol(value: str | None) -> float | None:
    """Parse ``12% vol.`` / ``13,5 %`` into a percentage."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", clean(value))
    if not match:
        return None
    percent = float(match.group(1).replace(",", "."))
    return percent if 0 < percent <= 60 else None


def split_vintage(full_name: str) -> tuple[str, int | None]:
    """Split a published name into base name and vintage.

    Only a trailing four-digit year in a plausible range counts, so names like
    ``Cuvée 21`` keep their number.
    """
    text = clean(full_name)
    match = re.fullmatch(r"(.*?)[\s,]+((?:19|20)\d{2})", text)
    if not match:
        return text, None
    vintage = int(match.group(2))
    if not 1900 <= vintage <= datetime.now(UTC).year + 2:
        return text, None
    name = clean(match.group(1))
    return (name, vintage) if name else (text, None)


def parse_color(label: str | None) -> str:
    """Map a Swedish category label to a normalised colour."""
    text = clean(label).lower()
    if not text:
        return "other"
    if "mousser" in text or "champagne" in text:
        return "sparkling"
    if "ros" in text:
        return "rose"
    if "rött" in text or "rott" in text or text.startswith("röd"):
        return "red"
    if "vitt" in text or "vit " in text:
        return "white"
    if "starkvin" in text or "special" in text:
        return "fortified"
    if "dessert" in text or "sött" in text:
        return "dessert"
    return "other"


def parse_value_rating(label: str | None) -> str | None:
    """Map the printed value wording to a normalised verdict."""
    text = clean(label).lower()
    if not text:
        return None
    # Check the negative form first: "ej prisvärt" also contains "prisvärt".
    if text.startswith("ej ") or "inte prisvärd" in text:
        return "ej-prisvart"
    if "fynd" in text:
        return "fynd"
    if "mer än prisvärt" in text or "mer an prisvart" in text:
        return "mer-an-prisvart"
    if "prisvärt" in text or "prisvart" in text:
        return "prisvart"
    return None


def score_band(score: float | None, scale: int = 20) -> str | None:
    """Munskänkarna's published quality bands for the 20-point scale."""
    if score is None:
        return None
    value = (score / 100) * 20 if scale == 100 else score
    if value >= 18:
        return "exceptionellt"
    if value >= 15:
        return "hogklassigt"
    if value >= 12:
        return "bra"
    if value >= 9:
        return "medelbra"
    return "enkelt"


def price_per_litre(price_sek: float | None, volume_ml: int | None) -> float | None:
    """Normalise price to SEK per litre, for comparing across bottle sizes."""
    if price_sek is None or not volume_ml:
        return None
    return round(price_sek / volume_ml * 1000, 2)


def parse_release_date(title: str) -> str | None:
    """Pull an ISO date out of a release title, day- or month-precision."""
    text = clean(title).lower()
    months = "|".join(_MONTHS)

    if match := re.search(rf"(\d{{1,2}})\s+({months})\s+(\d{{4}})", text):
        return _iso_date(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)))
    if match := re.search(rf"({months})\s+(\d{{4}})", text):
        return _iso_date(int(match.group(2)), _MONTHS[match.group(1)], 1)
    return None


def _iso_date(year: int, month: int, day: int) -> str | None:
    """Build an ISO date, rejecting impossible ones rather than rolling over."""
    try:
        return datetime(year, month, day, tzinfo=UTC).date().isoformat()
    except ValueError:
        return None


def parse_assortment_kind(slug_or_title: str) -> tuple[str, str]:
    """Classify a release from its slug and/or title."""
    text = clean(slug_or_title).lower()
    for pattern, kind, label in _ASSORTMENT_RULES:
        if re.search(pattern, text):
            return kind, label
    return "ovrigt", "Övrigt"


def slugify(value: str) -> str:
    """Lowercase ASCII slug with Swedish characters folded."""
    text = clean(value).lower()
    for source, target in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e")):
        text = text.replace(source, target)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


# ---------------------------------------------------------------------------
# Systembolaget linking
# ---------------------------------------------------------------------------


def normalize_article_number(value: str | None) -> str | None:
    """Article numbers are digits only; reject anything implausible."""
    digits = re.sub(r"\D", "", clean(value))
    return digits if 3 <= len(digits) <= 10 else None


def build_product_url(article_number: str | None) -> str | None:
    """Canonical product URL, or ``None`` when there is no article number."""
    normalized = normalize_article_number(article_number)
    return f"{PRODUCT_URL_BASE}/{normalized}/" if normalized else None


def build_search_url(query: str) -> str:
    """Catalog search, used when a wine has no article number."""
    return f"{SEARCH_URL_BASE}?q={quote_plus(clean(query))}"


# ---------------------------------------------------------------------------
# Release index
# ---------------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    """Parse with the stdlib backend.

    `html.parser` needs no compiled dependency, which matters for Home
    Assistant OS and Alpine installs, and yields identical results to lxml on
    this site — checked field by field against full live release pages.
    """
    return BeautifulSoup(html or "", "html.parser")


def _release_slug_from_href(href: str) -> str | None:
    """Extract a release slug from an href, or None for facet/detail links."""
    path = href.split("?")[0].split("#")[0]
    match = re.search(r"/sv/vinlocus/([^/]+)/?$", path)
    if not match:
        return None
    slug = match.group(1)
    return None if slug in _NON_RELEASE_SEGMENTS else slug


def _build_release(slug: str, title: str, heading_hint: str, base_url: str) -> ReleaseDict:
    """Classify by slug first, then title, then the surrounding heading."""
    kind, label = parse_assortment_kind(slug)
    if kind == "ovrigt":
        kind, label = parse_assortment_kind(title)
    if kind == "ovrigt" and heading_hint:
        kind, label = parse_assortment_kind(heading_hint)

    return ReleaseDict(
        id=slug,
        title=title,
        kind=kind,
        kind_label=label,
        date=parse_release_date(title) or parse_release_date(slug.replace("-", " ")),
        url=urljoin(base_url, f"/sv/vinlocus/{slug}"),
        summary=None,
        wine_count=0,
    )


def parse_release_index(html: str, base_url: str = DEFAULT_BASE_URL) -> list[ReleaseDict]:
    """Discover published releases from the "Provningstyper" index.

    The index groups release links under an ``<h3>`` per tasting type, so the
    document is walked in order while tracking the most recent heading. That
    heading is only a hint: the slug is the authoritative classifier.
    """
    soup = _soup(html)
    releases: dict[str, ReleaseDict] = {}
    heading = ""

    for element in soup.find_all(["h3", "a"]):
        if element.name == "h3":
            heading = clean(element.get_text())
            continue

        href = element.get("href")
        if not href or "/vinlocus/" not in href:
            continue

        slug = _release_slug_from_href(href)
        if not slug or slug in releases:
            continue

        title = clean(element.get_text()) or clean(element.get("title"))
        if not title or title.lower().startswith("visa fler"):
            continue

        releases[slug] = _build_release(slug, title, heading, base_url)

    return list(releases.values())


# ---------------------------------------------------------------------------
# Release pages
# ---------------------------------------------------------------------------


def _text_of(node: Tag | None) -> str:
    return clean(node.get_text()) if node else ""


def safe_url(href: str | None, base_url: str) -> str | None:
    """Resolve a scraped href, or return None if it is not a safe link.

    Rejects anything that is not http(s) after resolution, and anything that
    resolves off the site being scraped. Control characters are stripped first,
    because browsers ignore them inside a scheme: `java\tscript:` is a live
    `javascript:` URL to a browser but not to a naive string comparison.
    """
    if not href:
        return None

    # Strip whitespace and C0/C1 control characters anywhere in the string.
    cleaned = "".join(ch for ch in href if ch.isprintable() and not ch.isspace())
    if not cleaned:
        return None

    try:
        resolved = urljoin(base_url, cleaned)
        parts = urlsplit(resolved)
    except ValueError:
        return None

    if parts.scheme.lower() not in _SAFE_URL_SCHEMES:
        return None

    # Only links back to the site we are scraping are ours to publish.
    base_host = urlsplit(base_url).hostname
    if base_host and parts.hostname != base_host:
        return None

    return resolved


def _parse_origin(card: Tag) -> tuple[str | None, str | None, str | None]:
    """Origin row: ``<a>Spanien</a>, <a>Navarra</a>, Cava``.

    Country and region are links; whatever trails them is the appellation.
    """
    row = card.select_one(".c-wine-info__faded, .c-wine-bottle__origin")
    if row is None:
        return None, None, None

    links = [clean(a.get_text()) for a in row.select('a[href*="/vinlocus/land/"]')]
    links = [link for link in links if link]

    full_text = clean(row.get_text())
    appellation: str | None = None
    if links:
        last = links[-1]
        tail_start = full_text.rfind(last)
        if tail_start != -1:
            appellation = clean_or_none(
                re.sub(r"^[,\s·|-]+", "", full_text[tail_start + len(last):])
            )
    else:
        appellation = clean_or_none(full_text)

    return (
        links[0] if links else None,
        links[1] if len(links) > 1 else None,
        appellation,
    )


def _parse_article_number(card: Tag) -> str | None:
    """Article number from the Systembolaget link, or from adjacent text."""
    link = card.select_one('a[href*="systembolaget.se"]')
    if link is not None:
        span = link.find("span")
        if number := normalize_article_number(_text_of(span) or clean(link.get_text())):
            return number
        # Matches https://systembolaget.se/9049001 and /produkt/vin/.../9049001/
        if match := re.search(r"(\d{4,10})/?(?:[?#].*)?$", link.get("href", "")):
            return normalize_article_number(match.group(1))

    if match := re.search(r"Systembolaget:?\s*(\d{4,10})", clean(card.get_text()), re.I):
        return normalize_article_number(match.group(1))
    return None


def _parse_wine_card(
    card: Tag, release_id: str, section_label: str, base_url: str
) -> WineDict | None:
    """Parse a single ``.c-wine-info`` card. Returns None when there is no name."""
    title_link = card.select_one(".c-wine-info__headings h3 a")
    full_name = ""
    if title_link is not None:
        span = title_link.find("span")
        full_name = _text_of(span) or clean(title_link.get_text())
    if not full_name:
        full_name = _text_of(card.find("h3"))
    if not full_name:
        return None

    review_href = title_link.get("href") if title_link is not None else None
    name, vintage = split_vintage(full_name)

    score_label = clean_or_none(_text_of(card.select_one(".wine-points")))
    score = parse_score(score_label)

    price_sek = parse_price(
        _text_of(card.select_one(".c-wine-info__price, .c-wine-bottle__price"))
    )

    value_label = clean_or_none(
        _text_of(card.select_one('.c-wine-info__stat[name="category"] span'))
    )

    color_image = card.select_one(".c-wine-info__catimg")
    color_label = (
        clean(color_image.get("alt")) if color_image is not None else ""
    ) or _text_of(card.select_one(".c-wine-info__catvolumealcohol .tooltiptext")) or section_label

    measures = [
        clean(span.get_text()) for span in card.select(".c-wine-info__volumeAlocoholValues span")
    ]
    volume_source = next((m for m in measures if re.search(r"\b(cl|ml|l)\b", m, re.I)), None)
    volume_ml = parse_volume_ml(volume_source)
    alcohol_percent = parse_alcohol(next((m for m in measures if "%" in m), None))

    country, region, appellation = _parse_origin(card)
    producer = clean_or_none(_text_of(card.select_one(".c-wine-info__producer span")))
    article_number = _parse_article_number(card)

    grapes: list[str] = []
    for anchor in card.select('a[href*="/vinlocus/druva/"]'):
        grape = clean(anchor.get_text()).lower()
        if grape and grape not in grapes:
            grapes.append(grape)

    slug_source = review_href.rstrip("/").split("/")[-1] if review_href else full_name

    return WineDict(
        id=f"{release_id}/{slugify(slug_source)}",
        name=name,
        full_name=full_name,
        vintage=vintage,
        producer=producer,
        importer=None,
        color=parse_color(color_label),
        color_label=color_label or "Okänd",
        country=country,
        region=region,
        appellation=appellation,
        grapes=grapes,
        price_sek=price_sek,
        volume_ml=volume_ml,
        alcohol_percent=alcohol_percent,
        price_per_litre=price_per_litre(price_sek, volume_ml),
        score=score,
        score_label=score_label,
        band=score_band(score),
        value_rating=parse_value_rating(value_label),
        value_label=value_label,
        typical=card.select_one(".c-wine-info__typical") is not None,
        tasting_note=clean_or_none(_text_of(card.select_one(".c-wine-info__text"))),
        review_url=safe_url(review_href, base_url),
        article_number=article_number,
        product_url=build_product_url(article_number),
        release_id=release_id,
    )


def parse_release_page(
    html: str,
    release_id: str,
    title: str,
    base_url: str = DEFAULT_BASE_URL,
) -> ParseResult:
    """Parse a release page into its wines.

    Never raises on unexpected markup: an unparseable card is skipped with a
    warning and an unrecognised page yields an empty result plus a warning, so
    a site redesign degrades the sensor rather than breaking the integration.
    """
    soup = _soup(html)
    warnings: list[str] = []
    wines: list[WineDict] = []
    seen: set[str] = set()

    heading = _text_of(soup.find("h1"))
    summary = clean_or_none(_text_of(soup.select_one(".c-wine-contentdescription")))
    if summary:
        summary = re.sub(r"^Om provningen\s*", "", summary, flags=re.I) or None

    kind, kind_label = parse_assortment_kind(release_id)
    if kind == "ovrigt":
        kind, kind_label = parse_assortment_kind(title)

    release_title = heading or title
    release = ReleaseDict(
        id=release_id,
        title=release_title,
        kind=kind,
        kind_label=kind_label,
        date=parse_release_date(release_title) or parse_release_date(title),
        url=urljoin(base_url, f"/sv/vinlocus/{release_id}"),
        summary=summary,
        wine_count=0,
    )

    # Cards live in `ul#wine-bottles-list`; `li.wine-section` rows are colour
    # headings that apply to the cards following them.
    items = soup.select("#wine-bottles-list > li")
    section_label = ""

    for item in items:
        classes = item.get("class") or []
        if "wine-section" in classes:
            section_label = _text_of(item.select_one(".cat-header")) or _text_of(item)
            continue

        card = item if "c-wine-info" in classes else item.select_one(".c-wine-info")
        if card is None:
            continue

        wine = _parse_wine_card(card, release_id, section_label, base_url)
        if wine is None:
            warnings.append(f"Skipped an unparseable card in {release_id}")
            continue
        if wine["id"] in seen:
            continue
        seen.add(wine["id"])
        wines.append(wine)

    if not wines:
        warnings.append(
            f"No wines found for {release_id} — the page markup may have changed, "
            "or the release may require an authenticated session."
        )

    release["wine_count"] = len(wines)
    return ParseResult(release=release, wines=wines, warnings=warnings)


# ---------------------------------------------------------------------------
# Wine detail pages
# ---------------------------------------------------------------------------


def parse_wine_detail(html: str) -> dict[str, Any]:
    """Parse a review page for the few fields listings omit (the importer)."""
    soup = _soup(html)
    detail: dict[str, Any] = {}

    def labelled(label: str) -> str | None:
        for row in soup.select(".c-wine-bottle__datarow"):
            strong = row.find("strong")
            if strong is None or not re.match(label, clean(strong.get_text()), re.I):
                continue
            copy = clean(row.get_text())
            return clean_or_none(re.sub(rf"^{label}:?\s*", "", copy, flags=re.I))
        return None

    if importer := labelled("Importör"):
        detail["importer"] = importer
    if producer := labelled("Producent"):
        detail["producer"] = producer

    grapes: list[str] = []
    for anchor in soup.select('a[href*="/vinlocus/druva/"]'):
        grape = clean(anchor.get_text()).lower()
        if grape and grape not in grapes:
            grapes.append(grape)
    if grapes:
        detail["grapes"] = grapes

    about = clean(_text_of(soup.select_one("#c-wine-bottle-desktop-other")) or _text_of(soup))
    match = re.search(r"Volym\s*([\d.,]+\s*(?:cl|ml|l)\b)", about, re.I)
    if match and (volume := parse_volume_ml(match.group(1))) is not None:
        detail["volume_ml"] = volume

    match = re.search(r"Alkohol\s*([\d.,]+\s*%)", about, re.I)
    if match and (alcohol := parse_alcohol(match.group(1))) is not None:
        detail["alcohol_percent"] = alcohol

    return detail
