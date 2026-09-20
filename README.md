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

Working. Twelve read-only tools over MCP, fronted by a single-user OAuth 2.1
server, running in a container. See [DEPLOY.md](DEPLOY.md) to run it and
[PLAN.md](PLAN.md) for how it was built.

## Tools

| Tool | Returns |
| --- | --- |
| `list_courses` | Active courses, term, current score |
| `list_assignments` | Every assignment, no date filtering, bodies omitted |
| `get_assignment` | One assignment including its full description |
| `list_announcements` | Recent announcements across all courses, with text (or titles only) |
| `list_discussions` / `get_discussion` | Topics, and one topic with the user's own participation counted; the reply tree on request |
| `list_discussion_participation` | Every graded discussion with the reply requirement and the user's post counts, in one call |
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

- `list_discussion_participation` answers "do I still owe any discussion
  posts?" in one call: a row per graded discussion with its dates, submission
  state, the sentences of its description that mention replies, and
  `my_participation` — top-level posts, replies to other people, and replies
  to yourself, counted separately. No post bodies.
- `list_assignments` puts a `completion_caveat` and a `discussion_topic_id` on
  every `discussion_topic` row.
- `get_assignment` repeats the caveat in full.
- `get_discussion` reads the **whole thread**, nested replies included, and
  returns `my_participation` plus the topic text. Pass `entries="mine"` or
  `entries="all"` to get the posts themselves, and `include_messages=False` to
  get them without bodies.

That last one matters more than it sounds. Canvas serves only top-level entries
from `/entries`, and peer replies are always nested: on one Week 4 topic that
endpoint returned 22 of 77 entries, hiding every reply anyone had written. The
server reads the full thread view instead, and says `full_thread: false` when
Canvas could only give it the top level.

## Token budget

Everything these tools return lands in a model's context window, so lists are
lean and detail is opt-in:

- List rows omit null fields, except `due_at`, whose absence is the point.
- `list_assignments` omits bodies and `html_url`; `get_assignment` has both.
- `get_discussion` returns counts and the topic text by default, not the
  thread. On one real Week 4 topic that is under 1k characters instead of 36k.
- `list_announcements` takes `include_messages=False` and a `course_id`.
- `list_discussion_participation` replaces a `list_assignments` sweep followed
  by `get_assignment` and `get_discussion` per discussion. On a course with 20
  graded discussions that is roughly 14k characters in place of 765k.

## Skills

The server reports; it doesn't conclude. Two skills do the concluding, and ship
in this repo as a Claude Code plugin alongside the server itself:

| Skill | Does |
| --- | --- |
| `canvas-week-report` | A prioritized "what's actually left" list: converts UTC deadlines to local time, estimates real dates for assignments whose course was copied without rolling the dates forward, and counts discussion replies still owed against what the description asks for. Budgeted to about four tool calls. |
| `canvas-dashboard` | Runs that gather in a Sonnet subagent, then publishes the result as a dashboard artifact with announcements and a written summary. Needs a client with subagents and artifacts. |

Install both, plus the server, in one step:

```
/plugin marketplace add lennyitb/canvas-viewer-mcp
/plugin install canvas-viewer@canvas-viewer
```

then configure it:

```
/canvas-viewer:setup
```

Setup finds your Canvas host by school name, links you to the page that mints a
token, stores both under `~/.config/canvas-viewer-mcp/`, and verifies the whole
path before it says it worked. Nothing goes in a shell profile.

The server runs over stdio via `uvx` — on your machine, against your own Canvas
host and token, with no login and no hosted instance in the path. To point the
plugin at a deployed instance instead, replace [.mcp.json](.mcp.json) with the
HTTP form from [DEPLOY.md](DEPLOY.md).

Skills are a Claude Code mechanism. Other MCP clients get the twelve tools and
the server's own usage instructions, but not the skills; on claude.ai, a skill
folder can be zipped and uploaded under Settings → Capabilities.

## Configuration

Three sources, in descending precedence: environment variables, a TOML file at
`~/.config/canvas-viewer-mcp/config.toml`, then defaults. The environment wins so
that a leftover file in a home directory can never redirect a deployed container.

```toml
# ~/.config/canvas-viewer-mcp/config.toml — written by /canvas-viewer:setup
base_url = "https://yourschool.instructure.com"
# token_file = "/some/other/path"   # optional; defaults to ./token beside this file
```

See [.env.example](.env.example) for the environment form, which is what the
container uses. The Canvas API token is never read from inside the repository and
never from `config.toml`; supply it via `CANVAS_TOKEN`, a `CANVAS_TOKEN_FILE` path
(how the container receives it), or `~/.config/canvas-viewer-mcp/token`. Keeping
it in its own file is what makes `config.toml` safe to paste into a bug report.

Generate a token in Canvas under **Account → Settings → Approved Integrations →
"+ New Access Token"**. It grants full access to your Canvas account, so treat it
like a password.

For a hosted deployment, that token is the only secret to prepare. The connector
login is gated by a pairing code the server issues itself on first run and prints
once to its logs; set `AUTH_PASSWORD_HASH` to choose a password instead, which
also revokes any code already issued. See [DEPLOY.md](DEPLOY.md).

## License

MIT
