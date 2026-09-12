"""Phase 2a — API client tests (written before `api.py`).

The login contract was derived from the live site: Munskänkarna runs Umbraco,
and the member login is a standard SurfaceController form post carrying
`__RequestVerificationToken` and `ufprt` hidden fields. These tests pin that
contract with mocked HTTP so no credentials ever leave the test suite.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from custom_components.munskankarna.api import (
    CannotConnect,
    InvalidAuth,
    MunskankarnaClient,
    RateLimited,
)
from custom_components.munskankarna.const import DEFAULT_BASE_URL

LOGIN_PAGE = """
<html><body>
  <div class="js-nav-user-loggedout">
    <form action="/" method="post" class="js-login" enctype="multipart/form-data">
      <input name="__RequestVerificationToken" type="hidden" value="TOKEN-ABC" />
      <input name="ufprt" type="hidden" value="UFPRT-XYZ" />
      <input name="Username" type="text" />
      <input name="Password" type="password" />
    </form>
  </div>
</body></html>
"""

LOGGED_IN_PAGE = """
<html><body>
  <div class="js-nav-user-loggedin">
    <a href="/umbraco/surface/member/logout" class="js-logout">Logga ut</a>
  </div>
</body></html>
"""


@pytest.fixture
def client() -> MunskankarnaClient:
    return MunskankarnaClient(base_url=DEFAULT_BASE_URL)


@respx.mock
async def test_fetch_text_returns_body(client: MunskankarnaClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    async with client:
        assert await client.fetch_text("/sv/vinlocus/") == "<html>ok</html>"


@respx.mock
async def test_fetch_text_raises_cannot_connect_on_server_error(
    client: MunskankarnaClient,
) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/boom").mock(return_value=httpx.Response(503))
    async with client:
        with pytest.raises(CannotConnect):
            await client.fetch_text("/boom")


@respx.mock
async def test_fetch_text_raises_cannot_connect_on_transport_error(
    client: MunskankarnaClient,
) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/down").mock(side_effect=httpx.ConnectError("refused"))
    async with client:
        with pytest.raises(CannotConnect):
            await client.fetch_text("/down")


@respx.mock
async def test_validate_connection_without_credentials(client: MunskankarnaClient) -> None:
    """Vinlocus is public: with no credentials we only prove reachability."""
    respx.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
        return_value=httpx.Response(
            200,
            text='<html><h3>Hitlista</h3><a href="/sv/vinlocus/hitlista-3-september-2026">'
            "Hitlista 3 september 2026</a></html>",
        )
    )
    async with client:
        assert await client.async_validate_connection() is True


@respx.mock
async def test_validate_connection_rejects_unparseable_index(
    client: MunskankarnaClient,
) -> None:
    """A 200 that yields no releases means the site changed - not a success."""
    respx.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
        return_value=httpx.Response(200, text="<html><body>nothing here</body></html>")
    )
    async with client:
        with pytest.raises(CannotConnect):
            await client.async_validate_connection()


@respx.mock
async def test_login_posts_umbraco_tokens_and_succeeds() -> None:
    """A successful login echoes the logged-in chrome back."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    login_route = respx.post(DEFAULT_BASE_URL + "/").mock(
        return_value=httpx.Response(200, text=LOGGED_IN_PAGE)
    )

    client = MunskankarnaClient(DEFAULT_BASE_URL, username="member@example.com", password="pw")
    async with client:
        assert await client.async_login() is True

    request = login_route.calls.last.request
    body = request.content.decode("utf-8", "replace")
    # The Umbraco surface contract: both tokens plus the credentials.
    assert "TOKEN-ABC" in body
    assert "UFPRT-XYZ" in body
    assert "member@example.com" in body
    assert "pw" in body


@respx.mock
async def test_login_detects_failure_when_form_is_returned() -> None:
    """Umbraco re-renders the login form on bad credentials."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))

    client = MunskankarnaClient(DEFAULT_BASE_URL, username="bad", password="wrong")
    async with client:
        with pytest.raises(InvalidAuth):
            await client.async_login()


@respx.mock
async def test_login_succeeds_when_auth_cookie_is_set() -> None:
    """Some responses redirect; the identity cookie is the stronger signal."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(
        return_value=httpx.Response(
            200,
            text=LOGIN_PAGE,  # body still looks logged out ...
            headers={"set-cookie": ".AspNetCore.Identity.Application=abc123; path=/; httponly"},
        )
    )
    client = MunskankarnaClient(DEFAULT_BASE_URL, username="u", password="p")
    async with client:
        assert await client.async_login() is True  # ... but the cookie proves it worked


@respx.mock
async def test_login_without_credentials_is_a_noop() -> None:
    """Anonymous use is the supported default, not an error."""
    client = MunskankarnaClient(DEFAULT_BASE_URL)
    async with client:
        assert await client.async_login() is False


@respx.mock
async def test_login_raises_cannot_connect_when_tokens_are_missing() -> None:
    """No login form means we cannot honour the Umbraco contract."""
    respx.get(DEFAULT_BASE_URL + "/").mock(
        return_value=httpx.Response(200, text="<html><body>no form</body></html>")
    )
    client = MunskankarnaClient(DEFAULT_BASE_URL, username="u", password="p")
    async with client:
        with pytest.raises(CannotConnect):
            await client.async_login()


@respx.mock
async def test_fetch_releases_parses_the_index(client: MunskankarnaClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/provningstyp").mock(
        return_value=httpx.Response(
            200,
            text='<html><h3>Tillfälligt sortiment</h3>'
            '<a href="/sv/vinlocus/tillfalligt-sortiment-11-september-2026">'
            "Tillfälligt sortiment 11 september 2026</a></html>",
        )
    )
    async with client:
        releases = await client.async_fetch_releases()
    assert releases[0]["kind"] == "tillfalligt-sortiment"
    assert releases[0]["date"] == "2026-09-11"


@respx.mock
async def test_fetch_release_returns_parsed_wines(
    client: MunskankarnaClient, load_fixture_html
) -> None:
    release_id = "hitlista-3-september-2026"
    respx.get(f"{DEFAULT_BASE_URL}/sv/vinlocus/{release_id}").mock(
        return_value=httpx.Response(200, text=load_fixture_html("release-hitlista.html"))
    )
    async with client:
        result = await client.async_fetch_release(release_id, "Hitlista 3 september 2026")
    assert len(result["wines"]) == 5
    assert all(w["article_number"] for w in result["wines"])


# ---------------------------------------------------------------------------
# Event-loop safety
#
# httpx.AsyncClient() with no `verify` argument calls ssl.create_default_context(),
# which reads certifi's CA bundle from disk. Doing that on the event loop trips
# Home Assistant's blocking-call detector:
#
#   Detected blocking call to load_verify_locations ... inside the event loop
#
# These tests pin the two ways the client avoids it.
# ---------------------------------------------------------------------------


@pytest.fixture
def ssl_probe(monkeypatch):
    """Record every ssl.create_default_context call and the thread it ran on."""
    import ssl as ssl_module
    import threading

    from custom_components.munskankarna import api as api_module

    # Drop any context cached by an earlier test so the build path is exercised.
    monkeypatch.setattr(api_module, "_DEFAULT_SSL_CONTEXT", None, raising=False)

    calls: list[threading.Thread] = []
    real = ssl_module.create_default_context

    def recording(*args, **kwargs):
        calls.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(ssl_module, "create_default_context", recording)
    return calls


@respx.mock
async def test_ssl_context_is_never_built_on_the_event_loop(ssl_probe) -> None:
    """With no context supplied, the build must happen off the loop thread."""
    import threading

    loop_thread = threading.current_thread()

    respx.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(200, text="ok"))
    async with MunskankarnaClient(DEFAULT_BASE_URL) as client:
        await client.fetch_text("/x")

    assert ssl_probe, "expected the client to build a default SSL context"
    for thread in ssl_probe:
        assert thread is not loop_thread, (
            "ssl.create_default_context ran on the event loop thread; "
            "Home Assistant would flag this as a blocking call"
        )


@respx.mock
async def test_supplied_ssl_context_is_used_without_building_one(ssl_probe) -> None:
    """Passing Home Assistant's cached context must skip the build entirely."""
    import ssl as ssl_module

    context = ssl_module.create_default_context()
    ssl_probe.clear()  # ignore the context we just built for the test itself

    respx.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(200, text="ok"))
    async with MunskankarnaClient(DEFAULT_BASE_URL, verify=context) as client:
        await client.fetch_text("/x")

    assert ssl_probe == [], "a context was supplied, so none should have been built"


async def test_default_ssl_context_is_cached_across_clients(ssl_probe) -> None:
    """Building it once per integration, not once per request."""
    from custom_components.munskankarna.api import async_default_ssl_context

    first = await async_default_ssl_context()
    second = await async_default_ssl_context()

    assert first is second
    assert len(ssl_probe) == 1, f"built the context {len(ssl_probe)} times"


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


@respx.mock
async def test_an_injected_client_is_not_closed_by_us() -> None:
    """Home Assistant owns any client it hands us; closing it would break others."""
    injected = httpx.AsyncClient()
    respx.get(f"{DEFAULT_BASE_URL}/x").mock(return_value=httpx.Response(200, text="ok"))

    async with MunskankarnaClient(DEFAULT_BASE_URL, client=injected) as client:
        await client.fetch_text("/x")

    assert not injected.is_closed, "the injected client must stay open"
    await injected.aclose()


@respx.mock
async def test_login_is_idempotent() -> None:
    """A session is established once, not re-posted for every fetch."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    login = respx.post(DEFAULT_BASE_URL + "/").mock(
        return_value=httpx.Response(200, text=LOGGED_IN_PAGE)
    )

    async with MunskankarnaClient(DEFAULT_BASE_URL, username="u", password="p") as client:
        assert await client.async_login() is True
        assert await client.async_login() is True
        assert await client.async_login() is True

    assert login.call_count == 1, f"logged in {login.call_count} times"


# ---------------------------------------------------------------------------
# Login must require proof, not merely the absence of a form
# ---------------------------------------------------------------------------


MAINTENANCE_PAGE = "<html><body><h1>Underhåll pågår</h1></body></html>"
UNRELATED_PAGE = "<html><body><h1>Vinlocus</h1><p>Provningar</p></body></html>"


@respx.mock
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        # Being told to slow down is not an authentication outcome at all.
        (429, "<html><body>Too many requests</body></html>", RateLimited),
        # Nor is the site being down.
        (503, MAINTENANCE_PAGE, CannotConnect),
        (500, MAINTENANCE_PAGE, CannotConnect),
        # A 200 that proves nothing either way must not be read as success.
        # CannotConnect rather than InvalidAuth on purpose: we cannot tell a
        # rejection from a layout change here, and prompting the user to
        # re-enter working credentials is the more destructive guess.
        (200, UNRELATED_PAGE, CannotConnect),
        (200, "", CannotConnect),
    ],
)
async def test_login_never_authenticates_without_positive_proof(
    status: int, body: str, expected: type[Exception]
) -> None:
    """The absence of a password field is not evidence of a session.

    Every one of these responses lacks an `input[name=Password]`, which was the
    whole of the old success test — so all of them authenticated, and a rate
    limit or a maintenance page silently became `authenticated = True`.
    """
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(status, text=body))

    client = MunskankarnaClient(DEFAULT_BASE_URL, username="u", password="p")
    async with client:
        with pytest.raises(expected):
            await client.async_login()
        assert client.authenticated is False, "a failed login left the client marked as authed"


@respx.mock
async def test_login_rate_limit_carries_the_retry_after() -> None:
    """A 429 on the login POST must surface its cooldown like any other."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(
        return_value=httpx.Response(429, text="slow down", headers={"Retry-After": "1800"})
    )

    client = MunskankarnaClient(DEFAULT_BASE_URL, username="u", password="p")
    async with client:
        with pytest.raises(RateLimited) as excinfo:
            await client.async_login()
    assert excinfo.value.retry_after == 1800


@respx.mock
async def test_login_accepts_the_logged_in_chrome_as_proof() -> None:
    """The positive signal is the member chrome, not an absent form."""
    respx.get(DEFAULT_BASE_URL + "/").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(DEFAULT_BASE_URL + "/").mock(
        return_value=httpx.Response(200, text=LOGGED_IN_PAGE)
    )
    client = MunskankarnaClient(DEFAULT_BASE_URL, username="u", password="p")
    async with client:
        assert await client.async_login() is True
        assert client.authenticated is True
