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


def flatten_topic(raw: JsonObject, *, include_message: bool = False) -> DiscussionTopic:
    author = raw.get("author") or {}
    return DiscussionTopic(
        id=raw["id"],
        course_id=raw.get("course_id"),
        title=raw.get("title") or "(untitled)",
        is_announcement=bool(raw.get("is_announcement")),
        posted_at=raw.get("posted_at"),
        created_at=raw.get("created_at"),
        last_reply_at=raw.get("last_reply_at"),
        todo_date=raw.get("todo_date"),
        reply_count=int(raw.get("discussion_subentry_count") or 0),
        locked=bool(raw.get("locked")),
        published=bool(raw.get("published", True)),
        author=author.get("display_name") if isinstance(author, dict) else raw.get("user_name"),
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
        author=raw.get("user_name"),
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
    return [flatten_topic(t, include_message=include_messages) for t in raw]


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


async def fetch_discussion(
    client: CanvasClient, course_id: int, topic_id: int
) -> tuple[DiscussionTopic, list[DiscussionReply], bool]:
    """Fetch one topic with every entry in its thread, nested replies included.

    Returns ``(topic, entries, full_tree)``. ``full_tree`` is False when Canvas
    could not serve the materialized view and only top-level entries were
    recoverable -- a caller asking "did I reply to anyone?" cannot answer it
    from a partial thread, and needs to be told which it got.

    Canvas builds the ``/view`` payload asynchronously and answers 503 while
    that job runs, so the top-level endpoint remains as a fallback.
    """
    topic_raw = await client.get(f"courses/{course_id}/discussion_topics/{topic_id}")
    topic = flatten_topic(topic_raw, include_message=True)
    base = f"courses/{course_id}/discussion_topics/{topic_id}"

    try:
        view = await client.get(f"{base}/view")
    except CanvasError:
        entries = await client.paginate(f"{base}/entries")
        return topic, [flatten_reply(e) for e in entries], False

    if not isinstance(view, dict) or "view" not in view:
        entries = await client.paginate(f"{base}/entries")
        return topic, [flatten_reply(e) for e in entries], False

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

    return topic, replies, True


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
    raw = await client.get(f"courses/{course_id}/pages/{page_url}")
    return flatten_page(raw, include_body=True)


async def fetch_modules(client: CanvasClient, course_id: int) -> list[Module]:
    raw = await client.paginate(f"courses/{course_id}/modules", {"include[]": ["items"]})
    return [flatten_module(m) for m in raw]
