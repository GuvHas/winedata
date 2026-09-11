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
MAX_TOP_COUNT: Final = 40

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
