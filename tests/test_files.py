"""Tests for text extraction from course files, Word documents in particular.

The .docx fixtures are assembled here from raw OOXML rather than with a Word
library, so the test suite needs nothing mammoth does not already bring.
"""

from __future__ import annotations

import io
import zipfile

from canvas_viewer_mcp.canvas.files import DOCX_TYPE, MAX_EXTRACTED_CHARS, extract_text

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
    assert note is not None and "Word (.docx)" in note


def test_legacy_doc_is_not_supported() -> None:
    text, note = extract_text(b"\xd0\xcf\x11\xe0", "application/msword", "old.doc")
    assert text is None
    assert note is not None and note.startswith("No text extractor for application/msword")
