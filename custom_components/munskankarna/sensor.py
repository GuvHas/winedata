"""Sensor platform for the Munskänkarna integration.

Entity design is shaped by two Home Assistant constraints:

* a state is capped at 255 characters, so the wine list can never *be* the
  state — every sensor's state is a small scalar (a count, a name, a date);
* attributes are written to the recorder and broadcast over the websocket on
  every update, so the wine list is trimmed to display fields and capped by the
  `top_count` option (10 wines ≈ 2.3 kB, versus ≈ 49 kB for a full release).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_NAME,
    DOMAIN,
    KIND_LABELS,
    MANUFACTURER,
    VALUE_FYND,
)
from .coordinator import MunskankarnaCoordinator
from .parser import WineDict


def wine_summary(wine: WineDict) -> dict[str, Any]:
    """Project a wine onto the fields a dashboard card actually renders.

    Tasting notes are excluded on purpose: they are the bulk of the payload and
    would multiply the recorder cost of every update several times over.
    """
    return {
        "name": wine.get("name"),
        "full_name": wine.get("full_name"),
        "vintage": wine.get("vintage"),
        "producer": wine.get("producer"),
        "score": wine.get("score"),
        "band": wine.get("band"),
        "value": wine.get("value_rating"),
        "price": wine.get("price_sek"),
        "price_per_litre": wine.get("price_per_litre"),
        "volume_ml": wine.get("volume_ml"),
        "color": wine.get("color"),
        "country": wine.get("country"),
        "region": wine.get("region"),
        "grapes": wine.get("grapes"),
        "article_number": wine.get("article_number"),
        "url": wine.get("product_url"),
        "review_url": wine.get("review_url"),
    }


@dataclass(frozen=True, kw_only=True)
class MunskankarnaSensorDescription(SensorEntityDescription):
    """Describes a sensor built from coordinator data."""

    value_fn: Callable[[MunskankarnaCoordinator], Any]
    attributes_fn: Callable[[MunskankarnaCoordinator], dict[str, Any]]


def _top_pick(coordinator: MunskankarnaCoordinator) -> WineDict | None:
    wines = coordinator.all_wines()
    return wines[0] if wines else None


def _fynd_wines(coordinator: MunskankarnaCoordinator) -> list[WineDict]:
    return [w for w in coordinator.all_wines() if w.get("value_rating") == VALUE_FYND]


def _latest_release_date(coordinator: MunskankarnaCoordinator) -> str | None:
    if not coordinator.data:
        return None
    dates = [
        result["release"]["date"]
        for result in coordinator.data["releases"].values()
        if result["release"].get("date")
    ]
    return max(dates) if dates else None


GLOBAL_SENSORS: tuple[MunskankarnaSensorDescription, ...] = (
    MunskankarnaSensorDescription(
        key="top_pick",
        translation_key="top_pick",
        name="Top pick",
        icon="mdi:trophy",
        value_fn=lambda c: (wine["name"] if (wine := _top_pick(c)) else None),
        attributes_fn=lambda c: (
            wine_summary(wine) if (wine := _top_pick(c)) else {}
        ),
    ),
    MunskankarnaSensorDescription(
        key="fynd",
        translation_key="fynd",
        name="Fynd",
        icon="mdi:tag-heart",
        native_unit_of_measurement="viner",
        value_fn=lambda c: len(_fynd_wines(c)),
        attributes_fn=lambda c: {
            "wines": [wine_summary(w) for w in _fynd_wines(c)[: c.top_count]]
        },
    ),
    MunskankarnaSensorDescription(
        key="latest_release",
        translation_key="latest_release",
        name="Latest release",
        icon="mdi:calendar-star",
        value_fn=_latest_release_date,
        attributes_fn=lambda c: {
            "releases": [
                {
                    "kind": kind,
                    "title": result["release"]["title"],
                    "date": result["release"]["date"],
                    "wine_count": result["release"]["wine_count"],
                    "url": result["release"]["url"],
                }
                for kind, result in (c.data["releases"].items() if c.data else [])
            ]
        },
    ),
    MunskankarnaSensorDescription(
        key="total_wines",
        translation_key="total_wines",
        name="Wines tested",
        icon="mdi:glass-wine",
        native_unit_of_measurement="viner",
        value_fn=lambda c: sum(
            result["release"]["wine_count"]
            for result in (c.data["releases"].values() if c.data else [])
        ),
        attributes_fn=lambda c: {"warnings": (c.data or {}).get("warnings", [])[:5]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors for a config entry."""
    coordinator: MunskankarnaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        MunskankarnaGlobalSensor(coordinator, entry, description)
        for description in GLOBAL_SENSORS
    ]
    # One sensor per tasting type that actually loaded.
    entities.extend(
        MunskankarnaReleaseSensor(coordinator, entry, kind)
        for kind in (coordinator.data["releases"] if coordinator.data else {})
    )
    async_add_entities(entities)


class MunskankarnaEntity(CoordinatorEntity[MunskankarnaCoordinator], SensorEntity):
    """Shared device grouping and availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MunskankarnaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model="Vinlocus",
            configuration_url=f"{coordinator.base_url}/sv/vinlocus/",
            entry_type=None,
        )


class MunskankarnaGlobalSensor(MunskankarnaEntity):
    """A sensor summarising every tracked release."""

    entity_description: MunskankarnaSensorDescription

    def __init__(
        self,
        coordinator: MunskankarnaCoordinator,
        entry: ConfigEntry,
        description: MunskankarnaSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attributes_fn(self.coordinator)


class MunskankarnaReleaseSensor(MunskankarnaEntity):
    """One sensor per tasting type, tracking its current release."""

    _attr_icon = "mdi:bottle-wine"
    _attr_native_unit_of_measurement = "viner"

    def __init__(
        self, coordinator: MunskankarnaCoordinator, entry: ConfigEntry, kind: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._kind = kind
        self._attr_unique_id = f"{entry.entry_id}_release_{kind}"
        self._attr_name = KIND_LABELS.get(kind, kind)

    @property
    def available(self) -> bool:
        """Unavailable when this particular release failed to load."""
        return super().available and self.coordinator.release_for(self._kind) is not None

    @property
    def native_value(self) -> int | None:
        """The number of wines in the current release."""
        result = self.coordinator.release_for(self._kind)
        return result["release"]["wine_count"] if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        result = self.coordinator.release_for(self._kind)
        if result is None:
            return {}
        release = result["release"]
        return {
            "kind": self._kind,
            "kind_label": KIND_LABELS.get(self._kind, self._kind),
            "release_id": release["id"],
            "release_title": release["title"],
            "release_date": release["date"],
            "release_url": release["url"],
            "summary": release["summary"],
            "wines": [
                wine_summary(w) for w in self.coordinator.top_wines(self._kind)
            ],
        }
