#!/usr/bin/env python3
"""Package each skill in ``skills/`` as a zip claude.ai will accept.

Skills reach a Claude Code user as a directory and a claude.ai user as an
uploaded zip, and the zip is the one nobody can produce by hand: it wants a
``.claude-plugin/plugin.json`` next to the skill folder, which a person who
downloads this repository as a zip and re-zips a subfolder will not get right.
Building it here, and attaching it to every release, is what lets the README
say "download this file and upload it" instead of describing an archive
layout.

The layout is what claude.ai itself produces when a skill is exported:

    .claude-plugin/plugin.json      {"name": ..., "description": ...}
    skills/<name>/SKILL.md          the skill, plus any files beside it

Entries are written with a fixed timestamp so that rebuilding an unchanged
skill produces a byte-identical archive, and a release asset that differs
means the skill actually changed.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Fixed DOS timestamp (1980-01-01) for reproducible archives.
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def parse_frontmatter(skill_md: Path) -> dict[str, str]:
    """Pull ``name`` and ``description`` out of a SKILL.md header.

    Deliberately not a YAML parser: the header is two single-line scalars and
    adding a dependency to read them would mean the release job needs one too.
    A value may be quoted, since that is how claude.ai writes it back out.
    """
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SystemExit(f"{skill_md}: missing YAML frontmatter.")

    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or key != key.strip() or not key.strip():
            continue  # a continuation or a stray line; only top-level keys matter
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value

    for required in ("name", "description"):
        if not fields.get(required):
            raise SystemExit(f"{skill_md}: frontmatter has no {required}.")
    return fields


def build(skill_dir: Path, out_dir: Path) -> Path:
    meta = parse_frontmatter(skill_dir / "SKILL.md")
    name = meta["name"]
    if name != skill_dir.name:
        raise SystemExit(
            f"{skill_dir}: frontmatter name {name!r} does not match the directory name. "
            "They must agree, or the uploaded skill is filed under a name nobody expects."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.zip"

    plugin_json = json.dumps({"name": name, "description": meta["description"]})
    files = sorted(p for p in skill_dir.rglob("*") if p.is_file())

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(".claude-plugin/plugin.json", FIXED_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, plugin_json)
        for path in files:
            arcname = f"skills/{name}/{path.relative_to(skill_dir).as_posix()}"
            info = zipfile.ZipInfo(arcname, FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "dist", help="output directory (default: dist/)"
    )
    args = parser.parse_args()

    skill_dirs = sorted(d for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").is_file())
    if not skill_dirs:
        raise SystemExit(f"No skills found under {SKILLS_DIR}.")

    for skill_dir in skill_dirs:
        out = build(skill_dir, args.out)
        print(f"{out.relative_to(REPO_ROOT)}  ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
