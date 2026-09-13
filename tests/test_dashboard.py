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


@pytest.fixture
async def configured(hass: HomeAssistant):
    """A fully set-up integration with realistic data."""
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: 5}
    )
    wines = [
        build_wine(RELEASE_ID, "Toppvinet", 17.0, value="fynd", price=189.0),
        build_wine(RELEASE_ID, "Nummer Två", 15.5, value="prisvart", price=99.0),
        build_wine(RELEASE_ID, "Utan Nummer", 14.0, value="fynd", price=150.0,
                   article_number=None),
    ]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if release_id.startswith("tillfalligt") else KIND_HITLISTAN
        return {
            "release": build_release(release_id, kind, "2026-09-11", wine_count=len(wines)),
            "wines": list(wines),
            "warnings": [],
        }

    index = [
        build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11"),
        build_release("hitlista-3-september-2026", KIND_HITLISTAN, "2026-09-03"),
    ]
    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=index)
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
