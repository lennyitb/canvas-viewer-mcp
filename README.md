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
