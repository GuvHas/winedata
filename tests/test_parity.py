"""Cross-language parity: the Python port must match the TypeScript parser.

This repository carries two parsers over the same source — the Node sync that
feeds the web app, and this Python port that feeds Home Assistant. They read
the same fixtures, so they can be held to the same output. This test fails if
either side is changed without the other.

Skipped automatically when the Node toolchain is unavailable (e.g. a HACS-only
checkout or a CI job that installs Python alone).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from custom_components.munskankarna.parser import parse_release_page

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (shutil.which("npx") and (REPO_ROOT / "node_modules" / "tsx").exists()),
    reason="Node toolchain not installed; run `npm install` to enable parity checks",
)

_TS_DUMP = """
import { readFileSync } from 'node:fs';
import { parseReleasePage } from '@/lib/ingest/munskankarna';
const rel = { id: process.argv[2], title: process.argv[3], kind: 'ovrigt',
  kindLabel: 'Övrigt', date: null, url: '', summary: null, wineCount: 0 };
const r = parseReleasePage(readFileSync(process.argv[4], 'utf8'), rel);
console.log(JSON.stringify(r.wines.map(w => ({
  name: w.name, vintage: w.vintage, score: w.review.score, band: w.review.band,
  value: w.review.valueRating, typical: w.review.typical, color: w.color,
  producer: w.producer, country: w.country, region: w.region,
  appellation: w.appellation, grapes: w.grapes, price: w.priceSek,
  vol: w.volumeMl, abv: w.alcoholPercent, ppl: w.pricePerLitre,
  art: w.systembolaget.articleNumber, url: w.systembolaget.productUrl,
  note: w.review.tastingNote,
}))));
"""


def _python_shape(html: str, release_id: str, title: str) -> list[dict]:
    """Project the Python parser onto the fields the TypeScript dump emits."""
    result = parse_release_page(html, release_id=release_id, title=title)
    return [
        {
            "name": w["name"], "vintage": w["vintage"], "score": w["score"],
            "band": w["band"], "value": w["value_rating"], "typical": w["typical"],
            "color": w["color"], "producer": w["producer"], "country": w["country"],
            "region": w["region"], "appellation": w["appellation"], "grapes": w["grapes"],
            "price": w["price_sek"], "vol": w["volume_ml"], "abv": w["alcohol_percent"],
            "ppl": w["price_per_litre"], "art": w["article_number"],
            "url": w["product_url"], "note": w["tasting_note"],
        }
        for w in result["wines"]
    ]


@pytest.mark.parametrize(
    ("fixture", "release_id", "title"),
    [
        (
            "release-tillfalligt-sortiment.html",
            "tillfalligt-sortiment-11-september-2026",
            "Tillfälligt sortiment 11 september 2026",
        ),
        ("release-hitlista.html", "hitlista-3-september-2026", "Hitlista 3 september 2026"),
    ],
)
def test_python_matches_typescript(
    fixture: str, release_id: str, title: str, tmp_path: pathlib.Path, load_fixture_html
) -> None:
    script = tmp_path / "dump.ts"
    script.write_text(_TS_DUMP, encoding="utf-8")
    fixture_path = REPO_ROOT / "tests" / "fixtures" / fixture

    proc = subprocess.run(  # noqa: S603
        ["npx", "tsx", str(script), release_id, title, str(fixture_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"TypeScript parser could not run: {proc.stderr[-300:]}")

    assert json.loads(proc.stdout) == _python_shape(load_fixture_html(fixture), release_id, title)
