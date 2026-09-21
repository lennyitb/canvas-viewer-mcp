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
    server._self_cache = None
    yield
    server._client = None
    server._courses_cache = None
    server._self_cache = None


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
    assert "description" not in row, "bodies must stay out of the list payload"
    assert row["created_at"] == "2026-08-25T00:00:00Z"
    assert "due_at" in row and row["due_at"] is None, "a null due date is written out"
    assert "unlock_at" not in row, "other nulls are dropped from list rows"
    assert "html_url" not in row


@respx.mock
async def test_discussion_rows_are_flagged_and_other_rows_are_not() -> None:
    """A graded discussion reads `submitted` once the main post is up, with no
    field anywhere for the replies still owed. The row has to say so, or a
    sweep counts it done."""
    _mock_courses()
    discussion = {**_assignment(10, 1), "submission_types": ["discussion_topic"]}
    quiz = {**_assignment(11, 1), "submission_types": ["online_quiz"]}
    respx.get(f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(200, json=[discussion, quiz])
    )
    respx.get(f"{API}/courses/2/assignments").mock(return_value=httpx.Response(200, json=[]))

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_assignments", {})).data

    rows = {r["id"]: r for r in result["assignments"] if r.get("id")}
    assert "completion_caveat" in rows[10]
    assert "completion_caveat" not in rows[11]
    assert result["discussion_count"] == 1
    assert "replies" in result["discussion_note"]


@respx.mock
async def test_no_discussion_note_when_there_are_no_discussions() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(
            200, json=[{**_assignment(11, 1), "submission_types": ["online_quiz"]}]
        )
    )
    respx.get(f"{API}/courses/2/assignments").mock(return_value=httpx.Response(200, json=[]))

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_assignments", {})).data

    assert "discussion_note" not in result


@respx.mock
async def test_get_assignment_carries_the_caveat_and_the_topic_id() -> None:
    """The caveat is useless without a way to act on it, so the row hands over
    the topic id `get_discussion` needs."""
    _mock_courses()
    respx.get(f"{API}/courses/1/assignments/10").mock(
        return_value=httpx.Response(
            200,
            json={
                **_assignment(10, 1),
                "submission_types": ["discussion_topic"],
                "description": "<p>Reply to two classmates by Sunday.</p>",
                "discussion_topic": {"id": 946269},
            },
        )
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_assignment", {"course_id": 1, "assignment_id": 10})).data

    assert "completion_caveat" in result
    assert result["discussion_topic_id"] == 946269
    assert "two classmates" in result["description"]


@respx.mock
async def test_list_rows_carry_the_topic_id_for_discussions() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    **_assignment(10, 1),
                    "submission_types": ["discussion_topic"],
                    "discussion_topic": {"id": 946269},
                }
            ],
        )
    )
    respx.get(f"{API}/courses/2/assignments").mock(return_value=httpx.Response(200, json=[]))

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_assignments", {})).data

    assert result["assignments"][0]["discussion_topic_id"] == 946269


@respx.mock
async def test_announcement_bodies_can_be_left_out() -> None:
    _mock_courses()
    route = respx.get(f"{API}/announcements").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 5,
                    "title": "Deadline moved",
                    "posted_at": "2026-09-18T00:00:00Z",
                    "message": "<p>Essay 1 is now due Friday.</p>",
                    "context_code": "course_1",
                }
            ],
        )
    )

    async with Client(server.mcp) as c:
        full = (await c.call_tool("list_announcements", {})).data
        lean = (await c.call_tool("list_announcements", {"include_messages": False})).data
        one = (await c.call_tool("list_announcements", {"course_id": 2})).data

    assert "due Friday" in full["announcements"][0]["message"]
    assert "message" not in lean["announcements"][0]
    assert (
        full["announcements"][0]["course_id"] == 1
    ), "the announcements endpoint names the course with context_code, not course_id"
    codes = route.calls.last.request.url.params.get_list("context_codes[]")
    assert codes == ["course_2"], "course_id narrows the sweep to that course"
    assert one["count"] == 1


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
        "list_discussion_participation",
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


def test_topic_course_id_survives_every_shape_canvas_sends() -> None:
    """A topic's course must resolve whichever field carries it.

    ``courses/{id}/discussion_topics`` sends ``course_id``; the account-wide
    ``announcements`` endpoint sends ``context_code``; a stray row may carry
    neither and only link into the course.
    """
    from canvas_viewer_mcp.canvas.content import flatten_topic

    row = {"id": 5, "title": "t"}

    assert flatten_topic({**row, "course_id": 7}).course_id == 7
    assert flatten_topic({**row, "context_code": "course_7"}).course_id == 7
    assert flatten_topic({**row, "context_code": "user_7"}).course_id is None
    assert (
        flatten_topic(
            {**row, "html_url": "https://canvas.test/courses/7/discussion_topics/5"}
        ).course_id
        == 7
    )
    assert flatten_topic(row).course_id is None


# ---- starting before the platform has assigned a domain ----------------------


def test_http_start_without_a_public_url_serves_health_instead_of_exiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Exiting here produced a crash loop on Railway, which is the worst state
    to be in: the deploy never reports healthy, and that is exactly when a
    platform is least willing to hand out the domain that would have fixed
    it. Serve health and refuse everything else instead."""
    served: dict[str, Any] = {}
    monkeypatch.setattr(
        server,
        "_run_unconfigured",
        lambda reason, host, port: served.update(reason=reason, host=host, port=port),
    )
    monkeypatch.setattr(server.mcp, "run", lambda **kw: served.update(ran_mcp=True))

    monkeypatch.setenv("CANVAS_CONFIG_FILE", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("CANVAS_BASE_URL", "https://x.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN", "9112~token")
    monkeypatch.setenv("PORT", "8123")
    for name in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RENDER_EXTERNAL_URL", "FLY_APP_NAME"):
        monkeypatch.delenv(name, raising=False)

    server.main()

    assert "ran_mcp" not in served, "the MCP endpoint must not be served without a public URL"
    assert "PUBLIC_BASE_URL" in served["reason"]
    assert served["port"] == 8123


def test_canvas_misconfiguration_still_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The degraded mode is only for a missing public URL, which arrives by
    itself once a domain is assigned. A bad Canvas host never fixes itself, so
    it must still fail the deploy loudly."""
    monkeypatch.setenv("CANVAS_CONFIG_FILE", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.setenv("CANVAS_TOKEN", "9112~token")

    with pytest.raises(SystemExit):
        server.main()


# ---- the endpoint people paste ----------------------------------------------


def _http_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> set[str]:
    """Build the HTTP app the way main() does and report its routes."""
    import uvicorn
    from starlette.routing import Route

    monkeypatch.setenv("CANVAS_CONFIG_FILE", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://canvas.example.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    monkeypatch.delenv("AUTH_PASSWORD_HASH", raising=False)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))

    server.enable_http_auth()
    server._serve_http("127.0.0.1", 8000)

    return {r.path for r in captured["app"].routes if isinstance(r, Route)}


def test_mcp_is_served_at_the_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The root is what people paste. An address that silently needs /mcp
    glued on answers 404, which Claude reports as not being able to work out
    how the server signs in -- a dead end dressed up as an auth problem."""
    assert "/" in _http_paths(monkeypatch, tmp_path)


def test_the_old_paths_still_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """/mcp is what earlier versions served and what an already-authorized
    connector points at; its RFC 9728 metadata sat one level down. Moving the
    endpoint must not log those connectors out to save a suffix."""
    paths = _http_paths(monkeypatch, tmp_path)
    assert "/mcp" in paths
    assert "/.well-known/oauth-protected-resource/mcp" in paths


def test_the_oauth_routes_survive_serving_at_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Mounting MCP at / must not shadow the routes the sign-in flow needs."""
    paths = _http_paths(monkeypatch, tmp_path)
    for required in (
        "/health",
        "/login",
        "/authorize",
        "/token",
        "/register",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
    ):
        assert required in paths, required
