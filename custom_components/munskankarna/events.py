"""Announce new releases and new wines on the Home Assistant event bus.

Everything downstream — a Companion App alert when a Fynd lands, an LED rack
that reacts to a high score — is the same primitive, so it is built once here
and the routing is left to automations. The integration deliberately owns no
notification path of its own: Home Assistant already has one, and a scraper
that reimplements targeting and quiet hours earns nothing but maintenance.

The hazard is announcing history. A fresh install backfills several releases
per kind from the index, and an upgrade restores them from `.storage`; firing
for those would notify dozens of times for wines published weeks ago. Two
rules keep that from happening, and the second is what makes it robust:

* the first cycle with no record to compare against seeds silently;
* nothing dated before the newest release already recorded is ever announced,
  so raising the retention depth, or an eviction from the bounded record,
  cannot resurrect old news.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import (
    EVENT_RELEASE_PUBLISHED,
    EVENT_WINE_RELEASED,
    MAX_SEEN_RELEASES,
    MAX_SEEN_WINES,
)
from .parser import ParseResult

_LOGGER = logging.getLogger(__name__)


def _remember(record: dict[str, None], key: str, cap: int) -> None:
    """Add a key, evicting the oldest once the record is full.

    A dict preserves insertion order and re-assigning an existing key does not
    move it, so this is a first-in-first-out set in three lines.
    """
    record[key] = None
    while len(record) > cap:
        del record[next(iter(record))]


class Announcer:
    """Remembers what has been announced so nothing is announced twice."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._releases: dict[str, None] = {}
        self._wines: dict[str, None] = {}
        #: The newest release date recorded. Anything older is history.
        self._high_water: str | None = None
        #: False until a record exists to compare against.
        self._armed = False

    @property
    def armed(self) -> bool:
        """Whether a cycle would announce rather than seed."""
        return self._armed

    def restore(self, stored: Any) -> None:
        """Adopt a persisted record.

        Anything absent or malformed leaves this unarmed, which is the safe
        direction: the next cycle seeds silently instead of announcing a
        backfill. That is also what an upgrade from 1.1.3 looks like, since
        its stored payload has no record at all.
        """
        if not isinstance(stored, dict):
            return
        releases, wines = stored.get("releases"), stored.get("wines")
        if not isinstance(releases, list) or not isinstance(wines, list):
            _LOGGER.warning("The stored announcement record had an unexpected shape")
            return

        self._releases = dict.fromkeys(str(r) for r in releases[-MAX_SEEN_RELEASES:])
        self._wines = dict.fromkeys(str(w) for w in wines[-MAX_SEEN_WINES:])
        high_water = stored.get("high_water")
        self._high_water = high_water if isinstance(high_water, str) else None
        self._armed = True

    def as_stored(self) -> dict[str, Any]:
        """The record, in the shape `restore` accepts."""
        return {
            "releases": list(self._releases),
            "wines": list(self._wines),
            "high_water": self._high_water,
        }

    def _is_news(self, date: str | None) -> bool:
        """Whether a release of this date could still be new.

        An unknown date cannot be ruled out, so it falls through to the
        record; a date behind the high-water mark is history whatever the
        record says.
        """
        if date is None or self._high_water is None:
            return True
        return date >= self._high_water

    @callback
    def async_announce(self, history: dict[str, list[ParseResult]]) -> int:
        """Fire for everything new and return how many events went out.

        Imported here rather than at module scope: `sensor` imports the
        package root, which imports the coordinator, which imports this.
        """
        from .sensor import wine_summary

        armed, fired = self._armed, 0

        for kind, results in history.items():
            # Oldest first, so a burst reads in publication order.
            for result in reversed(results):
                release = result["release"]
                release_id, date = release["id"], release.get("date")
                news = armed and self._is_news(date)

                if release_id not in self._releases:
                    if news:
                        self._fire_release(kind, result)
                        fired += 1
                    _remember(self._releases, release_id, MAX_SEEN_RELEASES)

                for wine in result["wines"]:
                    if wine["id"] in self._wines:
                        continue
                    if news:
                        self._fire_wine(kind, result, wine_summary(wine))
                        fired += 1
                    _remember(self._wines, wine["id"], MAX_SEEN_WINES)

                if date is not None and (
                    self._high_water is None or date > self._high_water
                ):
                    self._high_water = date

        if not armed:
            _LOGGER.debug("Seeded the announcement record; nothing announced")
        self._armed = True
        return fired

    @callback
    def _fire_release(self, kind: str, result: ParseResult) -> None:
        release = result["release"]
        self._hass.bus.async_fire(
            EVENT_RELEASE_PUBLISHED,
            {
                "kind": kind,
                "kind_label": release.get("kind_label"),
                "release_id": release["id"],
                "title": release.get("title"),
                "date": release.get("date"),
                "url": release.get("url"),
                "wine_count": len(result["wines"]),
            },
        )

    @callback
    def _fire_wine(
        self, kind: str, result: ParseResult, summary: dict[str, Any]
    ) -> None:
        release = result["release"]
        self._hass.bus.async_fire(
            EVENT_WINE_RELEASED,
            {
                "kind": kind,
                "kind_label": release.get("kind_label"),
                "release_id": release["id"],
                "release_date": release.get("date"),
                **summary,
            },
        )
