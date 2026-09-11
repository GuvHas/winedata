"""Shared pytest configuration for the Munskänkarna Home Assistant integration.

The HTML fixtures in ``tests/fixtures/`` are the *same* files the TypeScript
parser is tested against, so both implementations are held to one contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    """Load `custom_components/` for tests that use Home Assistant.

    Requested lazily: the parser tests are pure functions over HTML and would
    otherwise pay to boot a `hass` instance they never touch.
    """
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
    yield


@pytest.fixture
def load_fixture_html():
    """Return a reader for the captured HTML fixtures."""

    def _load(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _load
