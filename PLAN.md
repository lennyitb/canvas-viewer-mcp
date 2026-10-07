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

## Stage 9 — Assignment feedback

A score says how an assignment went; the comments say what to do about it.
"Resubmit by Friday for full credit" is outstanding work that no date field
carries, and until now the server dropped it along with every rubric mark.

- [x] `canvas/feedback.py`: submission comments (author, `mine`, attachments,
      media flag, capped text) and rubric criteria joined to their marks,
      unmarked criteria kept with `points: null`
- [x] `get_assignment` returns `feedback`; a 403 on it is a note, not a failure
- [x] `list_feedback`: one request per course via `students/submissions`,
      windowed on `graded_at` or any comment by someone else, filtered here
      rather than by `graded_since` because a comment needs no regrade
- [x] `posted_at` kept: null means grades are unreleased and feedback may be
      hidden
- [x] Checkpoint: live against the real account, through the MCP tools.
      `list_feedback(days=30)` returned 30 submissions across 11 courses in
      about 20k characters, none unavailable; a history discussion graded
      60/100 showed why on its rubric -- "Replies to classmates: 0/40" --
      which neither the score nor the submission state says
- [x] Rubric titles arrive with the editor's line breaks inside them
      ("Comprehension\n(Relevance of\nPost)"); found live, now collapsed
- [ ] The same, through the deployed connector in claude.ai

## Stage 10 — Files you can actually get

Getting a whole file, rather than its text, meant calling `list_files`,
copying the `url` off a row and fetching it. No tool said "download", so a
model rarely thought to, and the route failed outright in any course hiding
its Files tab -- Linear Algebra and US History on the real account. Yet
`files/:id` answered in both: the Linear Algebra syllabus through its module
item, the History syllabus through a link on the Syllabus tab. Only the list
was blocked.

- [x] `linked_files()`: file ids lifted from HTML links and embeds, named from
      the `title` attribute where the editor writes the real filename
- [x] `linked_files` on pages, the syllabus, assignments, discussions and
      announcements; `file_id` on module File items; `id` on feedback files;
      hidden overrides filtered out of all of them
- [x] `list_files` on a hidden tab rebuilds the list from modules, syllabus,
      front page, assignments, pages (one by one from the modules when the
      Pages tab is hidden too), discussions and announcements, each row with
      `linked_from`
- [x] `download_course_file`: the pre-signed link for any file, lock dates for
      locked ones, and images under 3.75 MB inline
- [x] `read_course_file` carries `download_url`, and a file it cannot read
      points at it instead of stopping
- [x] `list_files` rows lose the link, filename, created date and folder
- [x] PowerPoint and Excel text, behind a zip-bomb guard shared with Word
- [x] Checkpoint: live against the real account, in process.
      `list_files(68894)` found the History syllabus `.docx` on the Syllabus
      tab and `list_files(69008)` the Linear Algebra one in a module; the
      `.pptx` link from `download_course_file` fetched 2,652,719 bytes, exactly
      the size Canvas reports; a 97-file listing went from 39k characters
      to 17k
- [ ] The same, through the deployed connector in claude.ai, including a fresh
      chat asked to "download the History syllabus"

## Stage 11 — When a grade changed, and whether it is final

Circuits released Quiz 5 at 1.4/10 on 10/4, pending review of its written
answers, and regraded it to 10/10 on 10/6. `list_feedback` caught the regrade,
but nothing on the row said the first score had been provisional or which
change was the latest, so the dashboard dated it by `posted_at` and lost the
pending state. The same course posts grades by hand and holds them for up to
a day after grading. While a grade is held, Canvas strips the score, the
rubric marks and the comments written before release. On release the only
stamp that moves is `posted_at`, so a grade held longer than the window
never showed up in the sweep at all.

- [x] `last_change()`: the latest of `graded_at`, `posted_at`, and anyone
      else's comment, written or edited. `posted_at` counts only when it
      released something (a grade or someone else's comment), because posting
      a whole section also stamps submissions with nothing on them
- [x] `list_feedback` windows on it. This is a bug fix: a grade held past the
      window and then released, or an edited comment, was skipped
- [x] `last_change_at` on every feedback row, in UTC, and `list_feedback`
      sorted newest first across courses
- [x] `workflow_state` on feedback rows, left off when `graded`;
      `pending_review` marks a provisional score
- [x] `posted_at` on `list_assignments` submissions: `graded` with no
      `posted_at` is a hidden grade
- [x] A sweep row whose rubric has no assessment behind it leaves the rubric
      out rather than listing every criterion unmarked. `get_assignment`
      keeps the definition, because before grading it is the only place the
      rubric shows
- [x] Tool docstrings and connection instructions describe the hidden-grade
      shape; README follows
- [x] Checkpoint: live against the real account, in process. Quiz 5 is dated
      by its 10/6 regrade and Lab 3 by its 9/27 release, not its 9/26
      grading. `list_feedback(days=30)` returned the same 36 rows as before,
      newest first across courses, in 23.8k characters against 22.3k: the
      added length is `last_change_at`. Every rubric on the account had
      marks, so none was dropped
- [ ] The connection instructions were already past the 2,048 characters a
      Claude client shows (it cuts them off mid-word in the Files paragraph),
      and this adds about 60 more. The detail went into the `list_feedback`
      description instead; the instructions still need cutting to fit
- [ ] Live: a Circuits lab during its hidden gap reads `graded` with
      `graded_at` set and no `posted_at`, score or rubric marks
- [ ] Live: the next Circuits quiz with written questions reads
      `pending_review` with its partial score and `posted_at` set

The skills that consume these fields are a separate change: dating grades by
last change, showing provisional and hidden grades under Waiting, and diffing
a snapshot between runs.

## Running concern — response size
Tool results consume model context and mobile latency. List tools return
summaries plus IDs; detail is fetched on request. Bodies are converted to
Markdown, stripped of boilerplate, and hard-capped with visible truncation
markers. Two calls beat one blown context window.
