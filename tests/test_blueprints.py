"""The shipped blueprint, validated by Home Assistant rather than by eye.

A blueprint is YAML that only fails when somebody imports it, which is the
worst moment to find out. These tests run it through the same schema and the
same placeholder machinery Home Assistant uses on import, so a typo in a
selector or an `!input` that no input declares fails here instead.

Home Assistant cannot auto-install a custom integration's blueprints — the
copy-on-populate path in `blueprint.models` reads core's own folder — so this
file is imported by URL. That makes `source_url` load-bearing: it is what lets
an imported copy be re-imported to update.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import parse_yaml

from custom_components.munskankarna.sensor import wine_summary
from tests.helpers import build_wine

BLUEPRINT_DIR = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "munskankarna"
BLUEPRINTS = sorted(BLUEPRINT_DIR.glob("*.yaml"))


def _load(path: Path) -> Blueprint:
    return Blueprint(
        parse_yaml(path.read_text(encoding="utf-8")),
        path=str(path),
        expected_domain="automation",
        schema=BLUEPRINT_SCHEMA,
    )


def test_blueprints_are_shipped() -> None:
    assert BLUEPRINTS, f"no blueprint found under {BLUEPRINT_DIR}"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_the_blueprint_validates(path: Path) -> None:
    """Constructing it is the check.

    `Blueprint.__init__` runs the schema, rejects the wrong domain, and — the
    part a hand-written blueprint gets wrong — raises when an `!input` has no
    matching input definition. A typo in a placeholder fails right here.
    """
    blueprint = _load(path)

    assert blueprint.domain == "automation"
    assert blueprint.validate() is None, blueprint.validate()
    assert blueprint.inputs, "the blueprint declares no inputs"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_the_blueprint_is_re_importable(path: Path) -> None:
    """Without source_url an imported copy cannot be updated in place."""
    assert _load(path).metadata.get("source_url", "").startswith("https://github.com/"), (
        f"{path.name} has no usable source_url"
    )


async def test_the_defaults_produce_a_valid_automation(hass: HomeAssistant) -> None:
    """Substituting every default must yield something the automation schema takes.

    A blueprint that validates on its own can still produce an automation that
    does not, which is only discovered when somebody saves it.

    Async because the schema compiles the templates in `variables`, and from
    2026.8 Home Assistant refuses to build a Template off the event loop.
    """
    path = BLUEPRINT_DIR / "fynd_alert.yaml"
    blueprint = _load(path)

    without_default = [
        name
        for name, spec in blueprint.inputs.items()
        if not isinstance(spec, dict) or "default" not in spec
    ]
    assert not without_default, (
        f"every input needs a default or the blueprint cannot be saved as-is: "
        f"{sorted(without_default)}"
    )

    config = BlueprintInputs(
        blueprint,
        {"use_blueprint": {"path": path.name, "input": {}}},
    ).async_substitute()
    PLATFORM_SCHEMA(config)


def test_the_bargain_filter_matches_the_event_payload() -> None:
    """The condition reads fields the integration actually announces."""
    announced = {"kind", "kind_label", "release_id", "release_date"} | set(
        wine_summary(build_wine("r1", "Ett Vin"))
    )
    source = (BLUEPRINT_DIR / "fynd_alert.yaml").read_text(encoding="utf-8")

    for field in ("value", "score", "price", "kind", "color", "name", "producer",
                  "vintage", "url", "review_url"):
        assert f"wine.{field}" in source, f"the blueprint never reads {field}"
        assert field in announced, f"the blueprint reads wine.{field}, which is never announced"
