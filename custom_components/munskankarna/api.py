"""Async HTTP client for Munskänkarna.

Strictly `httpx.AsyncClient` — no blocking I/O, so this is safe to await
directly from Home Assistant's event loop with no executor hop.

SSL contexts
------------
`httpx.AsyncClient()` with no `verify` argument calls
`ssl.create_default_context()`, which reads certifi's CA bundle from disk.
On the event loop that trips Home Assistant's blocking-call detector:

    Detected blocking call to load_verify_locations ... inside the event loop

So a context is never built implicitly here. Callers inside Home Assistant pass
`verify=homeassistant.util.ssl.get_default_context()`, which HA has already
built off-loop. Standalone callers (tests, CLI use) get a context built in a
worker thread via `async_default_ssl_context()` and cached for the process.

This module deliberately imports nothing from Home Assistant, so it can be
exercised on Python versions the HA test harness does not yet support.

Authentication notes
--------------------
The Vinlocus review pages this integration reads are **public**, so credentials
are optional and the default configuration is anonymous.

When credentials are supplied, login follows Munskänkarna's actual mechanism:
the site runs Umbraco, and its member login is a SurfaceController form post.
Two hidden fields must be replayed from a freshly fetched page —
`__RequestVerificationToken` (ASP.NET Core antiforgery) and `ufprt` (Umbraco's
form-post-redirect token) — alongside `Username` and `Password`, encoded as
multipart/form-data. Success is detected either by an identity cookie being
set or by the login form disappearing from the response.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Final, Self

import httpx
from bs4 import BeautifulSoup

from .const import DEFAULT_BASE_URL
from .parser import ParseResult, ReleaseDict, parse_release_index, parse_release_page

_LOGGER = logging.getLogger(__name__)

RELEASE_INDEX_PATH: Final = "/sv/vinlocus/provningstyp"
RELEASE_PATH: Final = "/sv/vinlocus/{release_id}"

DEFAULT_TIMEOUT: Final = httpx.Timeout(30.0, connect=10.0)

#: Identifies the integration to the source site rather than impersonating a
#: browser, so the traffic is attributable and easy for them to block if they
#: ever wish to.
USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; HomeAssistant-Munskankarna/1.0; "
    "+https://github.com/GuvHas/winedata)"
)

#: Cookie-name fragments that indicate an authenticated ASP.NET/Umbraco session.
_AUTH_COOKIE_HINTS: Final[tuple[str, ...]] = ("identity", "aspxauth", "umb_", "member")

#: Politeness delay between consecutive requests, in seconds.
_REQUEST_SPACING: Final = 0.75

#: Attempts per request, including the first. Server errors are usually
#: transient and a poll runs only every few hours, so one retry is worth it;
#: more would just prolong an outage.
_MAX_ATTEMPTS: Final = 2

#: Base seconds for exponential backoff between attempts.
_RETRY_BACKOFF: Final = 2.0

#: Status codes worth a second attempt. 429 is deliberately absent: being told
#: to slow down is not an invitation to retry.
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({500, 502, 503, 504})

#: Process-wide cache for the fallback SSL context. Building one reads the CA
#: bundle from disk, so it is done once, in a worker thread. A benign race here
#: would only build it twice and keep one; a lock bound to a particular event
#: loop would be worse.
_DEFAULT_SSL_CONTEXT: ssl.SSLContext | None = None


async def async_default_ssl_context() -> ssl.SSLContext:
    """Return a default SSL context, built off the event loop and cached.

    Only used when the caller supplies no context of its own. Inside Home
    Assistant, prefer passing `homeassistant.util.ssl.get_default_context()`.
    """
    global _DEFAULT_SSL_CONTEXT  # noqa: PLW0603
    if _DEFAULT_SSL_CONTEXT is None:
        _DEFAULT_SSL_CONTEXT = await asyncio.to_thread(ssl.create_default_context)
    return _DEFAULT_SSL_CONTEXT


class MunskankarnaError(Exception):
    """Base error for this integration."""


class CannotConnect(MunskankarnaError):
    """The site is unreachable, errored, or returned something unparseable."""


class InvalidAuth(MunskankarnaError):
    """The supplied member credentials were rejected."""


class RateLimited(MunskankarnaError):
    """The site asked us to slow down (HTTP 429).

    Distinct from CannotConnect so callers can abandon the rest of the cycle
    instead of continuing to request from a server that just pushed back.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class MunskankarnaClient:
    """Fetches and parses Munskänkarna's Vinlocus review pages."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        username: str | None = None,
        password: str | None = None,
        client: httpx.AsyncClient | None = None,
        verify: ssl.SSLContext | bool | None = None,
        retry_backoff: float = _RETRY_BACKOFF,
    ) -> None:
        """Store configuration. The HTTP client is created lazily on entry.

        `client` — an externally owned client to use as-is. It is never closed
        by this class, since Home Assistant manages the lifetime of the ones it
        hands out.

        `verify` — an SSL context (or bool) for a client created here. Pass
        Home Assistant's cached context to avoid building one at all; leave it
        as None and one is built off the loop and reused.
        """
        self._base_url = base_url.rstrip("/")
        self._username = username or None
        self._password = password or None
        self._client = client
        self._owns_client = client is None
        self._verify = verify
        self._retry_backoff = retry_backoff
        self._authenticated = False
        self._last_request = 0.0

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> Self:
        if self._client is None:
            # Resolve the context before constructing the client: letting httpx
            # default it would read the CA bundle from disk on this thread.
            verify = self._verify if self._verify is not None else (
                await async_default_ssl_context()
            )
            self._client = httpx.AsyncClient(
                verify=verify,
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
                },
            )
            self._owns_client = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def authenticated(self) -> bool:
        """True once a member session has been established."""
        return self._authenticated

    @property
    def has_credentials(self) -> bool:
        """True when both a username and a password were configured."""
        return bool(self._username and self._password)

    # -- low level ---------------------------------------------------------

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self._base_url}{path}"

    async def _throttle(self) -> None:
        """Keep a minimum spacing between requests to the source site."""
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - self._last_request
        if self._last_request and elapsed < _REQUEST_SPACING:
            await asyncio.sleep(_REQUEST_SPACING - elapsed)
        self._last_request = loop.time()

    async def fetch_text(self, path: str) -> str:
        """GET a page as text.

        Retries transient server errors with exponential backoff. A 429 raises
        `RateLimited` immediately — retrying a rate limit is the one thing that
        makes it worse — and every other failure becomes `CannotConnect`.
        """
        if self._client is None:
            raise RuntimeError("MunskankarnaClient must be used as an async context manager")

        url = self._url(path)
        last_error: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await self._throttle()
            try:
                response = await self._client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as err:
                status = err.response.status_code
                if status == 429:
                    retry_after = _parse_retry_after(err.response.headers.get("Retry-After"))
                    raise RateLimited(
                        f"Rate limited by {url}"
                        + (f"; retry after {retry_after:.0f}s" if retry_after else ""),
                        retry_after,
                    ) from err
                last_error = CannotConnect(f"HTTP {status} for {url}")
                if status not in _RETRYABLE_STATUS:
                    raise last_error from err
            except httpx.HTTPError as err:
                last_error = CannotConnect(f"Request to {url} failed: {err}")
            else:
                return response.text

            if attempt < _MAX_ATTEMPTS and self._retry_backoff:
                await asyncio.sleep(self._retry_backoff * attempt)

        raise last_error or CannotConnect(f"Request to {url} failed")

    # -- authentication ----------------------------------------------------

    async def async_login(self) -> bool:
        """Establish a member session.

        Returns False (not an error) when no credentials are configured, since
        anonymous access is the supported default.
        """
        if not self.has_credentials:
            return False
        if self._authenticated:
            # The session cookie is already on the jar; re-posting credentials
            # on every fetch would be wasteful and rude to the login endpoint.
            return True
        if self._client is None:
            raise RuntimeError("MunskankarnaClient must be used as an async context manager")

        page = await self.fetch_text("/")
        token, ufprt = self._extract_login_tokens(page)
        if not token or not ufprt:
            raise CannotConnect(
                "Could not find the Munskänkarna login form; the site layout may have changed"
            )

        await self._throttle()
        try:
            response = await self._client.post(
                self._url("/"),
                # The form declares enctype="multipart/form-data"; httpx emits
                # that encoding when fields are passed via `files`.
                files={
                    "__RequestVerificationToken": (None, token),
                    "ufprt": (None, ufprt),
                    "Username": (None, self._username or ""),
                    "Password": (None, self._password or ""),
                },
            )
        except httpx.HTTPError as err:
            raise CannotConnect(f"Login request failed: {err}") from err

        self._authenticated = self._login_succeeded(response)
        if not self._authenticated:
            raise InvalidAuth("Munskänkarna rejected the supplied credentials")

        _LOGGER.debug("Munskänkarna member session established")
        return True

    @staticmethod
    def _extract_login_tokens(html: str) -> tuple[str | None, str | None]:
        """Pull the antiforgery and Umbraco form tokens out of the login form."""
        soup = BeautifulSoup(html, "html.parser")
        form = soup.select_one("form.js-login") or soup.find("form")
        if form is None:
            return None, None

        def hidden(name: str) -> str | None:
            field = form.find("input", attrs={"name": name})
            return field.get("value") if field is not None else None

        return hidden("__RequestVerificationToken"), hidden("ufprt")

    def _login_succeeded(self, response: httpx.Response) -> bool:
        """Decide whether a login POST actually authenticated us.

        Two independent signals, because Umbraco's response shape varies with
        redirect configuration: an identity cookie on the jar, or the login
        form no longer being rendered.
        """
        if self._client is not None:
            for cookie in self._client.cookies.jar:
                name = cookie.name.lower()
                if any(hint in name for hint in _AUTH_COOKIE_HINTS) and cookie.value:
                    return True

        soup = BeautifulSoup(response.text, "html.parser")
        still_showing_form = soup.find("input", attrs={"name": "Password"}) is not None
        return not still_showing_form

    # -- high level --------------------------------------------------------

    async def async_validate_connection(self) -> bool:
        """Verify the integration can reach *and understand* the site.

        Used by the config flow. A 200 that yields no releases is treated as a
        failure: it means the markup changed and every sensor would be empty.
        """
        if self.has_credentials:
            await self.async_login()

        releases = await self.async_fetch_releases()
        if not releases:
            raise CannotConnect(
                "Reached Munskänkarna but found no releases; the site layout may have changed"
            )
        return True

    async def async_fetch_releases(self) -> list[ReleaseDict]:
        """Fetch and parse the release index."""
        return parse_release_index(await self.fetch_text(RELEASE_INDEX_PATH), self._base_url)

    async def async_fetch_release(self, release_id: str, title: str) -> ParseResult:
        """Fetch and parse a single release page."""
        html = await self.fetch_text(RELEASE_PATH.format(release_id=release_id))
        return parse_release_page(html, release_id=release_id, title=title, base_url=self._base_url)

    async def async_fetch_many(
        self, releases: list[ReleaseDict]
    ) -> tuple[list[ParseResult], list[str]]:
        """Fetch several releases in sequence, collecting per-release errors.

        Sequential by design: throttling matters more than latency here, and
        one failing release must not discard the others.
        """
        results: list[ParseResult] = []
        errors: list[str] = []
        for release in releases:
            try:
                results.append(await self.async_fetch_release(release["id"], release["title"]))
            except RateLimited as err:
                # Abandon the cycle: continuing would keep hitting a server
                # that has just asked us to back off.
                errors.append(f"{release['id']}: {err}")
                _LOGGER.warning(
                    "Rate limited while fetching %s; abandoning this update cycle",
                    release["id"],
                )
                break
            except MunskankarnaError as err:
                errors.append(f"{release['id']}: {err}")
                _LOGGER.warning("Could not fetch release %s: %s", release["id"], err)
        return results, errors


async def async_validate_credentials(
    base_url: str,
    username: str | None,
    password: str | None,
    verify: ssl.SSLContext | bool | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Validate a config-flow submission.

    `verify`/`client` let the caller supply Home Assistant's SSL context or a
    ready-made client, so no context is built on the event loop.

    Raises `InvalidAuth` or `CannotConnect`; returns a small summary on success.
    """
    async with MunskankarnaClient(base_url, username, password, client, verify) as client:
        await client.async_validate_connection()
        releases = await client.async_fetch_releases()
        return {
            "authenticated": client.authenticated,
            "release_count": len(releases),
        }


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header, which may be seconds or an HTTP date."""
    if not value:
        return None

    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass

    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
