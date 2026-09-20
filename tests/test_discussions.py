"""Tests for the discussion-completeness contract.

A graded discussion is the one assignment type whose submission state lies by
omission: Canvas records it submitted on the first post and has no field for
the replies to classmates a rubric also demands. Two things follow, and both
are asserted here -- the thread must come back whole so the user's replies can
actually be counted, and every surface that reports a discussion must carry the
caveat that its submission state is not a completion state.
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

ME = {"id": 500, "name": "Test Student"}
COURSES = [{"id": 1, "name": "History", "course_code": "HIS", "enrollments": []}]

TOPIC = {
    "id": 9,
    "title": "Week 4 Discussion",
    "assignment_id": 77,
    # Canvas's own total counts every nested entry, not just the top-level ones.
    "discussion_subentry_count": 5,
    "message": "<p>Post a question by Wednesday. Reply to two classmates by Sunday.</p>",
}

# One top-level post each from the user and two classmates; the user replies to
# one classmate, and also to their own post. Only the first of those two counts
# toward a "reply to classmates" requirement.
VIEW = {
    "participants": [
        {"id": 500, "display_name": "Test Student"},
        {"id": 600, "display_name": "Classmate A"},
        {"id": 700, "display_name": "Classmate B"},
    ],
    "view": [
        {
            "id": 1,
            "user_id": 500,
            "parent_id": None,
            "created_at": "2026-09-16T03:31:37Z",
            "message": "<p>My question</p>",
            "replies": [
                {
                    "id": 2,
                    "user_id": 500,
                    "parent_id": 1,
                    "created_at": "2026-09-16T04:00:00Z",
                    "message": "<p>Following up on myself</p>",
                }
            ],
        },
        {
            "id": 3,
            "user_id": 600,
            "parent_id": None,
            "created_at": "2026-09-15T10:00:00Z",
            "message": "<p>A's question</p>",
            "replies": [
                {
                    "id": 4,
                    "user_id": 500,
                    "parent_id": 3,
                    "created_at": "2026-09-17T09:00:00Z",
                    "message": "<p>My reply to A</p>",
                }
            ],
        },
        {"id": 5, "user_id": 700, "parent_id": None, "message": "<p>B's question</p>"},
    ],
}


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


def _mock_thread(view: dict[str, Any] | None = None) -> None:
    respx.get(f"{API}/users/self").mock(return_value=httpx.Response(200, json=ME))
    respx.get(f"{API}/courses/1/discussion_topics/9").mock(
        return_value=httpx.Response(200, json=TOPIC)
    )
    respx.get(f"{API}/courses/1/discussion_topics/9/view").mock(
        return_value=httpx.Response(200, json=view if view is not None else VIEW)
    )


@respx.mock
async def test_nested_replies_are_returned_not_just_top_level_posts() -> None:
    """The bug this guards: `/entries` returns top-level posts only, so a
    student's replies -- which are always nested -- were invisible."""
    _mock_thread()

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_discussion", {"course_id": 1, "topic_id": 9})).data

    assert result["entry_count"] == 5
    assert result["full_thread"] is True
    assert {e["id"] for e in result["entries"]} == {1, 2, 3, 4, 5}
    assert [e["depth"] for e in result["entries"] if e["id"] in (2, 4)] == [1, 1]


@respx.mock
async def test_participation_separates_replies_to_others_from_replies_to_self() -> None:
    """A "reply to two classmates" requirement is not met by replying to
    yourself, so the two are counted apart."""
    _mock_thread()

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_discussion", {"course_id": 1, "topic_id": 9})).data

    mine = result["my_participation"]
    assert mine["user_id"] == 500
    assert mine["top_level_posts"] == 1
    assert mine["replies_to_others"] == 1
    assert mine["replies_to_self"] == 1
    assert mine["total_entries"] == 3
    assert mine["latest_entry_at"] == "2026-09-17T09:00:00Z"
    assert mine["counts_are_complete"] is True


@respx.mock
async def test_entries_carry_the_author_of_the_post_they_reply_to() -> None:
    _mock_thread()

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_discussion", {"course_id": 1, "topic_id": 9})).data

    by_id = {e["id"]: e for e in result["entries"]}
    assert by_id[4]["parent_user_id"] == 600
    assert by_id[4]["author"] == "Test Student"
    assert by_id[2]["parent_user_id"] == 500


@respx.mock
async def test_unmaterialized_view_falls_back_and_says_the_counts_are_partial() -> None:
    """Canvas builds the thread view asynchronously and answers 503 meanwhile.
    Degrading silently would hand back participation counts that look
    authoritative while missing every nested reply."""
    respx.get(f"{API}/users/self").mock(return_value=httpx.Response(200, json=ME))
    respx.get(f"{API}/courses/1/discussion_topics/9").mock(
        return_value=httpx.Response(200, json=TOPIC)
    )
    respx.get(f"{API}/courses/1/discussion_topics/9/view").mock(
        return_value=httpx.Response(503, json={"status": "in_progress"})
    )
    respx.get(f"{API}/courses/1/discussion_topics/9/entries").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "user_id": 500, "parent_id": None, "message": "<p>My question</p>"}],
        )
    )

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_discussion", {"course_id": 1, "topic_id": 9})).data

    assert result["full_thread"] is False
    assert "lower bounds" in result["warning"]
    assert result["my_participation"]["counts_are_complete"] is False
    assert result["canvas_subentry_count"] == 5, "Canvas's own total exposes the shortfall"


@respx.mock
async def test_recent_posts_missing_from_the_view_are_merged_in() -> None:
    """Canvas serves posts made since the view was materialized in a separate
    `new_entries` list -- exactly where a reply posted minutes ago lands."""
    view = {
        **VIEW,
        "new_entries": [
            {
                "id": 6,
                "user_id": 500,
                "parent_id": 5,
                "created_at": "2026-09-20T18:00:00Z",
                "message": "<p>My reply to B</p>",
            }
        ],
    }
    _mock_thread(view)

    async with Client(server.mcp) as c:
        result = (await c.call_tool("get_discussion", {"course_id": 1, "topic_id": 9})).data

    assert 6 in {e["id"] for e in result["entries"]}
    assert result["my_participation"]["replies_to_others"] == 2
