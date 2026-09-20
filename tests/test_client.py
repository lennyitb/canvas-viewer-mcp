"""Tests for the Canvas HTTP client.

Pagination gets the most attention here. A client that ignores ``rel="next"``
returns the first page and raises nothing, so the bug is invisible in manual
testing -- exactly the failure this project is meant to eliminate.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from canvas_viewer_mcp.canvas.client import CanvasClient
from canvas_viewer_mcp.canvas.errors import (
    CanvasAuthError,
    CanvasNotFoundError,
    CanvasPaginationError,
    CanvasRateLimitError,
)
from canvas_viewer_mcp.config import Config

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"


@pytest.fixture
def config() -> Config:
    return Config(base_url=BASE, token="test-token", timeout_seconds=5.0, max_pages=5)


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep backoff logic exercised but instant."""

    async def _instant(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(CanvasClient, "_sleep_backoff", staticmethod(_instant))


def _page(items: list[dict[str, int]], next_url: str | None) -> httpx.Response:
    headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
    return httpx.Response(200, json=items, headers=headers)


@respx.mock
async def test_paginate_follows_link_header_across_pages(config: Config) -> None:
    respx.get(f"{API}/courses").mock(
        side_effect=[
            _page([{"id": 1}, {"id": 2}], f"{API}/courses?page=2"),
            _page([{"id": 3}, {"id": 4}], f"{API}/courses?page=3"),
            _page([{"id": 5}], None),
        ]
    )

    async with CanvasClient(config) as client:
        items = await client.paginate("courses")

    assert [i["id"] for i in items] == [1, 2, 3, 4, 5], "dropped items from later pages"


@respx.mock
async def test_paginate_does_not_clobber_the_next_cursor(config: Config) -> None:
    """The next URL carries its own cursor; re-sending the original params
    would reset it to page 1 and loop forever."""
    route = respx.get(f"{API}/courses").mock(
        side_effect=[
            _page([{"id": 1}], f"{API}/courses?page=2&per_page=100"),
            _page([{"id": 2}], None),
        ]
    )

    async with CanvasClient(config) as client:
        await client.paginate("courses", {"enrollment_state": "active"})

    first, second = route.calls[0].request.url, route.calls[1].request.url
    assert first.params["enrollment_state"] == "active"
    assert second.params["page"] == "2", "second request lost the cursor"
    assert "enrollment_state" not in second.params, "original params clobbered the cursor URL"


@respx.mock
async def test_paginate_raises_rather_than_truncating(config: Config) -> None:
    """Exceeding the page budget must fail loudly, never return a short list."""
    respx.get(f"{API}/assignments").mock(
        return_value=_page([{"id": 1}], f"{API}/assignments?page=99")
    )

    async with CanvasClient(config) as client:
        with pytest.raises(CanvasPaginationError, match="Refusing to return a partial list"):
            await client.paginate("assignments")


@respx.mock
async def test_single_page_without_link_header(config: Config) -> None:
    respx.get(f"{API}/courses").mock(return_value=_page([{"id": 1}], None))

    async with CanvasClient(config) as client:
        assert len(await client.paginate("courses")) == 1


@respx.mock
async def test_rate_limit_is_403_not_429(config: Config) -> None:
    """Canvas reports throttling as 403. Misclassifying it as an auth error
    would send you off regenerating a perfectly good token."""
    respx.get(f"{API}/courses").mock(
        return_value=httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
    )

    async with CanvasClient(config) as client:
        with pytest.raises(CanvasRateLimitError):
            await client.paginate("courses")


@respx.mock
async def test_rate_limit_is_retried_then_succeeds(config: Config) -> None:
    respx.get(f"{API}/courses").mock(
        side_effect=[
            httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)"),
            _page([{"id": 7}], None),
        ]
    )

    async with CanvasClient(config) as client:
        assert [i["id"] for i in await client.paginate("courses")] == [7]


@respx.mock
async def test_server_error_is_retried(config: Config) -> None:
    respx.get(f"{API}/courses").mock(
        side_effect=[httpx.Response(500, text="boom"), _page([{"id": 8}], None)]
    )

    async with CanvasClient(config) as client:
        assert [i["id"] for i in await client.paginate("courses")] == [8]


@respx.mock
async def test_401_is_an_auth_error_with_actionable_message(config: Config) -> None:
    respx.get(f"{API}/users/self").mock(return_value=httpx.Response(401, text="unauthorized"))

    async with CanvasClient(config) as client:
        with pytest.raises(CanvasAuthError, match="Approved Integrations"):
            await client.current_user()


@respx.mock
async def test_404_is_distinguishable(config: Config) -> None:
    respx.get(f"{API}/courses/999").mock(return_value=httpx.Response(404, text="not found"))

    async with CanvasClient(config) as client:
        with pytest.raises(CanvasNotFoundError):
            await client.get("courses/999")


@respx.mock
async def test_authorization_header_is_sent(config: Config) -> None:
    route = respx.get(f"{API}/users/self").mock(return_value=httpx.Response(200, json={"id": 1}))

    async with CanvasClient(config) as client:
        await client.current_user()

    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"
