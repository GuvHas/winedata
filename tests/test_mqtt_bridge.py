"""Phase 4 — MQTT publisher tests (written before `mqtt_bridge.py`).

The bridge exists so things outside Home Assistant — a static site generator,
another broker client, a second HA instance — can consume the same parsed data.
Messages are published **retained** so subscribers (including HA's own MQTT
discovery) get current state on connect rather than waiting for the next poll.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.munskankarna.const import (
    CONF_KINDS,
    CONF_TOP_COUNT,
    DEFAULT_MQTT_TOPIC,
    DOMAIN,
    KIND_TILLFALLIGT,
)
from custom_components.munskankarna.coordinator import MunskankarnaCoordinator
from custom_components.munskankarna.mqtt_bridge import (
    async_publish_snapshot,
    build_discovery_config,
    build_state_payload,
)
from tests.helpers import build_release, build_wine, create_entry

RELEASE_ID = "tillfalligt-sortiment-11-september-2026"


@pytest.fixture
def mqtt_available():
    """Simulate a configured, connected MQTT broker."""
    with patch(
        "custom_components.munskankarna.mqtt_bridge.async_wait_for_mqtt_client",
        new=AsyncMock(return_value=True),
    ):
        yield


@pytest.fixture
async def coordinator(hass: HomeAssistant) -> MunskankarnaCoordinator:
    """A coordinator holding one loaded release."""
    entry = create_entry(hass, options={CONF_KINDS: [KIND_TILLFALLIGT], CONF_TOP_COUNT: 3})
    wines = [
        build_wine(RELEASE_ID, "Toppvinet", 17.0, value="fynd", price=189.0),
        build_wine(RELEASE_ID, "Nummer Två", 15.5, value="prisvart", price=99.0),
        build_wine(RELEASE_ID, "Nummer Tre", 14.0, value="fynd", price=250.0),
    ]

    async def fake_fetch(self, release_id: str, title: str) -> dict:  # noqa: ANN001
        return {
            "release": build_release(release_id, KIND_TILLFALLIGT, "2026-09-11", wine_count=3),
            "wines": wines,
            "warnings": [],
        }

    with (
        patch.object(
            MunskankarnaCoordinator,
            "_async_fetch_index",
            new=AsyncMock(return_value=[build_release(RELEASE_ID, KIND_TILLFALLIGT, "2026-09-11")]),
        ),
        patch.object(MunskankarnaCoordinator, "_async_fetch_release", new=fake_fetch),
    ):
        coord = MunskankarnaCoordinator(hass, entry)
        # async_refresh(), not async_config_entry_first_refresh(): from HA
        # 2025.x the latter asserts the entry is in SETUP_IN_PROGRESS, and this
        # fixture populates a coordinator directly rather than setting the
        # entry up.
        await coord.async_refresh()
    return coord


def test_state_payload_is_json_serialisable(coordinator: MunskankarnaCoordinator) -> None:
    payload = build_state_payload(coordinator, KIND_TILLFALLIGT)
    encoded = json.dumps(payload)  # must not raise
    assert json.loads(encoded) == payload


def test_state_payload_shape(coordinator: MunskankarnaCoordinator) -> None:
    """`state` is a scalar; the list rides alongside it."""
    payload = build_state_payload(coordinator, KIND_TILLFALLIGT)

    assert payload["state"] == 3
    assert payload["release_id"] == RELEASE_ID
    assert payload["release_date"] == "2026-09-11"
    assert payload["kind"] == KIND_TILLFALLIGT

    wines = payload["wines"]
    assert [w["name"] for w in wines] == ["Toppvinet", "Nummer Två", "Nummer Tre"]
    assert wines[0]["url"] == "https://www.systembolaget.se/produkt/vin/9049001/"
    assert "tasting_note" not in wines[0]


def test_state_payload_respects_top_count(coordinator: MunskankarnaCoordinator) -> None:
    assert len(build_state_payload(coordinator, KIND_TILLFALLIGT, limit=1)["wines"]) == 1


def test_discovery_config_follows_the_ha_convention(
    coordinator: MunskankarnaCoordinator,
) -> None:
    """A valid MQTT-discovery config so subscribers auto-create the entity."""
    config = build_discovery_config(coordinator, KIND_TILLFALLIGT, DEFAULT_MQTT_TOPIC)

    assert config["state_topic"] == f"{DEFAULT_MQTT_TOPIC}/{KIND_TILLFALLIGT}/state"
    assert config["value_template"] == "{{ value_json.state }}"
    assert config["json_attributes_topic"] == config["state_topic"]
    assert config["unique_id"].startswith("munskankarna_")
    assert config["unit_of_measurement"] == "viner"
    # Device block groups discovered entities the same way the native ones are.
    assert config["device"]["manufacturer"] == "Munskänkarna"
    assert config["device"]["identifiers"]


async def test_publish_snapshot_publishes_retained(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator, mqtt_available
) -> None:
    """Retained is what makes discovered entities survive an HA restart."""
    with patch(
        "custom_components.munskankarna.mqtt_bridge.mqtt.async_publish", new=AsyncMock()
    ) as publish:
        await async_publish_snapshot(hass, coordinator, topic=DEFAULT_MQTT_TOPIC)

    topics = [call.args[1] for call in publish.await_args_list]
    assert f"{DEFAULT_MQTT_TOPIC}/{KIND_TILLFALLIGT}/state" in topics
    assert any("/config" in topic for topic in topics)
    assert all(call.kwargs.get("retain") is True for call in publish.await_args_list)


async def test_publish_snapshot_emits_valid_json(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator, mqtt_available
) -> None:
    with patch(
        "custom_components.munskankarna.mqtt_bridge.mqtt.async_publish", new=AsyncMock()
    ) as publish:
        await async_publish_snapshot(hass, coordinator, topic=DEFAULT_MQTT_TOPIC)

    for call in publish.await_args_list:
        decoded = json.loads(call.args[2])
        assert isinstance(decoded, dict)


async def test_publish_snapshot_can_opt_out_of_retain(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator, mqtt_available
) -> None:
    with patch(
        "custom_components.munskankarna.mqtt_bridge.mqtt.async_publish", new=AsyncMock()
    ) as publish:
        await async_publish_snapshot(hass, coordinator, topic=DEFAULT_MQTT_TOPIC, retain=False)
    assert all(call.kwargs.get("retain") is False for call in publish.await_args_list)


async def test_publish_snapshot_without_data_is_a_noop(hass: HomeAssistant) -> None:
    """Never publish an empty snapshot over a good retained message."""
    coord = MunskankarnaCoordinator(hass, create_entry(hass))
    with patch(
        "custom_components.munskankarna.mqtt_bridge.mqtt.async_publish", new=AsyncMock()
    ) as publish:
        await async_publish_snapshot(hass, coord, topic=DEFAULT_MQTT_TOPIC)
    publish.assert_not_awaited()


async def test_publish_is_skipped_when_mqtt_is_not_configured(
    hass: HomeAssistant, coordinator: MunskankarnaCoordinator
) -> None:
    """MQTT is optional: its absence must not raise."""
    with (
        patch(
            "custom_components.munskankarna.mqtt_bridge.async_wait_for_mqtt_client",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "custom_components.munskankarna.mqtt_bridge.mqtt.async_publish", new=AsyncMock()
        ) as publish,
    ):
        await async_publish_snapshot(hass, coordinator, topic=DEFAULT_MQTT_TOPIC)
    publish.assert_not_awaited()


async def test_publish_mqtt_service_is_registered(hass: HomeAssistant) -> None:
    from custom_components.munskankarna import _async_register_services

    _async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "publish_mqtt")
