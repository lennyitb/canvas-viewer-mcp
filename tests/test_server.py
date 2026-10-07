"""Tests for the MCP tool layer.

The focus is the degradation contract: a course with a disabled tab must
produce a note, not an exception, and must not abort a sweep over the other
courses.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client

from canvas_viewer_mcp import overrides, server

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"

COURSES = [
    {"id": 1, "name": "Open Course", "course_code": "OPEN", "enrollments": []},
    {"id": 2, "name": "Locked Course", "course_code": "LOCK", "enrollments": []},
]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CANVAS_BASE_URL", BASE)
    monkeypatch.setenv("CANVAS_TOKEN", "test-token")
    # Never let a developer's real overrides file leak into the suite.
    monkeypatch.setenv("CANVAS_OVERRIDES_FILE", str(tmp_path / "overrides.toml"))
    overrides.reset_cache()
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
async def test_disabled_files_tab_with_nothing_linked_reports_unavailable() -> None:
    _mock_courses()
    # Files hidden, and every source the list could be rebuilt from refused too.
    respx.get(url__regex=rf"^{API}/courses/2(/|\?)").mock(
        return_value=httpx.Response(403, text='{"status":"unauthorized"}')
    )
    respx.get(url__startswith=f"{API}/announcements").mock(
        return_value=httpx.Response(200, json=[])
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_files", {"course_id": 2})).data

    assert result["unavailable"] is True
    assert "disabled or hidden" in result["reason"]
    assert "file_id" in result["hint"]


@respx.mock
async def test_missing_pages_tab_reports_unavailable() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/2/pages").mock(return_value=httpx.Response(404, text="not found"))

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_pages", {"course_id": 2})).data

    assert result["unavailable"] is True
    assert "front_page" in result["hint"], "a hidden list should point at the home page"


@respx.mock
async def test_front_page_is_fetched_from_its_own_endpoint() -> None:
    _mock_courses()
    route = respx.get(f"{API}/courses/2/front_page").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "home",
                "title": "Home",
                "body": '<a href="/courses/2/pages/week-1">Week 1</a>',
            },
        )
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_page", {"course_id": 2, "page_url": "front_page"})).data

    assert route.called
    assert "/courses/2/pages/week-1" in result["body"]


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
        result = (
            await c.call_tool(
                "get_assignment",
                {"course_id": 1, "assignment_id": 10, "include_feedback": False},
            )
        ).data

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
        "list_feedback",
        "list_announcements",
        "list_discussions",
        "get_discussion",
        "list_discussion_participation",
        "list_files",
        "read_course_file",
        "download_course_file",
        "list_pages",
        "get_page",
        "list_modules",
        "get_syllabus",
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


# ---- feedback ----------------------------------------------------------------


def _mock_self(user_id: int = 7) -> None:
    respx.get(f"{API}/users/self").mock(
        return_value=httpx.Response(200, json={"id": user_id, "name": "Me"})
    )


def _recent() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


@respx.mock
async def test_get_assignment_returns_feedback_joined_to_the_rubric() -> None:
    _mock_courses()
    _mock_self()
    respx.get(f"{API}/courses/1/assignments/10").mock(
        return_value=httpx.Response(
            200,
            json={
                **_assignment(10, 1),
                "rubric": [{"id": "_1", "description": "Thesis", "points": 10.0}],
            },
        )
    )
    route = respx.get(f"{API}/courses/1/assignments/10/submissions/self").mock(
        return_value=httpx.Response(
            200,
            json={
                "score": 6.0,
                "posted_at": "2026-09-20T00:00:00Z",
                "submission_comments": [
                    {"author_id": 3, "author_name": "Dr. I", "comment": "Resubmit by Friday."}
                ],
                "rubric_assessment": {"_1": {"points": 6.0, "comments": "Vague."}},
            },
        )
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_assignment", {"course_id": 1, "assignment_id": 10})).data

    fb = result["feedback"]
    assert fb["score"] == 6.0
    assert fb["comments"][0]["comment"] == "Resubmit by Friday."
    assert fb["comments"][0]["mine"] is False
    assert fb["rubric"][0] == {
        "criterion": "Thesis",
        "points": 6.0,
        "points_possible": 10.0,
        "rating": None,
        "comments": "Vague.",
    }
    includes = route.calls.last.request.url.params.get_list("include[]")
    assert set(includes) == {"submission_comments", "rubric_assessment"}


@respx.mock
async def test_get_assignment_survives_unavailable_feedback() -> None:
    _mock_courses()
    _mock_self()
    respx.get(f"{API}/courses/1/assignments/10").mock(
        return_value=httpx.Response(200, json=_assignment(10, 1))
    )
    respx.get(f"{API}/courses/1/assignments/10/submissions/self").mock(
        return_value=httpx.Response(403, text='{"status":"unauthorized"}')
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_assignment", {"course_id": 1, "assignment_id": 10})).data

    assert result["name"] == "HW 10"
    assert result["feedback"]["unavailable"] is True


@respx.mock
async def test_get_assignment_without_feedback_makes_no_submission_request() -> None:
    _mock_courses()
    respx.get(f"{API}/courses/1/assignments/10").mock(
        return_value=httpx.Response(200, json=_assignment(10, 1))
    )
    route = respx.get(f"{API}/courses/1/assignments/10/submissions/self")

    async with Client(server.mcp) as c:
        result = (
            await c.call_tool(
                "get_assignment",
                {"course_id": 1, "assignment_id": 10, "include_feedback": False},
            )
        ).data

    assert "feedback" not in result
    assert not route.called


@respx.mock
async def test_list_feedback_sweeps_past_a_locked_course_and_hides_own_comments() -> None:
    _mock_courses()
    _mock_self()
    respx.get(f"{API}/courses/1/students/submissions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "score": 4.0,
                    "graded_at": _recent(),
                    "submission_comments": [
                        {"author_id": 7, "comment": "Mine", "created_at": _recent()},
                        {"author_id": 3, "comment": "See me", "created_at": _recent()},
                    ],
                    "assignment": {"id": 10, "name": "HW 10", "points_possible": 10.0},
                },
                {"graded_at": "2020-01-01T00:00:00Z", "assignment": {"id": 11, "name": "Old"}},
            ],
        )
    )
    respx.get(f"{API}/courses/2/students/submissions").mock(
        return_value=httpx.Response(403, text='{"status":"unauthorized"}')
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_feedback", {"days": 7})).data
        with_own = (
            await c.call_tool("list_feedback", {"course_id": 1, "include_own_comments": True})
        ).data

    rows = result["feedback"]
    assert rows[0]["name"] == "HW 10"
    assert rows[0]["course_name"] == "Open Course"
    assert [c["comment"] for c in rows[0]["comments"]] == ["See me"]
    assert "mine" not in rows[0]["comments"][0], "redundant once own comments are dropped"
    assert rows[1] == {"course_id": 2, **rows[1], "unavailable": True}
    assert result["count"] == 2, "the old submission is outside the window"

    own = with_own["feedback"][0]["comments"]
    assert [(c["comment"], c["mine"]) for c in own] == [("Mine", True), ("See me", False)]


@respx.mock
async def test_list_feedback_marks_provisional_scores_and_sorts_newest_first() -> None:
    _mock_courses()
    _mock_self()
    now = datetime.now(UTC)

    def ago(hours: int) -> str:
        return (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    respx.get(f"{API}/courses/1/students/submissions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "score": 9.0,
                    "workflow_state": "graded",
                    "graded_at": ago(48),
                    "posted_at": ago(47),
                    "assignment": {"id": 10, "name": "Lab 2"},
                }
            ],
        )
    )
    respx.get(f"{API}/courses/2/students/submissions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "score": 1.4,
                    "workflow_state": "pending_review",
                    "graded_at": ago(5),
                    "posted_at": ago(5),
                    "assignment": {"id": 20, "name": "Quiz 5", "points_possible": 10.0},
                }
            ],
        )
    )

    async with Client(server.mcp) as c:
        rows = (await c.call_tool("list_feedback", {})).data["feedback"]

    assert [r["name"] for r in rows] == ["Quiz 5", "Lab 2"], "newest first, across courses"
    assert rows[0]["workflow_state"] == "pending_review"
    assert rows[0]["score"] == 1.4
    assert rows[0]["last_change_at"] == ago(5)
    assert "workflow_state" not in rows[1], "graded is the usual state, so it is left out"
    assert rows[1]["last_change_at"] == ago(47), "the release, not the grading"


# ---- file downloads ----------------------------------------------------------

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _file(fid: int, name: str, kind: str, size: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": fid,
        "display_name": name,
        "filename": name,
        "content-type": kind,
        "size": size,
        "created_at": "2026-08-24T00:00:00Z",
        "updated_at": "2026-08-25T00:00:00Z",
        "folder_id": 99,
        "url": f"{BASE}/files/{fid}/download?download_frd=1&verifier=v{fid}",
        **extra,
    }


@respx.mock
async def test_download_hands_out_the_link_without_fetching_the_file() -> None:
    respx.get(f"{API}/files/5").mock(
        return_value=httpx.Response(200, json=_file(5, "Lecture.pptx", "application/x-pptx", 900))
    )
    blob = respx.get(f"{BASE}/files/5/download")

    async with Client(server.mcp) as c:
        result = await c.call_tool("download_course_file", {"file_id": 5})

    row = result.structured_content
    assert row is not None
    assert row["download_url"].endswith("verifier=v5")
    assert "curl -L -o" in row["how_to"]
    assert row["display_name"] == "Lecture.pptx"
    assert not blob.called
    assert all(block.type == "text" for block in result.content)


@respx.mock
async def test_download_shows_a_small_image_inline() -> None:
    respx.get(f"{API}/files/6").mock(
        return_value=httpx.Response(200, json=_file(6, "pinout.png", "image/png", len(PNG_1PX)))
    )
    respx.get(f"{BASE}/files/6/download").mock(return_value=httpx.Response(200, content=PNG_1PX))

    async with Client(server.mcp) as c:
        result = await c.call_tool("download_course_file", {"file_id": 6})

    kinds = [block.type for block in result.content]
    assert kinds == ["text", "image"]
    assert result.content[1].mime_type == "image/png"
    assert result.structured_content is not None
    assert result.structured_content["download_url"].endswith("verifier=v6")


@respx.mock
async def test_download_does_not_fetch_an_image_too_large_to_show() -> None:
    size = server.INLINE_IMAGE_MAX_BYTES + 1
    respx.get(f"{API}/files/7").mock(
        return_value=httpx.Response(200, json=_file(7, "board.png", "image/png", size))
    )
    blob = respx.get(f"{BASE}/files/7/download")

    async with Client(server.mcp) as c:
        result = await c.call_tool("download_course_file", {"file_id": 7})

    assert not blob.called
    assert [block.type for block in result.content] == ["text"]
    assert result.structured_content is not None
    assert "Too large" in result.structured_content["note"]
    assert result.structured_content["download_url"]


@respx.mock
async def test_download_of_a_locked_file_says_when_it_opens() -> None:
    locked = _file(8, "Week 9.pdf", "application/pdf", 10, url="", locked_for_user=True)
    locked["unlock_at"] = "2026-10-19T04:00:00Z"
    respx.get(f"{API}/files/8").mock(return_value=httpx.Response(200, json=locked))

    async with Client(server.mcp) as c:
        row = (await c.call_tool("download_course_file", {"file_id": 8})).structured_content

    assert row is not None
    assert row["locked"] is True
    assert row["unlock_at"] == "2026-10-19T04:00:00Z"
    assert "2026-10-19" in row["note"]
    assert "download_url" not in row


@respx.mock
async def test_read_course_file_points_at_the_original_when_it_has_no_text() -> None:
    respx.get(f"{API}/files/9").mock(
        return_value=httpx.Response(200, json=_file(9, "lib.zip", "application/zip", 4))
    )
    respx.get(f"{BASE}/files/9/download").mock(
        return_value=httpx.Response(200, content=b"PK\x03\x04")
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("read_course_file", {"file_id": 9})).data

    assert result["text"] is None
    assert result["download_url"].endswith("verifier=v9")
    assert "download_url" in result["note"]


@respx.mock
async def test_file_rows_leave_out_the_link_and_other_bulk() -> None:
    respx.get(f"{API}/courses/1/files").mock(
        return_value=httpx.Response(200, json=[_file(5, "a.pdf", "application/pdf", 10)])
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_files", {"course_id": 1})).data

    assert result["files"] == [
        {
            "id": 5,
            "display_name": "a.pdf",
            "content_type": "application/pdf",
            "size": 10,
            "updated_at": "2026-08-25T00:00:00Z",
            "locked": False,
        }
    ]


def _mock_hidden_files_course(course_id: int) -> None:
    """A course like Linear Algebra or US History: Files and Pages refused,
    the files still linked from modules, the syllabus and assignments."""
    base = f"{API}/courses/{course_id}"
    respx.get(f"{base}/files").mock(return_value=httpx.Response(403, text="{}"))
    respx.get(f"{base}/modules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Welcome",
                    "items": [
                        {"id": 10, "title": "Syllabus.docx", "type": "File", "content_id": 500},
                        {"id": 11, "title": "Week 1", "type": "Page", "page_url": "week-1"},
                    ],
                }
            ],
        )
    )
    respx.get(base, params={"include[]": "syllabus_body"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": course_id,
                "syllabus_body": '<a href="/courses/9/files/500?wrap=1" title="Full.docx">here</a>',
            },
        )
    )
    respx.get(f"{base}/front_page").mock(return_value=httpx.Response(404, text="{}"))
    respx.get(f"{base}/assignments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 3,
                    "name": "Project 1",
                    "description": '<a href="/courses/9/files/501/download">data.xlsx</a>',
                }
            ],
        )
    )
    respx.get(f"{base}/pages").mock(return_value=httpx.Response(404, text="{}"))
    respx.get(f"{base}/pages/week-1").mock(
        return_value=httpx.Response(
            200,
            json={"title": "Week 1", "body": '<img src="/courses/9/files/502/x" alt="a.png">'},
        )
    )
    respx.get(f"{base}/discussion_topics").mock(
        return_value=httpx.Response(403, text='{"status":"unauthorized"}')
    )
    respx.get(url__startswith=f"{API}/announcements").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 4,
                    "title": "Updated handout",
                    "message": "<p>See attached.</p>",
                    "attachments": [{"id": 503, "display_name": "handout-v2.pdf"}],
                }
            ],
        )
    )


@respx.mock
async def test_hidden_files_tab_lists_the_files_the_course_links_to() -> None:
    _mock_courses()
    _mock_hidden_files_course(9)

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_files", {"course_id": 9})).data

    assert result["files_tab_hidden"] is True
    assert "read_course_file" in result["note"]
    assert result["sources_unavailable"] == ["discussions"]
    by_id = {f["id"]: f for f in result["files"]}
    assert list(by_id) == [500, 501, 502, 503]
    # The module's label wins, and every place the file is linked is kept.
    assert by_id[500] == {
        "id": 500,
        "display_name": "Syllabus.docx",
        "linked_from": ["module: Welcome", "syllabus"],
    }
    assert by_id[501]["linked_from"] == ["assignment: Project 1"]
    # Pages were hidden too, so the module's page was read on its own.
    assert by_id[502] == {"id": 502, "display_name": "a.png", "linked_from": ["page: Week 1"]}
    assert by_id[503]["display_name"] == "handout-v2.pdf"


@respx.mock
async def test_content_tools_carry_the_files_they_link_to() -> None:
    _mock_courses()
    body = '<p><a href="/courses/1/files/77" title="lab.pdf">Lab</a></p>'
    respx.get(f"{API}/courses/1/pages/week-1").mock(
        return_value=httpx.Response(200, json={"url": "week-1", "title": "Week 1", "body": body})
    )
    respx.get(f"{API}/courses/1/pages").mock(
        return_value=httpx.Response(200, json=[{"url": "week-1", "title": "Week 1"}])
    )
    respx.get(f"{API}/courses/1", params={"include[]": "syllabus_body"}).mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Open", "syllabus_body": body})
    )
    respx.get(f"{API}/courses/1/assignments/5").mock(
        return_value=httpx.Response(200, json={**_assignment(5, 1), "description": body})
    )
    respx.get(f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(200, json=[{**_assignment(5, 1), "description": body}])
    )

    async with Client(server.mcp) as c:
        page = (await c.call_tool("get_page", {"course_id": 1, "page_url": "week-1"})).data
        syllabus = (await c.call_tool("get_syllabus", {"course_id": 1})).data
        assignment = (
            await c.call_tool(
                "get_assignment", {"course_id": 1, "assignment_id": 5, "include_feedback": False}
            )
        ).data
        pages = (await c.call_tool("list_pages", {"course_id": 1})).data
        assignments = (await c.call_tool("list_assignments", {"course_id": 1})).data

    expected = [{"id": 77, "name": "lab.pdf"}]
    assert page["linked_files"] == expected
    assert syllabus["linked_files"] == expected
    assert assignment["linked_files"] == expected
    # Lists carry no bodies, so they carry no links either.
    assert "linked_files" not in pages["pages"][0]
    assert "linked_files" not in assignments["assignments"][0]


@respx.mock
async def test_module_file_items_carry_a_file_id() -> None:
    respx.get(f"{API}/courses/1/modules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Week 1",
                    "items": [
                        {"id": 10, "title": "Notes.pdf", "type": "File", "content_id": 500},
                        {"id": 11, "title": "Quiz 1", "type": "Quiz", "content_id": 600},
                    ],
                }
            ],
        )
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("list_modules", {"course_id": 1})).data

    file_item, quiz_item = result["modules"][0]["items"]
    assert file_item["file_id"] == 500 and "content_id" not in file_item
    assert quiz_item["content_id"] == 600 and "file_id" not in quiz_item
