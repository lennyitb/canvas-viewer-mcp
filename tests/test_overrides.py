"""Tests for user overrides: the file format, and each tool's silent swap.

The contract is that a replaced item looks exactly like Canvas content and a
hidden item looks exactly like one Canvas refused, so these tests check both
what is served and that nothing gives the override away -- in particular, no
Canvas download URL for a replaced file, which would lead back to the old one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client

from canvas_viewer_mcp import overrides, server
from canvas_viewer_mcp.canvas.files import DOCX_TYPE
from canvas_viewer_mcp.overrides import OverridesError, guess_type, parse

from .test_files import _docx, _p

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"
COURSES = [{"id": 7, "name": "Microcontrollers", "course_code": "EET 250", "enrollments": []}]


@pytest.fixture
def odir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("CANVAS_BASE_URL", BASE)
    monkeypatch.setenv("CANVAS_TOKEN", "test-token")
    monkeypatch.setenv("CANVAS_OVERRIDES_FILE", str(tmp_path / "overrides.toml"))
    overrides.reset_cache()
    server._client = None
    server._courses_cache = None
    server._self_cache = None
    yield tmp_path
    overrides.reset_cache()
    server._client = None
    server._courses_cache = None


def _write(odir: Path, toml: str, **files: str) -> None:
    for name, text in files.items():
        path = odir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (odir / "overrides.toml").write_text(toml)


async def _call(tool: str, args: dict[str, Any]) -> Any:
    async with Client(server.mcp) as c:
        return (await c.call_tool(tool, args)).data


# ---- the file format ---------------------------------------------------------


def test_replace_is_implied_by_with_and_resolves_relative_to_the_file(tmp_path: Path) -> None:
    rules = parse('[[override]]\ncourse = 1\nkind = "syllabus"\nwith = "s.md"', tmp_path)
    (rule,) = rules.rules
    assert rule.action == "replace"
    assert rule.replacement == tmp_path / "s.md"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('course = 1\nkind = "quiz"\naction = "hide"', "`kind` must be one of"),
        ('course = 1\nkind = "syllabus"', "set `with`"),
        ('course = 1\nkind = "syllabus"\naction = "hide"\nwith = "x"', "takes no `with`"),
        ('course = 1\nkind = "file"\naction = "hide"', "exactly one of `id` or `name`"),
        ('course = 1\nkind = "file"\nid = 2\nname = "a"\naction = "hide"', "exactly one"),
        ('course = 1\nkind = "page"\nid = 2\naction = "hide"', "exactly one of `url`"),
        ('course = 1\nkind = "syllabus"\nid = 2\naction = "hide"', "by `course` alone"),
        ('kind = "syllabus"\naction = "hide"', "`course` is required"),
        ('course = "7"\nkind = "syllabus"\naction = "hide"', "must be an integer"),
        ('course = 1\nkind = "syllabus"\naction = "hide"\nfile = "x"', "unknown key"),
    ],
)
def test_bad_entries_are_rejected_with_their_position(
    tmp_path: Path, body: str, message: str
) -> None:
    ok = '[[override]]\ncourse = 9\nkind = "syllabus"\naction = "hide"\n'
    with pytest.raises(OverridesError, match=message) as info:
        parse(f"{ok}\n[[override]]\n{body}", tmp_path)
    assert "#2" in str(info.value)


def test_file_name_globs_ignore_case(tmp_path: Path) -> None:
    rules = parse(
        '[[override]]\ncourse = 1\nkind = "file"\nname = "hal_v1*.h"\naction = "hide"', tmp_path
    )
    assert rules.for_file(1, 99, "HAL_v1_gpio.H") is not None
    assert rules.for_file(1, 99, "hal_v2_gpio.h") is None
    assert rules.for_file(2, 99, "hal_v1_gpio.h") is None, "rules are per course"


def test_missing_file_means_no_overrides(odir: Path) -> None:
    assert not overrides.current()


def test_edits_apply_without_restart_and_a_broken_edit_keeps_the_last_good_set(
    odir: Path,
) -> None:
    path = odir / "overrides.toml"
    path.write_text('[[override]]\ncourse = 1\nkind = "syllabus"\naction = "hide"')
    assert overrides.current().for_syllabus(1) is not None

    path.write_text('[[override]]\ncourse = 2\nkind = "syllabus"\naction = "hide"')
    os.utime(path, (1, 1))  # a distinct mtime even on a coarse-grained filesystem
    assert overrides.current().for_syllabus(2) is not None

    path.write_text("[[override]\nbroken")
    os.utime(path, (2, 2))
    assert overrides.current().for_syllabus(2) is not None


def test_a_broken_file_at_startup_is_an_error(odir: Path) -> None:
    (odir / "overrides.toml").write_text("not toml [")
    with pytest.raises(OverridesError, match="not valid TOML"):
        overrides.current()


def test_c_sources_are_served_as_text(odir: Path) -> None:
    _write(odir, "", **{"lib/uart.c": "void uart_init(void);\n"})
    rule = parse(
        '[[override]]\ncourse = 1\nkind = "file"\nid = 5\nwith = "lib/uart.c"', odir
    ).rules[0]
    local = overrides.load_replacement(rule)
    assert local.text == "void uart_init(void);\n"
    assert local.content_type == "text/x-c"


# ---- the tools ---------------------------------------------------------------


def _mock_syllabus() -> respx.Route:
    return respx.get(f"{API}/courses/7").mock(
        return_value=httpx.Response(
            200, json={"id": 7, "name": "Microcontrollers", "syllabus_body": "<p>Old plan</p>"}
        )
    )


@respx.mock
async def test_syllabus_is_read_from_canvas_without_overrides(odir: Path) -> None:
    _mock_syllabus()
    result = await _call("get_syllabus", {"course_id": 7})
    assert "Old plan" in result["body"]
    assert result["html_url"] == f"{BASE}/courses/7/assignments/syllabus"


@respx.mock
async def test_syllabus_is_replaced_silently(odir: Path) -> None:
    _write(
        odir,
        '[[override]]\ncourse = 7\nkind = "syllabus"\nwith = "rev2.md"',
        **{"rev2.md": "# Revised plan\nExam moved to week 9."},
    )
    _mock_syllabus()
    result = await _call("get_syllabus", {"course_id": 7})
    assert result["body"] == "# Revised plan\nExam moved to week 9."
    assert "override" not in str(result).lower()


@respx.mock
async def test_hidden_syllabus_looks_like_a_disabled_tab(odir: Path) -> None:
    _write(odir, '[[override]]\ncourse = 7\nkind = "syllabus"\naction = "hide"')
    route = _mock_syllabus()
    result = await _call("get_syllabus", {"course_id": 7})
    assert result["unavailable"] is True
    assert not route.called


@respx.mock
async def test_missing_replacement_file_is_reported_not_papered_over(odir: Path) -> None:
    _write(odir, '[[override]]\ncourse = 7\nkind = "syllabus"\nwith = "gone.pdf"')
    _mock_syllabus()
    result = await _call("get_syllabus", {"course_id": 7})
    assert "Old plan" not in str(result)
    assert "gone.pdf" in result["error"]


FILES = [
    {"id": 1, "display_name": "uart.c", "content-type": "text/x-c", "size": 10,
     "url": f"{BASE}/files/1/download?verifier=x"},
    {"id": 2, "display_name": "hal_v1.h", "content-type": "text/x-c", "size": 20,
     "url": f"{BASE}/files/2/download?verifier=y"},
    {"id": 3, "display_name": "notes.pdf", "content-type": "application/pdf", "size": 30,
     "url": f"{BASE}/files/3/download?verifier=z"},
]  # fmt: skip

MICRO_RULES = """
[[override]]
course = 7
kind = "file"
id = 1
with = "lib/uart.c"

[[override]]
course = 7
kind = "file"
name = "hal_v1*"
action = "hide"
"""


@respx.mock
async def test_list_files_hides_and_rewrites_without_leaking_the_old_url(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": "// new uart driver\n"})
    respx.get(f"{API}/courses/7/files").mock(return_value=httpx.Response(200, json=FILES))

    result = await _call("list_files", {"course_id": 7})

    by_id = {f["id"]: f for f in result["files"]}
    assert set(by_id) == {1, 3}
    assert result["count"] == 2
    assert by_id[1]["display_name"] == "uart.c"
    assert by_id[1]["size"] == len("// new uart driver\n")
    assert "url" not in by_id[1]
    # No row carries the link any more; download_course_file hands it out.
    assert "url" not in by_id[3]
    assert by_id[3]["display_name"] == FILES[2]["display_name"]


@respx.mock
async def test_read_course_file_serves_the_local_copy_without_downloading(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": "// new uart driver\n"})
    respx.get(f"{API}/files/1").mock(return_value=httpx.Response(200, json=FILES[0]))
    download = respx.get(f"{BASE}/files/1/download").mock(
        return_value=httpx.Response(200, text="// OLD")
    )

    result = await _call("read_course_file", {"file_id": 1})

    assert result["text"] == "// new uart driver\n"
    assert result["display_name"] == "uart.c"
    assert not download.called


@respx.mock
async def test_read_course_file_gives_no_link_to_a_replaced_file(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": "// new uart driver\n"})
    respx.get(f"{API}/files/1").mock(return_value=httpx.Response(200, json=FILES[0]))

    result = await _call("read_course_file", {"file_id": 1})

    assert result["download_url"] is None


async def _download(file_id: int) -> Any:
    async with Client(server.mcp) as c:
        return (await c.call_tool("download_course_file", {"file_id": file_id})).structured_content


@respx.mock
async def test_download_honours_hide_and_replace(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": "// new uart driver\n"})
    for f in FILES:
        respx.get(f"{API}/files/{f['id']}").mock(return_value=httpx.Response(200, json=f))

    replaced, hidden, untouched = [await _download(i) for i in (1, 2, 3)]

    assert "download_url" not in replaced, "the Canvas link leads back to the old file"
    assert "read_course_file" in replaced["note"]
    assert hidden["unavailable"] is True
    assert untouched["download_url"].endswith("verifier=z")


@respx.mock
async def test_hidden_files_leave_no_trail_in_links_or_the_rebuilt_list(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": "// new uart driver\n"})
    respx.get(f"{API}/courses").mock(return_value=httpx.Response(200, json=COURSES))
    body = (
        '<a href="/courses/7/files/2" title="hal_v1.h">HAL</a>'
        '<a href="/courses/7/files/1" title="uart.c">UART</a>'
    )
    respx.get(f"{API}/courses/7/pages/week-1").mock(
        return_value=httpx.Response(200, json={"url": "week-1", "title": "Week 1", "body": body})
    )
    respx.get(f"{API}/courses/7/files").mock(return_value=httpx.Response(403, text="{}"))
    respx.get(f"{API}/courses/7/modules").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/courses/7").mock(return_value=httpx.Response(200, json={"id": 7}))
    respx.get(f"{API}/courses/7/front_page").mock(return_value=httpx.Response(404, text="{}"))
    respx.get(f"{API}/courses/7/assignments").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/courses/7/pages").mock(
        return_value=httpx.Response(200, json=[{"title": "Week 1", "body": body}])
    )
    respx.get(f"{API}/courses/7/discussion_topics").mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__startswith=f"{API}/announcements").mock(
        return_value=httpx.Response(200, json=[])
    )

    page = await _call("get_page", {"course_id": 7, "page_url": "week-1"})
    listing = await _call("list_files", {"course_id": 7})

    assert page["linked_files"] == [{"id": 1, "name": "uart.c"}]
    assert [f["id"] for f in listing["files"]] == [1]


def test_docx_type_does_not_depend_on_the_host_mime_table() -> None:
    assert guess_type(Path("rubric.DOCX")) == DOCX_TYPE


@respx.mock
async def test_read_course_file_serves_a_local_docx_as_text(odir: Path) -> None:
    rules = '[[override]]\ncourse = 7\nkind = "file"\nid = 3\nwith = "notes-rev2.docx"'
    _write(odir, rules)
    (odir / "notes-rev2.docx").write_bytes(_docx(_p("Revised lab notes")))
    respx.get(f"{API}/files/3").mock(return_value=httpx.Response(200, json=FILES[2]))

    result = await _call("read_course_file", {"file_id": 3})

    assert result["text"] == "Revised lab notes"
    assert result["content_type"] == DOCX_TYPE
    assert result["note"] is None


@respx.mock
async def test_read_course_file_hides_by_name(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": ""})
    respx.get(f"{API}/files/2").mock(return_value=httpx.Response(200, json=FILES[1]))
    result = await _call("read_course_file", {"file_id": 2})
    assert result["unavailable"] is True


@respx.mock
async def test_modules_drop_items_linking_to_hidden_content(odir: Path) -> None:
    _write(odir, MICRO_RULES, **{"lib/uart.c": ""})
    respx.get(f"{API}/courses/7/modules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 100,
                    "name": "Week 1",
                    "items": [
                        {"id": 1, "title": "uart.c", "type": "File", "content_id": 1},
                        {"id": 2, "title": "hal_v1.h", "type": "File", "content_id": 2},
                        {"id": 3, "title": "Lab 1", "type": "Assignment", "content_id": 50},
                    ],
                }
            ],
        )
    )
    result = await _call("list_modules", {"course_id": 7})
    assert [i["title"] for i in result["modules"][0]["items"]] == ["uart.c", "Lab 1"]


@respx.mock
async def test_front_page_is_matched_by_its_real_slug(odir: Path) -> None:
    _write(
        odir,
        '[[override]]\ncourse = 7\nkind = "page"\nurl = "home"\nwith = "home.md"',
        **{"home.md": "Current schedule"},
    )
    respx.get(f"{API}/courses/7/front_page").mock(
        return_value=httpx.Response(200, json={"url": "home", "title": "Home", "body": "<p>x</p>"})
    )
    result = await _call("get_page", {"course_id": 7, "page_url": "front_page"})
    assert result["body"] == "Current schedule"
    assert result["url"] == "home"


@respx.mock
async def test_hidden_page_drops_from_the_list(odir: Path) -> None:
    _write(odir, '[[override]]\ncourse = 7\nkind = "page"\ntitle = "old *"\naction = "hide"')
    respx.get(f"{API}/courses/7/pages").mock(
        return_value=httpx.Response(
            200, json=[{"url": "old-syllabus", "title": "Old Syllabus"}, {"url": "a", "title": "A"}]
        )
    )
    result = await _call("list_pages", {"course_id": 7})
    assert [p["url"] for p in result["pages"]] == ["a"]
