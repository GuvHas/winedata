"""Shared helpers for the Home Assistant integration tests."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.munskankarna.const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


def create_entry(
    hass: HomeAssistant,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Add a config entry to `hass` and return it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Munskänkarna",
        data=data or {CONF_BASE_URL: DEFAULT_BASE_URL},
        options=options or {},
        unique_id=DEFAULT_BASE_URL,
    )
    entry.add_to_hass(hass)
    return entry


def build_release(
    release_id: str,
    kind: str = "tillfalligt-sortiment",
    date: str | None = "2026-09-11",
    *,
    title: str | None = None,
    wine_count: int = 0,
) -> dict[str, Any]:
    """A minimal release dict matching `parser.ReleaseDict`."""
    return {
        "id": release_id,
        "title": title or release_id.replace("-", " ").title(),
        "kind": kind,
        "kind_label": kind,
        "date": date,
        "url": f"{DEFAULT_BASE_URL}/sv/vinlocus/{release_id}",
        "summary": None,
        "wine_count": wine_count,
    }


def build_wine(
    release_id: str,
    name: str,
    score: float | None = 15.0,
    *,
    value: str | None = "fynd",
    price: float | None = 199.0,
    article_number: str | None = "9049001",
    color: str = "red",
) -> dict[str, Any]:
    """A minimal wine dict matching `parser.WineDict`."""
    return {
        "id": f"{release_id}/{name.lower().replace(' ', '-')}",
        "name": name,
        "full_name": f"{name} 2020",
        "vintage": 2020,
        "producer": "Test Producer",
        "importer": None,
        "color": color,
        "color_label": "Rött vin",
        "country": "Frankrike",
        "region": "Bordeaux",
        "appellation": None,
        "grapes": ["merlot"],
        "price_sek": price,
        "volume_ml": 750,
        "alcohol_percent": 13.5,
        "price_per_litre": round(price / 750 * 1000, 2) if price else None,
        "score": score,
        "score_label": str(score).replace(".", ","),
        "band": "hogklassigt" if (score or 0) >= 15 else "bra",
        "value_rating": value,
        "value_label": value,
        "typical": False,
        "tasting_note": "En provsmakningsnot.",
        "review_url": f"{DEFAULT_BASE_URL}/sv/vinlocus/{release_id}/{name.lower()}",
        "article_number": article_number,
        "product_url": (
            f"https://www.systembolaget.se/produkt/vin/{article_number}/"
            if article_number
            else None
        ),
        "release_id": release_id,
    }
