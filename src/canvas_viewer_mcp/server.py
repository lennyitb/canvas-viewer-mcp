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
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import __version__
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
from .canvas.errors import CanvasAuthError, CanvasError, CanvasNotFoundError
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

Graded discussions need extra scrutiny before being called done. Canvas records
a discussion submission the instant the first post goes up and has no field at
all for the replies to classmates that most rubrics also require, so a
discussion reads `submitted` or even `graded` while replies are still owed.
Its `due_at` is usually only the *post* deadline; the reply deadline is later
and stated in prose in the description. Never treat a `discussion_topic`
assignment as complete from its submission state -- read the description for
the reply requirement and call `get_discussion`, which reports how many entries
the user actually posted and which of them were replies to other people.

These tools report what Canvas holds and classify nothing. When the real
deadline matters, read the assignment body, the course announcements, and the
module ordering before concluding anything from `due_at` alone.
""".strip()

DISCUSSION_CAVEAT = (
    "Graded discussion. Canvas marks this submitted on the first post alone and "
    "tracks no reply requirement, so `submission` state cannot tell you whether "
    "this is finished, and `due_at` is typically the post deadline with a later "
    "reply deadline named only in the description. Read the description, then "
    "call `get_discussion` to count the user's own posts and replies."
)

# Short enough to sit on every row of a sweep without swamping the payload.
ROW_CAVEAT = (
    "discussion: `submitted` reflects the main post only; required replies to "
    "classmates are not tracked by Canvas and may still be outstanding"
)

PARTICIPATION_NOTE = (
    "`my_participation` counts what the user actually posted. Canvas does not "
    "record the reply requirement anywhere structured, so compare these counts "
    "against the requirement stated in the topic `message` before deciding this "
    "discussion is complete."
)


def _is_discussion(row: dict[str, Any]) -> bool:
    return "discussion_topic" in (row.get("submission_types") or [])


mcp: FastMCP[Any] = FastMCP(name="canvas-viewer", instructions=INSTRUCTIONS)

_client: CanvasClient | None = None
_client_lock = asyncio.Lock()
_courses_cache: tuple[float, list[dict[str, Any]]] | None = None
_self_cache: dict[str, Any] | None = None
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


async def _self() -> dict[str, Any] | None:
    """The authenticated user, cached for the process.

    Needed to tell the user's own discussion entries from a classmate's.
    Returns None rather than raising: an account that cannot read its own
    profile should lose the participation counts, not the whole thread.
    """
    global _self_cache
    if _self_cache is None:
        client = await get_client()
        try:
            _self_cache = await client.current_user()
        except CanvasError:
            return None
    return _self_cache


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

    Rows whose `submission_types` include `discussion_topic` carry a
    `completion_caveat`: their submission state covers the main post only and
    says nothing about required replies. Do not count those as done here.
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
        for assignment in items:
            row = assignment.model_dump()
            if _is_discussion(row):
                row["completion_caveat"] = ROW_CAVEAT
            results.append(row)

    discussions = [r for r in results if r.get("completion_caveat")]
    out: dict[str, Any] = {"count": len(results), "assignments": results}
    if discussions:
        out["discussion_count"] = len(discussions)
        out["discussion_note"] = DISCUSSION_CAVEAT
    return out


@mcp.tool
async def get_assignment(course_id: int, assignment_id: int) -> dict[str, Any]:
    """Fetch one assignment including its full description.

    The description often names the real deadline in prose when the `due_at`
    field is wrong or absent. For a discussion it also names the reply
    requirement and the separate, later reply deadline, neither of which Canvas
    exposes as a field.
    """
    client = await get_client()
    raw = await client.get(
        f"courses/{course_id}/assignments/{assignment_id}", {"include[]": ["submission"]}
    )
    course = next((c for c in await _courses() if c["id"] == course_id), None)
    assignment = flatten_assignment(
        raw, course_name=course.get("name") if course else None, include_description=True
    )
    row = assignment.model_dump()
    if _is_discussion(row):
        row["completion_caveat"] = DISCUSSION_CAVEAT
        row["discussion_topic_id"] = (raw.get("discussion_topic") or {}).get("id")
    return row


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
    """Fetch one discussion topic with every entry in its thread.

    Nested replies are included, not just top-level posts -- peer replies are
    always nested, so a top-level-only view cannot show whether the user
    replied to anyone.

    `my_participation` summarises the user's own entries, separating top-level
    posts from replies to other people. That is the only way to check a
    "post once, reply to two classmates" requirement, because Canvas records
    the submission on the first post and tracks the replies nowhere.
    """
    client = await get_client()
    try:
        topic, entries, full_thread = await fetch_discussion(client, course_id, topic_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)

    me = await _self()
    my_id = me.get("id") if me else None

    result: dict[str, Any] = {
        "topic": topic.model_dump(),
        "entry_count": len(entries),
        # Canvas's own subentry total. A gap against `entry_count` means part
        # of the thread did not come back, so participation counts are floors.
        "canvas_subentry_count": topic.reply_count,
        "full_thread": full_thread,
        "note": PARTICIPATION_NOTE,
    }

    if my_id is None:
        result["my_participation"] = {
            "unavailable": True,
            "reason": (
                "Could not identify the current Canvas user, so entries cannot be attributed."
            ),
        }
    else:
        mine = [e for e in entries if e.user_id == my_id]
        result["my_participation"] = {
            "user_id": my_id,
            "display_name": me.get("name") if me else None,
            "total_entries": len(mine),
            "top_level_posts": sum(1 for e in mine if e.depth == 0),
            "replies_to_others": sum(
                1 for e in mine if e.depth > 0 and e.parent_user_id not in (None, my_id)
            ),
            "replies_to_self": sum(1 for e in mine if e.depth > 0 and e.parent_user_id == my_id),
            "latest_entry_at": max((e.created_at for e in mine if e.created_at), default=None),
            "counts_are_complete": full_thread,
        }

    if not full_thread:
        result["warning"] = (
            "Canvas would not serve the full thread, so only top-level entries "
            "were retrieved. Nested replies -- including the user's own -- are "
            "missing, and the participation counts below are lower bounds."
        )

    result["entries"] = [e.model_dump() for e in entries]
    return result


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


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    """Liveness probe.

    Deliberately does not call Canvas. A health check that depended on an
    external service would report this container unhealthy -- and invite an
    orchestrator to restart it -- during a Canvas outage it can do nothing
    about. Canvas reachability is a matter for the tools, which can say so.
    """
    return JSONResponse({"status": "ok", "version": __version__})


def enable_http_auth() -> None:
    """Put the OAuth server in front of the MCP endpoint.

    Only applied for HTTP. Over stdio the transport is a pipe to a local
    process that already runs as the user, so there is no one to authenticate
    and demanding a browser login would make local testing impossible.
    """
    from .auth.login import register_login_routes
    from .auth.provider import SingleUserOAuthProvider
    from .config import AuthConfig

    provider = SingleUserOAuthProvider(AuthConfig.from_env())
    mcp.auth = provider
    register_login_routes(mcp, provider)


def main() -> None:
    """Entry point. MCP_TRANSPORT selects stdio (default) or http."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if transport not in ("http", "streamable-http"):
        raise SystemExit(f"Unsupported MCP_TRANSPORT {transport!r}; use 'stdio' or 'http'.")

    enable_http_auth()
    mcp.run(
        transport="http",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
