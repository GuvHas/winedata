"""Phase 5 — HACS and Home Assistant manifest compliance.

These assert the packaging rules HACS and the HA loader enforce, so a broken
release is caught here rather than by users' update feeds.
"""

from __future__ import annotations

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = REPO_ROOT / "custom_components" / "munskankarna"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hacs_config() -> dict:
    return json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))


def test_manifest_has_required_keys(manifest: dict) -> None:
    """Keys the Home Assistant loader requires for a custom integration."""
    for key in ("domain", "name", "version", "documentation", "codeowners", "requirements"):
        assert key in manifest, f"manifest.json is missing {key!r}"


def test_manifest_domain_matches_directory(manifest: dict) -> None:
    assert manifest["domain"] == COMPONENT.name


def test_manifest_version_is_semver(manifest: dict) -> None:
    """HACS requires a version and uses it to offer updates."""
    parts = manifest["version"].split(".")
    assert len(parts) == 3, "version must be MAJOR.MINOR.PATCH"
    assert all(part.isdigit() for part in parts)


def test_manifest_declares_config_flow(manifest: dict) -> None:
    assert manifest["config_flow"] is True


def test_manifest_iot_class_is_valid(manifest: dict) -> None:
    assert manifest["iot_class"] in {
        "assumed_state", "cloud_polling", "cloud_push",
        "calculated", "local_polling", "local_push",
    }
    # This integration polls a website, so cloud_polling is the honest answer.
    assert manifest["iot_class"] == "cloud_polling"


def test_manifest_requirements_are_pinned_to_a_floor(manifest: dict) -> None:
    """Every requirement needs a version specifier so installs are reproducible."""
    assert manifest["requirements"]
    for requirement in manifest["requirements"]:
        assert any(op in requirement for op in ("==", ">=", "~=")), requirement


def test_hacs_config_is_valid(hacs_config: dict) -> None:
    assert hacs_config["name"]
    # Tells HACS this repo ships an integration from custom_components/.
    assert hacs_config.get("content_in_root") is not True
    assert "homeassistant" in hacs_config, "declare a minimum HA version"


def test_required_files_exist() -> None:
    for filename in (
        "__init__.py", "manifest.json", "const.py", "config_flow.py",
        "coordinator.py", "sensor.py", "parser.py", "api.py",
        "mqtt_bridge.py", "services.yaml",
    ):
        assert (COMPONENT / filename).is_file(), f"missing {filename}"


def test_translations_exist_and_match() -> None:
    """Swedish and English must expose the same keys, or the UI breaks in one."""
    translations = COMPONENT / "translations"
    english = json.loads((translations / "en.json").read_text(encoding="utf-8"))
    swedish = json.loads((translations / "sv.json").read_text(encoding="utf-8"))

    def keys(node: dict, prefix: str = "") -> set[str]:
        found: set[str] = set()
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            found.add(path)
            if isinstance(value, dict):
                found |= keys(value, path)
        return found

    assert keys(english) == keys(swedish)


def test_config_flow_errors_are_all_translated() -> None:
    """Every error string the flow can set must have a translation."""
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")

    declared = set(english["config"]["error"])
    for error in ("cannot_connect", "invalid_auth", "unknown"):
        assert error in declared
        assert f'"{error}"' in source


def test_services_yaml_matches_registered_services() -> None:
    """`services.yaml` documents exactly the services the code registers."""
    import yaml

    services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    assert set(services) == {"trigger_sync", "publish_mqtt"}
    for name in services:
        assert name in source


def test_no_blocking_io_in_the_event_loop() -> None:
    """The integration must be fully async: httpx only, never requests."""
    for path in COMPONENT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import requests" not in source, f"{path.name} uses blocking requests"
        assert "urllib.request" not in source, f"{path.name} uses blocking urllib"


def test_component_imports_no_test_only_dependencies() -> None:
    for path in COMPONENT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import pytest" not in source, f"{path.name} imports pytest"
