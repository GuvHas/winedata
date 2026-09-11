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


def test_license_file_exists() -> None:
    """HACS expects a license at the repository root."""
    candidates = [REPO_ROOT / name for name in ("LICENSE", "LICENSE.md", "LICENSE.txt")]
    assert any(path.is_file() for path in candidates), "no LICENSE file at the repo root"


def test_repository_is_lean() -> None:
    """No web-app or Node scaffolding should remain alongside the integration."""
    forbidden = [
        "package.json", "package-lock.json", "tsconfig.json", "next.config.ts",
        "postcss.config.mjs", "eslint.config.mjs", "node_modules",
    ]
    present = [name for name in forbidden if (REPO_ROOT / name).exists()]
    assert not present, f"leftover Node/web scaffolding: {present}"

    for directory in ("app", "components", "lib", "seeds", "types"):
        assert not (REPO_ROOT / directory).exists(), f"leftover directory: {directory}/"


def test_hacs_minimum_matches_declared_support() -> None:
    """hacs.json's floor must be a version the integration is actually tested on."""
    import json as _json

    config = _json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert config["homeassistant"] == "2024.12.0"

    workflow = (REPO_ROOT / ".github" / "workflows" / "hacs.yaml").read_text(encoding="utf-8")
    # The floor and the target must both appear in the CI matrix.
    assert "0.13.195" in workflow, "CI does not test the declared minimum"
    assert "0.13.355" in workflow, "CI does not test the 2026.8 target"


def test_release_workflow_publishes_a_real_github_release() -> None:
    """HACS shows a commit unless a published Release exists.

    HACS decides via GitHub's `repos.releases.list` API, which returns Release
    objects only — a bare git tag is not enough — and it skips drafts and
    pre-releases. This workflow is what turns the manifest version into such a
    Release.
    """
    workflow_path = REPO_ROOT / ".github" / "workflows" / "release.yaml"
    assert workflow_path.is_file(), "no release workflow"

    workflow = workflow_path.read_text(encoding="utf-8")
    assert "gh release create" in workflow, "workflow never publishes a Release"

    # Inspect the actual invocation rather than the whole file, so explanatory
    # comments mentioning a flag do not count as using it.
    start = workflow.index("gh release create")
    invocation = ""
    for line in workflow[start:].splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        invocation += " " + stripped
        if not stripped.endswith("\\"):
            break

    assert "--latest" in invocation
    # A draft or pre-release would be ignored by HACS.
    assert "--draft" not in invocation, "a draft Release would be skipped by HACS"
    assert "--prerelease" not in invocation, "a pre-release would be skipped by HACS"
    # Writing tags and releases needs an explicit permission block.
    assert "contents: write" in workflow

    # The version must come from the manifest so the two cannot disagree.
    assert "manifest.json" in workflow


def test_manifest_version_matches_pyproject() -> None:
    """One version, declared in two places that must agree."""
    import json as _json
    import re as _re

    manifest = _json.loads(
        (COMPONENT / "manifest.json").read_text(encoding="utf-8")
    )["version"]
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = _re.search(r'^version\s*=\s*"([^"]+)"', pyproject, _re.M)
    assert declared, "pyproject.toml declares no version"
    assert declared.group(1) == manifest, (
        f"pyproject {declared.group(1)} != manifest {manifest}"
    )
