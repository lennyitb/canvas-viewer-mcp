# canvas-viewer-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that exposes Canvas LMS
coursework to an AI assistant — **including the assignments the Canvas dashboard
hides from you.**

## Why this exists

The Canvas dashboard and the `/api/v1/planner/items` endpoint behind it are both
views over one field: `due_at`. That works right up until an instructor stops
maintaining due dates — and then coursework silently disappears from your
dashboard. It isn't marked late or flagged. It simply isn't there.

This server refuses to trust `due_at` alone. It enumerates coursework with no date
filter and reports what it finds in three buckets:

| Bucket | Meaning |
| --- | --- |
| **Dated & upcoming** | What the dashboard already shows you |
| **Undated & unsubmitted** | `due_at: null` — invisible to the dashboard |
| **Stale-dated & unsubmitted** | Past `due_at`, nothing submitted, still unlocked |

It also returns the raw material needed to work out the *real* deadline:
assignment bodies, recent announcements, module sequencing, and unlock/lock
windows. Instructors who don't maintain `due_at` usually still say "due Friday"
in the assignment text or an announcement.

**Deliberately, the server does not guess dates.** It surfaces evidence; the model
reads it. A date-guessing heuristic buried in a server is wrong in ways you can't
see, and this is a tool whose entire purpose is to stop missing things.

## Scope

Strictly read-only. There are no write paths in the codebase — not disabled,
absent. It cannot submit work, post to a discussion, or alter your Canvas account.

## Status

Under construction. See [PLAN.md](PLAN.md) for the build stages.

## Configuration

See [.env.example](.env.example). The Canvas API token is never read from inside
the repository; supply it via `CANVAS_TOKEN`, a `CANVAS_TOKEN_FILE` path (how the
container receives it), or `~/.config/canvas-viewer-mcp/token`.

Generate a token in Canvas under **Account → Settings → Approved Integrations →
"+ New Access Token"**. It grants full access to your Canvas account, so treat it
like a password.

## License

MIT
