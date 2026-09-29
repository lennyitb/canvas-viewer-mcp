"""Tests for HTML to Markdown conversion."""

from __future__ import annotations

from canvas_viewer_mcp.canvas.html_text import (
    REPLY_KEYWORDS,
    excerpt_sentences,
    html_to_markdown,
    linked_files,
)


def test_empty_and_whitespace_become_none() -> None:
    assert html_to_markdown(None) is None
    assert html_to_markdown("") is None
    assert html_to_markdown("   \n  ") is None
    assert html_to_markdown("<p></p>") is None


def test_basic_structure_is_preserved() -> None:
    out = html_to_markdown("<h2>Week 4</h2><p>Due <strong>Friday</strong>.</p>")
    assert out is not None
    assert "## Week 4" in out
    assert "**Friday**" in out


def test_links_survive_because_they_may_carry_the_real_instructions() -> None:
    out = html_to_markdown('<p>See <a href="https://x.test/syllabus">syllabus</a>.</p>')
    assert out is not None
    assert "https://x.test/syllabus" in out


def test_base64_images_are_replaced_not_inlined() -> None:
    html = '<p>Diagram: <img src="data:image/png;base64,' + "A" * 5000 + '"></p>'
    out = html_to_markdown(html)
    assert out is not None
    assert "base64" not in out
    assert "[image]" in out
    assert len(out) < 200, "a base64 blob must not reach the context window"


def test_excess_blank_lines_collapse() -> None:
    out = html_to_markdown("<p>a</p><p></p><p></p><p></p><p>b</p>")
    assert out is not None
    assert "\n\n\n" not in out


def test_truncation_is_announced_never_silent() -> None:
    out = html_to_markdown("<p>" + "x" * 500 + "</p>", max_chars=100)
    assert out is not None
    assert "truncated" in out
    assert "400 more characters" in out


def test_short_text_is_not_truncated() -> None:
    out = html_to_markdown("<p>Read chapter 3.</p>", max_chars=100)
    assert out == "Read chapter 3."


def test_excerpt_keeps_only_sentences_naming_a_reply_requirement() -> None:
    text = (
        "Post your initial response by Wednesday.\n\n"
        "Respond to at least two peers by Sunday.\nUse APA citations.\n"
        "Late work loses 10%."
    )
    out = excerpt_sentences(text, REPLY_KEYWORDS)
    assert out == (
        "Post your initial response by Wednesday. Respond to at least two peers by Sunday."
    )


def test_excerpt_returns_none_when_nothing_matches() -> None:
    assert excerpt_sentences("Read chapter 3.", REPLY_KEYWORDS) is None
    assert excerpt_sentences(None, REPLY_KEYWORDS) is None


def test_excerpt_is_capped() -> None:
    out = excerpt_sentences("reply " * 200, REPLY_KEYWORDS, max_chars=50)
    assert out is not None and out.endswith("[...]") and len(out) <= 60


# The History syllabus as Canvas serves it: Files tab hidden, the syllabus a
# .docx linked from the Syllabus tab, its real name only in the title.
SYLLABUS = (
    '<p><a class="instructure_file_link" title="Modern US History Syllabus.docx" '
    'href="https://school.instructure.com/courses/68894/files/10033817?wrap=1" '
    'data-api-endpoint="https://school.instructure.com/api/v1/courses/68894/files/10033817" '
    'data-api-returntype="File"><em>HIS 1225 Syllabus</em></a></p>'
)


def test_linked_file_takes_its_name_from_the_title() -> None:
    assert [f.model_dump() for f in linked_files(SYLLABUS) or []] == [
        {"id": 10033817, "name": "Modern US History Syllabus.docx"}
    ]


def test_every_canvas_file_link_shape_is_recognized() -> None:
    html = (
        '<a href="/courses/1/files/11">rel</a>'
        '<a href="https://x.test/files/12/download?download_frd=1">dl</a>'
        '<a href="https://x.test/api/v1/courses/1/files/13">api</a>'
        '<img src="/users/5/files/14/preview" alt="diagram.png">'
        '<iframe src="/courses/1/files/15/file_preview"></iframe>'
        '<a href="#" data-api-endpoint="https://x.test/api/v1/courses/1/files/16">ep</a>'
    )
    found = linked_files(html) or []
    assert [f.id for f in found] == [11, 12, 13, 14, 15, 16]
    assert found[3].name == "diagram.png"
    assert found[0].name == "rel"


def test_a_file_linked_twice_is_listed_once_keeping_the_first_name() -> None:
    html = '<a href="/files/7">Lab 3</a>, <a href="/courses/1/files/7" title="x.pdf">here</a>'
    assert [f.model_dump() for f in linked_files(html) or []] == [{"id": 7, "name": "Lab 3"}]


def test_links_that_are_not_to_one_file_are_ignored() -> None:
    html = (
        '<a href="/courses/1/quizzes/2">quiz</a>'
        '<a href="/courses/1/files">all files</a>'
        '<a href="/courses/1/files/folder/Labs">folder</a>'
        '<a href="https://github.com/org/files/abc">elsewhere</a>'
    )
    assert linked_files(html) is None


def test_no_html_means_no_linked_files() -> None:
    assert linked_files(None) is None
    assert linked_files("") is None
    assert linked_files("<p>Read chapter 3.</p>") is None
