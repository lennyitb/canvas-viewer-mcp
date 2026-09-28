"""User overrides: hide a Canvas item, or silently serve a local file in its place.

Canvas is sometimes simply wrong and stays wrong: a revised syllabus handed out
in class that never reached the Syllabus tab, a folder of library code the
instructor has since replaced. Reporting that faithfully means Claude keeps
quoting the stale version. An override fixes it at the source, per item.

Overrides live in ``overrides.toml`` beside ``config.toml``, or wherever
CANVAS_OVERRIDES_FILE points. A missing file means no overrides, which is the
normal case. Each entry names one Canvas item and either hides it or replaces
it with a local file, whose path is resolved against the TOML file's own
directory so the whole set can live in one folder::

    [[override]]
    course = 12345
    kind = "syllabus"
    with = "syllabus-rev2.pdf"

    [[override]]
    course = 67890
    kind = "file"
    name = "stm32_hal_v1*.h"
    action = "hide"

Replacement is silent by design: the tools return the same shape Canvas
content would have, with nothing marking it as an override. That is the
point -- a marker invites the reader to weigh the two against each other --
and it is why ``canvas-probe overrides`` exists, to show what each rule
actually matched.

The file is re-read when its modification time changes, so an edit applies
without a restart. A broken edit keeps the last good set rather than dropping
every override at once.
"""

from __future__ import annotations

import mimetypes
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from .canvas.files import extract_text
from .config import _config_file_path

Kind = Literal["syllabus", "file", "page"]
Action = Literal["hide", "replace"]
KINDS: tuple[str, ...] = ("syllabus", "file", "page")

# mimetypes knows .c and .h, but not every source extension a course ships.
_TEXT_SUFFIXES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cpp": "text/x-c++",
    ".hpp": "text/x-c++",
    ".s": "text/x-asm",
    ".asm": "text/x-asm",
    ".ino": "text/x-c++",
    ".py": "text/x-python",
    ".txt": "text/plain",
}


class OverridesError(RuntimeError):
    """Raised when the overrides file cannot be used."""


@dataclass(frozen=True)
class Override:
    index: int
    """1-based position in the file, for error messages and the probe."""
    course: int
    kind: Kind
    action: Action
    id: int | None = None
    name: str | None = None
    url: str | None = None
    title: str | None = None
    replacement: Path | None = None

    def describe(self) -> str:
        target = (
            f"id {self.id}"
            if self.id is not None
            else f"url {self.url!r}"
            if self.url
            else f"name {self.name!r}"
            if self.name
            else f"title {self.title!r}"
            if self.title
            else ""
        )
        what = f"course {self.course} {self.kind}" + (f" {target}" if target else "")
        how = "hide" if self.action == "hide" else f"replace with {self.replacement}"
        return f"#{self.index} {what} -> {how}"


@dataclass(frozen=True)
class Replacement:
    """A local file read and extracted the way a Canvas file would be."""

    text: str | None
    note: str | None
    content_type: str
    size: int
    updated_at: str


def overrides_path() -> Path:
    override = os.environ.get("CANVAS_OVERRIDES_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _config_file_path().parent / "overrides.toml"


def _glob(pattern: str | None, value: str | None) -> bool:
    return pattern is not None and value is not None and fnmatch(value.lower(), pattern.lower())


class Overrides:
    def __init__(self, rules: list[Override]) -> None:
        self.rules = rules

    def __bool__(self) -> bool:
        return bool(self.rules)

    def _find(self, kind: str, course: int | None, match: Any) -> Override | None:
        for rule in self.rules:
            if rule.kind == kind and (course is None or rule.course == course) and match(rule):
                return rule
        return None

    def for_syllabus(self, course_id: int) -> Override | None:
        return self._find("syllabus", course_id, lambda r: True)

    def for_file(self, course_id: int | None, file_id: int, name: str | None) -> Override | None:
        """The rule for one file. ``course_id`` None matches any course.

        That case is ``read_course_file``, which is given only a file id.
        Canvas file ids are unique across the instance, so an id rule is
        unambiguous without the course; a name rule could in principle catch
        a same-named file in another course, which the probe would show.
        """
        return self._find("file", course_id, lambda r: r.id == file_id or _glob(r.name, name))

    def for_page(self, course_id: int, url: str | None, title: str | None) -> Override | None:
        return self._find(
            "page",
            course_id,
            lambda r: (r.url is not None and r.url == url) or _glob(r.title, title),
        )


def _int(entry: dict[str, Any], key: str, where: str, *, required: bool) -> int | None:
    value = entry.get(key)
    if value is None:
        if required:
            raise OverridesError(f"{where}: `{key}` is required.")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverridesError(f"{where}: `{key}` must be an integer, got {value!r}.")
    return value


def _str(entry: dict[str, Any], key: str, where: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OverridesError(f"{where}: `{key}` must be a non-empty string.")
    return value.strip()


def parse(text: str, base_dir: Path, *, source: str = "overrides.toml") -> Overrides:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise OverridesError(f"{source} is not valid TOML: {exc}") from None

    unknown_top = set(data) - {"override"}
    if unknown_top:
        raise OverridesError(
            f"{source}: unexpected top-level key(s) {sorted(unknown_top)}; "
            "entries go under [[override]]."
        )
    entries = data.get("override", [])
    if not isinstance(entries, list):
        raise OverridesError(f"{source}: use [[override]] (an array of tables).")

    allowed = {"course", "kind", "action", "with", "id", "name", "url", "title"}
    rules: list[Override] = []
    for index, entry in enumerate(entries, start=1):
        where = f"{source} override #{index}"
        if not isinstance(entry, dict):
            raise OverridesError(f"{where}: expected a table.")
        if extra := set(entry) - allowed:
            raise OverridesError(f"{where}: unknown key(s) {sorted(extra)}.")

        kind = entry.get("kind")
        if kind not in KINDS:
            raise OverridesError(f"{where}: `kind` must be one of {list(KINDS)}, got {kind!r}.")

        course = _int(entry, "course", where, required=True)
        assert course is not None
        with_ = _str(entry, "with", where)
        action = entry.get("action", "replace" if with_ else None)
        if action not in ("hide", "replace"):
            raise OverridesError(
                f'{where}: set `with` to a replacement file, or `action = "hide"`.'
            )
        if action == "hide" and with_:
            raise OverridesError(f'{where}: `action = "hide"` takes no `with`.')
        if action == "replace" and not with_:
            raise OverridesError(f"{where}: a replacement needs `with`.")

        file_id = _int(entry, "id", where, required=False)
        name = _str(entry, "name", where)
        url = _str(entry, "url", where)
        title = _str(entry, "title", where)

        if kind == "file":
            if (file_id is None) == (name is None):
                raise OverridesError(f"{where}: a file needs exactly one of `id` or `name`.")
            if url or title:
                raise OverridesError(f"{where}: a file matches by `id` or `name` only.")
        elif kind == "page":
            if (url is None) == (title is None):
                raise OverridesError(f"{where}: a page needs exactly one of `url` or `title`.")
            if file_id is not None or name:
                raise OverridesError(f"{where}: a page matches by `url` or `title` only.")
        elif any(v is not None for v in (file_id, name, url, title)):
            raise OverridesError(f"{where}: a syllabus is matched by `course` alone.")

        replacement = None
        if with_:
            replacement = Path(with_).expanduser()
            if not replacement.is_absolute():
                replacement = base_dir / replacement

        rules.append(
            Override(
                index=index,
                course=course,
                kind=kind,
                action=action,
                id=file_id,
                name=name,
                url=url,
                title=title,
                replacement=replacement,
            )
        )
    return Overrides(rules)


def load_file(path: Path) -> Overrides:
    """Parse the file at ``path``; a missing file is an empty set."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Overrides([])
    except OSError as exc:
        raise OverridesError(f"Cannot read {path}: {exc.strerror or exc}.") from None
    return parse(text, path.parent, source=str(path))


_cache: tuple[Path, float | None, Overrides] | None = None


def current() -> Overrides:
    """The active overrides, re-read when the file changes.

    A file that turns bad after startup keeps the last good set and says so on
    stderr: losing every override because of one typo would silently put the
    stale Canvas content back.
    """
    global _cache
    path = overrides_path()
    try:
        mtime: float | None = path.stat().st_mtime
    except OSError:
        mtime = None

    if _cache is not None and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]

    try:
        loaded = load_file(path)
    except OverridesError as exc:
        if _cache is not None and _cache[0] == path:
            print(f"canvas-viewer-mcp: ignoring overrides edit: {exc}", file=sys.stderr)
            return _cache[2]
        raise
    _cache = (path, mtime, loaded)
    return loaded


def reset_cache() -> None:
    global _cache
    _cache = None


def guess_type(path: Path) -> str:
    return (
        _TEXT_SUFFIXES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def load_replacement(rule: Override) -> Replacement:
    """Read a rule's local file. Raises OverridesError if it is unreadable."""
    path = rule.replacement
    if path is None:
        raise OverridesError(f"Override #{rule.index} has no replacement file.")
    try:
        data = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise OverridesError(
            f"Override #{rule.index}: cannot read {path}: {exc.strerror or exc}."
        ) from None
    content_type = guess_type(path)
    text, note = extract_text(data, content_type)
    updated = datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Replacement(
        text=text, note=note, content_type=content_type, size=len(data), updated_at=updated
    )
