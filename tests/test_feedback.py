"""Tests for submission feedback: comments, rubric marks, and the recent sweep.

As with assignments, nothing may be lost or filled in. An unmarked rubric
criterion and an unposted grade are both signals, not gaps to tidy away.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from canvas_viewer_mcp.canvas.client import CanvasClient
from canvas_viewer_mcp.canvas.feedback import (
    COMMENT_MAX_CHARS,
    fetch_recent_feedback,
    flatten_feedback,
    flatten_rubric,
)
from canvas_viewer_mcp.config import Config

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"
ME = 7

RUBRIC: list[dict[str, Any]] = [
    {
        "id": "_1",
        "description": "Thesis",
        "points": 10.0,
        "ratings": [
            {"id": "r_full", "description": "Clear and arguable", "points": 10.0},
            {"id": "r_half", "description": "Present but vague", "points": 5.0},
        ],
    },
    {"id": "_2", "description": "Citations", "points": 5.0, "ratings": []},
]


@pytest.fixture
def config() -> Config:
    return Config(base_url=BASE, token="test-token", timeout_seconds=5.0, max_pages=5)


def comment(author_id: int, text: str, created_at: str = "2026-09-20T12:00:00Z") -> dict[str, Any]:
    return {
        "id": author_id * 100,
        "author_id": author_id,
        "author_name": "Me" if author_id == ME else "Dr. Instructor",
        "comment": text,
        "created_at": created_at,
        "edited_at": None,
        "attempt": 1,
    }


def test_rubric_join_keeps_unmarked_criteria_and_names_the_rating() -> None:
    results = flatten_rubric(
        RUBRIC, {"_1": {"points": 5.0, "rating_id": "r_half", "comments": "Sharpen this."}}
    )

    assert [r.criterion for r in results] == ["Thesis", "Citations"]
    assert results[0].points == 5.0
    assert results[0].points_possible == 10.0
    assert results[0].rating == "Present but vague"
    assert results[0].comments == "Sharpen this."
    assert results[1].points is None, "an unmarked criterion stays, with no points"
    assert results[1].points_possible == 5.0


def test_rubric_mark_with_unknown_rating_keeps_points_without_a_rating() -> None:
    results = flatten_rubric(RUBRIC, {"_1": {"points": 8.0, "rating_id": "r_gone"}})
    assert results[0].points == 8.0
    assert results[0].rating is None


def test_criterion_names_lose_their_wrapping_line_breaks() -> None:
    rubric = [{"id": "_1", "description": "Comprehension\n(Relevance of\nPost)", "points": 30}]
    assert flatten_rubric(rubric, None)[0].criterion == "Comprehension (Relevance of Post)"


def test_absent_rubric_is_an_empty_list() -> None:
    assert flatten_rubric(None, {"_1": {"points": 1}}) == []
    assert flatten_feedback({"score": 3.0}, None, ME).rubric == []


def test_comments_are_attributed_to_the_user_or_not() -> None:
    fb = flatten_feedback(
        {"submission_comments": [comment(ME, "Question?"), comment(1, "Answer.")]}, None, ME
    )
    assert [c.mine for c in fb.comments] == [True, False]


def test_attribution_is_unknown_when_the_user_is() -> None:
    fb = flatten_feedback({"submission_comments": [comment(1, "Hi")]}, None, None)
    assert fb.comments[0].mine is None
    assert fb.comments[0].comment == "Hi"


def test_attachments_and_media_comments_survive() -> None:
    raw = {
        **comment(1, ""),
        "attachments": [
            {"display_name": "marked-up.pdf", "content-type": "application/pdf", "url": "u"}
        ],
        "media_comment": {"media_id": "m1"},
    }
    fb = flatten_feedback({"submission_comments": [raw]}, None, ME)
    c = fb.comments[0]
    assert c.comment is None, "an empty comment body is None, not an empty string"
    assert c.attachments[0].display_name == "marked-up.pdf"
    assert c.attachments[0].content_type == "application/pdf"
    assert c.media_comment is True


def test_unposted_grade_keeps_posted_at_null() -> None:
    fb = flatten_feedback({"score": 9.0, "graded_at": "2026-09-20T00:00:00Z"}, None, ME)
    assert fb.posted_at is None
    assert fb.score == 9.0


def test_long_comment_is_truncated_with_a_marker() -> None:
    fb = flatten_feedback(
        {"submission_comments": [comment(1, "x" * (COMMENT_MAX_CHARS + 50))]}, None, ME
    )
    text = fb.comments[0].comment
    assert text is not None
    assert text.startswith("x" * COMMENT_MAX_CHARS)
    assert "truncated, 50 more characters" in text


@respx.mock
async def test_recent_sweep_filters_by_window_and_ignores_own_comments(config: Config) -> None:
    since = datetime(2026, 9, 15, tzinfo=UTC)
    route = respx.get(f"{API}/courses/99/students/submissions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {  # graded in the window
                    "graded_at": "2026-09-16T00:00:00Z",
                    "assignment": {"id": 1, "name": "Graded", "rubric": RUBRIC},
                },
                {  # graded long ago, but the instructor commented recently
                    "graded_at": "2026-08-01T00:00:00Z",
                    "submission_comments": [comment(1, "Resubmit by Friday")],
                    "assignment": {"id": 2, "name": "Commented"},
                },
                {  # only the user's own recent comment: not feedback
                    "graded_at": None,
                    "submission_comments": [comment(ME, "Is this right?")],
                    "assignment": {"id": 3, "name": "Own"},
                },
                {  # nothing recent
                    "graded_at": "2026-08-01T00:00:00Z",
                    "assignment": {"id": 4, "name": "Old"},
                },
            ],
        )
    )

    async with CanvasClient(config) as client:
        items = await fetch_recent_feedback(client, 99, my_id=ME, since=since)

    assert [a["name"] for a, _ in items] == ["Graded", "Commented"]
    assert len(items[0][1].rubric) == 2, "the embedded assignment rubric is joined"
    params = route.calls.last.request.url.params
    assert params.get_list("student_ids[]") == ["self"]
    assert set(params.get_list("include[]")) == {
        "submission_comments",
        "rubric_assessment",
        "assignment",
    }
