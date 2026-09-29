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
from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
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

# Every Office file is a zip too; this is the ceiling on all its parts together.
MAX_OOXML_INFLATED_BYTES = 100 * 1024 * 1024
# A spreadsheet is cut to this many non-empty rows and columns per sheet.
MAX_SHEET_ROWS = 200
MAX_SHEET_COLS = 30

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Types Canvas sometimes gives an Office upload instead of the real one, and
# the real type to use instead, going by the file's name.
_GENERIC_TYPES = {"application/octet-stream", "application/zip", "application/x-zip-compressed"}
_OFFICE_SUFFIXES = {".docx": DOCX_TYPE, ".pptx": PPTX_TYPE, ".xlsx": XLSX_TYPE}

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
    # When a locked file opens, if Canvas says. Weekly handouts are commonly
    # uploaded at the start of term and unlocked one week at a time.
    unlock_at: str | None = None


class ExtractedFile(BaseModel):
    id: int
    display_name: str
    content_type: str | None = None
    size: int | None = None
    text: str | None = None
    note: str | None = None
    """Why text is absent or incomplete, when it is."""
    download_url: str | None = None
    """The original file, for when the text is not what is wanted or not there."""


def flatten_file(raw: JsonObject) -> CourseFile:
    lock_info = raw.get("lock_info")
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
        unlock_at=raw.get("unlock_at")
        or (lock_info.get("unlock_at") if isinstance(lock_info, dict) else None),
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


def _inflation_note(data: bytes, label: str) -> str | None:
    """Why an Office file must not be opened, or None if it is safe to.

    The download cap bounds the zip, not what it inflates to, and all three
    parsers inflate every part they read.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            inflated = sum(member.file_size for member in archive.infolist())
    except zipfile.BadZipFile as exc:
        return f"Could not parse {label}: {exc}"
    if inflated > MAX_OOXML_INFLATED_BYTES:
        return (
            f"{label[0].upper()}{label[1:]} inflates to {inflated / 1_048_576:.0f} MB, over the "
            f"{MAX_OOXML_INFLATED_BYTES // 1_048_576} MB limit. Not extracted."
        )
    return None


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|") if value is not None else ""


def _markdown_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _shape_text(shape: Any) -> list[str]:
    """The text of one slide shape, groups included, tables as Markdown."""
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        return [t for inner in shape.shapes for t in _shape_text(inner)]
    if getattr(shape, "has_table", False):
        rows = [[_cell(c.text) for c in row.cells] for row in shape.table.rows]
        return [_markdown_table(rows)] if rows else []
    if getattr(shape, "has_text_frame", False):
        return [
            "  " * p.level + "- " + text if p.level else text
            for p in shape.text_frame.paragraphs
            if (text := " ".join(p.text.split()))
        ]
    return []


def _extract_pptx(data: bytes) -> tuple[str | None, str | None]:
    if note := _inflation_note(data, "PowerPoint file"):
        return None, note
    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:  # python-pptx raises a wide variety on bad input
        return None, f"Could not parse PowerPoint file: {exc}"

    slides = []
    for number, slide in enumerate(deck.slides, start=1):
        title_shape = slide.shapes.title
        title = " ".join(title_shape.text.split()) if title_shape is not None else ""
        # python-pptx hands out a new proxy per access, so compare by id.
        title_id = title_shape.shape_id if title_shape is not None else None
        lines = [
            text
            for shape in slide.shapes
            if shape.shape_id != title_id
            for text in _shape_text(shape)
        ]
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                lines.append("Notes: " + " ".join(notes.text.split()))
        if title or lines:
            heading = f"## Slide {number}" + (f": {title}" if title else "")
            slides.append("\n\n".join([heading, *lines]))

    if not slides:
        return None, "PowerPoint file contains no text."
    return "\n\n".join(slides), None


def _extract_xlsx(data: bytes) -> tuple[str | None, str | None]:
    if note := _inflation_note(data, "Excel file"):
        return None, note
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl, likewise
        return None, f"Could not parse Excel file: {exc}"

    sheets: list[str] = []
    cut: list[str] = []
    try:
        for sheet in book.worksheets:
            rows: list[list[str]] = []
            more_rows = wide = False
            for values in sheet.iter_rows(values_only=True):
                cells = [_cell(v) for v in values]
                if not any(cells):
                    continue
                if len(rows) == MAX_SHEET_ROWS:
                    more_rows = True
                    break
                wide = wide or len(cells) > MAX_SHEET_COLS
                rows.append(cells[:MAX_SHEET_COLS])
            if not rows:
                continue
            # Drop the columns that are empty all the way down.
            width = max(
                (i + 1 for r in rows for i, c in enumerate(r) if c),
                default=0,
            )
            rows = [r[:width] for r in rows]
            sheets.append(f"## {sheet.title}\n\n{_markdown_table(rows)}")
            if more_rows or wide:
                cut.append(sheet.title)
    finally:
        book.close()

    if not sheets:
        return None, "Excel file contains no values."
    note = None
    if cut:
        note = (
            f"Sheets cut to {MAX_SHEET_ROWS} rows and {MAX_SHEET_COLS} columns: "
            + ", ".join(cut)
            + "."
        )
    return "\n\n".join(sheets), note


def _extract_docx(data: bytes) -> tuple[str | None, str | None]:
    if note := _inflation_note(data, "Word document"):
        return None, note
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
    if kind in _GENERIC_TYPES:
        suffix = (filename or "").lower().rpartition(".")[2]
        kind = _OFFICE_SUFFIXES.get(f".{suffix}", kind)
    text: str | None
    note: str | None = None

    if kind == "application/pdf":
        text, note = _extract_pdf(data)
    elif kind == DOCX_TYPE:
        text, note = _extract_docx(data)
    elif kind == PPTX_TYPE:
        text, note = _extract_pptx(data)
    elif kind == XLSX_TYPE:
        text, note = _extract_xlsx(data)
    elif kind in TEXTUAL_TYPES or kind.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
    else:
        text = None
        note = (
            f"No text extractor for {kind or 'unknown type'}; PDF, Word, PowerPoint, "
            "Excel and text formats are supported. `download_url` fetches the original."
        )

    if text and len(text) > MAX_EXTRACTED_CHARS:
        removed = len(text) - MAX_EXTRACTED_CHARS
        text = text[:MAX_EXTRACTED_CHARS]
        suffix = f" Truncated, {removed} more characters."
        note = (note + suffix) if note else suffix.strip()
    return text, note


async def fetch_file_meta(client: CanvasClient, file_id: int) -> CourseFile:
    return flatten_file(await client.get(f"files/{file_id}"))


async def download(meta: CourseFile) -> bytes:
    """The bytes of a file whose metadata carries a download URL."""
    if not meta.url:
        raise CanvasError(f"File {meta.id} has no download URL.")
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as blob:
            response = await blob.get(meta.url)
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        raise CanvasError(f"Could not download file {meta.id}: {exc}") from exc


def unavailable_note(meta: CourseFile) -> str:
    """Why a file has no download URL."""
    if meta.unlock_at:
        return f"The file is locked until {meta.unlock_at}."
    return "Canvas returned no download URL; the file is locked or hidden."


async def read_file(
    client: CanvasClient, file_id: int, *, meta: CourseFile | None = None
) -> ExtractedFile:
    """Fetch one file's metadata and extract its text, where that is possible.

    ``meta`` skips the metadata request when the caller already has it.
    """
    if meta is None:
        meta = await fetch_file_meta(client, file_id)

    result = ExtractedFile(
        id=meta.id,
        display_name=meta.display_name,
        content_type=meta.content_type,
        size=meta.size,
        download_url=meta.url or None,
    )

    if not meta.url:
        result.note = unavailable_note(meta)
        return result

    if meta.size and meta.size > MAX_DOWNLOAD_BYTES:
        result.note = (
            f"File is {meta.size / 1_048_576:.1f} MB, over the "
            f"{MAX_DOWNLOAD_BYTES // 1_048_576} MB limit for reading here. "
            "`download_url` fetches the original."
        )
        return result

    data = await download(meta)
    result.text, result.note = extract_text(
        data, meta.content_type, meta.filename or meta.display_name
    )
    return result
