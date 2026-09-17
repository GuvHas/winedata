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
from dataclasses import dataclass, field
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


#: One queued announcement: the event type and its payload.
type Event = tuple[str, dict[str, Any]]


@dataclass(slots=True)
class Pending:
    """What one cycle recorded, and everything needed to undo it."""

    events: list[Event] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    wines: list[str] = field(default_factory=list)
    marks_before: dict[str, str] = field(default_factory=dict)


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
        #: The newest release date recorded, per kind. Anything older is
        #: history. Per kind rather than global because the categories publish
        #: on very different schedules: one global mark lets a weekly release
        #: dated later rule out a monthly one that is genuinely new, and the
        #: monthly release's ids are recorded either way, so it would never be
        #: announced at all.
        self._high_water: dict[str, str] = {}
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

        # A record written before the mark was split per kind carries a single
        # string. It is dropped rather than applied to every kind, which would
        # reproduce the cross-category suppression it is being replaced for.
        # The marks rebuild on the first cycle, and the seen set — intact
        # across the upgrade — is what actually keeps history quiet meanwhile.
        high_water = stored.get("high_water")
        self._high_water = (
            {str(k): str(v) for k, v in high_water.items()}
            if isinstance(high_water, dict)
            else {}
        )
        self._armed = True

    def as_stored(self) -> dict[str, Any]:
        """The record, in the shape `restore` accepts."""
        return {
            "releases": list(self._releases),
            "wines": list(self._wines),
            "high_water": self._high_water,
        }

    def _is_news(self, kind: str, date: str | None) -> bool:
        """Whether a release of this kind and date could still be new.

        An unknown date cannot be ruled out, so it falls through to the
        record; a date behind this kind's high-water mark is history whatever
        the record says.
        """
        mark = self._high_water.get(kind)
        if date is None or mark is None:
            return True
        return date >= mark

    @callback
    def async_collect(self, history: dict[str, list[ParseResult]]) -> Pending:
        """Record what is new and return the events, without firing them.

        Recording and dispatch are separate on purpose. The record has to
        reach disk before anything is announced: fire first and a stop — or a
        write failure, which the save helper swallows by design — leaves the
        events sent and the record behind them missing, so a restart announces
        the same week all over again.

        Imported here rather than at module scope: `sensor` imports the
        package root, which imports the coordinator, which imports this.
        """
        from .sensor import wine_summary

        armed = self._armed
        pending = Pending(marks_before=dict(self._high_water))

        for kind, results in history.items():
            # Oldest first, so a burst reads in publication order.
            for result in reversed(results):
                release = result["release"]
                release_id, date = release["id"], release.get("date")
                news = armed and self._is_news(kind, date)

                if release_id not in self._releases:
                    if news:
                        pending.events.append(self._release_event(kind, result))
                    _remember(self._releases, release_id, MAX_SEEN_RELEASES)
                    pending.releases.append(release_id)

                for wine in result["wines"]:
                    if wine["id"] in self._wines:
                        continue
                    if news:
                        pending.events.append(
                            self._wine_event(kind, result, wine_summary(wine))
                        )
                    _remember(self._wines, wine["id"], MAX_SEEN_WINES)
                    pending.wines.append(wine["id"])

                mark = self._high_water.get(kind)
                if date is not None and (mark is None or date > mark):
                    self._high_water[kind] = date

        if not armed:
            _LOGGER.debug("Seeded the announcement record; nothing announced")
        self._armed = True
        return pending

    @callback
    def async_dispatch(self, pending: Pending) -> int:
        """Fire what `async_collect` recorded, once the record is on disk."""
        for event_type, payload in pending.events:
            self._hass.bus.async_fire(event_type, payload)
        return len(pending.events)

    @callback
    def async_rollback(self, pending: Pending) -> None:
        """Forget what was recorded, so the next cycle offers it again.

        Used when the record could not be persisted. Without it the ids stay
        marked seen in memory and the events are never announced at all —
        deferring them to the next cycle is the honest outcome.
        """
        for release_id in pending.releases:
            self._releases.pop(release_id, None)
        for wine_id in pending.wines:
            self._wines.pop(wine_id, None)
        self._high_water = pending.marks_before

    @callback
    def _release_event(self, kind: str, result: ParseResult) -> Event:
        release = result["release"]
        return (
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
    def _wine_event(
        self, kind: str, result: ParseResult, summary: dict[str, Any]
    ) -> Event:
        release = result["release"]
        return (
            EVENT_WINE_RELEASED,
            {
                "kind": kind,
                "kind_label": release.get("kind_label"),
                "release_id": release["id"],
                "release_date": release.get("date"),
                **summary,
            },
        )
