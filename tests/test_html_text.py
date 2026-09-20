"""Tests for HTML to Markdown conversion."""

from __future__ import annotations

from canvas_viewer_mcp.canvas.html_text import html_to_markdown


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
