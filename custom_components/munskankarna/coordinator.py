"""Data update coordinator for the Munskänkarna integration.

Fetches the release index once per cycle, keeps only the newest release of each
configured tasting type, and sorts each release's wines best-first. Sensors are
then thin projections over that prepared shape.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Final, TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.ssl import get_default_context

from .api import InvalidAuth, MunskankarnaClient, MunskankarnaError, RateLimited
from .const import (
    CONF_BASE_URL,
    CONF_HISTORY_COUNT,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TOP_COUNT,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_HISTORY_COUNT,
    DEFAULT_KINDS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOP_COUNT,
    DOMAIN,
    MAX_HISTORY_COUNT,
    MAX_TOP_COUNT,
    VALUE_FYND,
    VALUE_ORDER,
)
from .events import Announcer
from .parser import ParseResult, ReleaseDict, WineDict

_LOGGER = logging.getLogger(__name__)

#: Unrated wines sort last, whichever direction is being applied.
_UNRATED_RANK = len(VALUE_ORDER) + 1

#: Cooldown applied when a 429 arrives with no usable Retry-After. Being told
#: to slow down without being told for how long still has to mean something.
_DEFAULT_COOLDOWN: Final = 900.0

#: Ceiling on an honoured Retry-After. A misconfigured or hostile header could
#: otherwise park the integration for days with no way back but a reload.
_MAX_COOLDOWN: Final = 6 * 3600.0

#: Schema version of the on-disk history cache.
STORAGE_VERSION: Final = 1


def _fingerprint(payload: dict[str, Any]) -> str:
    """A stable digest of exactly what would be written.

    Serialising 257 kB costs a millisecond or two once every six hours, which
    buys skipping the write itself in the ~99% of cycles that change nothing.
    Keys are sorted so a reordering is not mistaken for a change.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.blake2b(encoded, digest_size=16).hexdigest()


def history_storage_key(entry_id: str) -> str:
    """Where one config entry's retained releases live under `.storage`.

    Per entry, so two entries pointing at different sites cannot share or
    overwrite a cache — the same reasoning that scopes the MQTT topics.
    """
    return f"{DOMAIN}.history_{entry_id}"




class CoordinatorData(TypedDict):
    """Everything the sensors need, keyed by tasting kind."""

    releases: dict[str, ParseResult]
    #: The retained releases per kind, newest first. `releases` is the head of
    #: each of these lists; it is kept as its own key so every existing sensor,
    #: template and MQTT consumer keeps working unchanged.
    history: dict[str, list[ParseResult]]
    warnings: list[str]
    last_success: str


def _value_rank(wine: WineDict) -> int:
    rating = wine.get("value_rating")
    return VALUE_ORDER.index(rating) if rating in VALUE_ORDER else _UNRATED_RANK


def sort_wines(wines: list[WineDict]) -> list[WineDict]:
    """Best first: highest score, then Munskänkarna's own value verdict, then price.

    The panel judged value in context, so their verdict outranks any ratio we
    could compute after the fact.
    """
    return sorted(
        wines,
        key=lambda w: (
            -(w.get("score") or -1),
            _value_rank(w),
            w.get("price_sek") if w.get("price_sek") is not None else float("inf"),
            w.get("name") or "",
        ),
    )


def newest_per_kind(releases: list[ReleaseDict], kinds: list[str]) -> dict[str, ReleaseDict]:
    """Pick the most recent release for each requested tasting type.

    Undated releases are only used when a kind has nothing dated, so a stray
    missing date cannot mask the current week's release.
    """
    chosen: dict[str, ReleaseDict] = {}
    for release in releases:
        kind = release.get("kind")
        if kind not in kinds:
            continue
        current = chosen.get(kind)
        if current is None or _release_sort_key(release) > _release_sort_key(current):
            chosen[kind] = release
    return chosen


def merge_history(
    existing: list[ParseResult], incoming: list[ParseResult], limit: int
) -> list[ParseResult]:
    """Fold this cycle's results into the retained ones, newest first.

    Keyed by release id, so re-polling the current release updates it in place
    rather than stacking a second copy. Incoming wins: it is the fresher read.

    Retention is count-based (see `DEFAULT_HISTORY_COUNT` for why), and the
    limit is floored at one — however the option is set, losing the current
    release is never an acceptable outcome.

    Returns a new list; the caller's snapshot is shared and must not be mutated.
    """
    by_id: dict[str, ParseResult] = {r["release"]["id"]: r for r in existing}
    by_id.update({r["release"]["id"]: r for r in incoming})

    ordered = sorted(
        by_id.values(), key=lambda r: _release_sort_key(r["release"]), reverse=True
    )
    return ordered[: max(1, limit)]


def newest_releases_per_kind(
    releases: list[ReleaseDict], kinds: list[str], limit: int
) -> dict[str, list[ReleaseDict]]:
    """The most recent `limit` releases for each requested tasting type.

    Same ordering rule as `newest_per_kind`: dated releases outrank undated
    ones, so a stray missing date cannot mask the current week.
    """
    grouped: dict[str, list[ReleaseDict]] = {}
    for release in releases:
        kind = release.get("kind")
        if kind not in kinds:
            continue
        grouped.setdefault(kind, []).append(release)

    return {
        kind: sorted(items, key=_release_sort_key, reverse=True)[: max(1, limit)]
        for kind, items in grouped.items()
    }


def _release_sort_key(release: ReleaseDict) -> tuple[int, str]:
    """Dated releases outrank undated ones; otherwise compare ISO dates."""
    date = release.get("date")
    return (1, date) if date else (0, "")


class MunskankarnaCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Polls Munskänkarna and prepares data for the sensors."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise with the interval configured in the options flow."""
        self.entry = entry
        #: The client for the in-flight update cycle, if any.
        self._api: MunskankarnaClient | None = None
        #: Monotonic deadline before which no request may be made, set from a
        #: 429's Retry-After. Monotonic rather than wall clock so a system
        #: clock change cannot extend or cancel it.
        self._rate_limited_until: float = 0.0
        #: The retained releases, persisted outside the recorder. `.storage` is
        #: a document cache: no recorder rows, no websocket traffic, and a
        #: restart does not re-read a dozen unchanged release pages.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, history_storage_key(entry.entry_id)
        )
        #: History read back from disk, used until the first cycle replaces it.
        self._restored_history: dict[str, list[ParseResult]] = {}
        #: Remembers what has already gone out on the bus, so polling every
        #: six hours does not re-announce the same week all week.
        self._announcer = Announcer(hass)
        #: Digest of the payload last handed to the store, so an unchanged
        #: cycle costs no write at all.
        self._persisted: str | None = None
        hours = entry.options.get(CONF_SCAN_INTERVAL_HOURS)
        interval = timedelta(hours=hours) if hours else DEFAULT_SCAN_INTERVAL

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
            config_entry=entry,
        )

    # -- configuration -----------------------------------------------------

    @property
    def base_url(self) -> str:
        return self.entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL

    @property
    def kinds(self) -> list[str]:
        return list(self.entry.options.get(CONF_KINDS) or DEFAULT_KINDS)

    @property
    def top_count(self) -> int:
        """How many wines each sensor exposes in its attributes.

        Clamped rather than trusted: the options schema only constrains a form
        being submitted, so an entry saved under an older, higher ceiling would
        otherwise keep recording that many wines forever.
        """
        try:
            value = int(self.entry.options.get(CONF_TOP_COUNT) or DEFAULT_TOP_COUNT)
        except (TypeError, ValueError):
            return DEFAULT_TOP_COUNT
        if value < 1:
            return DEFAULT_TOP_COUNT
        return min(value, MAX_TOP_COUNT)

    @property
    def history_count(self) -> int:
        """How many releases to retain per tasting type.

        Clamped like `top_count`: the options schema only constrains a form
        being submitted, and each retained release costs a full attribute
        payload on every update.
        """
        try:
            value = int(self.entry.options.get(CONF_HISTORY_COUNT) or DEFAULT_HISTORY_COUNT)
        except (TypeError, ValueError):
            return DEFAULT_HISTORY_COUNT
        if value < 1:
            return DEFAULT_HISTORY_COUNT
        return min(value, MAX_HISTORY_COUNT)

    def _client(self) -> MunskankarnaClient:
        """Build an API client for one update cycle.

        Home Assistant's cached SSL context is passed in rather than letting
        httpx create one: building a context reads the CA bundle from disk,
        which is a blocking call the event loop would flag.
        """
        return MunskankarnaClient(
            self.base_url,
            self.entry.data.get(CONF_USERNAME),
            self.entry.data.get(CONF_PASSWORD),
            verify=get_default_context(),
        )

    # -- persisted history -------------------------------------------------

    async def async_load_history(self) -> None:
        """Restore the retained releases from disk before the first poll.

        A cache is an optimisation, never a dependency: anything unreadable or
        the wrong shape is discarded and the cycle simply refetches.
        """
        try:
            stored = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a bad cache must never block setup
            _LOGGER.warning("Could not read the stored history; starting empty",
                            exc_info=True)
            return

        # Restored before the history is validated: a cache whose shape went
        # bad must not also wipe the record of what was already announced,
        # which would make the next cycle announce the refetch.
        self._announcer.restore((stored or {}).get("seen"))

        history = (stored or {}).get("history")
        if not isinstance(history, dict):
            if history is not None:
                _LOGGER.warning("Stored history had an unexpected shape; ignoring it")
            return

        restored = {
            kind: releases
            for kind, releases in history.items()
            if isinstance(releases, list) and releases
        }
        self._restored_history = restored
        # What a cycle that changes nothing would write. Seeding it here means
        # a restart that finds the site unchanged writes nothing at all.
        self._persisted = _fingerprint(
            {"history": restored, "seen": self._announcer.as_stored()}
        )
        if restored:
            _LOGGER.debug(
                "Restored %d retained release(s) from storage",
                sum(len(v) for v in restored.values()),
            )

    async def _async_save_history(self, history: dict[str, list[ParseResult]]) -> bool:
        """Persist the retained releases if they changed; say whether they are on disk.

        Published release pages are immutable and only the newest per kind is
        re-read, so most cycles produce a byte-identical file. Writing it
        anyway cost about 1 MB a day at the defaults, usually onto an SD card.

        The digest covers the announcement record as well as the history, not
        just the history: the cycle that seeds the record leaves the releases
        untouched, and skipping that write would make every restart seed again
        and re-announce the current week.
        """
        payload = {"history": history, "seen": self._announcer.as_stored()}
        if (digest := _fingerprint(payload)) == self._persisted:
            _LOGGER.debug("History unchanged since the last write; not rewriting")
            # Unchanged means what is on disk already says this, so the caller
            # can announce: there is nothing here a restart would lose.
            return True

        try:
            # Written rather than deferred: async_save already serialises and
            # writes in an executor, so it costs the event loop nothing, and a
            # delay would leave a window where a hard stop loses the cycle for
            # no real gain once the unchanged cycles are skipped outright.
            await self._store.async_save(payload)
        except Exception:  # noqa: BLE001 - failing to cache is not failing to update
            _LOGGER.warning("Could not persist the history cache", exc_info=True)
            return False
        self._persisted = digest
        return True

    async def async_remove_storage(self) -> None:
        """Delete the cache when the config entry is removed."""
        await self._store.async_remove()

    # -- rate-limit cooldown -----------------------------------------------

    @property
    def cooldown_remaining(self) -> float:
        """Seconds left before a request is permitted again; 0 when clear."""
        return max(0.0, self._rate_limited_until - time.monotonic())

    def _begin_cooldown(self, retry_after: float | None) -> float:
        """Record when we may talk to the site again, and return that span."""
        seconds = retry_after if retry_after and retry_after > 0 else _DEFAULT_COOLDOWN
        seconds = min(seconds, _MAX_COOLDOWN)
        self._rate_limited_until = time.monotonic() + seconds
        _LOGGER.warning(
            "Munskänkarna asked us to slow down; no further requests for %.0f minutes",
            seconds / 60,
        )
        return seconds

    def _active_client(self) -> MunskankarnaClient:
        """The client for the update cycle currently in flight."""
        if self._api is None:
            raise RuntimeError("No active client; call inside _async_update_data")
        return self._api

    # -- fetch seams (patched in tests) ------------------------------------
    #
    # These reuse the cycle's single client rather than opening their own, so
    # one poll means one client, one SSL setup and one login regardless of how
    # many releases are fetched.

    async def _async_fetch_index(self) -> list[ReleaseDict]:
        """Fetch the release index."""
        return await self._active_client().async_fetch_releases()

    async def _async_fetch_release(self, release_id: str, title: str) -> ParseResult:
        """Fetch one release page."""
        return await self._active_client().async_fetch_release(release_id, title)

    def _async_more_links(self) -> dict[str, str]:
        """Each tasting type's own page, as the last index fetch advertised."""
        return getattr(self._api, "more_links", {}) or {}

    async def _async_fetch_more(self, kind: str, url: str) -> list[ReleaseDict]:
        """Fetch one tasting type's own page."""
        return await self._active_client().async_fetch_kind_index(url)

    # -- update ------------------------------------------------------------

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch the current release for each configured tasting type.

        One client and one login serve the whole cycle.
        """
        # Checked before a client is built, so a cooldown costs no connection,
        # no SSL setup and above all no login POST. This is enforced for a
        # manual refresh exactly as for a scheduled one: pressing the button
        # is not a reason to ignore the site asking for quiet.
        if (remaining := self.cooldown_remaining) > 0:
            raise UpdateFailed(
                "Rate limited by Munskänkarna; not requesting again for another "
                f"{remaining / 60:.0f} min"
            )

        async with self._client() as client:
            self._api = client
            try:
                # A rejected login is fatal for the cycle; anonymous access
                # returns False here and simply carries on.
                await client.async_login()
                return await self._async_collect()
            except RateLimited as err:
                # Reachable from the login request itself, which is a request
                # like any other and must start the cooldown too.
                self._begin_cooldown(err.retry_after)
                raise UpdateFailed(
                    f"Rate limited by Munskänkarna: {err}. Consider a longer update interval."
                ) from err
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed(
                    "Munskänkarna rejected the configured credentials"
                ) from err
            finally:
                self._api = None

    async def _async_deepen_index(
        self, index: list[ReleaseDict], warnings: list[str]
    ) -> list[ReleaseDict]:
        """Top up any tracked kind the index lists fewer of than configured.

        The index carries five releases per tasting type and then a link to
        that type's own page, while the retention depth goes to six — so the
        sixth release exists and was simply never seen. Following costs a
        request, so it happens only for a kind that is actually short, which
        at the default depth of three is none of them.

        A kind still short afterwards is reported rather than quietly
        truncated. That also covers the per-type page not parsing: the depth
        the user asked for is not met either way, and they should know.
        """
        more_links = self._async_more_links()
        # Copied, not appended to: the caller's list is not ours to grow, and
        # a reused index would otherwise accumulate across cycles.
        deepened = list(index)

        for kind in self.kinds:
            found = {r["id"] for r in deepened if r["kind"] == kind}
            if len(found) >= self.history_count:
                continue

            if url := more_links.get(kind):
                try:
                    extra = await self._async_fetch_more(kind, url)
                except RateLimited:
                    # Deepening history is never worth walking into a limit.
                    raise
                except MunskankarnaError as err:
                    _LOGGER.warning("Could not read the page for %s: %s", kind, err)
                    warnings.append(f"{kind}: could not read the tasting type's page: {err}")
                    extra = []
                for release in extra:
                    if release["kind"] == kind and release["id"] not in found:
                        deepened.append(release)
                        found.add(release["id"])

            if len(found) < self.history_count:
                warnings.append(
                    f"{kind}: Munskänkarna lists {len(found)} release(s), fewer than "
                    f"the {self.history_count} configured"
                )

        return deepened

    async def _async_collect(self) -> CoordinatorData:
        """Fetch the index and every configured release using the active client."""
        try:
            index = await self._async_fetch_index()
        except RateLimited as err:
            self._begin_cooldown(err.retry_after)
            raise UpdateFailed(
                f"Rate limited by Munskänkarna: {err}. Consider a longer update interval."
            ) from err
        except MunskankarnaError as err:
            raise UpdateFailed(f"Could not fetch the Munskänkarna release index: {err}") from err

        index_warnings: list[str] = []
        index = await self._async_deepen_index(index, index_warnings)

        wanted = newest_releases_per_kind(index, self.kinds, self.history_count)
        if not wanted:
            raise UpdateFailed(
                "No releases matched the configured tasting types; "
                "the site layout may have changed"
            )

        releases: dict[str, ParseResult] = {}
        history: dict[str, list[ParseResult]] = {}
        warnings: list[str] = list(index_warnings)
        #: What the previous cycle published, so a kind that cannot be
        #: refreshed keeps what it had rather than vanishing from the snapshot.
        previous: dict[str, ParseResult] = (self.data or {}).get("releases", {})
        previous_history: dict[str, list[ParseResult]] = (
            (self.data or {}).get("history") or self._restored_history
        )

        rate_limited = False

        for kind, candidates in wanted.items():
            if rate_limited:
                break

            cached = {r["release"]["id"]: r for r in previous_history.get(kind, [])}
            collected: list[ParseResult] = []
            #: Whether a page was actually read for this kind this cycle, as
            #: opposed to being served from the cache.
            fetched_any = False
            #: Whether the *current* release — candidate 0 — was among them.
            #: Tracked separately because backfilling an older candidate is not
            #: evidence that this week's release loaded.
            current_refreshed = False

            for position, release in enumerate(candidates):
                release_id = release["id"]

                # A published release page does not change; only the newest one
                # can still gain corrections. Re-reading the rest every cycle
                # would triple the request count against a small volunteer-run
                # site for no new information.
                if position > 0 and release_id in cached:
                    collected.append(cached[release_id])
                    continue

                try:
                    result = await self._async_fetch_release(release_id, release["title"])
                except RateLimited as err:
                    # Stop the cycle rather than keep requesting from a server
                    # that has just asked us to back off. Whatever loaded before
                    # the limit is still published — but the cooldown still
                    # applies, so the next poll does not walk straight back in.
                    self._begin_cooldown(err.retry_after)
                    warnings.append(f"{release_id}: {err}")
                    _LOGGER.warning(
                        "Rate limited fetching %s; abandoning the rest of this update",
                        release_id,
                    )
                    rate_limited = True
                    break
                except MunskankarnaError as err:
                    # One bad release must not blank the other sensors.
                    warnings.append(f"{release_id}: {err}")
                    _LOGGER.warning("Could not fetch release %s: %s", release_id, err)
                    continue

                if not result.get("page_valid", True):
                    # An unrecognised page parses to zero wines just like a quiet
                    # week does. Accepting it would replace good cached data with
                    # a 0-wine state; skipping it leaves the previous data in
                    # place and, if nothing else loaded, fails the update below.
                    warnings.append(
                        f"{release_id}: the page was not recognised as a release page"
                    )
                    _LOGGER.warning(
                        "Release %s did not look like a release page; keeping the "
                        "previous data for this tasting type",
                        release_id,
                    )
                    continue

                result["wines"] = sort_wines(result["wines"])
                warnings.extend(result.get("warnings") or [])
                collected.append(result)
                fetched_any = True
                if position == 0:
                    current_refreshed = True

            merged = merge_history(
                previous_history.get(kind, []), collected, self.history_count
            )
            if merged:
                history[kind] = merged
                if current_refreshed:
                    # The current release is the newest retained one.
                    releases[kind] = merged[0]
                elif fetched_any:
                    # Older candidates loaded but this week's page did not. The
                    # newest release we hold is still the best answer, but it is
                    # not freshly confirmed — publishing it unflagged would show
                    # last week's wines as this week's.
                    releases[kind] = {**merged[0], "stale": True}

        # Nothing refreshed this cycle: fail, so Home Assistant keeps the whole
        # previous snapshot rather than republishing it as if it were current.
        if not releases:
            raise UpdateFailed(
                "Every configured release failed to load: " + "; ".join(warnings[:3])
            )

        # At least one kind refreshed. Any kind that did not — a fetch error, an
        # unrecognised page, or one the rate-limit `break` never reached — keeps
        # its previous result. Without this the returned snapshot replaces the
        # coordinator's entire data, so a partial failure silently discarded the
        # failed kind's wines and took its sensor offline.
        for kind in wanted:
            if kind in releases:
                continue
            if (carried := previous.get(kind)) is not None:
                releases[kind] = {**carried, "stale": True}
            if kind not in history and (carried_history := previous_history.get(kind)):
                history[kind] = carried_history

        # Recorded, persisted, then announced — in that order. Firing first
        # would leave the events sent and the record behind them missing if
        # the write failed or the process stopped, and a restart would then
        # announce the same week again. A write that fails rolls the record
        # back so the next cycle offers the same wines rather than swallowing
        # them.
        pending = self._announcer.async_collect(history)
        if await self._async_save_history(history):
            self._announcer.async_dispatch(pending)
        else:
            self._announcer.async_rollback(pending)

        return CoordinatorData(
            releases=releases,
            history=history,
            warnings=warnings,
            last_success=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    # -- projections used by sensors and the MQTT bridge -------------------

    def release_for(self, kind: str) -> ParseResult | None:
        """Return the prepared result for a tasting kind, if it loaded."""
        if not self.data:
            return None
        return self.data["releases"].get(kind)

    def top_wines(self, kind: str, limit: int | None = None) -> list[WineDict]:
        """The best wines of a release, capped for attribute size."""
        result = self.release_for(kind)
        if result is None:
            return []
        return result["wines"][: limit if limit is not None else self.top_count]

    def all_wines(self) -> list[WineDict]:
        """Every wine across every loaded release, best first."""
        if not self.data:
            return []
        wines: list[WineDict] = []
        for result in self.data["releases"].values():
            wines.extend(result["wines"])
        return sort_wines(wines)

    def retained(self, kind: str) -> list[ParseResult]:
        """Every retained release for a tasting kind, newest first."""
        if not self.data:
            return []
        return self.data.get("history", {}).get(kind, [])

    def retained_releases(self) -> list[tuple[str, ParseResult]]:
        """(kind, release) for every retained release, newest first across kinds.

        Flattened here rather than in the sensor: a dashboard reads one list in
        publication order, which is not the same as "grouped by kind".
        """
        if not self.data:
            return []
        pairs = [
            (kind, result)
            for kind, results in self.data.get("history", {}).items()
            for result in results
        ]
        pairs.sort(key=lambda pair: _release_sort_key(pair[1]["release"]), reverse=True)
        return pairs

    def fynd_in_history(self) -> dict[str, int]:
        """How many Fynd each kind published across its retained releases."""
        counts: dict[str, int] = {}
        for kind, result in self.retained_releases():
            counts[kind] = counts.get(kind, 0) + sum(
                1 for wine in result["wines"] if wine.get("value_rating") == VALUE_FYND
            )
        return counts

    def as_payload(self, limit: int | None = None) -> dict[str, Any]:
        """A JSON-serialisable snapshot, used by the MQTT bridge and diagnostics."""
        if not self.data:
            return {"releases": [], "generated_at": None}
        cap = limit if limit is not None else self.top_count
        return {
            "generated_at": self.data["last_success"],
            "releases": [
                {
                    "kind": kind,
                    "id": result["release"]["id"],
                    "title": result["release"]["title"],
                    "date": result["release"]["date"],
                    "url": result["release"]["url"],
                    "wine_count": result["release"]["wine_count"],
                    # Carried over from an earlier poll rather than refreshed.
                    "stale": bool(result.get("stale")),
                    "wines": result["wines"][:cap],
                }
                for kind, result in self.data["releases"].items()
            ],
        }
