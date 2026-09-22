"""MCP server exposing Canvas coursework.

Three conventions run through every tool here.

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

**Lists are lean; detail is opt-in.** Every payload here lands in a language
model's context window, where a field costs the same whether or not it is
read. So list rows omit null fields, list tools omit bodies, and the tools
that can return a great deal (a whole discussion thread, every announcement
body) take parameters that default to the smaller answer. ``due_at`` is the
one null that is always written out, because its absence is the signal this
project exists to surface and an absent key is too easy to read past.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

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
    fetch_thread,
    summarise_participation,
)
from .canvas.errors import CanvasAuthError, CanvasError, CanvasNotFoundError
from .canvas.feedback import fetch_feedback, fetch_recent_feedback
from .canvas.files import fetch_files, read_file
from .canvas.html_text import REPLY_KEYWORDS, excerpt_sentences, html_to_markdown
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
assignment as complete from its submission state. To check every graded
discussion at once, call `list_discussion_participation`: one call, no post
bodies, and for each discussion the reply-requirement sentences from its
description next to a count of what the user actually posted, replies to
other people counted separately. `get_discussion` does the same for one topic
and can also return the thread itself when the posts need reading.

Instructor feedback -- comments, rubric marks, attached files -- is in the
`feedback` field of `get_assignment`, and `list_feedback` sweeps what was
graded or commented on recently. Comments sometimes carry work the grade does
not show: a resubmission offer, a revision deadline, a request to meet. A
`posted_at` of null means grades are not yet released and some feedback may
still be hidden from the user.

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

# Sits on every discussion row of a sweep, so it is a pointer, not the text.
ROW_CAVEAT = "replies untracked by Canvas; see discussion_note"

SWEEP_NOTE = (
    "`my_participation` counts what the user posted in each thread. "
    "`reply_requirement` holds the sentences of the description that mention "
    "replies, responses, peers or classmates, verbatim; compare the counts "
    "against it, and call `get_assignment` when the excerpt is not enough. "
    "Ungraded topics are not listed here; use `list_discussions`."
)

PARTICIPATION_NOTE = (
    "`my_participation` counts what the user actually posted. Canvas does not "
    "record the reply requirement anywhere structured, so compare these counts "
    "against the requirement stated in the topic `message` before deciding this "
    "discussion is complete."
)


def _is_discussion(row: dict[str, Any]) -> bool:
    return "discussion_topic" in (row.get("submission_types") or [])


def _dump(model: Any, *, keep: tuple[str, ...] = (), drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """Serialise a model for a list row: null fields out, ``keep`` fields in.

    Dropping nulls is the single largest saving available on a sweep -- most
    optional fields on most rows are null -- and it loses nothing, since a
    missing key reads the same as a null one. Fields named in ``keep`` are
    written even when null, for the cases where the null itself is the point.
    """
    row: dict[str, Any] = model.model_dump(exclude_none=True, exclude=set(drop))
    for field in keep:
        row.setdefault(field, None)
    return row


# How many discussion threads a sweep fetches at once. Canvas's quota is a
# leaky bucket per token, so this is kept modest rather than maximal.
SWEEP_CONCURRENCY = 4


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
    those with no due date at all. Rows are compact: bodies and `html_url`
    are omitted, and null fields other than `due_at` are left out. Use
    `get_assignment` for one assignment's full text and link.

    Compare `due_at` against `created_at` before treating anything as overdue.

    Rows whose `submission_types` include `discussion_topic` carry a
    `completion_caveat` and a `discussion_topic_id`: their submission state
    covers the main post only and says nothing about required replies. Do not
    count those as done here; `list_discussion_participation` checks them all
    in one call.
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
            row = _dump(assignment, keep=("due_at",), drop=("description", "html_url"))
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
async def get_assignment(
    course_id: int, assignment_id: int, include_feedback: bool = True
) -> dict[str, Any]:
    """Fetch one assignment including its full description and feedback.

    The description often names the real deadline in prose when the `due_at`
    field is wrong or absent. For a discussion it also names the reply
    requirement and the separate, later reply deadline, neither of which Canvas
    exposes as a field.

    `feedback` holds the grade, every submission comment (each marked `mine`
    when the user wrote it), and the rubric with each criterion's mark.
    `include_feedback=False` skips the extra request.
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
    if include_feedback:
        me = await _self()
        try:
            feedback = await fetch_feedback(
                client,
                course_id,
                assignment_id,
                rubric=raw.get("rubric"),
                my_id=me.get("id") if me else None,
            )
            row["feedback"] = feedback.model_dump()
        except (CanvasAuthError, CanvasNotFoundError) as exc:
            row["feedback"] = _unavailable(exc)
    return row


@mcp.tool
async def list_feedback(
    days: int = 14, course_id: int | None = None, include_own_comments: bool = False
) -> dict[str, Any]:
    """Assignments graded, or commented on by someone else, in the last `days`.

    One request per course. Each row carries the grade, the comments, and the
    rubric marks; the user's own comments are left out unless
    `include_own_comments` is set. Rows with `posted_at` absent are not yet
    released, and may be missing feedback the instructor has written.

    Nothing here decides whether feedback needs acting on. Read the comments.
    """
    client = await get_client()
    courses = await _courses()
    if course_id is not None:
        courses = [c for c in courses if c["id"] == course_id]
        if not courses:
            return {"error": f"No active course with id {course_id}."}

    me = await _self()
    my_id = me.get("id") if me else None
    since = datetime.now(UTC) - timedelta(days=days)
    comment_drop = () if include_own_comments else ("mine",)

    results: list[dict[str, Any]] = []
    for course in courses:
        try:
            items = await fetch_recent_feedback(client, course["id"], my_id=my_id, since=since)
        except (CanvasAuthError, CanvasNotFoundError) as exc:
            results.append({"course_id": course["id"], **_unavailable(exc)})
            continue
        for assignment, feedback in items:
            comments = [c for c in feedback.comments if include_own_comments or c.mine is not True]
            row: dict[str, Any] = {
                "assignment_id": assignment.get("id"),
                "course_id": course["id"],
                "course_name": course.get("name"),
                "name": assignment.get("name"),
                "points_possible": assignment.get("points_possible"),
                **_dump(feedback, drop=("comments", "rubric")),
            }
            if comments:
                row["comments"] = [_dump(c, drop=comment_drop) for c in comments]
            if feedback.rubric:
                row["rubric"] = [_dump(r) for r in feedback.rubric]
            results.append({k: v for k, v in row.items() if v is not None})

    return {
        "count": len(results),
        "since": since.strftime("%Y-%m-%d"),
        "feedback": results,
    }


# ---- announcements and discussions -------------------------------------------


@mcp.tool
async def list_announcements(
    days: int = 21, course_id: int | None = None, include_messages: bool = True
) -> dict[str, Any]:
    """Recent announcements, with their full text unless `include_messages` is off.

    Announcements are where date changes are usually communicated, so these
    are often the best evidence of what is genuinely due when assignment
    metadata disagrees. Pass `course_id` to read one course, and
    `include_messages=False` to survey titles and dates alone.
    """
    client = await get_client()
    course_ids = [c["id"] for c in await _courses() if course_id in (None, c["id"])]
    if course_id is not None and not course_ids:
        return {"error": f"No active course with id {course_id}."}
    start = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    topics = await fetch_announcements(
        client, course_ids, start_date=start, include_messages=include_messages
    )
    return {
        "count": len(topics),
        "since": start,
        "announcements": [_dump(t, drop=("is_announcement",)) for t in topics],
    }


@mcp.tool
async def list_discussions(course_id: int) -> dict[str, Any]:
    """List discussion topics in a course. Bodies omitted; use `get_discussion`."""
    client = await get_client()
    try:
        topics = await fetch_discussions(client, course_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {"count": len(topics), "discussions": [_dump(t) for t in topics]}


@mcp.tool
async def get_discussion(
    course_id: int,
    topic_id: int,
    entries: Literal["none", "mine", "all"] = "none",
    include_messages: bool = True,
) -> dict[str, Any]:
    """Fetch one discussion topic and report the user's participation in it.

    The whole thread is read, nested replies included -- peer replies are
    always nested, so a top-level-only view cannot show whether the user
    replied to anyone -- but by default none of it is returned. The answer to
    "have I done this?" is `my_participation`, which separates top-level posts
    from replies to other people, together with the topic `message` that
    states the requirement.

    `entries` returns the posts themselves: `"mine"` for the user's own,
    `"all"` for the full thread (which on a large class can run to a hundred
    posts). `include_messages=False` returns entry metadata without bodies.

    `my_participation` is the only way to check a "post once, reply to two
    classmates" requirement, because Canvas records the submission on the
    first post and tracks the replies nowhere.
    """
    client = await get_client()
    try:
        topic, thread, full_thread = await fetch_discussion(client, course_id, topic_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)

    me = await _self()
    my_id = me.get("id") if me else None

    result: dict[str, Any] = {
        "topic": _dump(topic),
        "entry_count": len(thread),
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
        result["my_participation"] = {
            "user_id": my_id,
            "display_name": me.get("name") if me else None,
            **summarise_participation(thread, my_id, full_thread=full_thread),
        }

    if not full_thread:
        result["warning"] = (
            "Canvas would not serve the full thread, so only top-level entries "
            "were retrieved. Nested replies -- including the user's own -- are "
            "missing, and the participation counts are lower bounds."
        )

    if entries != "none":
        selected = thread if entries == "all" else [e for e in thread if e.user_id == my_id]
        drop = () if include_messages else ("message",)
        result["entries"] = [_dump(e, drop=drop) for e in selected]
    return result


@mcp.tool
async def list_discussion_participation(course_id: int | None = None) -> dict[str, Any]:
    """Every graded discussion, with what the user has posted in each. One call.

    This is the tool for "do I still owe any discussion posts or replies?".
    It sweeps the discussion assignments of every active course (or one), reads
    each thread in full, and returns one compact row per discussion: the dates,
    the submission state, the reply-requirement sentences lifted verbatim from
    the description, and `my_participation` counts. No post bodies, and no
    full descriptions -- `get_assignment` and `get_discussion` have those.

    Nothing here decides whether a discussion is finished. Read
    `reply_requirement` against `replies_to_others` for each row.
    """
    client = await get_client()
    courses = await _courses()
    if course_id is not None:
        courses = [c for c in courses if c["id"] == course_id]
        if not courses:
            return {"error": f"No active course with id {course_id}."}

    me = await _self()
    my_id = me.get("id") if me else None
    semaphore = asyncio.Semaphore(SWEEP_CONCURRENCY)

    async def one(course: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
        assignment = flatten_assignment(raw, course_name=course.get("name"))
        row = _dump(
            assignment,
            keep=("due_at", "discussion_topic_id"),
            drop=(
                "description",
                "html_url",
                "submission_types",
                "grading_type",
                "omit_from_final_grade",
                "assignment_group_id",
                "position",
                "availability",
            ),
        )
        row["reply_requirement"] = excerpt_sentences(
            html_to_markdown(raw.get("description")), REPLY_KEYWORDS
        )
        topic_id = assignment.discussion_topic_id
        if topic_id is None:
            row["my_participation"] = {
                "unavailable": True,
                "reason": "Canvas returned no discussion topic for this assignment.",
            }
            return row
        if my_id is None:
            row["my_participation"] = {
                "unavailable": True,
                "reason": "Could not identify the current Canvas user.",
            }
            return row
        async with semaphore:
            try:
                thread, full_thread = await fetch_thread(client, course["id"], topic_id)
            except CanvasError as exc:
                row["my_participation"] = _unavailable(exc)
                return row
        row["thread_entry_count"] = len(thread)
        row["my_participation"] = summarise_participation(thread, my_id, full_thread=full_thread)
        return row

    results: list[dict[str, Any]] = []
    for course in courses:
        try:
            raw_assignments = await client.paginate(
                f"courses/{course['id']}/assignments", {"include[]": ["submission"]}
            )
        except (CanvasAuthError, CanvasNotFoundError) as exc:
            results.append({"course_id": course["id"], **_unavailable(exc)})
            continue
        graded = [a for a in raw_assignments if _is_discussion(a)]
        results.extend(await asyncio.gather(*(one(course, a) for a in graded)))

    return {"count": len(results), "note": SWEEP_NOTE, "discussions": results}


# ---- files, pages, modules ---------------------------------------------------


@mcp.tool
async def list_files(course_id: int) -> dict[str, Any]:
    """List a course's files. Many courses hide the Files tab from students."""
    client = await get_client()
    try:
        files = await fetch_files(client, course_id)
    except (CanvasAuthError, CanvasNotFoundError) as exc:
        return _unavailable(exc)
    return {"count": len(files), "files": [_dump(f) for f in files]}


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
    return {"count": len(pages), "pages": [_dump(p) for p in pages]}


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
    return {"count": len(modules), "modules": [_dump(m) for m in modules]}


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


def _preflight() -> None:
    """Refuse to start without usable Canvas configuration.

    ``Config.from_env()`` is resolved lazily inside ``get_client()``, so a
    missing or mistyped CANVAS_BASE_URL used to surface as a server that
    starts, authorizes, lists every tool, and then fails on every single call.
    That is the hardest shape of failure to diagnose from the Claude side,
    because nothing about it points at configuration.

    Checking here turns it into a crashed start with the reason on the first
    line of the log, which is where somebody who just filled in a deploy form
    will actually look.
    """
    from .config import ConfigError

    try:
        Config.from_env()
    except ConfigError as exc:
        raise SystemExit(f"\ncanvas-viewer-mcp cannot start.\n\n  {exc}\n") from None


def main() -> None:
    """Entry point. MCP_TRANSPORT selects stdio (default) or http."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    _preflight()

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if transport not in ("http", "streamable-http"):
        raise SystemExit(f"Unsupported MCP_TRANSPORT {transport!r}; use 'stdio' or 'http'.")

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    from .config import ConfigError

    try:
        enable_http_auth()
    except ConfigError as exc:
        # Almost always: the platform has not assigned this service a domain
        # yet, so there is no hostname to build OAuth metadata from. Exiting
        # here produced a crash loop, and a crash loop is the worst possible
        # state to be in -- the deploy never reports healthy, which is exactly
        # when a platform is least willing to hand out the domain that would
        # have fixed it. Serve health instead and refuse everything else, so
        # the deploy settles, the domain can be assigned, and the restart that
        # follows comes up properly configured.
        _run_unconfigured(str(exc), host, port)
        return

    _serve_http(host, port)


def _serve_http(host: str, port: int) -> None:
    """Serve MCP at the root, and at /mcp as well.

    The root is what people paste. Told to add a connector, someone copies the
    address out of their hosting dashboard -- and an address that silently
    needs `/mcp` glued onto the end produces a 404, which Claude reports as
    not being able to work out how the server signs in. That is a dead end
    dressed up as an authentication problem, and it lands on exactly the
    people who cannot tell the difference.

    /mcp stays because it is what earlier versions served, what DEPLOY.md
    documented, and what any already-authorized connector is pointed at.
    Moving the endpoint would log those out to save a suffix.
    """
    import uvicorn
    from starlette.routing import Route

    app = mcp.http_app(path="/")

    def alias(existing: str, new_path: str) -> None:
        """Serve an existing route at a second path.

        The same endpoint object, so the two paths share one session manager
        rather than running two independent MCP servers in one process.
        """
        route = next(r for r in app.routes if isinstance(r, Route) and r.path == existing)
        app.router.routes.append(
            Route(new_path, endpoint=route.endpoint, methods=sorted(route.methods or ()))
        )

    alias("/", "/mcp")
    # Serving MCP at the root moves its RFC 9728 metadata to the bare
    # well-known path. A connector authorized against an earlier version
    # discovered it one level down, so keep answering there too rather than
    # 404 a client that is only re-checking what it was told before.
    alias("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp")
    uvicorn.run(app, host=host, port=port, log_level="info")


def _run_unconfigured(reason: str, host: str, port: int) -> None:
    """Serve only a health check, and say why, until configuration arrives.

    Deliberately serves no MCP endpoint and no OAuth endpoint: without a
    public URL there is no safe way to run either, and a server that answered
    anyway would be advertising metadata pointing at the wrong host.
    """
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route

    banner = (
        "\n"
        "  ---------------------------------------------------------------\n"
        "  canvas-viewer-mcp is running but not yet reachable.\n"
        "\n"
        f"  {reason}\n"
        "\n"
        "  On Railway, Render or Fly this normally means the service has\n"
        "  no public domain yet. Generate one -- on Railway that is\n"
        "  Settings -> Networking -> Generate Domain -- and the service\n"
        "  will restart and pick it up. Nothing else needs changing.\n"
        "  ---------------------------------------------------------------\n"
    )
    print(banner, file=sys.stderr, flush=True)

    async def health(request: Request) -> Response:
        # 200 so the platform marks the deploy healthy and will assign a
        # domain; the body is honest about the state rather than claiming ok.
        return JSONResponse(
            {"status": "awaiting_public_url", "version": __version__, "reason": reason},
            status_code=200,
        )

    async def unavailable(request: Request) -> Response:
        return JSONResponse({"error": "not_configured", "reason": reason}, status_code=503)

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/{path:path}", unavailable, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        ]
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
