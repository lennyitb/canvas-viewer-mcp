"""Find a course's files when the Files tab will not list them.

Instructors hide the Files tab often -- two of five real courses on one
account -- and Canvas answers the file list with a 403. The files themselves
are unaffected: each is still served by id to anyone enrolled. What is lost
is only the list, and the course content keeps its own: module items point
at files, and the syllabus, pages, assignments, discussions and announcements
link to them. This module gathers those references into a list of ids.

It is a reconstruction, and says so. A file uploaded but linked from nowhere
cannot be found this way, and a link can outlive the file it points to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from .client import CanvasClient
from .content import Module, fetch_modules, topic_files
from .errors import CanvasAuthError, CanvasError, CanvasNotFoundError
from .html_text import linked_files

JsonObject = dict[str, Any]

# Pages a course hides from the Pages list are fetched one by one from the
# modules instead. Past this many, the rest are skipped and the skip reported.
MAX_MODULE_PAGES = 40
PAGE_CONCURRENCY = 4
ANNOUNCEMENT_DAYS = 365

# One reference to a file: its id, the name the reference gives it, and where.
Ref = tuple[int, str | None, str]


class LinkedCourseFile(BaseModel):
    id: int
    display_name: str | None = None
    linked_from: list[str] = []


def _refs(html: str | None, where: str) -> list[Ref]:
    return [(f.id, f.name, where) for f in linked_files(html) or []]


class Discovery(BaseModel):
    files: list[LinkedCourseFile] = []
    sources_unavailable: list[str] = []
    """Sources Canvas refused or failed to serve."""
    notes: list[str] = []
    """Sources read only in part."""


async def discover_linked_files(client: CanvasClient, course_id: int) -> Discovery:
    """Every file the course's content links to, and the sources that failed.

    Modules come first, both because their titles are the instructor's own
    label for a file and because they name the pages to read one by one if
    the Pages list is hidden too. The other sources are read concurrently,
    and a source that fails is named in the second list rather than raised:
    a partial list is still a list, where an exception would be nothing.
    """
    unavailable: list[str] = []
    notes: list[str] = []

    modules: list[Module] = []
    try:
        modules = await fetch_modules(client, course_id)
    except CanvasError:
        unavailable.append("modules")

    module_refs: list[Ref] = [
        (item.file_id, item.title, f"module: {module.name}")
        for module in modules
        for item in module.items
        if item.file_id is not None
    ]
    module_pages = list(
        dict.fromkeys(item.page_url for module in modules for item in module.items if item.page_url)
    )

    async def syllabus() -> list[Ref]:
        raw = await client.get(f"courses/{course_id}", {"include[]": ["syllabus_body"]})
        return _refs(raw.get("syllabus_body"), "syllabus")

    async def front_page() -> list[Ref]:
        try:
            raw = await client.get(f"courses/{course_id}/front_page")
        except CanvasNotFoundError:
            return []  # No front page set, which is common and no refusal.
        return _refs(raw.get("body"), "front page")

    async def assignments() -> list[Ref]:
        raw = await client.paginate(f"courses/{course_id}/assignments")
        return [r for a in raw for r in _refs(a.get("description"), f"assignment: {a.get('name')}")]

    async def pages() -> list[Ref]:
        try:
            raw = await client.paginate(f"courses/{course_id}/pages", {"include[]": ["body"]})
        except (CanvasAuthError, CanvasNotFoundError):
            return await _module_pages(client, course_id, module_pages, notes)
        return [r for p in raw for r in _refs(p.get("body"), f"page: {p.get('title')}")]

    async def discussions() -> list[Ref]:
        raw = await client.paginate(f"courses/{course_id}/discussion_topics")
        return [
            (f.id, f.name, f"discussion: {t.get('title')}")
            for t in raw
            for f in topic_files(t) or []
        ]

    async def announcements() -> list[Ref]:
        start = (datetime.now(UTC) - timedelta(days=ANNOUNCEMENT_DAYS)).strftime("%Y-%m-%d")
        raw = await client.paginate(
            "announcements", {"context_codes[]": [f"course_{course_id}"], "start_date": start}
        )
        return [
            (f.id, f.name, f"announcement: {t.get('title')}")
            for t in raw
            for f in topic_files(t) or []
        ]

    sources: dict[str, Callable[[], Awaitable[list[Ref]]]] = {
        "syllabus": syllabus,
        "front page": front_page,
        "assignments": assignments,
        "pages": pages,
        "discussions": discussions,
        "announcements": announcements,
    }
    results = await asyncio.gather(*(read() for read in sources.values()), return_exceptions=True)

    refs = list(module_refs)
    for name, result in zip(sources, results, strict=True):
        if isinstance(result, CanvasError):
            unavailable.append(name)
        elif isinstance(result, BaseException):
            raise result
        else:
            refs.extend(result)

    found: dict[int, LinkedCourseFile] = {}
    for file_id, file_name, where in refs:
        row = found.setdefault(file_id, LinkedCourseFile(id=file_id))
        if row.display_name is None and file_name:
            row.display_name = file_name
        if where not in row.linked_from:
            row.linked_from.append(where)
    return Discovery(files=list(found.values()), sources_unavailable=unavailable, notes=notes)


async def _module_pages(
    client: CanvasClient, course_id: int, slugs: list[str], notes: list[str]
) -> list[Ref]:
    """Read the pages the modules name, for a course that hides its page list."""
    if len(slugs) > MAX_MODULE_PAGES:
        notes.append(f"pages: read the first {MAX_MODULE_PAGES} of {len(slugs)} module pages")
        slugs = slugs[:MAX_MODULE_PAGES]
    semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)

    async def one(slug: str) -> list[Ref]:
        async with semaphore:
            try:
                raw = await client.get(f"courses/{course_id}/pages/{slug}")
            except (CanvasAuthError, CanvasNotFoundError):
                return []
        return _refs(raw.get("body"), f"page: {raw.get('title') or slug}")

    return [r for refs in await asyncio.gather(*(one(s) for s in slugs)) for r in refs]
