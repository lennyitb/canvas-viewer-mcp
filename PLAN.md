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
- [x] Checkpoint: `canvas-probe courses` lists 11 real courses against a real Canvas account

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
- [x] Built on FastMCP's `OAuthProvider`, which supplies DCR, PKCE, metadata
      documents and token endpoints; this project supplies storage and the login gate
- [x] `/.well-known/oauth-authorization-server` and
      `/.well-known/oauth-protected-resource/mcp` (RFC 9728 puts the latter under
      the resource path)
- [x] Dynamic Client Registration (required for Claude mobile), PKCE S256
- [x] Single-user login, argon2 hash from env, tokens in SQLite so a redeploy
      does not silently deauthorize the connector
- [x] Checkpoint: the full dance runs end to end in tests -- discover, register,
      authorize, log in, exchange with PKCE, call `/mcp` with the bearer token

`authorize()` issues no code. The endpoint is reachable by anyone, so it parks
the request and redirects to a password-gated page; only a correct password
turns a parked request into a code. Codes and refresh tokens are single-use,
and rotation kills both halves of a pair.

`PUBLIC_BASE_URL` is configuration, never inferred from the request. Behind a
reverse proxy the inbound scheme is http, so derived metadata would advertise
http:// endpoints and the connector would be rejected.

## Stage 5 — Containerize
- [x] Multi-stage Dockerfile, non-root (uid 10001), no secrets baked into the image
- [x] `compose.yaml`: app only, bound to 127.0.0.1, read-only rootfs, secret mount
- [x] GitHub Actions → GHCR on tag
- [x] Checkpoint: image built and run; full OAuth dance plus a live
      `list_courses` returning 11 real courses, with the token supplied as a
      mounted file rather than an environment variable

The `/data` volume is load-bearing. It holds the OAuth database, and without
it every redeploy silently deauthorizes the connector and demands a browser
round trip.

The health check deliberately does not call Canvas. Depending on an external
service would mark the container unhealthy during a Canvas outage it cannot do
anything about, and invite an orchestrator to restart it pointlessly.

No `cloudflared` sidecar: the host is served directly, so
TLS terminates in the existing reverse proxy and the container speaks plain
HTTP on its port.

## Stage 6 — Deploy and connect

Prepared here; the remaining steps need the homelab and the claude.ai account.

- [x] `v0.1.0` tagged, image published and verified to pull **anonymously**
      from `ghcr.io/lennyitb/canvas-viewer-mcp` (`:latest` and `:0.1.0`)
- [x] Published image smoke-tested: healthy, metadata advertises the public
      https URL, anonymous `/mcp` refused with 401
- [x] [DEPLOY.md](DEPLOY.md) written
- [ ] DNS: `A <your hostname> -> <public IP>`
- [ ] `docker compose up -d` on the homelab, with the Canvas token in place
- [ ] Reverse proxy vhost -> `127.0.0.1:8000`, **`proxy_buffering off`**
- [ ] Add the custom connector on claude.ai, then verify from the phone

Buffering is the one that will waste an evening if missed: MCP streams over
server-sent events, and a buffering proxy holds the stream until it fills, so
the symptom is tool calls that hang and time out rather than anything that
looks like a proxy problem.

## Stage 7 — Install without a terminal

Stage 6 gets one person connected. This stage is about somebody else being
able to, and the constraint that decides everything here is that they use
claude.ai in a browser and on a phone. That rules out a Claude Code plugin,
and it rules out a setup command, so whatever a hosting platform's deploy
form can collect has to be the entire configuration.

Two of the four required values were removed rather than explained:
`PUBLIC_BASE_URL` is read from the platform (`RAILWAY_PUBLIC_DOMAIN`,
`RENDER_EXTERNAL_URL`, `FLY_APP_NAME`), and the login accepts a plaintext
`AUTH_PASSWORD` a form can collect. That leaves the Canvas host and the token.

- [x] `v0.2.0` tagged; image rebuilt with the platform-aware configuration
- [x] Skills packaged as uploadable zips and attached to the release
- [x] [docs/railway-template.md](docs/railway-template.md) written
- [x] Railway template created and its URL put in the README button
- [ ] Template deployed into a throwaway project and walked end to end

The check that matters on that last one is the OAuth metadata: every URL it
advertises must begin with the deploy's own domain. That is the proof the
platform hostname was picked up, and getting it wrong is the failure that
sends someone else's Claude to authorize against the wrong server.

## Stage 8 — Install on a machine you own

Stage 7 removed the terminal for people who have nowhere to run a container.
This stage is for the ones who do, and the honest state of it was that
DEPLOY.md named its own audience as someone already running a box behind a
proxy. Everything else -- DNS, TLS, a proxy vhost with the right buffering,
a root-owned secrets file -- was left to the reader.

The path taken is not a shorter document. It is that the person asks an
assistant with a shell on the machine to do it, so DEPLOY.md is written for
that reader first, and the parts an assistant must not do are the parts the
setup script collects from a terminal instead.

- [x] `compose.yaml` gains `--profile tls` (Caddy, automatic certificates) and
      `--profile tunnel` (cloudflared, for a machine with no public IP)
- [x] `scripts/setup.sh`: interactive at a terminal, and for an assistant, a
      prepare-and-hand-over that never touches the Canvas token
- [x] The token checked against Canvas before it is written, rather than
      failing later as every tool call
- [x] DEPLOY.md reordered around the assistant, by-hand instructions kept
- [x] CI builds the image and starts it against a root-owned volume and secret
- [ ] `v0.4.0` tagged and the image published
- [ ] A real deploy on a real machine, by asking for one, start to finish

That last box is the only one that proves anything. Everything above it was
tested against the components -- the built image, all three compose profiles,
both halves of the script -- but no run has yet gone from "deploy this on this
machine" to a connector answering in claude.ai.

## Running concern — response size
Tool results consume model context and mobile latency. List tools return
summaries plus IDs; detail is fetched on request. Bodies are converted to
Markdown, stripped of boilerplate, and hard-capped with visible truncation
markers. Two calls beat one blown context window.
