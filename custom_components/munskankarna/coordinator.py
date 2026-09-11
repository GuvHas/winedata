"""Data update coordinator for the Munskänkarna integration.

Fetches the release index once per cycle, keeps only the newest release of each
configured tasting type, and sorts each release's wines best-first. Sensors are
then thin projections over that prepared shape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.ssl import get_default_context

from .api import InvalidAuth, MunskankarnaClient, MunskankarnaError
from .const import (
    CONF_BASE_URL,
    CONF_KINDS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TOP_COUNT,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_KINDS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOP_COUNT,
    DOMAIN,
    VALUE_ORDER,
)
from .parser import ParseResult, ReleaseDict, WineDict

_LOGGER = logging.getLogger(__name__)

#: Unrated wines sort last, whichever direction is being applied.
_UNRATED_RANK = len(VALUE_ORDER) + 1




class CoordinatorData(TypedDict):
    """Everything the sensors need, keyed by tasting kind."""

    releases: dict[str, ParseResult]
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
        """How many wines each sensor exposes in its attributes."""
        return int(self.entry.options.get(CONF_TOP_COUNT) or DEFAULT_TOP_COUNT)

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

    # -- update ------------------------------------------------------------

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch the current release for each configured tasting type.

        One client and one login serve the whole cycle.
        """
        async with self._client() as client:
            self._api = client
            try:
                # A rejected login is fatal for the cycle; anonymous access
                # returns False here and simply carries on.
                await client.async_login()
                return await self._async_collect()
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed(
                    "Munskänkarna rejected the configured credentials"
                ) from err
            finally:
                self._api = None

    async def _async_collect(self) -> CoordinatorData:
        """Fetch the index and every configured release using the active client."""
        try:
            index = await self._async_fetch_index()
        except MunskankarnaError as err:
            raise UpdateFailed(f"Could not fetch the Munskänkarna release index: {err}") from err

        wanted = newest_per_kind(index, self.kinds)
        if not wanted:
            raise UpdateFailed(
                "No releases matched the configured tasting types; "
                "the site layout may have changed"
            )

        releases: dict[str, ParseResult] = {}
        warnings: list[str] = []

        for kind, release in wanted.items():
            try:
                result = await self._async_fetch_release(release["id"], release["title"])
            except MunskankarnaError as err:
                # One bad release must not blank the other sensors.
                warnings.append(f"{release['id']}: {err}")
                _LOGGER.warning("Could not fetch release %s: %s", release["id"], err)
                continue

            result["wines"] = sort_wines(result["wines"])
            warnings.extend(result.get("warnings") or [])
            releases[kind] = result

        if not releases:
            raise UpdateFailed(
                "Every configured release failed to load: " + "; ".join(warnings[:3])
            )

        return CoordinatorData(
            releases=releases,
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
                    "wines": result["wines"][:cap],
                }
                for kind, result in self.data["releases"].items()
            ],
        }
