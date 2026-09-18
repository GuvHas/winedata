"""Sensor platform for the Munskänkarna integration.

Entity design is shaped by two Home Assistant constraints:

* a state is capped at 255 characters, so the wine list can never *be* the
  state — every sensor's state is a small scalar (a count, a name, a date);
* attributes are written to the recorder and broadcast over the websocket on
  every update, so the wine list is trimmed to display fields and capped by the
  `top_count` option (10 wines ≈ 2.3 kB, versus ≈ 49 kB for a full release).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MunskankarnaConfigEntry
from .const import (
    ATTRIBUTE_BUDGET,
    DEFAULT_NAME,
    DOMAIN,
    KIND_LABELS,
    MANUFACTURER,
    MAX_ATTRIBUTE_BYTES,
    MAX_SUMMARY_LENGTH,
    MAX_TITLE_LENGTH,
    VALUE_FYND,
)
from .coordinator import MunskankarnaCoordinator
from .migrate import canonical_object_id
from .parser import WineDict

_LOGGER = logging.getLogger(__name__)

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


def canonical_entity_id(hass: HomeAssistant, name: str) -> str:
    """The entity_id this entity would get under the integration's own name.

    Home Assistant derives an entity_id from `device.name_by_user or
    device.name` plus the entity name, so renaming the device in the UI
    changes the ids of every entity registered *afterwards* — while entities
    registered before it keep the old ones. An instance then ends up with both
    `sensor.munskankarna_hitlista` and `sensor.virtual_munskankarna_history`,
    and the shipped dashboard points at entities that do not exist.

    Pinning the id here keeps it stable whatever the device is called. The
    *display* name still follows the device, so a rename is still visible in
    the UI — only the identifier that automations and dashboards depend on is
    held still. Collisions are handled by async_generate_entity_id, so a
    second config entry gets a suffixed id rather than stealing the first's.

    The name passed in is the entity's own name, so the result matches exactly
    what Home Assistant produces on an unrenamed install — this changes no
    existing entity id.
    """
    return async_generate_entity_id(
        ENTITY_ID_FORMAT, canonical_object_id(name), hass=hass
    )


def _attribute_bytes(payload: dict[str, Any]) -> int:
    """Payload size as the recorder measures it."""
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode())


def largest_fitting(
    build: Callable[[int], dict[str, Any]], most: int
) -> tuple[int, dict[str, Any]]:
    """The biggest item count whose payload still fits `ATTRIBUTE_BUDGET`.

    Binary search rather than a descending scan: payload size grows
    monotonically with the count, so ~5 serialisations settle it instead of up
    to `most`.

    Trimming is what keeps the integration inside the recorder's limit for
    *every* reachable combination of options, not just the shipped defaults.
    """
    if (payload := build(most)) and _attribute_bytes(payload) <= ATTRIBUTE_BUDGET:
        return most, payload

    low, high = 0, most
    while low < high:
        mid = (low + high + 1) // 2
        if _attribute_bytes(build(mid)) <= ATTRIBUTE_BUDGET:
            low = mid
        else:
            high = mid - 1

    payload = build(low)
    if _attribute_bytes(payload) > ATTRIBUTE_BUDGET:
        # Zero items is not automatically small enough: whatever surrounds the
        # list may exceed the budget on its own. Report that rather than
        # returning a payload the recorder will reject.
        return -1, payload
    return low, payload


def history_wine(wine: WineDict) -> dict[str, Any]:
    """A wine as the archive carries it: only what a history card renders.

    Deliberately leaner than `wine_summary` — 8 fields rather than 17, and
    about 240 bytes rather than 587. The archive holds every retained release
    of every tracked type, so per-wine cost is multiplied by two orders of
    magnitude more wines than the current-release sensors carry.

    `url` collapses the product link and the review link into one field: a
    card wants "where do I click", and carrying both doubled the largest
    single field for no gain.
    """
    return {
        "name": markdown_safe(wine.get("name")),
        "vintage": wine.get("vintage"),
        "producer": markdown_safe(wine.get("producer")),
        "score": wine.get("score"),
        "value": wine.get("value_rating"),
        "price": wine.get("price_sek"),
        "price_per_litre": wine.get("price_per_litre"),
        "url": wine.get("product_url") or wine.get("review_url"),
    }


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
        "title": truncate(markdown_safe(release["title"]), MAX_TITLE_LENGTH),
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


def _median(values: list[float]) -> float | None:
    """The middle value, averaging the two middles of an even sample.

    `statistics.median` would do this, but it raises on an empty sample and
    the empty sample is the case that matters most here: a figure over no
    wines has to read unknown, never zero.
    """
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _numbers(coordinator: MunskankarnaCoordinator, field: str) -> list[float]:
    """Every present, numeric value of one field across the current releases.

    The parser allows any measurement to be missing independently, so a wine
    with no score still counts towards the price median and vice versa.
    """
    return [
        float(value)
        for wine in coordinator.all_wines()
        if isinstance(value := wine.get(field), (int, float))
    ]


def _sample(values: list[float]) -> dict[str, Any]:
    """How many wines a trend figure covers, so a reader can weigh it."""
    return {"sample_size": len(values)}


def _rounded_median(values: list[float], places: int) -> float | None:
    median = _median(values)
    return None if median is None else round(median, places)


def _fynd_share(coordinator: MunskankarnaCoordinator) -> float | None:
    """What share of the current releases is a bargain, as a percentage.

    Counted over every wine, not only the rated ones: an unrated wine is
    still a wine that is not a Fynd, so excluding it would inflate the share.
    """
    wines = coordinator.all_wines()
    if not wines:
        return None
    fynd = sum(1 for wine in wines if wine.get("value_rating") == VALUE_FYND)
    return round(fynd / len(wines) * 100, 1)


def _latest_release_date(coordinator: MunskankarnaCoordinator) -> str | None:
    if not coordinator.data:
        return None
    dates = [
        result["release"]["date"]
        for result in coordinator.data["releases"].values()
        if result["release"].get("date")
    ]
    return max(dates) if dates else None


def _latest_release_attributes(
    coordinator: MunskankarnaCoordinator,
) -> dict[str, Any]:
    """One row per currently loaded release.

    Small in normal use, but the title is scraped and was carried verbatim:
    seven tasting types with long enough headings measured 41 kB, over the
    recorder's limit on their own. Bounded and budgeted like the rest.
    """
    items = list(coordinator.data["releases"].items()) if coordinator.data else []

    def build(count: int) -> dict[str, Any]:
        return {
            "releases": [
                {
                    "kind": kind,
                    "title": truncate(
                        markdown_safe(result["release"]["title"]), MAX_TITLE_LENGTH
                    ),
                    "date": result["release"]["date"],
                    "wine_count": result["release"]["wine_count"],
                    "url": result["release"]["url"],
                }
                for kind, result in items[:count]
            ]
        }

    shown, payload = largest_fitting(build, len(items))
    return payload if shown >= 0 else {"releases": []}


def _history_attributes(coordinator: MunskankarnaCoordinator) -> dict[str, Any]:
    """The archive, trimmed to fit the recorder's attribute limit.

    Every retained release keeps its metadata whatever happens — dates, counts
    and links are the timeline, and dropping a week would make the archive lie
    about what was published. Only the wine lists are trimmed, uniformly, so
    the depth is the same for every week and can be stated in one number.

    `wines_per_release` and `truncated` publish that decision rather than
    hiding it: silent truncation is the failure this whole design avoids.
    """
    pairs = coordinator.retained_releases()
    ceiling = coordinator.top_count

    def build(cap: int) -> dict[str, Any]:
        return {
            "retained_per_kind": coordinator.history_count,
            "wines_per_release": cap,
            "truncated": cap < ceiling,
            "releases": [
                {
                    **release_summary(kind, result),
                    "kind_label": KIND_LABELS.get(kind, kind),
                    "wines": [history_wine(w) for w in result["wines"][:cap]],
                }
                for kind, result in pairs
            ],
        }

    cap, payload = largest_fitting(build, ceiling)
    if cap >= 0:
        return payload

    # Even with no wines the payload is too large — enough retained releases
    # with long titles do it. Drop releases, oldest first, until the timeline
    # itself fits, and say how many are missing rather than silently showing a
    # short list.
    def metadata_only(count: int) -> dict[str, Any]:
        return {
            "retained_per_kind": coordinator.history_count,
            "wines_per_release": 0,
            "truncated": True,
            "releases_omitted": len(pairs) - count,
            "releases": [
                {
                    **release_summary(kind, result),
                    "kind_label": KIND_LABELS.get(kind, kind),
                    "wines": [],
                }
                for kind, result in pairs[:count]
            ],
        }

    shown, payload = largest_fitting(metadata_only, len(pairs))
    if shown >= 0:
        return payload

    # A single release still will not fit. Nothing useful can be published in
    # attributes; the archive itself is intact in .storage.
    _LOGGER.warning(
        "The retained archive cannot be published in attributes within Home "
        "Assistant's %d byte limit; the history sensor will carry counts only",
        MAX_ATTRIBUTE_BYTES,
    )
    return {
        "retained_per_kind": coordinator.history_count,
        "wines_per_release": 0,
        "truncated": True,
        "releases_omitted": len(pairs),
        "releases": [],
    }


#: Small scalars the recorder can graph. The wine lists live in attributes,
#: which are not state history, so a chart card has nothing to plot from them.
#: These three change when a release lands and cost a handful of bytes a poll,
#: which is what makes a price-to-score trend possible with no HACS card at
#: all. `measurement` so long-term statistics accumulate.
TREND_SENSORS: tuple[MunskankarnaSensorDescription, ...] = (
    MunskankarnaSensorDescription(
        key="median_score",
        translation_key="median_score",
        name="Median score",
        icon="mdi:chart-bell-curve",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: _rounded_median(_numbers(c, "score"), 1),
        attributes_fn=lambda c: _sample(_numbers(c, "score")),
    ),
    MunskankarnaSensorDescription(
        key="median_price_per_litre",
        translation_key="median_price_per_litre",
        name="Median price per litre",
        icon="mdi:cash",
        native_unit_of_measurement="kr/l",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: _rounded_median(_numbers(c, "price_per_litre"), 2),
        attributes_fn=lambda c: _sample(_numbers(c, "price_per_litre")),
    ),
    MunskankarnaSensorDescription(
        key="fynd_share",
        translation_key="fynd_share",
        name="Fynd share",
        icon="mdi:tag-percent",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_fynd_share,
        attributes_fn=lambda c: _sample(c.all_wines()),
    ),
)

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
        attributes_fn=lambda c: _latest_release_attributes(c),
    ),
    MunskankarnaSensorDescription(
        key="history",
        translation_key="history",
        name="History",
        icon="mdi:history",
        native_unit_of_measurement="provningar",
        # A scalar state; the archive itself is in attributes, as it must be.
        value_fn=lambda c: len(c.retained_releases()),
        attributes_fn=lambda c: _history_attributes(c),
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
        for description in (*GLOBAL_SENSORS, *TREND_SENSORS)
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
        # Pinned so a renamed device cannot change it; see canonical_entity_id.
        self.entity_id = canonical_entity_id(coordinator.hass, description.name)

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
        # Pinned on the label, not the kind slug: the label is what Home
        # Assistant already derived these ids from, so existing entities keep
        # the ids they have (`hitlista`, not `hitlistan`).
        self.entity_id = canonical_entity_id(coordinator.hass, self._attr_name)

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
        wines = self.coordinator.top_wines(self._kind)

        def build(cap: int) -> dict[str, Any]:
            return {
                "kind": self._kind,
                "kind_label": KIND_LABELS.get(self._kind, self._kind),
                "release_id": release["id"],
                "release_title": markdown_safe(release["title"]),
                "release_date": release["date"],
                "release_url": release["url"],
                # True when this cycle could not refresh the release and the
                # previous result was carried over. Serving last week's wines
                # is better than blanking the sensor, but it must not be silent.
                "stale": bool(result.get("stale")),
                "summary": truncate(
                    markdown_safe(release["summary"]), MAX_SUMMARY_LENGTH
                ),
                "wines_shown": cap,
                "truncated": cap < len(wines),
                "wines": [wine_summary(w) for w in wines[:cap]],
                # Summaries only — dates and counts, no wine lists. Retention
                # must not grow the sensors that were already sized carefully.
                "history": [
                    release_summary(self._kind, r)
                    for r in self.coordinator.retained(self._kind)
                ],
            }

        # At the option ceiling of 25 wines this payload measured 15.8 kB of
        # the recorder's 16 kB limit before retention added the history
        # summaries — close enough that one long wine name breached it. Trim
        # rather than trust the arithmetic.
        cap, payload = largest_fitting(build, len(wines))
        if cap >= 0:
            return payload
        # Even with no wines this is too large — an over-long release title or
        # summary. Publish the identity of the release and nothing else.
        return {
            "kind": self._kind,
            "kind_label": KIND_LABELS.get(self._kind, self._kind),
            "release_id": release["id"],
            "release_date": release["date"],
            "stale": bool(result.get("stale")),
            "wines_shown": 0,
            "truncated": True,
            "wines": [],
            "history": [],
        }
