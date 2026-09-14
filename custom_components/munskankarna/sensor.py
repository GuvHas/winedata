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
from typing import Any, Final

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MunskankarnaConfigEntry
from .const import (
    DEFAULT_NAME,
    DOMAIN,
    KIND_LABELS,
    MANUFACTURER,
    MAX_SUMMARY_LENGTH,
    VALUE_FYND,
)
from .coordinator import MunskankarnaCoordinator
from .parser import WineDict

#: Characters that are structural in a Lovelace markdown card. The shipped
#: dashboard interpolates these fields into table cells and link labels, so a
#: "|" splits a row and a "]" closes a link label early — a scraped name of
#: `Vin](javascript:alert(1))[x` otherwise renders as a working script link.
_MARKDOWN_STRUCTURAL: Final = str.maketrans(
    {
        "|": "",
        "[": "",
        "]": "",
        "<": "",
        ">": "",
        "`": "",
        "\n": " ",
        "\r": " ",
        "\t": " ",
    }
)


def markdown_safe(value: str | None) -> str | None:
    """Neutralise markdown structure in scraped free text.

    The characters are removed rather than backslash-escaped: these attributes
    are also read by automations, templates and the MQTT bridge, where escape
    slashes would be noise, and no genuine wine name, producer or region
    contains them. Everything else — accents, ampersands, parentheses,
    apostrophes — is left exactly as published.
    """
    if value is None:
        return None
    return " ".join(value.translate(_MARKDOWN_STRUCTURAL).split()) or None


def truncate(value: str | None, limit: int) -> str | None:
    """Shorten an over-long attribute, making the cut visible to the reader."""
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def release_summary(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    """Per-release metadata with no wine list.

    Deliberately small: this rides on the per-kind sensors, which were already
    sized carefully, so retention must not grow them. The wines for every
    retained release live on the single history sensor instead.
    """
    release = result["release"]
    return {
        "kind": kind,
        "release_id": release["id"],
        "title": markdown_safe(release["title"]),
        "date": release["date"],
        "url": release["url"],
        "wine_count": release["wine_count"],
        "stale": bool(result.get("stale")),
    }


def wine_summary(wine: WineDict) -> dict[str, Any]:
    """Project a wine onto the fields a dashboard card actually renders.

    Tasting notes are excluded on purpose: they are the bulk of the payload and
    would multiply the recorder cost of every update several times over.
    """
    return {
        # Free text is scraped, so it is sanitised on the way out; numbers,
        # enums and URLs are already constrained by the parser.
        "name": markdown_safe(wine.get("name")),
        "full_name": markdown_safe(wine.get("full_name")),
        "vintage": wine.get("vintage"),
        "producer": markdown_safe(wine.get("producer")),
        "score": wine.get("score"),
        "band": wine.get("band"),
        "value": wine.get("value_rating"),
        "price": wine.get("price_sek"),
        "price_per_litre": wine.get("price_per_litre"),
        "volume_ml": wine.get("volume_ml"),
        "color": wine.get("color"),
        "country": markdown_safe(wine.get("country")),
        "region": markdown_safe(wine.get("region")),
        "grapes": [g for g in (markdown_safe(x) for x in wine.get("grapes") or []) if g],
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
        # The state itself is rendered into a markdown heading by the shipped
        # card, so it needs the same neutralisation as the attributes.
        value_fn=lambda c: (markdown_safe(wine["name"]) if (wine := _top_pick(c)) else None),
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
        key="history",
        translation_key="history",
        name="History",
        icon="mdi:history",
        native_unit_of_measurement="provningar",
        # A scalar state; the archive itself is in attributes, as it must be.
        value_fn=lambda c: len(c.retained_releases()),
        attributes_fn=lambda c: {
            "retained_per_kind": c.history_count,
            "releases": [
                {
                    **release_summary(kind, result),
                    "kind_label": KIND_LABELS.get(kind, kind),
                    # Capped per release: this one entity carries the whole
                    # archive, so the cap is what bounds it as retention grows.
                    "wines": [wine_summary(w) for w in result["wines"][: c.top_count]],
                }
                for kind, result in c.retained_releases()
            ],
        },
    ),
    MunskankarnaSensorDescription(
        key="fynd_history",
        translation_key="fynd_history",
        # Named so the entity_id is sensor.munskankarna_fynd_history, matching
        # the key. "Fynd (retained)" slugged to ..._fynd_retained, which the
        # shipped dashboard then silently failed to read.
        name="Fynd history",
        icon="mdi:tag-multiple",
        native_unit_of_measurement="viner",
        value_fn=lambda c: sum(c.fynd_in_history().values()),
        # A count and a per-kind breakdown only. The wines themselves are on
        # the history sensor; duplicating them here would double the cost of
        # the one payload worth watching.
        #
        # `per_kind` stays keyed by slug — that is the stable identifier an
        # automation should match on — and the display labels ride alongside,
        # so a card can show "Tillfälligt sortiment" rather than the slug.
        attributes_fn=lambda c: {
            "per_kind": (counts := c.fynd_in_history()),
            "kind_labels": {kind: KIND_LABELS.get(kind, kind) for kind in counts},
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
    entry: MunskankarnaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors for a config entry."""
    coordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        MunskankarnaGlobalSensor(coordinator, entry, description)
        for description in GLOBAL_SENSORS
    ]
    # One sensor per *configured* tasting type, not merely per type that
    # loaded on the first poll. Platform setup runs once, so keying this on the
    # first update meant a release that was down during startup never got an
    # entity and could not gain one by recovering — only a reload helped.
    # `MunskankarnaReleaseSensor.available` already reports the missing ones as
    # unavailable, which is the behaviour this restores.
    entities.extend(
        MunskankarnaReleaseSensor(coordinator, entry, kind) for kind in coordinator.kinds
    )
    async_add_entities(entities)


class MunskankarnaEntity(CoordinatorEntity[MunskankarnaCoordinator], SensorEntity):
    """Shared device grouping and availability."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MunskankarnaCoordinator, entry: MunskankarnaConfigEntry
    ) -> None:
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
        entry: MunskankarnaConfigEntry,
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
        self, coordinator: MunskankarnaCoordinator, entry: MunskankarnaConfigEntry, kind: str
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
            "release_title": markdown_safe(release["title"]),
            "release_date": release["date"],
            "release_url": release["url"],
            # True when this cycle could not refresh the release and the
            # previous result was carried over. Serving last week's wines is
            # better than blanking the sensor, but it must not be silent.
            "stale": bool(result.get("stale")),
            "summary": truncate(markdown_safe(release["summary"]), MAX_SUMMARY_LENGTH),
            "wines": [
                wine_summary(w) for w in self.coordinator.top_wines(self._kind)
            ],
            # Summaries only — dates and counts, no wine lists. Retention must
            # not grow the sensors that were already sized carefully.
            "history": [
                release_summary(self._kind, r)
                for r in self.coordinator.retained(self._kind)
            ],
        }
