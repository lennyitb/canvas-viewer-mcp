"""``canvas-probe`` -- a direct CLI over the Canvas client.

Exists so the Canvas layer can be verified against a real account before any
MCP, OAuth, or container machinery is in the way. When something breaks later,
this tells you which half is at fault.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence

from .canvas.assignments import fetch_assignments
from .canvas.client import CanvasClient
from .canvas.errors import CanvasError
from .config import Config, ConfigError


async def _whoami(client: CanvasClient, course_id: int | None) -> int:
    user = await client.current_user()
    print(f"Signed in as : {user.get('name', '(unknown)')}")
    print(f"Canvas id    : {user.get('id')}")
    if login := user.get("login_id"):
        print(f"Login        : {login}")
    return 0


async def _courses(client: CanvasClient, course_id: int | None) -> int:
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


async def _assignments(client: CanvasClient, course_id: int | None) -> int:
    """Dump assignments verbatim. No filtering, no classification."""
    courses = await client.active_courses()
    if course_id is not None:
        courses = [c for c in courses if c["id"] == course_id]
        if not courses:
            print(f"No active course with id {course_id}.", file=sys.stderr)
            return 1

    total = 0
    for course in courses:
        items = await fetch_assignments(client, course["id"], course_name=course.get("name"))
        if not items:
            continue
        print(f"\n=== [{course['id']}] {course.get('name')} ({len(items)}) ===")
        for a in items:
            sub = a.submission
            state = "-"
            if sub:
                if sub.excused:
                    state = "excused"
                elif sub.submitted_at:
                    state = "submitted"
                elif sub.missing:
                    state = "missing"
                else:
                    state = sub.workflow_state or "-"
            due = a.due_at[:10] if a.due_at else "NO DUE DATE"
            created = a.created_at[:10] if a.created_at else "?"
            print(f"  {a.id:>8}  due {due:<12} created {created:<12} {state:<10} {a.name[:44]}")
            total += 1

    print(f"\n{total} assignment(s).")
    return 0


def _hash_password() -> int:
    """Print an argon2 hash for AUTH_PASSWORD_HASH.

    Reads from a prompt rather than argv so the password does not end up in
    shell history or the process list.
    """
    import getpass

    from .auth.provider import hash_password

    first = getpass.getpass("Connector password: ")
    if len(first) < 12:
        print("Use at least 12 characters.", file=sys.stderr)
        return 2
    if first != getpass.getpass("Confirm: "):
        print("Passwords did not match.", file=sys.stderr)
        return 2

    print("\nAdd this to your environment (the hash, never the password):\n")
    print(f"AUTH_PASSWORD_HASH='{hash_password(first)}'")
    return 0


Handler = Callable[[CanvasClient, int | None], Awaitable[int]]

COMMANDS: dict[str, Handler] = {
    "whoami": _whoami,
    "courses": _courses,
    "assignments": _assignments,
}


async def _run(command: str, course_id: int | None) -> int:
    config = Config.from_env()
    async with CanvasClient(config) as client:
        return await COMMANDS[command](client, course_id)


def _reset_pairing() -> int:
    """Forget the self-issued pairing code so the next start prints a new one.

    Deliberately does not need PUBLIC_BASE_URL or any other deployment value;
    it is reached from inside a container that may be failing to start.
    """
    from .auth.provider import PAIRING_SECRET_NAME
    from .auth.store import OAuthStore
    from .config import default_db_path

    path = default_db_path()
    if not path.exists():
        print(f"No OAuth database at {path}; nothing to reset.", file=sys.stderr)
        return 1

    store = OAuthStore(path)
    try:
        if store.get_server_secret(PAIRING_SECRET_NAME) is None:
            print("No pairing code stored. One is issued on the next start.")
            return 0
        store.delete_server_secret(PAIRING_SECRET_NAME)
    finally:
        store.close()

    print("Pairing code cleared. Restart the server to have a new one printed.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="canvas-probe",
        description="Verify Canvas API access without going through MCP.",
    )
    parser.add_argument(
        "command",
        choices=sorted([*COMMANDS, "hash-password", "reset-pairing"]),
        help=(
            "what to fetch; hash-password generates AUTH_PASSWORD_HASH, "
            "reset-pairing forgets the self-issued pairing code"
        ),
    )
    parser.add_argument(
        "--course", type=int, default=None, help="limit to one course id (assignments only)"
    )
    args = parser.parse_args(argv)

    if args.command == "hash-password":
        return _hash_password()

    if args.command == "reset-pairing":
        return _reset_pairing()

    try:
        return asyncio.run(_run(args.command, args.course))
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except CanvasError as exc:
        print(f"Canvas error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
