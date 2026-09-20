"""Tests for the MCP tool layer.

The focus is the degradation contract: a course with a disabled tab must
produce a note, not an exception, and must not abort a sweep over the other
courses.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client

from canvas_viewer_mcp import server

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"

COURSES = [
    {"id": 1, "name": "Open Course", "course_code": "OPEN", "enrollments": []},
    {"id": 2, "name": "Locked Course", "course_code": "LOCK", "enrollments": []},
]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CANVAS_BASE_URL", BASE)
    monkeypatch.setenv("CANVAS_TOKEN", "test-token")
    server._client = None
    server._courses_cache = None
    yield
    server._client = None
    server._courses_cache = None


def _mock_courses() -> None:
    respx.get(f"{API}/courses").mock(return_value=httpx.Response(200, json=COURSES))


def _assignment(aid: int, course_id: int) -> dict[str, Any]:
    return {
        "id": aid,
        "course_id": course_id,
        "name": f"HW {aid}",
        "due_at": None,
        "created_at": "2026-08-25T00:00:00Z",
    }


@respx.mock
async def test_disabled_files_tab_reports_unavailable_instead_of_raising() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/2/files").mock(
        return_value=httpx.Response(403, text='{"status":"unauthorized"}')
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_files", {"course_id": 2})).data

    assert result["unavailable"] is True
    assert "disabled or hidden" in result["reason"]


@respx.mock
async def test_missing_pages_tab_reports_unavailable() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/2/pages").mock(return_value=httpx.Response(404, text="not found"))

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_pages", {"course_id": 2})).data

    assert result["unavailable"] is True


@respx.mock
async def test_one_broken_course_does_not_abort_the_sweep() -> None:
    """The whole point of a sweep is completeness; a single locked course
    must not cost the user every other course's assignments."""
    _mock_courses()
    respx.get(f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(200, json=[_assignment(10, 1), _assignment(11, 1)])
    )
    respx.get(f"{API}/courses/2/assignments").mock(
        return_value=httpx.Response(403, text='{"status":"unauthorized"}')
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_assignments", {})).data

    returned = [a for a in result["assignments"] if a.get("id")]
    unavailable = [a for a in result["assignments"] if a.get("unavailable")]
    assert [a["id"] for a in returned] == [10, 11]
    assert len(unavailable) == 1
    assert unavailable[0]["course_id"] == 2


@respx.mock
async def test_assignments_omit_bodies_but_keep_creation_timestamps() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(200, json=[_assignment(10, 1)])
    )
    respx.get(f"{API}/courses/2/assignments").mock(return_value=httpx.Response(200, json=[]))

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_assignments", {})).data

    row = result["assignments"][0]
    assert row["description"] is None, "bodies must stay out of the list payload"
    assert row["created_at"] == "2026-08-25T00:00:00Z"
    assert row["due_at"] is None


@respx.mock
async def test_course_list_is_cached_across_tool_calls() -> None:
    route = respx.get(f"{API}/courses").mock(return_value=httpx.Response(200, json=COURSES))

    async with Client(server.mcp) as c:
        await c.call_tool("list_courses", {})
        await c.call_tool("list_courses", {})

    assert route.call_count == 1, "course list should be fetched once, then cached"


async def test_every_tool_is_registered() -> None:
    async with Client(server.mcp) as c:
        names = {t.name for t in await c.list_tools()}

    assert names == {
        "list_courses",
        "list_assignments",
        "get_assignment",
        "list_announcements",
        "list_discussions",
        "get_discussion",
        "list_files",
        "read_course_file",
        "list_pages",
        "get_page",
        "list_modules",
    }


async def test_no_tool_can_write() -> None:
    """Read-only is a property of the code, not a setting. The client class
    exposes no verb but GET, so assert that stays true."""
    from canvas_viewer_mcp.canvas.client import CanvasClient

    for verb in ("post", "put", "patch", "delete"):
        assert not hasattr(CanvasClient, verb), f"CanvasClient grew a {verb} method"
