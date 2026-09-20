"""Course files: listing, and extracting readable text from one.

Downloads deliberately do not go through CanvasClient. Canvas file URLs are
pre-signed and already carry their own credentials in the query string; the
Authorization header is unnecessary there, and the host may differ from the
API host. A plain client also keeps the API's retry and pagination logic from
being applied to what is really a blob fetch.
"""

from __future__ import annotations

import io
from typing import Any

import httpx
from pydantic import BaseModel
from pypdf import PdfReader

from .client import CanvasClient
from .errors import CanvasError

JsonObject = dict[str, Any]

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_CHARS = 20_000
MAX_PDF_PAGES = 50

TEXTUAL_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/json",
    "application/xml",
    "text/xml",
}


class CourseFile(BaseModel):
    id: int
    display_name: str
    filename: str | None = None
    content_type: str | None = None
    size: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    folder_id: int | None = None
    url: str | None = None
    locked: bool = False


class ExtractedFile(BaseModel):
    id: int
    display_name: str
    content_type: str | None = None
    size: int | None = None
    text: str | None = None
    note: str | None = None
    """Why text is absent or incomplete, when it is."""


def flatten_file(raw: JsonObject) -> CourseFile:
    return CourseFile(
        id=raw["id"],
        display_name=raw.get("display_name") or raw.get("filename") or "(unnamed)",
        filename=raw.get("filename"),
        content_type=raw.get("content-type") or raw.get("content_type"),
        size=raw.get("size"),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        folder_id=raw.get("folder_id"),
        url=raw.get("url"),
        locked=bool(raw.get("locked") or raw.get("locked_for_user")),
    )


async def fetch_files(client: CanvasClient, course_id: int) -> list[CourseFile]:
    raw = await client.paginate(f"courses/{course_id}/files")
    return [flatten_file(f) for f in raw]


def _extract_pdf(data: bytes) -> tuple[str | None, str | None]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a wide variety on malformed input
        return None, f"Could not parse PDF: {exc}"

    pages = reader.pages[:MAX_PDF_PAGES]
    chunks = []
    for index, page in enumerate(pages, start=1):
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append(f"[page {index} could not be extracted]")

    text = "\n\n".join(c for c in chunks if c.strip())
    if not text.strip():
        return None, (
            "PDF contains no extractable text. It is most likely a scan, which would need OCR."
        )

    note = None
    if len(reader.pages) > MAX_PDF_PAGES:
        note = f"Read first {MAX_PDF_PAGES} of {len(reader.pages)} pages."
    return text, note


async def read_file(client: CanvasClient, file_id: int) -> ExtractedFile:
    """Fetch one file's metadata and extract its text, where that is possible."""
    meta_raw = await client.get(f"files/{file_id}")
    meta = flatten_file(meta_raw)

    if not meta.url:
        return ExtractedFile(
            id=meta.id,
            display_name=meta.display_name,
            content_type=meta.content_type,
            size=meta.size,
            note="Canvas returned no download URL; the file may be locked.",
        )

    if meta.size and meta.size > MAX_DOWNLOAD_BYTES:
        return ExtractedFile(
            id=meta.id,
            display_name=meta.display_name,
            content_type=meta.content_type,
            size=meta.size,
            note=f"File is {meta.size / 1_048_576:.1f} MB, over the "
            f"{MAX_DOWNLOAD_BYTES // 1_048_576} MB limit. Not downloaded.",
        )

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as blob:
            response = await blob.get(meta.url)
            response.raise_for_status()
            data = response.content
    except httpx.HTTPError as exc:
        raise CanvasError(f"Could not download file {file_id}: {exc}") from exc

    content_type = (meta.content_type or "").split(";")[0].strip().lower()
    text: str | None
    note: str | None = None

    if content_type == "application/pdf":
        text, note = _extract_pdf(data)
    elif content_type in TEXTUAL_TYPES or content_type.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
    else:
        text = None
        note = (
            f"No text extractor for {content_type or 'unknown type'}. "
            "Supported: PDF and text-based formats."
        )

    if text and len(text) > MAX_EXTRACTED_CHARS:
        removed = len(text) - MAX_EXTRACTED_CHARS
        text = text[:MAX_EXTRACTED_CHARS]
        suffix = f" Truncated, {removed} more characters."
        note = (note + suffix) if note else suffix.strip()

    return ExtractedFile(
        id=meta.id,
        display_name=meta.display_name,
        content_type=meta.content_type,
        size=meta.size,
        text=text,
        note=note,
    )
