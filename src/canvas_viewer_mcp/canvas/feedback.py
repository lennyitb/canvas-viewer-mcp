"""Flatten the feedback on a submission: comments, rubric marks, grade.

Canvas keeps a grade on the submission, the instructor's comments in a
separate ``submission_comments`` list, and rubric marks in a
``rubric_assessment`` keyed by criterion id that means nothing without the
assignment's ``rubric`` beside it. This module joins those into one record.

As with assignments, it normalizes and does not classify. A comment reading
"resubmit by Friday" is actionable work, but deciding that is the caller's
job, with the comment text in hand.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .client import CanvasClient
from .html_text import TRUNCATION_NOTE

JsonObject = dict[str, Any]

# Comments are plain text and usually short; the cap is for the essay-length
# one that would otherwise crowd everything else out of a sweep.
COMMENT_MAX_CHARS = 2000

FEEDBACK_INCLUDES = ["submission_comments", "rubric_assessment"]

_WHITESPACE = re.compile(r"\s+")


class FeedbackAttachment(BaseModel):
    display_name: str | None = None
    content_type: str | None = None
    url: str | None = None


class FeedbackComment(BaseModel):
    author_id: int | None = None
    author_name: str | None = None
    # Whether the user wrote it. None when the current user is unknown.
    mine: bool | None = None
    created_at: str | None = None
    edited_at: str | None = None
    attempt: int | None = None
    comment: str | None = None
    attachments: list[FeedbackAttachment] = []
    media_comment: bool = False


class RubricResult(BaseModel):
    """One rubric criterion with the mark it received, if any."""

    criterion: str | None = None
    points: float | None = None
    points_possible: float | None = None
    rating: str | None = None
    comments: str | None = None


class Feedback(BaseModel):
    score: float | None = None
    grade: str | None = None
    graded_at: str | None = None
    # Null while the instructor has not posted grades; comments may be hidden.
    posted_at: str | None = None
    comments: list[FeedbackComment] = []
    rubric: list[RubricResult] = []


def _cap(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    text = text.strip()
    if len(text) > COMMENT_MAX_CHARS:
        removed = len(text) - COMMENT_MAX_CHARS
        text = text[:COMMENT_MAX_CHARS] + TRUNCATION_NOTE.format(removed=removed)
    return text


def flatten_comment(raw: JsonObject, my_id: int | None) -> FeedbackComment:
    author_id = raw.get("author_id")
    return FeedbackComment(
        author_id=author_id,
        author_name=raw.get("author_name"),
        mine=None if my_id is None else author_id == my_id,
        created_at=raw.get("created_at"),
        edited_at=raw.get("edited_at"),
        attempt=raw.get("attempt"),
        comment=_cap(raw.get("comment")),
        attachments=[
            FeedbackAttachment(
                display_name=a.get("display_name"),
                content_type=a.get("content-type") or a.get("content_type"),
                url=a.get("url"),
            )
            for a in raw.get("attachments") or []
            if isinstance(a, dict)
        ],
        media_comment=bool(raw.get("media_comment")),
    )


def flatten_rubric(
    rubric: list[JsonObject] | None, assessment: JsonObject | None
) -> list[RubricResult]:
    """Join rubric criteria with their marks, keeping unmarked criteria.

    An unmarked criterion stays in the list with ``points`` null, so a rubric
    marked only in part reads as such rather than as a shorter rubric.
    """
    assessment = assessment or {}
    out: list[RubricResult] = []
    for criterion in rubric or []:
        mark = assessment.get(str(criterion.get("id"))) or {}
        rating_id = mark.get("rating_id")
        rating = next(
            (r for r in criterion.get("ratings") or [] if rating_id and r.get("id") == rating_id),
            None,
        )
        name = criterion.get("description")
        out.append(
            RubricResult(
                # Rubric titles are table headers and arrive with the line
                # breaks that wrapped them in the editor.
                criterion=_WHITESPACE.sub(" ", name).strip() if name else None,
                points=mark.get("points"),
                points_possible=criterion.get("points"),
                rating=rating.get("description") if rating else None,
                comments=_cap(mark.get("comments")),
            )
        )
    return out


def flatten_feedback(
    submission: JsonObject | None,
    rubric: list[JsonObject] | None,
    my_id: int | None,
) -> Feedback:
    submission = submission or {}
    return Feedback(
        score=submission.get("score"),
        grade=submission.get("grade"),
        graded_at=submission.get("graded_at"),
        posted_at=submission.get("posted_at"),
        comments=[
            flatten_comment(c, my_id)
            for c in submission.get("submission_comments") or []
            if isinstance(c, dict)
        ],
        rubric=flatten_rubric(rubric, submission.get("rubric_assessment")),
    )


async def fetch_feedback(
    client: CanvasClient,
    course_id: int,
    assignment_id: int,
    *,
    rubric: list[JsonObject] | None,
    my_id: int | None,
) -> Feedback:
    """The user's own submission for one assignment, with its feedback."""
    raw = await client.get(
        f"courses/{course_id}/assignments/{assignment_id}/submissions/self",
        {"include[]": FEEDBACK_INCLUDES},
    )
    return flatten_feedback(raw, rubric, my_id)


def _on_or_after(stamp: str | None, since: datetime) -> bool:
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) >= since
    except ValueError:
        return False


async def fetch_recent_feedback(
    client: CanvasClient,
    course_id: int,
    *,
    my_id: int | None,
    since: datetime,
) -> list[tuple[JsonObject, Feedback]]:
    """Submissions in one course graded or commented on by others since ``since``.

    One request per course. The window is applied here rather than through
    Canvas's ``graded_since``, because an instructor can comment without
    regrading and that comment is exactly what this should catch.

    Returns each submission's embedded assignment alongside its feedback.
    """
    raw = await client.paginate(
        f"courses/{course_id}/students/submissions",
        {"student_ids[]": ["self"], "include[]": [*FEEDBACK_INCLUDES, "assignment"]},
    )
    out: list[tuple[JsonObject, Feedback]] = []
    for submission in raw:
        comments = submission.get("submission_comments") or []
        fresh_comment = any(
            _on_or_after(c.get("created_at"), since)
            for c in comments
            if isinstance(c, dict) and (my_id is None or c.get("author_id") != my_id)
        )
        if not (fresh_comment or _on_or_after(submission.get("graded_at"), since)):
            continue
        assignment = submission.get("assignment") or {}
        out.append((assignment, flatten_feedback(submission, assignment.get("rubric"), my_id)))
    return out
