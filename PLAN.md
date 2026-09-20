# Build plan

Each stage ends at a checkpoint that is verifiable on its own. Nothing advances
until the current stage is green.

## Stage 0 — Repo hygiene
- [x] `.gitignore` written *before* `git init` (public repo; token must never be stageable)
- [x] Canvas token relocated to `~/.config/canvas-viewer-mcp/token` (0600), outside the repo tree
- [x] Project scaffold, `pyproject.toml`, `README`, `LICENSE`, `.env.example`
- [ ] gitleaks: pre-commit hook + CI job (two independent chances to catch a leak)
- [ ] Push to GitHub

## Stage 1 — Canvas client (no MCP yet)
- [ ] `canvas/client.py`: auth, **Link-header pagination**, rate-limit backoff
- [ ] `canvas-probe` CLI for direct verification
- [ ] Checkpoint: `canvas-probe courses` lists real courses

Pagination is the highest-risk item in the project: miss `rel="next"` and you get
a partial list with no error — a silent truncation bug in a tool built to stop
missing things. It gets a dedicated test.

## Stage 2 — Reconciliation logic
- [ ] Record real API responses as fixtures, then **scrub** them (names, IDs, hostnames)
- [ ] Pure bucketing functions: dated-upcoming / undated-unsubmitted / stale-unsubmitted
- [ ] Edge cases: null `due_at`, submitted-ungraded, locked, past-due-submitted, excused
- [ ] Checkpoint: `pytest` green; `canvas-probe status` shows correct buckets

## Stage 3 — MCP server over stdio
- [ ] Tools: `coursework_status`, `list_courses`, `assignment`, `course_files`,
      `read_file`, `discussions`, `discussion`, `announcements`, `pages`, `page`
- [ ] HTML → Markdown with boilerplate stripped; hard size caps with explicit truncation markers
- [ ] Checkpoint: driven live from Claude Code against real Canvas data, and it
      surfaces something the Canvas dashboard hides

Testing over stdio first means the whole tool surface is debugged before any
OAuth, networking, or deployment exists.

## Stage 4 — HTTP transport + OAuth 2.1
- [ ] Verify current MCP auth spec before building (this surface has been moving)
- [ ] `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`
- [ ] Dynamic Client Registration (required for Claude mobile), PKCE S256
- [ ] Single-user login, argon2 password hash from env, tokens in SQLite
- [ ] Checkpoint: full OAuth dance completes against a local client

## Stage 5 — Containerize
- [ ] Multi-stage Dockerfile, non-root, no secrets baked into the image
- [ ] `compose.yaml`: app + `cloudflared` sidecar
- [ ] GitHub Actions → GHCR on tag

## Stage 6 — Deploy and connect
- [ ] Pull image on target host, bring up tunnel
- [ ] DNS: `CNAME canvas-viewer-mcp.lenny.zone -> <TUNNEL-ID>.cfargotunnel.com` (proxied)
- [ ] Add custom connector on claude.ai, verify from phone

## Running concern — response size
Tool results consume model context and mobile latency. List tools return
summaries plus IDs; detail is fetched on request. Bodies are converted to
Markdown, stripped of boilerplate, and hard-capped with visible truncation
markers. Two calls beat one blown context window.
