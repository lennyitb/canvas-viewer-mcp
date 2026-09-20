# canvas-viewer-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that exposes Canvas LMS
coursework to an AI assistant — **including the assignments the Canvas dashboard
hides from you.**

## Why this exists

The Canvas dashboard and the `/api/v1/planner/items` endpoint behind it are both
views over one field: `due_at`. That works right up until an instructor stops
maintaining due dates — and then coursework silently disappears from your
dashboard. It isn't marked late or flagged. It simply isn't there.

This server refuses to trust `due_at` alone. It enumerates coursework with **no
date filter** and reports every item it finds, carrying the fields that reveal
what is actually going on: `due_at`, `unlock_at`, `lock_at`, submission state,
module position, publication state, and `updated_at`. Assignments with no due
date are reported like any other, because here their absence of a date is the
whole point.

**The server reports; it does not conclude.** There is no bucketing, no
staleness heuristic, and no guess at what is "really" due. Instructors who
don't maintain `due_at` usually still say "due Friday" in the assignment text
or an announcement, so the server also exposes assignment bodies, files,
discussions, and announcements — the evidence — and leaves the reading of it
to the model that asked.

This is a deliberate split. Classification logic frozen into a server is wrong
in ways you cannot see from the outside, and this is a tool whose entire
purpose is to stop things going missing.

## Scope

Strictly read-only. There are no write paths in the codebase — not disabled,
absent. It cannot submit work, post to a discussion, or alter your Canvas account.

## Status

Working. Eleven read-only tools over MCP, fronted by a single-user OAuth 2.1
server, running in a container. See [DEPLOY.md](DEPLOY.md) to run it and
[PLAN.md](PLAN.md) for how it was built.

## Tools

| Tool | Returns |
| --- | --- |
| `list_courses` | Active courses, term, current score |
| `list_assignments` | Every assignment, no date filtering, bodies omitted |
| `get_assignment` | One assignment including its full description |
| `list_announcements` | Recent announcements across all courses, with text |
| `list_discussions` / `get_discussion` | Topics, and one topic's full reply tree with the user's own participation counted |
| `list_files` / `read_course_file` | Course files, and text extracted from one |
| `list_pages` / `get_page` | Wiki pages, and one page's body |
| `list_modules` | Modules and their items, in instructor-intended order |

Course tabs are often disabled, and Canvas reports that as an error rather than
an empty list. Those tools return `{"unavailable": true, "reason": ...}` so a
sweep across courses is not lost to one locked course.

### Graded discussions are not done when Canvas says they are

Canvas records a discussion submission the moment the first post goes up, and
has no field anywhere for the replies to classmates that most rubrics also
require. So a discussion reads `submitted` — or even `graded` — with replies
still owed, and `due_at` usually carries only the *post* deadline while the
reply deadline sits in prose in the description. On one real course that is 20
of 23 assignments.

Nothing here classifies that for you, but the tools stop hiding it:

- `list_assignments` puts a `completion_caveat` on every `discussion_topic` row.
- `get_assignment` repeats it in full and hands back `discussion_topic_id`.
- `get_discussion` returns the **whole thread**, nested replies included, and
  summarises `my_participation` — top-level posts, replies to other people, and
  replies to yourself, counted separately.

That last one matters more than it sounds. Canvas serves only top-level entries
from `/entries`, and peer replies are always nested: on one Week 4 topic that
endpoint returned 22 of 77 entries, hiding every reply anyone had written. The
server reads the full thread view instead, and says `full_thread: false` when
Canvas could only give it the top level.

## Configuration

See [.env.example](.env.example). The Canvas API token is never read from inside
the repository; supply it via `CANVAS_TOKEN`, a `CANVAS_TOKEN_FILE` path (how the
container receives it), or `~/.config/canvas-viewer-mcp/token`.

Generate a token in Canvas under **Account → Settings → Approved Integrations →
"+ New Access Token"**. It grants full access to your Canvas account, so treat it
like a password.

## License

MIT
