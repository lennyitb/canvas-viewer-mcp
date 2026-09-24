"""Flatteners and fetchers for course content: discussions, announcements,
pages, and modules.

Availability is not uniform. Across a real account, files return 403 in five
courses of eleven and pages return 404 in seven -- instructors disable tabs,
and Canvas reports that as an error rather than an empty list. So every fetch
here can legitimately fail for a course that is otherwise perfectly healthy,
and callers are expected to treat that as a fact about the course rather than
a fault.

Modules matter more than they first appear. They are available in nearly every
course, and they encode the order an instructor intends work to be done in --
which is the one ordering signal that survives when due dates have gone stale.

Discussions need the whole tree, not the top of it. Canvas serves top-level
entries from ``/entries`` and hides every nested reply behind a separate
endpoint per entry. On a real graded discussion that is not a detail: one Week
4 topic held 77 entries of which only 22 were top-level, so ``/entries`` alone
loses 55 of 77 posts -- and peer replies, the part of a discussion a student is
usually graded on, are *all* nested. So the fetcher here uses the ``/view``
endpoint, which returns the entire tree in a single request.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from .client import CanvasClient
from .errors import CanvasError
from .html_text import html_to_markdown

JsonObject = dict[str, Any]


class DiscussionTopic(BaseModel):
    id: int
    course_id: int | None = None
    title: str
    is_announcement: bool = False
    posted_at: str | None = None
    created_at: str | None = None
    last_reply_at: str | None = None
    todo_date: str | None = None
    reply_count: int = 0
    locked: bool = False
    published: bool = True
    author: str | None = None
    assignment_id: int | None = None
    html_url: str | None = None
    message: str | None = None


class DiscussionReply(BaseModel):
    """One discussion entry, top-level or nested.

    ``depth`` is 0 for a top-level post and 1+ for a reply. ``parent_user_id``
    names whose entry this replies to, which is what separates a reply to a
    classmate from a reply to one's own post -- the distinction most peer-reply
    requirements turn on.
    """

    id: int
    parent_id: int | None = None
    depth: int = 0
    user_id: int | None = None
    author: str | None = None
    parent_user_id: int | None = None
    created_at: str | None = None
    message: str | None = None
    deleted: bool = False


class Page(BaseModel):
    page_id: int | None = None
    url: str | None = None
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    published: bool = True
    todo_date: str | None = None
    body: str | None = None


class ModuleItem(BaseModel):
    id: int
    title: str
    type: str | None = None
    position: int | None = None
    content_id: int | None = None
    page_url: str | None = None
    html_url: str | None = None
    completion_requirement: str | None = None
    completed: bool | None = None


class Module(BaseModel):
    id: int
    name: str
    position: int | None = None
    state: str | None = None
    unlock_at: str | None = None
    items: list[ModuleItem] = []


# ``/courses/123/discussion_topics/456`` -- the course a topic's links sit under.
_COURSE_PATH = re.compile(r"/courses/(\d+)")


def _course_id_of(raw: JsonObject, fallback: int | None = None) -> int | None:
    """The course a topic belongs to, however this endpoint happens to say it.

    A topic fetched under ``courses/{id}/discussion_topics`` carries
    ``course_id``, but the account-wide ``announcements`` endpoint does not:
    it names the course with a ``context_code`` of ``course_{id}`` instead.
    Reading only ``course_id`` there leaves every announcement with a null
    course, which is the one field a caller needs to act on it.

    ``fallback`` is the id the caller already put in the request path. It is
    consulted last but it is the most reliable source of the lot -- a course
    endpoint always knows which course it asked about, whereas the payload may
    say nothing and the link may be missing or point elsewhere. Passing it is
    what stops a topic being reported as belonging to no course.
    """
    course_id = raw.get("course_id")
    if course_id is not None:
        try:
            return int(course_id)
        except (TypeError, ValueError):
            return None

    code = raw.get("context_code")
    if isinstance(code, str) and code.startswith("course_") and code[7:].isdigit():
        return int(code[7:])

    for key in ("html_url", "url"):
        link = raw.get(key)
        if isinstance(link, str):
            found = _COURSE_PATH.search(link)
            if found:
                return int(found.group(1))
    return fallback


def _author_of(raw: JsonObject) -> str | None:
    """Whoever Canvas names as the writer of a topic or reply.

    The name arrives in one of three shapes: an ``author`` object (topics), a
    ``user`` object (replies), or a bare ``user_name``. All are consulted,
    because Canvas populates a different one per endpoint and sends the others
    empty -- an ``author`` of ``{}`` alongside a real ``user_name`` is routine,
    so an empty object must fall through rather than settle the question.

    A null result is itself information: instructor-created course topics are
    attributed to the course rather than to a person, and arrive with every
    one of these fields blank. Verified against a real account: 22 topics
    across three courses, none naming an author. Null is the honest report;
    the course name would be a fabrication.
    """
    for key in ("author", "user"):
        person = raw.get(key)
        if isinstance(person, dict) and (name := person.get("display_name")):
            return str(name)
    return raw.get("user_name")


def flatten_topic(
    raw: JsonObject, *, course_id: int | None = None, include_message: bool = False
) -> DiscussionTopic:
    return DiscussionTopic(
        id=raw["id"],
        course_id=_course_id_of(raw, course_id),
        title=raw.get("title") or "(untitled)",
        is_announcement=bool(raw.get("is_announcement")),
        posted_at=raw.get("posted_at"),
        created_at=raw.get("created_at"),
        last_reply_at=raw.get("last_reply_at"),
        todo_date=raw.get("todo_date"),
        reply_count=int(raw.get("discussion_subentry_count") or 0),
        locked=bool(raw.get("locked")),
        published=bool(raw.get("published", True)),
        author=_author_of(raw),
        assignment_id=raw.get("assignment_id"),
        html_url=raw.get("html_url") or raw.get("url"),
        message=html_to_markdown(raw.get("message")) if include_message else None,
    )


def flatten_reply(raw: JsonObject, *, depth: int = 0) -> DiscussionReply:
    return DiscussionReply(
        id=raw["id"],
        parent_id=raw.get("parent_id"),
        depth=depth,
        user_id=raw.get("user_id"),
        author=_author_of(raw),
        created_at=raw.get("created_at"),
        message=html_to_markdown(raw.get("message")),
        deleted=bool(raw.get("deleted")),
    )


def flatten_page(raw: JsonObject, *, include_body: bool = False) -> Page:
    return Page(
        page_id=raw.get("page_id"),
        url=raw.get("url"),
        title=raw.get("title") or "(untitled)",
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        published=bool(raw.get("published", True)),
        todo_date=raw.get("todo_date"),
        body=html_to_markdown(raw.get("body")) if include_body else None,
    )


def flatten_module(raw: JsonObject) -> Module:
    items = []
    for item in raw.get("items") or []:
        requirement = item.get("completion_requirement") or {}
        items.append(
            ModuleItem(
                id=item["id"],
                title=item.get("title") or "(untitled)",
                type=item.get("type"),
                position=item.get("position"),
                content_id=item.get("content_id"),
                page_url=item.get("page_url"),
                html_url=item.get("html_url"),
                completion_requirement=requirement.get("type")
                if isinstance(requirement, dict)
                else None,
                completed=requirement.get("completed") if isinstance(requirement, dict) else None,
            )
        )
    return Module(
        id=raw["id"],
        name=raw.get("name") or "(unnamed)",
        position=raw.get("position"),
        state=raw.get("state"),
        unlock_at=raw.get("unlock_at"),
        items=items,
    )


# ---- fetchers ----------------------------------------------------------------


async def fetch_discussions(
    client: CanvasClient, course_id: int, *, include_messages: bool = False
) -> list[DiscussionTopic]:
    raw = await client.paginate(f"courses/{course_id}/discussion_topics")
    return [flatten_topic(t, course_id=course_id, include_message=include_messages) for t in raw]


def _walk_view(
    nodes: list[JsonObject],
    participants: dict[int, str | None],
    *,
    depth: int = 0,
    parent_user_id: int | None = None,
) -> list[DiscussionReply]:
    """Flatten the ``/view`` reply tree depth-first into an ordered list.

    The tree is kept as a flat list with ``depth`` and ``parent_user_id``
    rather than nested objects: callers overwhelmingly want to count and
    filter entries, which a flat list makes trivial and a tree does not.
    """
    out: list[DiscussionReply] = []
    for node in nodes:
        user_id = node.get("user_id")
        out.append(
            DiscussionReply(
                id=node["id"],
                parent_id=node.get("parent_id"),
                depth=depth,
                user_id=user_id,
                author=participants.get(user_id) if user_id is not None else None,
                parent_user_id=parent_user_id,
                created_at=node.get("created_at"),
                # A deleted entry carries no message but still occupies a slot
                # in the thread, so it is kept and flagged rather than dropped.
                message=html_to_markdown(node.get("message")),
                deleted=bool(node.get("deleted")),
            )
        )
        out.extend(
            _walk_view(
                node.get("replies") or [],
                participants,
                depth=depth + 1,
                parent_user_id=user_id,
            )
        )
    return out


async def fetch_thread(
    client: CanvasClient, course_id: int, topic_id: int
) -> tuple[list[DiscussionReply], bool]:
    """Fetch every entry in a topic's thread, nested replies included.

    Returns ``(entries, full_tree)``. ``full_tree`` is False when Canvas
    could not serve the materialized view and only top-level entries were
    recoverable -- a caller asking "did I reply to anyone?" cannot answer it
    from a partial thread, and needs to be told which it got.

    Canvas builds the ``/view`` payload asynchronously and answers 503 while
    that job runs, so the top-level endpoint remains as a fallback.
    """
    base = f"courses/{course_id}/discussion_topics/{topic_id}"

    try:
        view = await client.get(f"{base}/view")
    except CanvasError:
        entries = await client.paginate(f"{base}/entries")
        return [flatten_reply(e) for e in entries], False

    if not isinstance(view, dict) or "view" not in view:
        entries = await client.paginate(f"{base}/entries")
        return [flatten_reply(e) for e in entries], False

    participants = {
        p["id"]: p.get("display_name") or p.get("short_name")
        for p in (view.get("participants") or [])
        if isinstance(p, dict) and p.get("id") is not None
    }
    replies = _walk_view(view.get("view") or [], participants)

    # `new_entries` holds posts made since the view was last materialized.
    # Skipping them would drop exactly the most recent replies -- the ones a
    # student checking their own participation is most likely asking about.
    by_id = {r.id: r for r in replies}
    for raw in view.get("new_entries") or []:
        entry_id = raw.get("id")
        if entry_id is None or entry_id in by_id:
            continue
        parent = by_id.get(raw.get("parent_id"))
        entry = flatten_reply(
            {**raw, "user_name": participants.get(raw.get("user_id"))},
            depth=0 if raw.get("parent_id") is None else (parent.depth + 1 if parent else 1),
        )
        entry.parent_user_id = parent.user_id if parent else None
        replies.append(entry)
        by_id[entry_id] = entry

    return replies, True


async def fetch_discussion(
    client: CanvasClient, course_id: int, topic_id: int
) -> tuple[DiscussionTopic, list[DiscussionReply], bool]:
    """Fetch one topic, with its message, and every entry in its thread.

    See :func:`fetch_thread` for the ``full_tree`` flag.
    """
    topic_raw = await client.get(f"courses/{course_id}/discussion_topics/{topic_id}")
    topic = flatten_topic(topic_raw, course_id=course_id, include_message=True)
    entries, full_tree = await fetch_thread(client, course_id, topic_id)
    return topic, entries, full_tree


def summarise_participation(
    entries: list[DiscussionReply], my_id: int, *, full_thread: bool
) -> JsonObject:
    """Count the user's own entries in a thread, replies to others kept apart.

    A "reply to two classmates" requirement is not met by replying to one's
    own post, so the two are counted separately. These are counts of what
    was posted, nothing more; whether they satisfy the requirement is for the
    caller to decide against the topic's text.
    """
    mine = [e for e in entries if e.user_id == my_id]
    return {
        "total_entries": len(mine),
        "top_level_posts": sum(1 for e in mine if e.depth == 0),
        "replies_to_others": sum(
            1 for e in mine if e.depth > 0 and e.parent_user_id not in (None, my_id)
        ),
        "replies_to_self": sum(1 for e in mine if e.depth > 0 and e.parent_user_id == my_id),
        "latest_entry_at": max((e.created_at for e in mine if e.created_at), default=None),
        "counts_are_complete": full_thread,
    }


async def fetch_announcements(
    client: CanvasClient,
    course_ids: list[int],
    *,
    start_date: str | None = None,
    include_messages: bool = True,
) -> list[DiscussionTopic]:
    """Announcements across several courses in a single request.

    Canvas exposes these through one endpoint taking many context codes, which
    keeps a whole-account sweep to one call instead of one per course.
    """
    if not course_ids:
        return []
    params: JsonObject = {"context_codes[]": [f"course_{cid}" for cid in course_ids]}
    if start_date:
        params["start_date"] = start_date
    raw = await client.paginate("announcements", params)
    return [flatten_topic(a, include_message=include_messages) for a in raw]


async def fetch_pages(
    client: CanvasClient, course_id: int, *, include_bodies: bool = False
) -> list[Page]:
    raw = await client.paginate(f"courses/{course_id}/pages")
    return [flatten_page(p, include_body=include_bodies) for p in raw]


async def fetch_page(client: CanvasClient, course_id: int, page_url: str) -> Page:
    # Canvas serves the course home page from its own endpoint, not as a slug.
    path = "front_page" if page_url == "front_page" else f"pages/{page_url}"
    raw = await client.get(f"courses/{course_id}/{path}")
    return flatten_page(raw, include_body=True)


async def fetch_modules(client: CanvasClient, course_id: int) -> list[Module]:
    raw = await client.paginate(f"courses/{course_id}/modules", {"include[]": ["items"]})
    return [flatten_module(m) for m in raw]
