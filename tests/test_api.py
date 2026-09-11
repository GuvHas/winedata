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
