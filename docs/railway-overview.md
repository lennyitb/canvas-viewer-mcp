# Deploy and Host Canvas Viewer for Claude on Railway

Canvas Viewer is a read-only bridge between a Canvas LMS account and Claude.
It exposes coursework, grades, files, discussions, announcements and modules
as tools Claude can call, so a student can ask what is actually due instead of
clicking through every course and pasting the answer into a chat window.

## About Hosting Canvas Viewer for Claude

This template runs one small container and nothing else. State is a single
SQLite file on a persistent volume, so there is no database to provision or
back up. The container fronts its tools with an OAuth 2.1 server that Claude
registers itself against, which is what lets claude.ai add it as a custom
connector; the volume holds that authorization, so redeploys do not send you
back to the login page.

Railway supplies the public hostname and the server reads it at startup, so
there is no URL to type before one exists. You provide a Canvas host, an
access token and a password — your instance, your token, your login.

## Common Use Cases

- **Finding work the Canvas dashboard hides.** The dashboard is a view over
  one field, `due_at`. When an instructor stops maintaining due dates, that
  coursework silently disappears from it. This lists every assignment with no
  date filter, alongside the fields that reveal the real state.
- **Catching discussion replies still owed.** Canvas marks a graded discussion
  submitted the moment the first post goes up, and has no field at all for the
  replies to classmates most rubrics also require. One call reports what was
  asked for next to what was actually posted.
- **Reading course material in the conversation that needs it.** Lecture PDFs,
  rubrics and wiki pages are listed and their text extracted on request, so
  there is no download-and-paste step.
- **Seeing where a grade actually stands**, including what is still ungraded
  and what a given assignment is worth.
- **Catching up on announcements and discussion threads** without opening
  each course in turn.

## Dependencies for Canvas Viewer Hosting

- A Canvas LMS account, and an access token generated from it under Account →
  Settings → Approved Integrations. Canvas OAuth apps need a developer key
  only an account administrator can issue, so a personal token is the route
  available to a student.
- A Claude plan that supports custom connectors, added from claude.ai in a
  browser. Once authorized, the iOS and Android apps pick the connector up on
  their own.
- A persistent volume mounted at `/data`, included in this template. Without
  it every redeploy discards the OAuth state and deauthorizes the connector.
- No external database, cache or object store. Everything the server keeps
  fits in SQLite on that volume.

### Deployment Dependencies

- [Source and documentation](https://github.com/lennyitb/canvas-viewer-mcp)
- [Generating a Canvas access token](https://community.canvaslms.com/t5/Student-Guide/How-do-I-manage-API-access-tokens-as-a-student/ta-p/273)
- [Canvas REST API reference](https://canvas.instructure.com/doc/api/)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [Skills for this connector](https://github.com/lennyitb/canvas-viewer-mcp/releases/latest),
  installed separately on claude.ai under Settings → Capabilities → Skills

### Implementation Details

Three variables are required, and the two that are usually hardest to get
right are deliberately absent.

`PUBLIC_BASE_URL` is not asked for. The server derives it from
`RAILWAY_PUBLIC_DOMAIN`, because it cannot be known before the first deploy
and because the OAuth metadata is built from it — a value copied out of
someone else's instructions would send Claude to authorize against their
server instead of yours.

`PORT` is not asked for either; Railway injects it and the server binds to it.

`CANVAS_BASE_URL` accepts whatever is in your browser's address bar while you
are looking at Canvas. A pasted course link such as
`https://yourschool.instructure.com/courses/12345/assignments` is trimmed to
its host.

The server is strictly read-only. There are no write paths in the codebase —
not disabled, absent. It cannot submit work, post to a discussion, or alter a
Canvas account. Revoking the token in Canvas cuts access immediately and
independently of anything running here.

### Why Deploy Canvas Viewer for Claude on Railway?

A connector has to be reachable from Anthropic's servers over HTTPS, which
normally means a domain, a TLS certificate, a reverse proxy and somewhere for
the container to live — four problems that have nothing to do with coursework
and are where a self-hosted install turns into a weekend project. Railway
collapses them into a form with three fields, and hands back an HTTPS address
the server configures itself from.

It also keeps the deployment genuinely separate. A Canvas token grants full
access to an account, so a shared instance would mean trusting a stranger's
server with it. Here each install is its own project, its own container and
its own volume, under the account of the person whose coursework it reads.
