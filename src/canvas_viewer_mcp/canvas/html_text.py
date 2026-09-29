"""Convert Canvas HTML bodies to Markdown.

Canvas stores assignment descriptions, page bodies, and discussion posts as
HTML produced by a WYSIWYG editor: nested tables used for layout, inline
styles, empty paragraphs, and occasional base64 images. Passed through raw it
is mostly noise, and noise costs context window and mobile latency.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify
from pydantic import BaseModel

DEFAULT_MAX_CHARS = 8000
TRUNCATION_NOTE = "\n\n[... truncated, {removed} more characters ...]"

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_DATA_URI_IMAGE = re.compile(r"!\[[^\]]*\]\(data:[^)]*\)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_WHITESPACE = re.compile(r"\s+")

# Words that mark the sentence of a discussion prompt which states how many
# classmates must be answered and by when. Matched as prefixes, so "reply",
# "replies", "respond", "responses", "peers" and "classmates" all hit.
REPLY_KEYWORDS = ("repl", "respon", "peer", "classmate", "comment on")

# The path of a link to one Canvas file, in every form the editor writes one:
# /courses/1/files/2?wrap=1, /files/2/download, /api/v1/courses/1/files/2,
# /users/3/files/2/preview. Only the path is matched; the host varies.
_FILE_PATH = re.compile(r"^(?:/api/v1)?(?:/(?:courses|users|groups)/\d+)?/files/(\d+)(?:/|$)")


def html_to_markdown(html: str | None, *, max_chars: int = DEFAULT_MAX_CHARS) -> str | None:
    """Return ``html`` as tidied Markdown, or None if there was nothing in it.

    Truncation is always announced. A body that is silently cut off can drop
    the one sentence naming the real deadline, which is precisely the
    information this project exists to surface.
    """
    if not html or not html.strip():
        return None

    text = markdownify(html, heading_style="ATX", strip=["script", "style"])

    # Inline base64 images carry no readable information but can be enormous.
    text = _DATA_URI_IMAGE.sub("[image]", text)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text).strip()

    if not text:
        return None

    if len(text) > max_chars:
        removed = len(text) - max_chars
        text = text[:max_chars] + TRUNCATION_NOTE.format(removed=removed)

    return text


def excerpt_sentences(
    text: str | None, keywords: tuple[str, ...], *, max_chars: int = 400
) -> str | None:
    """Return only the sentences of ``text`` that mention one of ``keywords``.

    This is extraction, not judgement: the sentences come back verbatim, in
    order, for the caller to read. It exists so a sweep over many discussions
    can carry the one or two sentences that state the reply requirement
    without carrying every description in full.
    """
    if not text:
        return None
    lowered = [k.lower() for k in keywords]
    hits = [
        _WHITESPACE.sub(" ", sentence).strip()
        for sentence in _SENTENCE_END.split(text)
        if sentence and any(k in sentence.lower() for k in lowered)
    ]
    if not hits:
        return None
    joined = " ".join(hits)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rstrip() + " [...]"
    return joined


class LinkedFile(BaseModel):
    """A Canvas file that a body of HTML links to or embeds."""

    id: int
    name: str | None = None


def linked_files(html: str | None) -> list[LinkedFile] | None:
    """The Canvas files ``html`` links to or embeds, in order, or None if none.

    A course that hides its Files tab refuses the file list, but the files
    themselves stay readable by id -- and the ids sit in the links the
    instructor put in the syllabus, pages and assignments. Converted to
    Markdown those links still carry the id, but only buried in a URL, which
    a reader easily takes for a page to visit rather than a file to fetch.
    This lifts them out.

    The ``title`` attribute is preferred for the name: the rich-content
    editor writes the file's real name there, while the link text is
    whatever the instructor typed.
    """
    if not html or "files/" not in html:
        return None

    found: dict[int, LinkedFile] = {}
    for tag in BeautifulSoup(html, "lxml").find_all(["a", "img", "iframe"]):
        for attr in ("href", "src", "data-api-endpoint"):
            value = tag.get(attr)
            if not isinstance(value, str):
                continue
            match = _FILE_PATH.match(urlsplit(value.strip()).path)
            if match is None:
                continue
            file_id = int(match.group(1))
            name = next(
                (
                    n.strip()
                    for n in (tag.get("title"), tag.get("alt"), tag.get_text())
                    if isinstance(n, str) and n.strip()
                ),
                None,
            )
            existing = found.get(file_id)
            if existing is None:
                found[file_id] = LinkedFile(id=file_id, name=name)
            elif existing.name is None:
                existing.name = name
            break

    return list(found.values()) or None
