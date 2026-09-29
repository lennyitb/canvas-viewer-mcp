"""Course files: listing, and extracting readable text from one.

Downloads deliberately do not go through CanvasClient. Canvas file URLs are
pre-signed and already carry their own credentials in the query string; the
Authorization header is unnecessary there, and the host may differ from the
API host. A plain client also keeps the API's retry and pagination logic from
being applied to what is really a blob fetch.
"""

from __future__ import annotations

import io
import sys
import zipfile
from typing import Any

import httpx
import mammoth
import mammoth.images
from pydantic import BaseModel
from pypdf import PdfReader

from .client import CanvasClient
from .errors import CanvasError
from .html_text import html_to_markdown

JsonObject = dict[str, Any]

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_CHARS = 20_000
MAX_PDF_PAGES = 50
# A .docx is a zip, so the 25 MB download cap says little about what it
# inflates to. Anything past this is a zip bomb, not a handout.
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Types Canvas sometimes gives a .docx upload instead of the real one.
_GENERIC_TYPES = {"application/octet-stream", "application/zip", "application/x-zip-compressed"}

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


def _blank_image(image: Any) -> dict[str, str]:
    # An empty data URI instead of the base64 mammoth would embed; html_to_markdown
    # turns it into "[image]", as it does for images in Canvas bodies.
    return {"src": "data:,"}


def _extract_docx(data: bytes) -> tuple[str | None, str | None]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            body = archive.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        return None, f"Could not parse Word document: {exc}"

    if body.file_size > MAX_DOCX_XML_BYTES:
        return None, (
            f"Word document body inflates to {body.file_size / 1_048_576:.0f} MB, over the "
            f"{MAX_DOCX_XML_BYTES // 1_048_576} MB limit. Not extracted."
        )

    try:
        result = mammoth.convert_to_html(
            io.BytesIO(data), convert_image=mammoth.images.img_element(_blank_image)
        )
    except Exception as exc:  # mammoth, like pypdf, raises a wide variety
        return None, f"Could not parse Word document: {exc}"

    # Truncation, and its note, is left to extract_text so it happens once.
    text = html_to_markdown(result.value, max_chars=sys.maxsize)
    if not text:
        return None, "Word document contains no text."
    return text, None


def extract_text(
    data: bytes, content_type: str | None, filename: str | None = None
) -> tuple[str | None, str | None]:
    """Readable text from a file's bytes, and a note when it is absent or cut.

    Shared by Canvas downloads and local override files, so a replacement
    comes back exactly as the original would have. ``filename`` is consulted
    only when the content type is too generic to go on.
    """
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in _GENERIC_TYPES and (filename or "").lower().endswith(".docx"):
        kind = DOCX_TYPE
    text: str | None
    note: str | None = None

    if kind == "application/pdf":
        text, note = _extract_pdf(data)
    elif kind == DOCX_TYPE:
        text, note = _extract_docx(data)
    elif kind in TEXTUAL_TYPES or kind.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
    else:
        text = None
        note = (
            f"No text extractor for {kind or 'unknown type'}. "
            "Supported: PDF, Word (.docx) and text-based formats."
        )

    if text and len(text) > MAX_EXTRACTED_CHARS:
        removed = len(text) - MAX_EXTRACTED_CHARS
        text = text[:MAX_EXTRACTED_CHARS]
        suffix = f" Truncated, {removed} more characters."
        note = (note + suffix) if note else suffix.strip()
    return text, note


async def fetch_file_meta(client: CanvasClient, file_id: int) -> CourseFile:
    return flatten_file(await client.get(f"files/{file_id}"))


async def read_file(
    client: CanvasClient, file_id: int, *, meta: CourseFile | None = None
) -> ExtractedFile:
    """Fetch one file's metadata and extract its text, where that is possible.

    ``meta`` skips the metadata request when the caller already has it.
    """
    if meta is None:
        meta = await fetch_file_meta(client, file_id)

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

    text, note = extract_text(data, meta.content_type, meta.filename or meta.display_name)

    return ExtractedFile(
        id=meta.id,
        display_name=meta.display_name,
        content_type=meta.content_type,
        size=meta.size,
        text=text,
        note=note,
    )
