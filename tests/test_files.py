"""Tests for text extraction from course files, Word documents in particular.

The .docx fixtures are assembled here from raw OOXML rather than with a Word
library, so the test suite needs nothing mammoth does not already bring.
"""

from __future__ import annotations

import io
import zipfile

from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from canvas_viewer_mcp.canvas.files import (
    DOCX_TYPE,
    MAX_EXTRACTED_CHARS,
    MAX_OOXML_INFLATED_BYTES,
    MAX_SHEET_ROWS,
    PPTX_TYPE,
    XLSX_TYPE,
    extract_text,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CONTENT_TYPES = f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="{DOCX_TYPE}.main+xml"/>
</Types>"""

PACKAGE_RELS = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELS}">
  <Relationship Id="rId1" Type="{DOC_REL}/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOCUMENT_RELS = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{RELS}">
  <Relationship Id="rNum" Type="{DOC_REL}/numbering" Target="numbering.xml"/>
  <Relationship Id="rImg" Type="{DOC_REL}/image" Target="media/image1.png"/>
</Relationships>"""

NUMBERING = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="{W}">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""

# A 1x1 PNG, so a leaked base64 payload would be recognizable.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _p(text: str, *, style: str | None = None, bullet: bool = False) -> str:
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if bullet:
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    ppr = f"<w:pPr>{props}</w:pPr>" if props else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _table(rows: list[list[str]]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl>{cells}</w:tbl>"


IMAGE = (
    '<w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="circuit"/>'
    "<a:graphic><a:graphicData><pic:pic><pic:blipFill>"
    '<a:blip r:embed="rImg"/>'
    "</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
)


def _docx(body: str, *, include_document: bool = True) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W}" xmlns:r="{R}"'
        ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", PACKAGE_RELS)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        archive.writestr("word/numbering.xml", NUMBERING)
        archive.writestr("word/media/image1.png", PNG)
        if include_document:
            archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_docx_keeps_headings_lists_and_tables() -> None:
    data = _docx(
        _p("Lab 3 Rubric", style="Heading1")
        + _p("Submit the following:")
        + _p("Schematic", bullet=True)
        + _p("Scope captures", bullet=True)
        + _table([["Criterion", "Points"], ["Wiring", "10"]])
    )

    text, note = extract_text(data, DOCX_TYPE)

    assert note is None
    assert text is not None
    assert "# Lab 3 Rubric" in text
    assert "- Schematic" in text or "* Schematic" in text
    assert "| Criterion | Points |" in text
    assert "| Wiring | 10 |" in text


def test_docx_images_become_a_placeholder_not_base64() -> None:
    text, _ = extract_text(_docx(_p("See figure.") + IMAGE), DOCX_TYPE)

    assert text is not None
    assert "[image]" in text
    assert "base64" not in text
    assert "iVBOR" not in text  # the PNG signature, base64-encoded


def test_corrupt_docx_is_reported_not_raised() -> None:
    text, note = extract_text(b"not a zip at all", DOCX_TYPE)
    assert text is None
    assert note is not None and note.startswith("Could not parse Word document")


def test_zip_without_a_document_body_is_reported() -> None:
    text, note = extract_text(_docx("", include_document=False), DOCX_TYPE)
    assert text is None
    assert note is not None and note.startswith("Could not parse Word document")


def test_empty_docx_says_so() -> None:
    text, note = extract_text(_docx("<w:p/>"), DOCX_TYPE)
    assert text is None
    assert note == "Word document contains no text."


def test_long_docx_is_truncated_once_with_the_usual_note() -> None:
    paragraph = "x" * 1000
    text, note = extract_text(_docx(_p(paragraph) * 30), DOCX_TYPE)

    assert text is not None and len(text) == MAX_EXTRACTED_CHARS
    assert note is not None and note.startswith("Truncated,")
    assert "truncated" not in text.lower()  # html_to_markdown did not cut it first


def test_generic_type_falls_back_to_the_filename() -> None:
    data = _docx(_p("Hello from Word"))

    text, _ = extract_text(data, "application/octet-stream", "Lab3.DOCX")
    assert text is not None and "Hello from Word" in text

    text, note = extract_text(data, "application/octet-stream", "Lab3.bin")
    assert text is None
    assert note is not None and "Word" in note
    assert "download_url" in note, "an unreadable file must point at the original"


def test_legacy_doc_is_not_supported() -> None:
    text, note = extract_text(b"\xd0\xcf\x11\xe0", "application/msword", "old.doc")
    assert text is None
    assert note is not None and note.startswith("No text extractor for application/msword")


# ---- PowerPoint and Excel ------------------------------------------------------


def _pptx() -> bytes:
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])  # title and content
    slide.shapes.title.text = "GPIO Basics"
    body = slide.placeholders[1].text_frame
    body.text = "Set the mode register"
    sub = body.add_paragraph()
    sub.text = "then write the ODR"
    sub.level = 1
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(4), Inches(1)).table
    for r, row in enumerate([["Pin", "Mode"], ["PA5", "Output"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    slide.notes_slide.notes_text_frame.text = "Demo on the Nucleo board."
    deck.slides.add_slide(deck.slide_layouts[6])  # blank, and left out
    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()


def _xlsx(rows: list[list[object]], title: str = "Prelab") -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = title
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_pptx_keeps_slide_titles_bullets_tables_and_notes() -> None:
    text, note = extract_text(_pptx(), PPTX_TYPE)

    assert note is None
    assert text is not None
    assert text.startswith("## Slide 1: GPIO Basics")
    assert text.count("GPIO Basics") == 1, "the title is the heading, not a line too"
    assert "Set the mode register" in text
    assert "  - then write the ODR" in text
    assert "| Pin | Mode |" in text and "| PA5 | Output |" in text
    assert "Notes: Demo on the Nucleo board." in text
    assert "Slide 2" not in text, "an empty slide adds nothing"


def test_xlsx_becomes_one_table_per_sheet() -> None:
    data = _xlsx(
        [["R (ohm)", "V", None], [1000, 4.7, None], [None, None, None], [2200, "a|b", None]]
    )

    text, note = extract_text(data, XLSX_TYPE)

    assert note is None
    assert text == ("## Prelab\n\n| R (ohm) | V |\n| --- | --- |\n| 1000 | 4.7 |\n| 2200 | a\\|b |")


def test_long_sheet_is_cut_and_says_so() -> None:
    text, note = extract_text(_xlsx([[i] for i in range(MAX_SHEET_ROWS + 50)]), XLSX_TYPE)

    assert text is not None and f"| {MAX_SHEET_ROWS - 1} |" in text
    assert f"| {MAX_SHEET_ROWS} |" not in text
    assert note is not None and "Prelab" in note


def test_office_files_labelled_generically_are_read_by_their_name() -> None:
    text, _ = extract_text(_pptx(), "application/octet-stream", "Lecture.PPTX")
    assert text is not None and "GPIO Basics" in text

    text, _ = extract_text(_xlsx([["a"]]), "application/zip", "calcs.xlsx")
    assert text is not None and "| a |" in text


def test_office_zip_bomb_is_refused_before_parsing() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", b"\0" * (MAX_OOXML_INFLATED_BYTES + 1))

    text, note = extract_text(buffer.getvalue(), PPTX_TYPE)

    assert text is None
    assert note is not None and "limit" in note


def test_corrupt_pptx_and_xlsx_are_reported_not_raised() -> None:
    for kind, label in ((PPTX_TYPE, "PowerPoint"), (XLSX_TYPE, "Excel")):
        text, note = extract_text(b"not a zip at all", kind)
        assert text is None
        assert note is not None and note.startswith(f"Could not parse {label}")
