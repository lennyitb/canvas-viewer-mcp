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
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .client import CanvasClient
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
    id: int
    parent_id: int | None = None
    author: str | None = None
    created_at: str | None = None
    message: str | None = None


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


def flatten_reply(raw: JsonObject) -> DiscussionReply:
    return DiscussionReply(
        id=raw["id"],
        parent_id=raw.get("parent_id"),
        author=raw.get("user_name"),
        created_at=raw.get("created_at"),
        message=html_to_markdown(raw.get("message")),
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


async def fetch_discussion(
    client: CanvasClient, course_id: int, topic_id: int
) -> tuple[DiscussionTopic, list[DiscussionReply]]:
    """Fetch one topic with its replies.

    Only top-level entries are returned. Canvas nests further replies behind a
    separate endpoint per entry, and walking that tree would multiply request
    count without usually adding much.
    """
    topic_raw = await client.get(f"courses/{course_id}/discussion_topics/{topic_id}")
    entries = await client.paginate(f"courses/{course_id}/discussion_topics/{topic_id}/entries")
    return flatten_topic(topic_raw, include_message=True), [flatten_reply(e) for e in entries]


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
