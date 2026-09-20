"""``canvas-probe`` -- a direct CLI over the Canvas client.

Exists so the Canvas layer can be verified against a real account before any
MCP, OAuth, or container machinery is in the way. When something breaks later,
this tells you which half is at fault.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from .canvas.client import CanvasClient
from .canvas.errors import CanvasError
from .config import Config, ConfigError


async def _whoami(client: CanvasClient) -> int:
    user = await client.current_user()
    print(f"Signed in as : {user.get('name', '(unknown)')}")
    print(f"Canvas id    : {user.get('id')}")
    if login := user.get("login_id"):
        print(f"Login        : {login}")
    return 0


async def _courses(client: CanvasClient) -> int:
    courses = await client.active_courses()
    if not courses:
        print("No active courses. If that's wrong, the token may lack scope.")
        return 1

    print(f"{len(courses)} active course(s):\n")
    for course in courses:
        name = course.get("name") or "(unnamed)"
        code = course.get("course_code") or ""
        print(f"  [{course.get('id'):>8}] {name}" + (f"  ({code})" if code else ""))

        enrolments = course.get("enrollments") or []
        scores = [
            e.get("computed_current_score")
            for e in enrolments
            if e.get("computed_current_score") is not None
        ]
        if scores:
            print(f"{'':>11} current score: {scores[0]}%")
    return 0


COMMANDS = {"whoami": _whoami, "courses": _courses}


async def _run(command: str) -> int:
    config = Config.from_env()
    async with CanvasClient(config) as client:
        return await COMMANDS[command](client)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="canvas-probe",
        description="Verify Canvas API access without going through MCP.",
    )
    parser.add_argument("command", choices=sorted(COMMANDS), help="what to fetch")
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_run(args.command))
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except CanvasError as exc:
        print(f"Canvas error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
