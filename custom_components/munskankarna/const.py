"""Constants for the Munskänkarna integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "munskankarna"
MANUFACTURER: Final = "Munskänkarna"
DEFAULT_NAME: Final = "Munskänkarna"

# --- Configuration keys -----------------------------------------------------
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_BASE_URL: Final = "base_url"
CONF_SCAN_INTERVAL_HOURS: Final = "scan_interval_hours"
CONF_TOP_COUNT: Final = "top_count"
CONF_HISTORY_COUNT: Final = "history_count"
CONF_KINDS: Final = "kinds"
CONF_MQTT_ENABLED: Final = "mqtt_enabled"
CONF_MQTT_TOPIC: Final = "mqtt_topic"

# --- Defaults ---------------------------------------------------------------
DEFAULT_BASE_URL: Final = "https://www.munskankarna.se"

#: Reviews are published weekly at most, so six hours is ample and keeps the
#: request volume on the source site negligible.
DEFAULT_SCAN_INTERVAL: Final = timedelta(hours=6)
MIN_SCAN_INTERVAL_HOURS: Final = 1
MAX_SCAN_INTERVAL_HOURS: Final = 168

#: How many wines to expose in sensor attributes. Attributes are written to the
#: recorder and broadcast over the websocket on every update, so this is kept
#: small deliberately: 10 wines is roughly 2.3 kB, the full release ~49 kB.
DEFAULT_TOP_COUNT: Final = 10

#: Ceiling on the configurable wine list. Each entry costs roughly 500 bytes of
#: attribute payload, which is recorded and broadcast on every update: 40 wines
#: measured at ~20 kB per update, or tens of megabytes of recorder growth per
#: year per sensor. 25 keeps the worst case near 13 kB; the default of 10 is
#: about 6 kB.
MAX_TOP_COUNT: Final = 25

#: Cap on the release `summary` attribute. Most tastings publish a sentence,
#: but Webbviner publishes a full editorial listing (~1500 characters) into the
#: same field. Attributes are recorded and broadcast on every update, so this
#: is bounded rather than trusted.
MAX_SUMMARY_LENGTH: Final = 280

#: Home Assistant's recorder refuses to store a state whose attributes exceed
#: this many bytes, logging "State attributes for ... exceed maximum size of
#: 16384 bytes" and dropping them: the entity keeps working live while its
#: history is silently lost. Mirrors MAX_STATE_ATTRS_BYTES in
#: homeassistant/components/recorder/db_schema.py, and a test pins the two
#: together. Mirrored rather than imported because the recorder may not be
#: loaded at all, and a sensor platform should not depend on it.
#: Cap on a release title in attributes. The parser takes it straight from the
#: page's <h1> without bounding it, so enough retained releases with long
#: enough titles push the payload past the recorder's limit with every wine
#: already removed. Bounding it is the cheaper half of that fix.
MAX_TITLE_LENGTH: Final = 120

#: Events fired on the Home Assistant bus when new material appears. The
#: integration publishes no notifications of its own: these are the primitive
#: an automation or a blueprint routes wherever the user wants it.
EVENT_WINE_RELEASED: Final = f"{DOMAIN}_wine_released"
EVENT_RELEASE_PUBLISHED: Final = f"{DOMAIN}_release_published"

#: How many announced ids are remembered, so a poll cannot re-announce a week
#: it already covered. Bounded because the record is persisted; correctness
#: does not rest on the size, since nothing older than the newest release
#: already recorded is announced at all.
MAX_SEEN_RELEASES: Final = 200
MAX_SEEN_WINES: Final = 2000

MAX_ATTRIBUTE_BYTES: Final = 16384

#: How much of that an integration may actually spend. Home Assistant injects
#: its own attributes — friendly_name, icon, unit_of_measurement, device_class,
#: attribution — *after* the payload is built, so a design that lands exactly
#: on the limit breaches it in practice. 87.5% leaves 2 KiB of headroom.
ATTRIBUTE_BUDGET: Final = 14336

#: How many releases to retain per tasting type. Retention is count-based
#: rather than age-based on purpose. Measured from the live release index, the
#: categories publish on very different cadences: Tillfälligt sortiment every
#: 7 days, Hitlistan every 14, Lokalt och småskaligt every 28–35, Fast
#: sortiment every 30–62. A fixed 21-day window would therefore hold 3–4
#: releases for one category and *nothing at all* for the others through most
#: of each month — worse than the single-release behaviour it replaced.
#:
#: The trade-off this makes instead: for a monthly category, three releases
#: reach back about three months rather than three weeks.
DEFAULT_HISTORY_COUNT: Final = 3

#: Ceiling on retained releases. Each retained release costs a full attribute
#: payload on every sensor update — at the measured 562 bytes per wine and the
#: default cap of 10 wines, roughly 5.6 kB per release per kind. Six keeps the
#: worst case near 34 kB per kind; the default of three is about 17 kB.
MAX_HISTORY_COUNT: Final = 6

DEFAULT_MQTT_TOPIC: Final = "munskankarna/wines"

# --- Tasting types ----------------------------------------------------------
KIND_TILLFALLIGT: Final = "tillfalligt-sortiment"
KIND_FAST: Final = "fast-sortiment"
KIND_HITLISTAN: Final = "hitlistan"
KIND_LOKALT: Final = "lokalt-och-smaskaligt"
KIND_ORDERVAROR: Final = "bestallningssortimentet"
KIND_WEBBVINER: Final = "webbviner"
KIND_TEMA: Final = "temaprovning"

ALL_KINDS: Final[tuple[str, ...]] = (
    KIND_TILLFALLIGT, KIND_FAST, KIND_HITLISTAN, KIND_LOKALT,
    KIND_ORDERVAROR, KIND_WEBBVINER, KIND_TEMA,
)

#: Tracked by default: the releases that map to wines you can actually buy at
#: Systembolaget this week. Webbviner are excluded because those wines are sold
#: by independent merchants and carry no article number.
DEFAULT_KINDS: Final[tuple[str, ...]] = (
    KIND_TILLFALLIGT, KIND_FAST, KIND_HITLISTAN, KIND_LOKALT,
)

KIND_LABELS: Final[dict[str, str]] = {
    KIND_TILLFALLIGT: "Tillfälligt sortiment",
    KIND_FAST: "Fast sortiment",
    KIND_HITLISTAN: "Hitlista",
    KIND_LOKALT: "Lokalt och småskaligt",
    KIND_ORDERVAROR: "Ordervaror",
    KIND_WEBBVINER: "Webbviner",
    KIND_TEMA: "Temaprovning",
}

# --- Services ---------------------------------------------------------------
SERVICE_TRIGGER_SYNC: Final = "trigger_sync"
SERVICE_PUBLISH_MQTT: Final = "publish_mqtt"

# --- Value verdicts, best first --------------------------------------------
VALUE_FYND: Final = "fynd"
VALUE_ORDER: Final[tuple[str, ...]] = ("fynd", "mer-an-prisvart", "prisvart", "ej-prisvart")
