"""Flatten Canvas assignment payloads into stable, compact rows.

Canvas returns ~73 keys per assignment, most of them grading-workflow plumbing
(anonymous peer review settings, moderated grading, LTI identifiers). This
module projects that down to the fields that carry signal and nothing else.

It normalizes. It does not classify. Nothing here decides whether an
assignment is stale, outstanding, or upcoming -- those are judgements that
depend on the question being asked, and they are made by the caller with the
assignment text in hand.

The fields kept are chosen so that judgement is *possible*. In particular
``created_at`` and ``updated_at`` are always present: an assignment created in
August 2026 whose ``due_at`` is September 2025 is a course copied from last
year with the dates never rolled forward, and without the creation timestamp
that is indistinguishable from genuinely overdue work.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .client import CanvasClient
from .html_text import html_to_markdown

JsonObject = dict[str, Any]


class Submission(BaseModel):
    """The current user's submission state for one assignment."""

    submitted_at: str | None = None
    workflow_state: str | None = None
    attempt: int | None = None
    missing: bool = False
    late: bool = False
    excused: bool = False
    score: float | None = None
    grade: str | None = None
    graded_at: str | None = None


class Assignment(BaseModel):
    """One assignment, flattened. Nulls are preserved, never defaulted away."""

    id: int
    course_id: int
    name: str
    course_name: str | None = None

    # Dates exactly as Canvas holds them, including None.
    due_at: str | None = None
    unlock_at: str | None = None
    lock_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    points_possible: float | None = None
    grading_type: str | None = None
    omit_from_final_grade: bool = False
    published: bool = True
    availability: str | None = None
    submission_types: list[str] = []
    assignment_group_id: int | None = None
    position: int | None = None
    html_url: str | None = None

    description: str | None = None
    submission: Submission | None = None


def flatten_submission(raw: JsonObject | None) -> Submission | None:
    if not raw:
        return None
    return Submission(
        submitted_at=raw.get("submitted_at"),
        workflow_state=raw.get("workflow_state"),
        attempt=raw.get("attempt"),
        missing=bool(raw.get("missing")),
        late=bool(raw.get("late")),
        excused=bool(raw.get("excused")),
        score=raw.get("score"),
        grade=raw.get("grade"),
        graded_at=raw.get("graded_at"),
    )


def flatten_assignment(
    raw: JsonObject,
    *,
    course_name: str | None = None,
    include_description: bool = False,
) -> Assignment:
    """Project one raw Canvas assignment onto the Assignment model."""
    availability = raw.get("availability_status") or {}

    return Assignment(
        id=raw["id"],
        course_id=raw["course_id"],
        name=raw.get("name") or "(unnamed)",
        course_name=course_name,
        due_at=raw.get("due_at"),
        unlock_at=raw.get("unlock_at"),
        lock_at=raw.get("lock_at"),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        points_possible=raw.get("points_possible"),
        grading_type=raw.get("grading_type"),
        omit_from_final_grade=bool(raw.get("omit_from_final_grade")),
        published=bool(raw.get("published", True)),
        availability=availability.get("status") if isinstance(availability, dict) else None,
        submission_types=list(raw.get("submission_types") or []),
        assignment_group_id=raw.get("assignment_group_id"),
        position=raw.get("position"),
        html_url=raw.get("html_url"),
        description=html_to_markdown(raw.get("description")) if include_description else None,
        submission=flatten_submission(raw.get("submission")),
    )


async def fetch_assignments(
    client: CanvasClient,
    course_id: int,
    *,
    course_name: str | None = None,
    include_descriptions: bool = False,
) -> list[Assignment]:
    """Fetch every assignment in a course.

    Deliberately passes no date filter and no bucket parameter. Canvas offers
    a ``bucket`` argument that would do the filtering server-side, and using it
    would reintroduce exactly the blind spot this project exists to remove.
    """
    raw = await client.paginate(
        f"courses/{course_id}/assignments",
        {"include[]": ["submission"]},
    )
    return [
        flatten_assignment(item, course_name=course_name, include_description=include_descriptions)
        for item in raw
    ]
