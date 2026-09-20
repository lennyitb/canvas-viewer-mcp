"""End-to-end OAuth 2.1 tests against the real ASGI app.

These drive the same sequence Claude performs when a custom connector is
added: discover metadata, register dynamically, authorize, log in, exchange a
code with PKCE, then call a tool with the resulting bearer token. Each step is
asserted because a connector that fails at any one of them presents the same
unhelpful "couldn't reach the MCP server" either way.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from canvas_viewer_mcp import server
from canvas_viewer_mcp.auth.provider import hash_password

PASSWORD = "correct-horse-battery-staple"
PUBLIC_URL = "http://localhost:8000"


@pytest.fixture(scope="module")
def _configured(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    db = tmp_path_factory.mktemp("auth") / "auth.sqlite"
    import os

    previous = dict(os.environ)
    os.environ.update(
        {
            "PUBLIC_BASE_URL": PUBLIC_URL,
            "AUTH_PASSWORD_HASH": hash_password(PASSWORD),
            "AUTH_DB_PATH": str(db),
            "CANVAS_BASE_URL": "https://canvas.test",
            "CANVAS_TOKEN": "test-token",
        }
    )
    server.enable_http_auth()
    yield db
    server.mcp.auth = None
    os.environ.clear()
    os.environ.update(previous)


@pytest.fixture
async def client(_configured: Path) -> AsyncIterator[httpx.AsyncClient]:
    """A client for the OAuth routes.

    No lifespan is started here. The MCP session manager's task group must be
    entered and exited in the same task, which a fixture cannot guarantee; the
    OAuth endpoints do not need it, and the one test that exercises /mcp
    manages the lifespan inline.
    """
    app = server.mcp.http_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL) as c:
        yield c


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _register(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/register",
        json={
            "client_name": "Test Client",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()
    return result


async def test_authorization_server_metadata_is_discoverable(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    meta = response.json()

    assert meta["issuer"].rstrip("/") == PUBLIC_URL
    assert "S256" in meta["code_challenge_methods_supported"], "PKCE S256 is required"
    assert meta["registration_endpoint"], "mobile clients require Dynamic Client Registration"
    for field in ("authorization_endpoint", "token_endpoint"):
        assert meta[field].startswith(PUBLIC_URL), f"{field} must use the configured public URL"


async def test_protected_resource_metadata_is_discoverable(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json()["authorization_servers"]


async def test_dynamic_client_registration(client: httpx.AsyncClient) -> None:
    registered = await _register(client)
    assert registered["client_id"]


async def test_authorize_redirects_to_login_rather_than_issuing_a_code(
    client: httpx.AsyncClient,
) -> None:
    """The endpoint is public, so it must never mint a code before a password."""
    registered = await _register(client)
    _, challenge = _pkce()

    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC_URL}/login"), location
    assert "code=" not in location, "a code was issued without authentication"


async def test_wrong_password_yields_no_code(client: httpx.AsyncClient) -> None:
    registered = await _register(client)
    _, challenge = _pkce()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )
    login_id = httpx.URL(response.headers["location"]).params["login_id"]

    bad = await client.post("/login", data={"login_id": login_id, "password": "wrong"})
    assert bad.status_code == 401
    assert "location" not in bad.headers


async def test_full_flow_yields_a_token_that_authenticates_a_tool_call(
    client: httpx.AsyncClient,
) -> None:
    registered = await _register(client)
    verifier, challenge = _pkce()

    authorize = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )
    login_id = httpx.URL(authorize.headers["location"]).params["login_id"]

    submitted = await client.post("/login", data={"login_id": login_id, "password": PASSWORD})
    assert submitted.status_code == 303
    callback = httpx.URL(submitted.headers["location"])
    assert callback.params["state"] == "xyz"
    code = callback.params["code"]

    token_response = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "client_id": registered["client_id"],
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200, token_response.text
    tokens = token_response.json()
    assert tokens["token_type"].lower() == "bearer"
    assert tokens["refresh_token"]

    # The code is single-use; replaying it is a standard attack.
    replay = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "client_id": registered["client_id"],
            "code_verifier": verifier,
        },
    )
    assert replay.status_code in (400, 401), "authorization code was accepted twice"

    await _assert_token_opens_mcp(tokens["access_token"])


async def test_refresh_token_rotates(client: httpx.AsyncClient) -> None:
    registered = await _register(client)
    verifier, challenge = _pkce()
    authorize = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "s",
        },
    )
    login_id = httpx.URL(authorize.headers["location"]).params["login_id"]
    submitted = await client.post("/login", data={"login_id": login_id, "password": PASSWORD})
    code = httpx.URL(submitted.headers["location"]).params["code"]
    first = (
        await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": registered["client_id"],
                "code_verifier": verifier,
            },
        )
    ).json()

    refreshed = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": registered["client_id"],
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.json()
    assert second["refresh_token"] != first["refresh_token"], "refresh token must rotate"

    reused = await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": registered["client_id"],
        },
    )
    assert reused.status_code in (400, 401), "an already-rotated refresh token was accepted"


async def _assert_token_opens_mcp(access_token: str) -> None:
    """The MCP endpoint must reject anonymous callers and accept the token.

    The lifespan is entered and exited inside this one coroutine because the
    session manager's task group cannot survive being handed between tasks.
    """
    app = server.mcp.http_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL) as c,
    ):
        anonymous = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert anonymous.status_code == 401, "the MCP endpoint served an anonymous caller"

        authenticated = await c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert authenticated.status_code == 200, authenticated.text
