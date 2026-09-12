"""Optional MQTT publisher for the Munskänkarna integration.

Publishes the parsed snapshot so consumers outside Home Assistant can use the
same data — a static site generator, another broker client, a second HA
instance.

Two design choices worth stating:

* Messages are **retained**. Retained discovery and state messages are
  re-delivered on connect, which is what makes MQTT-discovered entities survive
  a Home Assistant restart. Pushing state via the REST API instead would vanish
  on every restart until the next poll.
* The bridge is entirely optional and fails soft. MQTT is not a dependency of
  this integration, so if the MQTT integration is not set up, publishing is
  skipped with a log line rather than raising.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DEFAULT_MQTT_TOPIC, DOMAIN, KIND_LABELS, MANUFACTURER
from .coordinator import MunskankarnaCoordinator
from .sensor import wine_summary

_LOGGER = logging.getLogger(__name__)

try:  # The MQTT integration ships with Home Assistant but may be unconfigured.
    from homeassistant.components import mqtt
except ImportError:  # pragma: no cover - only on stripped-down cores
    mqtt = None  # type: ignore[assignment]


async def async_wait_for_mqtt_client(hass: HomeAssistant) -> bool:
    """Return True when MQTT is available and connected.

    Wrapped in its own function so tests can stub it, and so the several
    possible failure modes (no component, not configured, not connected) all
    collapse into one boolean at the call site.
    """
    if mqtt is None:
        return False
    try:
        return await mqtt.async_wait_for_mqtt_client(hass)
    except Exception as err:  # noqa: BLE001 - availability probe must never raise
        _LOGGER.debug("MQTT is not available: %s", err)
        return False


def _device_block(coordinator: MunskankarnaCoordinator) -> dict[str, Any]:
    """Shared device block so discovered entities group like the native ones."""
    return {
        "identifiers": [f"{DOMAIN}_{coordinator.entry.entry_id}"],
        "name": "Munskänkarna",
        "manufacturer": MANUFACTURER,
        "model": "Vinlocus",
        "configuration_url": f"{coordinator.base_url}/sv/vinlocus/",
    }


def build_state_payload(
    coordinator: MunskankarnaCoordinator, kind: str, limit: int | None = None
) -> dict[str, Any]:
    """Build the state message for one tasting kind.

    `state` is a scalar so it can drive a sensor's state directly; the wine
    list travels in the same message and is picked up via
    `json_attributes_topic`.
    """
    result = coordinator.release_for(kind)
    if result is None:
        return {"state": None, "kind": kind, "wines": []}

    release = result["release"]
    cap = limit if limit is not None else coordinator.top_count
    return {
        "state": release["wine_count"],
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "release_id": release["id"],
        "release_title": release["title"],
        "release_date": release["date"],
        "release_url": release["url"],
        "summary": release["summary"],
        "generated_at": coordinator.data["last_success"] if coordinator.data else None,
        "wines": [wine_summary(wine) for wine in result["wines"][:cap]],
    }


def default_base_topic(coordinator: MunskankarnaCoordinator) -> str:
    """The state-topic root used when the caller names none.

    Scoped to the config entry. Two entries on one broker — a second Home
    Assistant instance, or a second entry pointing at a different site — would
    otherwise publish retained state to the same topic, and the later message
    would simply replace the earlier one.

    A caller that passes an explicit topic keeps it verbatim: sharing one is
    then a deliberate choice rather than an accident of the default.
    """
    return f"{DEFAULT_MQTT_TOPIC}/{coordinator.entry.entry_id}"


def build_discovery_config(
    coordinator: MunskankarnaCoordinator, kind: str, base_topic: str
) -> dict[str, Any]:
    """Build a Home Assistant MQTT-discovery config for one tasting kind."""
    entry_id = coordinator.entry.entry_id
    state_topic = f"{base_topic}/{kind}/state"
    return {
        "name": KIND_LABELS.get(kind, kind),
        "unique_id": f"{DOMAIN}_{entry_id}_{kind}",
        # Per entry as well: a shared object_id makes two discovered entities
        # contend for one entity_id, and the loser is silently suffixed.
        "object_id": f"{DOMAIN}_{kind.replace('-', '_')}_{entry_id}",
        "state_topic": state_topic,
        "value_template": "{{ value_json.state }}",
        "json_attributes_topic": state_topic,
        "unit_of_measurement": "viner",
        "icon": "mdi:bottle-wine",
        "device": _device_block(coordinator),
    }


def discovery_topic(
    coordinator: MunskankarnaCoordinator,
    kind: str,
    discovery_prefix: str = "homeassistant",
) -> str:
    """Where Home Assistant looks for a discovered sensor's config.

    Includes the config entry id: discovery messages are retained and keyed by
    topic, so without it a second entry's config overwrites the first's on the
    broker and the two collapse into one entity.
    """
    node_id = f"{DOMAIN}_{coordinator.entry.entry_id}"
    return f"{discovery_prefix}/sensor/{node_id}/{kind.replace('-', '_')}/config"


async def async_publish_snapshot(
    hass: HomeAssistant,
    coordinator: MunskankarnaCoordinator,
    topic: str | None = None,
    retain: bool = True,
    discovery_prefix: str = "homeassistant",
) -> bool:
    """Publish discovery + state for every loaded release.

    Returns True when something was published. Never raises: MQTT is optional,
    and a broker problem must not take the sensors down with it.
    """
    if not coordinator.data or not coordinator.data["releases"]:
        _LOGGER.debug("Nothing to publish: the coordinator holds no data yet")
        return False

    if not await async_wait_for_mqtt_client(hass):
        _LOGGER.warning(
            "MQTT publish requested but the MQTT integration is not available; skipping"
        )
        return False

    base_topic = (topic or default_base_topic(coordinator)).rstrip("/")

    for kind in coordinator.data["releases"]:
        config = build_discovery_config(coordinator, kind, base_topic)
        payload = build_state_payload(coordinator, kind)
        try:
            # Discovery first, so a subscriber that has never seen this entity
            # can interpret the state message that follows.
            await mqtt.async_publish(
                hass,
                discovery_topic(coordinator, kind, discovery_prefix),
                json.dumps(config, ensure_ascii=False),
                retain=retain,
            )
            await mqtt.async_publish(
                hass,
                f"{base_topic}/{kind}/state",
                json.dumps(payload, ensure_ascii=False),
                retain=retain,
            )
        except Exception as err:  # noqa: BLE001 - a broker fault is not fatal here
            _LOGGER.error("Failed to publish %s to MQTT: %s", kind, err)
            return False

    _LOGGER.debug("Published %d release(s) to %s", len(coordinator.data["releases"]), base_topic)
    return True
