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
server, running in a container. See [PLAN.md](PLAN.md) for how it was built.

## Install

Three steps: run your own copy of the server, connect Claude to it, add the
skills. Fifteen minutes, and nothing after step 1 involves a terminal.

**Every install is its own instance.** You deploy your own container, holding
your own Canvas token, at your own address, with its own login. There is no
shared server in the middle — nothing about this depends on the author's
deployment continuing to exist, and no one else's instance can see your
coursework.

### 1. Run the server

**The easy way — Railway.** One click, three fields, about three minutes.
Railway runs the container, gives it an HTTPS address, and keeps it on.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/1cOY5u?referralCode=fkprGJ&utm_medium=integration&utm_source=template&utm_campaign=generic)

It asks for three things:

| | |
| --- | --- |
| **Your Canvas address** | Open Canvas in another tab and copy the address bar. Anything after the site name is trimmed off, so a link to a course works fine. |
| **Your Canvas token** | Canvas → Account → Settings → Approved Integrations → **"+ New Access Token"**. Leave the expiry blank. Copy it — Canvas shows it once. |
| **A password you choose** | At least 12 characters. Claude asks for it once, when you connect. |

That is the whole configuration. There is no fourth field for the server's own
public URL, which is the value a one-click install would otherwise founder on:
nobody can know it before deploying, because the platform assigns it. The
server reads it from the platform instead.

When the deploy finishes Railway assigns the service a public address. That
address **is** the connector URL — paste it as-is, no path on the end. (`/mcp`
also works, for connectors added before v0.3.0.)

If the service shows no domain, open **Settings → Networking → Generate
Domain**. Check the domain's target port matches what the container is
listening on; Railway detects it when the domain is created, so a domain
generated while an earlier deploy was failing can be left pointing at the
wrong one, and every request then returns Railway's own 502 page.

Railway is about $5/month. [docs/railway-template.md](docs/railway-template.md)
records exactly what the button deploys.

**Your own server.** If you already run things behind a reverse proxy,
[DEPLOY.md](DEPLOY.md) has the `docker compose` path. Same container, same
three values, in a `.env` file.

**On your own machine.** If you use Claude Code or another client that can
launch a local process, you can skip hosting entirely and run the server over
stdio — no OAuth, no public address, no monthly bill, and it only runs while
you're using it. It won't reach claude.ai in a browser or the phone apps.

```bash
pipx install canvas-viewer-mcp   # or: uvx canvas-viewer-mcp
mkdir -p ~/.config/canvas-viewer-mcp
printf 'base_url = "https://yourschool.instructure.com"\n' > ~/.config/canvas-viewer-mcp/config.toml
install -m 600 /dev/null ~/.config/canvas-viewer-mcp/token
read -rs CANVAS_TOKEN && printf '%s' "$CANVAS_TOKEN" > ~/.config/canvas-viewer-mcp/token
canvas-probe whoami   # should print your name
```

`read -rs` keeps the token out of your shell history. Then register it with
your client — for Claude Code:

```bash
claude mcp add canvas-viewer -- canvas-viewer-mcp
```

### 2. Connect Claude to it

Skip this if you're running over stdio; your client already has it.

On **claude.ai** in a browser — not the mobile apps, which can't add new
connectors:

**Settings → Connectors → Add custom connector**, and paste your
`https://.../mcp` URL.

Claude registers itself and sends you to a login page. Enter the password you
chose. Once it's authorized, the iOS and Android apps pick it up on their own.

### 3. Install the skills

The server reports; it doesn't conclude. The skills do the concluding — they're
what turns twelve tools into "here's what's actually due".

| Skill | Does |
| --- | --- |
| [`canvas-week-report`](skills/canvas-week-report/SKILL.md) | A prioritized "what's actually left" list: converts UTC deadlines to local time, estimates real dates for assignments whose course was copied without rolling the dates forward, and counts discussion replies still owed against what the description asks for. Budgeted to about four tool calls. |
| [`canvas-dashboard`](skills/canvas-dashboard/SKILL.md) | The same triage rendered as a dashboard page, plus recent announcements and a written summary of the week. Needs a client that can make artifacts. |

Skills install separately from the connector — there's no bundle that carries
both, on any client.

**On claude.ai:** download `canvas-week-report.zip` and `canvas-dashboard.zip`
from the [latest release](https://github.com/lennyitb/canvas-viewer-mcp/releases/latest),
then **Settings → Capabilities → Skills → Upload skill**, once per file. Don't
re-zip the folders from this repository by hand; the archive needs a layout the
release build produces for you.

**In Claude Code:** copy the folders instead.

```bash
git clone https://github.com/lennyitb/canvas-viewer-mcp
cp -r canvas-viewer-mcp/skills/canvas-* ~/.claude/skills/
```

Then ask for what's due. Neither skill needs to be named.

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

## Configuration

Three sources, in descending precedence: environment variables, a TOML file at
`~/.config/canvas-viewer-mcp/config.toml`, then defaults. The environment wins
so that a leftover file in a home directory can never redirect a deployed
container.

```toml
# ~/.config/canvas-viewer-mcp/config.toml
base_url = "https://yourschool.instructure.com"
# token_file = "/some/other/path"   # optional; defaults to ./token beside this file
```

See [.env.example](.env.example) for the environment form, which is what the
container uses.

The Canvas API token is never read from inside the repository, and never from
`config.toml` either. It comes from `CANVAS_TOKEN`, a `CANVAS_TOKEN_FILE` path
(how the container receives it, as a mounted secret), or
`~/.config/canvas-viewer-mcp/token`. Keeping it in its own file is what makes
`config.toml` safe to paste into a bug report.

`PUBLIC_BASE_URL` — the address Claude reaches the server on — is read from
`RAILWAY_PUBLIC_DOMAIN`, `RENDER_EXTERNAL_URL` or `FLY_APP_NAME` when the
platform sets one, and only needs setting for a custom domain or a reverse
proxy. It is never inferred from the incoming request, because behind a proxy
that request arrives as plain http and the OAuth metadata would advertise
`http://` endpoints that Claude rejects.

The connector login comes from `AUTH_PASSWORD`, or from an argon2
`AUTH_PASSWORD_HASH` if you'd rather no plaintext were stored (`canvas-probe
hash-password` generates one). Set neither and the server issues itself a
pairing code on first run and prints it once to its logs. Setting a password
deletes a code already issued, which is how one that reached a log aggregator
gets revoked.

## Revoking access

Revoke the token at **Canvas → Account → Settings → Approved Integrations**.
That cuts access immediately and independently of everything here — the
connector password only controls who may authorize, while the Canvas token is
what actually reads your account.

## License

MIT
