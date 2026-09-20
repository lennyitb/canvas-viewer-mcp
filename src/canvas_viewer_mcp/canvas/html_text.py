"""Convert Canvas HTML bodies to Markdown.

Canvas stores assignment descriptions, page bodies, and discussion posts as
HTML produced by a WYSIWYG editor: nested tables used for layout, inline
styles, empty paragraphs, and occasional base64 images. Passed through raw it
is mostly noise, and noise costs context window and mobile latency.
"""

from __future__ import annotations

import re

from markdownify import markdownify

DEFAULT_MAX_CHARS = 8000
TRUNCATION_NOTE = "\n\n[... truncated, {removed} more characters ...]"

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_DATA_URI_IMAGE = re.compile(r"!\[[^\]]*\]\(data:[^)]*\)")


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
