"""Tests for assignment flattening.

The recurring theme: the flattener must not quietly lose or "helpfully" fill in
anything. A null due date is a signal, not missing data.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from canvas_viewer_mcp.canvas.assignments import (
    fetch_assignments,
    flatten_assignment,
)
from canvas_viewer_mcp.canvas.client import CanvasClient
from canvas_viewer_mcp.config import Config

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"


@pytest.fixture
def config() -> Config:
    return Config(base_url=BASE, token="test-token", timeout_seconds=5.0, max_pages=5)


def raw_assignment(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 1,
        "course_id": 99,
        "name": "HW 1",
        "due_at": "2026-09-25T03:59:59Z",
        "unlock_at": None,
        "lock_at": None,
        "created_at": "2026-08-16T19:57:57Z",
        "updated_at": "2026-08-24T19:08:57Z",
        "points_possible": 10.0,
        "published": True,
        "availability_status": {"status": "open", "date": None},
        "submission_types": ["online_upload"],
        "html_url": f"{BASE}/courses/99/assignments/1",
        "description": "<p>Read chapter 3.</p>",
    }
    base.update(overrides)
    return base


def test_null_due_date_is_preserved_not_defaulted() -> None:
    a = flatten_assignment(raw_assignment(due_at=None))
    assert a.due_at is None, "a null due date is the signal; it must survive intact"


def test_stale_year_old_due_date_keeps_creation_timestamps() -> None:
    """The real-world case: a course copied from last year with dates never
    rolled forward. Without created_at/updated_at this is indistinguishable
    from genuinely overdue work."""
    a = flatten_assignment(
        raw_assignment(due_at="2025-09-25T03:59:59Z", created_at="2026-08-16T19:57:57Z")
    )
    assert a.due_at == "2025-09-25T03:59:59Z"
    assert a.created_at == "2026-08-16T19:57:57Z"
    assert a.updated_at == "2026-08-24T19:08:57Z"


def test_submission_state_survives_flattening() -> None:
    a = flatten_assignment(
        raw_assignment(
            submission={
                "submitted_at": None,
                "workflow_state": "unsubmitted",
                "missing": True,
                "late": False,
                "excused": False,
                "score": None,
                "grade": None,
            }
        )
    )
    assert a.submission is not None
    assert a.submission.missing is True
    assert a.submission.submitted_at is None


def test_excused_is_not_confused_with_unsubmitted() -> None:
    a = flatten_assignment(raw_assignment(submission={"submitted_at": None, "excused": True}))
    assert a.submission is not None
    assert a.submission.excused is True


def test_absent_submission_becomes_none_not_an_empty_shell() -> None:
    a = flatten_assignment(raw_assignment())
    assert a.submission is None


def test_description_is_omitted_by_default_and_included_on_request() -> None:
    assert flatten_assignment(raw_assignment()).description is None

    a = flatten_assignment(raw_assignment(), include_description=True)
    assert a.description == "Read chapter 3."


def test_unpublished_flag_survives() -> None:
    assert flatten_assignment(raw_assignment(published=False)).published is False


def test_missing_optional_fields_do_not_raise() -> None:
    a = flatten_assignment({"id": 5, "course_id": 9})
    assert a.name == "(unnamed)"
    assert a.points_possible is None
    assert a.availability is None


@respx.mock
async def test_fetch_sends_no_date_filter_or_bucket(config: Config) -> None:
    """Canvas offers a `bucket` parameter that filters by date server-side.
    Using it would reintroduce exactly the blind spot this project removes,
    so its absence is asserted rather than assumed."""
    route = respx.get(f"{API}/courses/99/assignments").mock(
        return_value=httpx.Response(200, json=[raw_assignment()])
    )

    async with CanvasClient(config) as client:
        await fetch_assignments(client, 99)

    params = route.calls[0].request.url.params
    assert "bucket" not in params
    assert not any(k.startswith("due") for k in params), "must not filter by date"
    assert params["include[]"] == "submission"


@respx.mock
async def test_fetch_returns_every_assignment_across_pages(config: Config) -> None:
    respx.get(f"{API}/courses/99/assignments").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[raw_assignment(id=1), raw_assignment(id=2)],
                headers={"Link": f'<{API}/courses/99/assignments?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[raw_assignment(id=3, due_at=None)]),
        ]
    )

    async with CanvasClient(config) as client:
        items = await fetch_assignments(client, 99, course_name="Circuits")

    assert [i.id for i in items] == [1, 2, 3]
    assert items[2].due_at is None
    assert all(i.course_name == "Circuits" for i in items)
