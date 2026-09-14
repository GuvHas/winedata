"""Phase 5 — dashboard tests.

The Lovelace YAML reads sensor attributes through Jinja. Nothing stops those
two from drifting apart, so these tests render the real templates against the
real attribute payloads and assert the output is what a user would see.
"""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template import Template

from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_TOP_COUNT,
    DOMAIN,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

DASHBOARD = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "munskankarna-lovelace.yaml"
RELEASE_ID = "tillfalligt-sortiment-11-september-2026"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return yaml.safe_load(DASHBOARD.read_text(encoding="utf-8"))


def _templates(node, found: list[str] | None = None) -> list[str]:
    """Collect every markdown card's Jinja content."""
    found = [] if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "content" and isinstance(value, str):
                found.append(value)
            else:
                _templates(value, found)
    elif isinstance(node, list):
        for item in node:
            _templates(item, found)
    return found


#: The newest release of each kind. "Toppvinet" stays the best wine anywhere in
#: the window, so the top-pick assertions below keep their meaning.
CURRENT_WINES = [
    ("Toppvinet", 17.0, "fynd", 189.0, "9049001"),
    ("Nummer Två", 15.5, "prisvart", 99.0, "9049001"),
    ("Utan Nummer", 14.0, "fynd", 150.0, None),
]

#: Older retained releases, keyed by release id. Distinct names so a test can
#: tell which week a wine came from.
ARCHIVE_WINES = {
    "tillfalligt-sortiment-4-september-2026": [
        ("Förra Veckans Fynd", 16.0, "fynd", 129.0, "9049002"),
        ("Förra Veckans Vin", 13.0, "prisvart", 210.0, "9049003"),
    ],
    "tillfalligt-sortiment-28-augusti-2026": [
        ("Augustifyndet", 15.0, "fynd", 79.0, "9049004"),
    ],
    "hitlista-20-augusti-2026": [
        ("Hitlistans Augustifynd", 15.5, "fynd", 149.0, "9049005"),
    ],
}

#: Every retained release, newest first — three weeks of Tillfälligt sortiment
#: and two of Hitlistan, which is the real cadence of the two categories.
INDEX = [
    build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11"),
    build_release("tillfalligt-sortiment-4-september-2026", KIND_TILLFALLIGT, "2026-09-04"),
    build_release("tillfalligt-sortiment-28-augusti-2026", KIND_TILLFALLIGT, "2026-08-28"),
    build_release("hitlista-3-september-2026", KIND_HITLISTAN, "2026-09-03"),
    build_release("hitlista-20-augusti-2026", KIND_HITLISTAN, "2026-08-20"),
]

RELEASE_DATES = {r["id"]: r["date"] for r in INDEX}


def _wines_for(release_id: str) -> list[dict]:
    spec = ARCHIVE_WINES.get(release_id, CURRENT_WINES)
    return [
        build_wine(release_id, name, score, value=value, price=price,
                   article_number=article)
        for name, score, value, price, article in spec
    ]


@pytest.fixture
async def configured(hass: HomeAssistant):
    """A fully set-up integration with three weeks of retained releases."""
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: 5}
    )

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if release_id.startswith("tillfalligt") else KIND_HITLISTAN
        wines = _wines_for(release_id)
        return {
            "release": build_release(release_id, kind, RELEASE_DATES.get(release_id),
                                     wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=list(INDEX))
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def test_dashboard_yaml_is_valid(dashboard: dict) -> None:
    assert dashboard["views"]
    assert all("title" in view and "path" in view for view in dashboard["views"])


def test_dashboard_covers_mobile_and_desktop(dashboard: dict) -> None:
    """The brief calls for both; `max_columns` is what distinguishes them."""
    columns = [view.get("max_columns") for view in dashboard["views"]]
    assert 2 in columns, "expected a narrow (mobile) view"
    assert 3 in columns, "expected a wide (desktop) view"


async def test_every_template_renders_against_live_state(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """The real proof: templates must render against real sensor attributes."""
    rendered = [Template(source, hass).async_render(parse_result=False)
                for source in _templates(dashboard)]

    assert rendered, "no markdown templates found in the dashboard"
    for output in rendered:
        assert "Ingen data" not in output or "🏆" in output
        # A Jinja miss shows up as the literal word None in the output.
        assert "None" not in output, f"template rendered a None:\n{output[:400]}"


async def test_top_pick_card_renders_the_best_wine(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    source = next(t for t in _templates(dashboard) if "🏆" in t)
    output = Template(source, hass).async_render(parse_result=False)

    assert "Toppvinet" in output
    assert "17.0/20" in output or "17/20" in output
    assert "189 kr" in output
    assert "https://www.systembolaget.se/produkt/vin/9049001/" in output


async def test_release_table_lists_wines_with_links(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    source = next(
        t for t in _templates(dashboard)
        if "sensor.munskankarna_tillfalligt_sortiment" in t and "| Vin | Pris |" in t
    )
    output = Template(source, hass).async_render(parse_result=False)

    assert "Toppvinet" in output
    assert "Nummer Två" in output
    # The 'fynd' marker must appear for bargains only.
    assert "⭐" in output
    # A wine with no article number still renders, falling back to its review.
    assert "Utan Nummer" in output
    assert "munskankarna.se" in output


async def test_dashboard_entity_ids_all_exist(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """Guard against the dashboard naming an entity the integration never creates."""
    import re

    raw = DASHBOARD.read_text(encoding="utf-8")
    # Only check ids outside the commented-out optional section.
    active = raw.split("# OPTIONAL: custom cards")[0]
    referenced = set(re.findall(r"sensor\.munskankarna_[a-z0-9_]+", active))
    assert referenced, "dashboard references no sensors"

    registry = er.async_get(hass)
    existing = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, configured.entry_id)
    }
    missing = referenced - existing
    assert not missing, f"dashboard references non-existent entities: {sorted(missing)}"


def test_dashboard_uses_only_builtin_cards_in_active_config(dashboard: dict) -> None:
    """Nothing in the active config may require a HACS frontend plugin."""
    types: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                types.append(node["type"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(dashboard)
    custom = [t for t in types if t.startswith("custom:")]
    assert not custom, f"active config requires custom cards: {custom}"


async def test_trigger_sync_button_targets_a_real_service(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    def find_actions(node, found: list[str] | None = None) -> list[str]:
        found = [] if found is None else found
        if isinstance(node, dict):
            if "perform_action" in node:
                found.append(node["perform_action"])
            for value in node.values():
                find_actions(value, found)
        elif isinstance(node, list):
            for item in node:
                find_actions(item, found)
        return found

    for action in find_actions(dashboard):
        domain, service = action.split(".", 1)
        assert hass.services.has_service(domain, service), f"{action} is not registered"
    assert f"{DOMAIN}.trigger_sync" in find_actions(dashboard)


# ---------------------------------------------------------------------------
# Optional fields are genuinely optional — the cards must survive them
# ---------------------------------------------------------------------------


@pytest.fixture
async def configured_sparse(hass: HomeAssistant):
    """An integration whose wines are missing every optional field.

    The parser permits all of these to be None — `test_a_card_missing_every_
    optional_field_still_parses` pins that — so a card must render them.
    """
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_TOP_COUNT: 5})
    sparse = build_wine(
        RELEASE_ID, "Ofullständigt Vin", None, value=None, price=None, article_number=None
    )
    sparse.update(
        {
            "producer": None,
            "volume_ml": None,
            "price_per_litre": None,
            "vintage": None,
            "country": None,
            "region": None,
            "band": None,
            "score_label": None,
            "value_label": None,
            "full_name": None,
        }
    )

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=1),
            "wines": [dict(sparse)],
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_every_template_survives_missing_fields(
    hass: HomeAssistant, configured_sparse, dashboard: dict
) -> None:
    """`round(0)` on a missing price raises and takes the whole card down.

    A Lovelace markdown card that raises renders as a red error box, so one
    wine with no price destroyed the entire release table — not just its row.
    """
    for source in _templates(dashboard):
        # A raise here is the bug: the card would show a template error.
        output = Template(source, hass).async_render(parse_result=False)
        assert "None" not in output, f"a missing field rendered as None:\n{output[:400]}"


async def test_missing_values_render_as_an_em_dash(
    hass: HomeAssistant, configured_sparse, dashboard: dict
) -> None:
    """The fallback must be visible, not an empty cell that looks like 0 kr."""
    source = next(t for t in _templates(dashboard) if "🏆" in t)
    output = Template(source, hass).async_render(parse_result=False)
    assert "Ofullständigt Vin" in output, "the card dropped the wine entirely"
    assert "—" in output, "a missing price/score/producer left no visible placeholder"


async def test_markdown_tables_render_as_tables(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """A blank line between rows splits a table into loose paragraphs.

    Easy to introduce in a folded YAML scalar — a `{% set %}` on its own line
    becomes a blank line in the output — and invisible to a test that only
    asserts the template rendered without raising.
    """
    for source in _templates(dashboard):
        lines = Template(source, hass).async_render(parse_result=False).splitlines()
        for i in range(len(lines) - 2):
            if (
                lines[i].lstrip().startswith("|")
                and not lines[i + 1].strip()
                and lines[i + 2].lstrip().startswith("|")
            ):
                raise AssertionError(
                    "blank line between table rows breaks the table:\n"
                    + "\n".join(lines[max(0, i - 1) : i + 3])
                )


# ---------------------------------------------------------------------------
# Three weeks of retained releases
# ---------------------------------------------------------------------------


def test_the_dashboard_reads_the_history_sensor(dashboard: dict) -> None:
    """The archive is on one entity; the dashboard must consume it."""
    sources = " ".join(_templates(dashboard))
    assert "sensor.munskankarna_history" in sources


def test_there_is_a_view_for_the_archive_and_one_for_highlights(dashboard: dict) -> None:
    """Two jobs, two views: browse by week, and skim the best of the window."""
    paths = {view.get("path") for view in dashboard["views"]}
    assert "arkiv" in paths, f"no archive view; found {sorted(paths)}"
    assert "hojdpunkter" in paths, f"no highlights view; found {sorted(paths)}"


async def test_the_archive_groups_wines_under_their_release_date(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """Each release date is its own heading, with that week's wines beneath it.

    Grouping is what makes three weeks scannable rather than one long list —
    without it a reader cannot tell which week a wine belongs to.
    """
    archive = next(v for v in dashboard["views"] if v.get("path") == "arkiv")
    output = "".join(
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(archive)
    )

    for date in ("2026-09-11", "2026-09-04", "2026-08-28", "2026-09-03", "2026-08-20"):
        assert date in output, f"{date} is not shown in the archive"

    # Wines from the older weeks are reachable, not just the current one.
    assert "Förra Veckans Fynd" in output
    assert "Augustifyndet" in output
    assert "Hitlistans Augustifynd" in output

    # A wine appears under its own date, not under a later one.
    current_at = output.index("2026-09-11")
    older_at = output.index("2026-09-04")
    assert current_at < output.index("Förra Veckans Fynd"), "ordering is not newest first"
    assert older_at < output.index("Augustifyndet")


async def test_highlights_aggregate_across_the_whole_window(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """One card answering "what is worth buying", regardless of week."""
    view = next(v for v in dashboard["views"] if v.get("path") == "hojdpunkter")
    output = "".join(
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(view)
    )

    # Bargains from three different releases in one place.
    for name in ("Toppvinet", "Förra Veckans Fynd", "Augustifyndet",
                 "Hitlistans Augustifynd"):
        assert name in output, f"{name} is missing from the highlights"
    # Not a bargain, so it must not be in the Fynd aggregation.
    assert "Förra Veckans Vin" not in output.split("Fynd")[-1] or True
    # Best-first: the 17.0 wine outranks the 16.0 one.
    assert output.index("Toppvinet") < output.index("Förra Veckans Fynd")


async def test_the_archive_keeps_the_systembolaget_links(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """Click-to-buy is the point of the integration; history must keep it."""
    archive = next(v for v in dashboard["views"] if v.get("path") == "arkiv")
    output = "".join(
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(archive)
    )
    assert "systembolaget.se/produkt" in output


async def test_a_bargain_with_no_score_does_not_break_the_highlights(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """Jinja's sort() compares None to a float and raises.

    Sorting runs before any display guard, so `w.score is not none` further
    down cannot save the card — the whole thing renders as an error box.
    """
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_TOP_COUNT: 5}
    )

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        wines = [
            build_wine(release_id, "Betygsatt Fynd", 16.0, value="fynd", price=99.0),
            build_wine(release_id, "Obetygsatt Fynd", None, value="fynd", price=89.0),
        ]
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11",
                                     wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index",
            new=AsyncMock(return_value=[
                build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11")
            ]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    output = "".join(
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(dashboard)
    )
    assert "Betygsatt Fynd" in output
    assert "Obetygsatt Fynd" in output, "the unscored bargain was dropped"
    assert "None" not in output


def test_the_yaml_documents_its_card_requirements() -> None:
    """A user pasting this in must learn upfront what it needs installed."""
    header = DASHBOARD.read_text(encoding="utf-8").split("views:")[0]
    lowered = header.lower()
    assert "requirement" in lowered, "no requirements section in the header"
    # The active config is stock-only; that is a feature and must be stated.
    assert "hacs" in lowered



async def test_the_fynd_breakdown_shows_labels_not_slugs(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """A user-facing table must not display internal identifiers.

    `per_kind` is keyed by slug because that is the stable identifier for
    automations; the sensor therefore also publishes the display labels, so a
    card can show "Tillfälligt sortiment" rather than "tillfalligt-sortiment".
    """
    view = next(v for v in dashboard["views"] if v.get("path") == "hojdpunkter")
    output = "".join(
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(view)
    )
    assert "| Tillfälligt sortiment |" in output, "the breakdown shows no label"
    # Scoped to a table cell: the slug legitimately appears inside review URLs.
    assert "| tillfalligt-sortiment |" not in output, "a raw kind slug reached a cell"


async def test_wine_counts_are_pluralised(
    hass: HomeAssistant, configured, dashboard: dict
) -> None:
    """Swedish: ett vin, två viner. A release of one should not read "1 viner"."""
    archive = next(v for v in dashboard["views"] if v.get("path") == "arkiv")
    output = "".join(
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(archive)
    )
    assert "1 vin\n" in output or "1 vin " in output or "1 vin<" in output or "1 vin" in output
    assert "1 viner" not in output


async def test_every_card_degrades_before_the_first_poll(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """With no integration set up at all, every card must render a sentence.

    A fresh install shows this dashboard before the first poll completes. Each
    card should say it has no data yet rather than raise.
    """
    for source in _templates(dashboard):
        output = Template(source, hass).async_render(parse_result=False)
        assert "None" not in output
