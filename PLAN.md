# Build plan

Each stage ends at a checkpoint that is verifiable on its own. Nothing advances
until the current stage is green.

## Stage 0 — Repo hygiene
- [x] `.gitignore` written *before* `git init` (public repo; token must never be stageable)
- [x] Canvas token relocated to `~/.config/canvas-viewer-mcp/token` (0600), outside the repo tree
- [x] Project scaffold, `pyproject.toml`, `README`, `LICENSE`, `.env.example`
- [x] gitleaks: pre-commit hook + CI job (two independent chances to catch a leak)
- [x] Push to GitHub

## Stage 1 — Canvas client (no MCP yet)
- [x] `canvas/client.py`: auth, **Link-header pagination**, rate-limit backoff
- [x] `canvas-probe` CLI for direct verification
- [x] Checkpoint: `canvas-probe courses` lists 11 real courses against vsc.instructure.com

Pagination is the highest-risk item in the project: miss `rel="next"` and you get
a partial list with no error — a silent truncation bug in a tool built to stop
missing things. It gets a dedicated test.

## Stage 2 — Assignment dump (normalize, do not interpret)

The server reports; it does not conclude. It fetches coursework with **no date
filter**, flattens each item to a stable row, and stops there. No bucketing, no
staleness heuristic, no guess at what is "really" due.

That division is deliberate. Bucketing encodes policy -- what counts as stale,
what counts as outstanding -- and policy frozen into a server is wrong in ways
you cannot see from the outside. Deciding it per question, with the assignment
text and announcements in hand, is both more accurate and less code.

- [x] Fetch all assignments per course, no date filter, `include[]=submission`
- [x] Flatten to a stable row: dates, submission state, points,
      publication state, `created_at`/`updated_at`, permalink
- [x] Preserve nulls faithfully -- `due_at: null` is the signal, not an absence
- [x] Tests: nothing dropped, nulls survive, submission state survives (27 passing)
- [x] Checkpoint: `canvas-probe assignments --course 68818` dumps all 9, exposing
      7 due dates left a year stale against a 2026-08-25 creation date

Term metadata stays in the output. The active-course list includes non-course
shells (orientation, placement, support), and distinguishing them is a
query-time judgement, not something the server should decide.

## Stage 3 — MCP server over stdio
- [x] 11 tools: `list_courses`, `list_assignments`, `get_assignment`,
      `list_announcements`, `list_discussions`, `get_discussion`, `list_files`,
      `read_course_file`, `list_pages`, `get_page`, `list_modules`
- [x] HTML → Markdown with boilerplate stripped; hard size caps with explicit truncation markers
- [x] Graceful degradation: a disabled course tab returns a note, not an exception
- [x] Checkpoint: driven live over MCP against real Canvas, and it surfaced
      what the dashboard hides (see below)

Availability is not uniform and the tools must survive it: across eleven real
courses, files 403 in five and pages 404 in seven. A sweep continues past them.

### What the checkpoint found

Introduction to Projects, module order vs. due dates:

| # | Assignment | due_at | state |
| --- | --- | --- | --- |
| 1 | Simulation/Breadboard | 2026-09-07 | submitted |
| 2 | Schematic Capture | 2026-09-21 | submitted |
| 3 | PCB Layout and Procurement #1 | 2025-09-18 | submitted |
| 4 | PCB Layout and Procurement #2 | 2025-09-25 | **missing** |
| 5 | BOM | 2025-10-01 | missing |
| 6 | PCB Assembly and Board Bench Test | 2025-11-13 | missing |
| 7 | Enclosure Design and Fabrication | 2025-11-20 | missing |
| 8 | PCB and Enclosure Assembly | 2025-12-04 | missing |
| 9 | Test & Demonstration | 2025-12-11 | missing |

The module ordering matches the stale date ordering exactly, offset by about a
year. Every assignment was created 2026-08-25; the first two had their dates
rolled forward and the rest did not. Adding a year to item 4 gives 2026-09-25,
which sits four days after item 3's corrected date -- consistent with the rest
of the sequence.

No code produced that reading. The tools supplied `due_at`, `created_at`,
module position, and submission state; the inference was made at query time.
That is the whole argument for keeping classification out of the server.

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
