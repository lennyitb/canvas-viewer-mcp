"""MCP server exposing Canvas coursework.

Two conventions run through every tool here.

**Unavailability is data, not failure.** Instructors disable course tabs, and
Canvas reports a disabled tab as 403 or 404 rather than an empty list. On a
real account that affects five courses of eleven for files and seven of eleven
for pages. A tool that raised in those cases would make a whole-account sweep
impossible, so each returns an ``unavailable`` note and the sweep continues.

**Tools report; they do not conclude.** Nothing here decides what is overdue,
outstanding, or upcoming. The tools return the fields that make such a
judgement possible -- notably ``created_at`` alongside ``due_at`` -- and leave
the judgement to the caller, who can read the assignment text and the
announcements before deciding.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp import FastMCP

from .canvas.assignments import fetch_assignments, flatten_assignment
from .canvas.client import CanvasClient
from .canvas.content import (
    fetch_announcements,
    fetch_discussion,
    fetch_discussions,
    fetch_modules,
    fetch_page,
    fetch_pages,
)
from .canvas.errors import CanvasAuthError, CanvasNotFoundError
from .canvas.files import fetch_files, read_file
from .config import Config

INSTRUCTIONS = """
Read-only access to the user's Canvas LMS account.

Canvas due dates are frequently unreliable. Courses are commonly copied from a
previous term without the dates being rolled forward, which leaves assignments
carrying due dates a year in the past that Canvas then reports as `missing`.
Every assignment therefore carries `created_at` and `updated_at` alongside
`due_at`: an assignment created weeks ago but dated last year is a stale copy,
not overdue work.

These tools report what Canvas holds and classify nothing. When the real
deadline matters, read the assignment body, the course announcements, and the
module ordering before concluding anything from `due_at` alone.
""".strip()

mcp: FastMCP[Any] = FastMCP(name="canvas-viewer", instructions=INSTRUCTIONS)

_client: CanvasClient | None = None
_client_lock = asyncio.Lock()
_courses_cache: tuple[float, list[dict[str, Any]]] | None = None
COURSES_TTL_SECONDS = 300.0


async def get_client() -> CanvasClient:
    global _client
    async with _client_lock:
        if _client is None:
            _client = CanvasClient(Config.from_env())
        return _client


async def _courses() -> list[dict[str, Any]]:
    """Active courses, cached briefly.

    The course list is needed by nearly every tool to label results, changes
    rarely, and costs a request each time. Caching it keeps multi-course
    sweeps from spending a large share of their request budget re-fetching it.
    """
    global _courses_cache
    now = asyncio.get_running_loop().time()
    if _courses_cache and now - _courses_cache[0] < COURSES_TTL_SECONDS:
        return _courses_cache[1]

    client = await get_client()
    courses = await client.active_courses()
    _courses_cache = (now, courses)
    return courses


def _unavailable(exc: Exception) -> dict[str, Any]:
    reason = (
        "This course has the feature disabled or hidden from students."
        if isinstance(exc, CanvasAuthError | CanvasNotFoundError)
        else str(exc)
    )
    return {"unavailable": True, "reason": reason}


# ---- courses -----------------------------------------------------------------


@mcp.tool
async def list_courses() -> list[dict[str, Any]]:
    """List the user's active Canvas courses.

    Includes non-teaching shells (orientation, placement, support resources)
    alongside real courses; the term and course code distinguish them.
    """
    out = []
    for c in await _courses():
        enrolments = c.get("enrollments") or []
        scores = [
            e.get("computed_current_score")
            for e in enrolments
            if e.get("computed_current_score") is not None
        ]
        term = c.get("term") or {}
        out.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "course_code": c.get("course_code"),
                "term": term.get("name") if isinstance(term, dict) else None,
                "current_score": scores[0] if scores else None,
                "start_at": c.get("start_at"),
                "end_at": c.get("end_at"),
            }
        )
    return out


# ---- assignments -------------------------------------------------------------


@mcp.tool
async def list_assignments(course_id: int | None = None) -> dict[str, Any]:
    """List assignments, with no date filtering of any kind.

    Every assignment in scope is returned regardless of due date, including
    those with no due date at all. Bodies are omitted for size; use
    `get_assignment` for one assignment's full text.

    Compare `due_at` against `created_at` before treating anything as overdue.
    """
    client = await get_client()
    courses = await _courses()
    if course_id is not None:
        courses = [c for c in courses if c["id"] == course_id]
        if not courses:
            return {"error": f"No active course with id {course_id}."}

    results: list[dict[str, Any]] = []
    for course in courses:
        try:
            items = await fetch_assignments(client, course["id"], course_name=course.get("name"))
        except (CanvasAuthError, CanvasNotFoundError) as exc:
            results.append({"course_id": course["id"], **_unavailable(exc)})
            continue
        results.extend(a.model_dump() for a in items)

    return {"count": len(results), "assignments": results}


@mcp.tool
async def get_assignment(course_id: int, assignment_id: int) -> dict[str, Any]:
    """Fetch one assignment including its full description.

    The description often names the real deadline in prose when the `due_at`
    field is wrong or absent.
    """
    client = await get_client()
    raw = await client.get(
        f"courses/{course_id}/assignments/{assignment_id}", {"include[]": ["submission"]}
    )
    course = next((c for c in await _courses() if c["id"] == course_id), None)
    assignment = flatten_assignment(
        raw, course_name=course.get("name") if course else None, include_description=True
    )
    return assignment.model_dump()


# ---- announcements and discussions -------------------------------------------


@mcp.tool
async def list_announcements(days: int = 21) -> dict[str, Any]:
    """Recent announcements across all active courses, with their full text.

    Announcements are where date changes are usually communicated, so these
    are often the best evidence of what is genuinely due when assignment
    metadata disagrees.
    """
    client = await get_client()
    course_ids = [c["id"] for c in await _courses()]
    start = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    topics = await fetch_announcements(client, course_ids, start_date=start)
    return {"count": len(topics), "since": start, "announcements": [t.model_dump() for t in topics]}


@mcp.tool
async def list_discussions(course_id: int) -> dict[str, Any]:
    """List discussion topics in a course. Bodies omitted; use `get_discussion`."""
    client = await get_client()
    try:
        topics = await fetch_discussions(client, course_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {"count": len(topics), "discussions": [t.model_dump() for t in topics]}


@mcp.tool
async def get_discussion(course_id: int, topic_id: int) -> dict[str, Any]:
    """Fetch one discussion topic with its top-level replies."""
    client = await get_client()
    try:
        topic, replies = await fetch_discussion(client, course_id, topic_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {
        "topic": topic.model_dump(),
        "reply_count": len(replies),
        "replies": [r.model_dump() for r in replies],
    }


# ---- files, pages, modules ---------------------------------------------------


@mcp.tool
async def list_files(course_id: int) -> dict[str, Any]:
    """List a course's files. Many courses hide the Files tab from students."""
    client = await get_client()
    try:
        files = await fetch_files(client, course_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {"count": len(files), "files": [f.model_dump() for f in files]}


@mcp.tool
async def read_course_file(file_id: int) -> dict[str, Any]:
    """Extract readable text from one Canvas file.

    Handles PDF and text-based formats. Scanned PDFs contain no extractable
    text and are reported as such rather than returned empty.
    """
    client = await get_client()
    try:
        return (await read_file(client, file_id)).model_dump()
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)


@mcp.tool
async def list_pages(course_id: int) -> dict[str, Any]:
    """List a course's wiki pages. Many courses hide the Pages tab."""
    client = await get_client()
    try:
        pages = await fetch_pages(client, course_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {"count": len(pages), "pages": [p.model_dump() for p in pages]}


@mcp.tool
async def get_page(course_id: int, page_url: str) -> dict[str, Any]:
    """Fetch one wiki page's full body. `page_url` is the slug from `list_pages`."""
    client = await get_client()
    try:
        return (await fetch_page(client, course_id, page_url)).model_dump()
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)


@mcp.tool
async def list_modules(course_id: int) -> dict[str, Any]:
    """List a course's modules and their items.

    Modules encode the order an instructor intends work to be done in, which
    is the ordering signal that survives when due dates have gone stale.
    """
    client = await get_client()
    try:
        modules = await fetch_modules(client, course_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {"count": len(modules), "modules": [m.model_dump() for m in modules]}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
