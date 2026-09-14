"""The 3-week history dashboard renders against real sensor attributes.

Same contract as the main dashboard tests: the YAML and the attribute shape
can drift apart silently, so these render the real templates against a real
coordinator rather than asserting on the text of the file.
"""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_TOP_COUNT,
    KIND_HITLISTAN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from tests.helpers import build_release, build_wine, create_entry

DASHBOARD = (
    pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "3-week-history.yaml"
)

INDEX = [
    build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11"),
    build_release("t-2026-09-04", KIND_TILLFALLIGT, "2026-09-04"),
    build_release("t-2026-08-28", KIND_TILLFALLIGT, "2026-08-28"),
    build_release("h-2026-09-03", KIND_HITLISTAN, "2026-09-03"),
]


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return yaml.safe_load(DASHBOARD.read_text(encoding="utf-8"))


def _templates(node, found: list[str] | None = None) -> list[str]:
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


async def _setup(hass: HomeAssistant, sparse: bool = False):
    entry = create_entry(
        hass,
        options={CONF_KINDS: [KIND_TILLFALLIGT, KIND_HITLISTAN], CONF_TOP_COUNT: 5},
    )

    def _make(release_id: str) -> list[dict]:
        if sparse:
            wine = build_wine(release_id, "Ofullständigt Vin", None,
                              value=None, price=None, article_number=None)
            wine.update({
                "producer": None, "volume_ml": None, "price_per_litre": None,
                "vintage": None, "country": None, "region": None, "band": None,
            })
            return [wine]
        return [
            build_wine(release_id, f"Vin {i}", 17.0 - i,
                       value="fynd" if i % 2 == 0 else "prisvart", price=120.0 + i)
            for i in range(4)
        ]

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        kind = KIND_TILLFALLIGT if rid.startswith("t-") else KIND_HITLISTAN
        wines = _make(rid)
        return {
            "release": build_release(rid, kind, rid[2:], wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator, "_async_fetch_index", new=AsyncMock(return_value=INDEX)
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def test_the_dashboard_is_valid_yaml_with_views(dashboard: dict) -> None:
    assert dashboard["views"], "no views"
    assert all("title" in v and "path" in v for v in dashboard["views"])
    assert _templates(dashboard), "no markdown templates found"


def test_the_dashboard_reads_the_history_sensor(dashboard: dict) -> None:
    """It must consume the entity built for it, not re-derive from the others."""
    sources = " ".join(_templates(dashboard))
    assert "sensor.munskankarna_history" in sources


async def test_every_template_renders_against_live_history(
    hass: HomeAssistant, dashboard: dict
) -> None:
    await _setup(hass)
    rendered = [
        Template(source, hass).async_render(parse_result=False)
        for source in _templates(dashboard)
    ]
    for output in rendered:
        assert "None" not in output, f"template rendered a None:\n{output[:500]}"

    combined = " ".join(rendered)
    # Every retained week is reachable from the dashboard.
    for release_date in ("2026-09-11", "2026-09-04", "2026-08-28", "2026-09-03"):
        assert release_date in combined, f"{release_date} is not shown anywhere"


async def test_every_template_survives_missing_metadata(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """A wine with no price, score or producer must not take a card down."""
    await _setup(hass, sparse=True)
    for source in _templates(dashboard):
        output = Template(source, hass).async_render(parse_result=False)
        assert "None" not in output, f"a missing field rendered as None:\n{output[:500]}"
    # The placeholder is visible rather than an empty cell reading as "0 kr".
    combined = " ".join(
        Template(s, hass).async_render(parse_result=False) for s in _templates(dashboard)
    )
    assert "—" in combined


async def test_the_dashboard_says_something_when_there_is_no_data(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """Before the first poll every card must degrade to a sentence, not an error."""
    for source in _templates(dashboard):
        Template(source, hass).async_render(parse_result=False)


async def test_every_entity_the_dashboard_names_actually_exists(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """A typo in an entity_id renders as an empty card, not an error.

    Nothing else catches it: `state_attr` on a missing entity returns None, so
    the card falls through to its "no data" branch and looks merely empty.
    """
    import re

    await _setup(hass)
    referenced = set()
    for source in _templates(dashboard):
        referenced.update(re.findall(r"sensor\.munskankarna_[a-z0-9_]+", source))

    assert referenced, "the dashboard names no sensors at all"
    missing = [eid for eid in sorted(referenced) if hass.states.get(eid) is None]
    assert not missing, f"dashboard references entities that do not exist: {missing}"


async def test_markdown_tables_render_as_tables(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """A blank line between rows splits a table into loose paragraphs.

    Easy to introduce in a folded YAML scalar — a `{% set %}` on its own line
    becomes a blank line in the output — and invisible to a test that only
    asserts the template rendered without raising.
    """
    await _setup(hass)
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


async def test_the_fynd_view_sorts_when_a_bargain_has_no_score(
    hass: HomeAssistant, dashboard: dict
) -> None:
    """Jinja's sort() compares None against a float and raises.

    The parser allows a scored and an unscored wine to sit in the same
    release, and both can be Fynd. Sorting happens before the display guards,
    so `w.score is not none` further down cannot save the card — the whole
    thing renders as an error box. The earlier sparse fixture missed this
    because its unscored wine was not a Fynd, so it never reached the sort.
    """
    entry = create_entry(
        hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_TOP_COUNT: 5}
    )

    def _mixed(release_id: str) -> list[dict]:
        scored = build_wine(release_id, "Betygsatt Fynd", 16.0, value="fynd", price=99.0)
        unscored = build_wine(release_id, "Obetygsatt Fynd", None, value="fynd", price=89.0)
        return [scored, unscored]

    async def fake_fetch(self, rid: str, title: str) -> dict:  # noqa: ANN001
        wines = _mixed(rid)
        return {
            "release": build_release(rid, KIND_TILLFALLIGT, "2026-09-11",
                                     wine_count=len(wines)),
            "wines": wines,
            "warnings": [],
            "page_valid": True,
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[
                build_release("t-2026-09-11", KIND_TILLFALLIGT, "2026-09-11")
            ]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    combined = ""
    for source in _templates(dashboard):
        combined += Template(source, hass).async_render(parse_result=False)

    # Both bargains are listed, and the unscored one shows a placeholder.
    assert "Betygsatt Fynd" in combined
    assert "Obetygsatt Fynd" in combined, "the unscored bargain was dropped"
    assert "None" not in combined
