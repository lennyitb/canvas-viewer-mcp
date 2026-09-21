# Canvas Viewer for Claude

Lets Claude or other AI agents read your Canvas coursework, so you can ask "what do I still have to do this week?" and get an answer you can trust.

## If you're unsure how to use things here on GitHub

Feel free to ask AI! Paste this in:

> Please help me figure out how to get started with this tool: https://github.com/lennyitb/canvas-viewer-mcp

## What it does

Canvas only shows you assignments that have a due date set. When an instructor
leaves the date blank, or copies a course from last year without updating the
dates, that work drops off your dashboard and to-do list. Nothing warns you.

Canvas Viewer gives Claude the full list of your coursework, dated or not,
along with the assignment instructions, announcements, discussions, course
files and modules. If the instructor wrote "due Friday" in the assignment text
or in an announcement, Claude can find it there.

It also checks discussion boards properly. Canvas marks a discussion as
submitted as soon as you make your first post, even when you still owe replies
to classmates. Claude can count what you have actually posted and compare it
with what the assignment asks for.

Things you can ask once it's set up:

- "What's due this week?"
- "Am I caught up in all my classes?"
- "Do I still owe any discussion replies?"
- "What did my instructors announce this week?"

## Is it safe?

- **It can only read.** It cannot submit work, post to a discussion, send a
  message, or change anything in your Canvas account. The code has no ability
  to do those things.
- **Your copy is yours alone.** You run your own private copy, with its own
  password. There is no shared service in the middle, and nobody else can see
  your coursework through it.
- **You can shut it off at any time** from inside Canvas. See
  [Turning it off](#turning-it-off).

## Install

There are three steps:

1. Run the server. This is the small program that talks to Canvas for you.
2. Connect Claude to it.
3. Add the skills, which teach Claude how to make sense of what it reads.

Step 1 has three options, so start by picking one.

### Which install do I need?

| | **A. Self-hosted** | **B. Railway** | **C. This computer only** |
| --- | --- | --- | --- |
| **Pick this if** | You have a computer that stays on all the time: a home server, a Raspberry Pi, a rented VPS | You don't have one, and want to use Claude in a browser or on your phone | You only use Claude Code, on one computer |
| **Works with claude.ai in a browser** | Yes | Yes | No |
| **Works with the Claude phone apps** | Yes | Yes | No |
| **Cost** | Whatever your machine already costs | About $5 a month, paid to Railway | Free |
| **Needs a terminal** | Yes, though Claude can do the typing | No | Yes |
| **Who runs it** | You | Railway, a hosting company | You |

If you have never rented a server and don't know what a terminal is, **B** is
the one that will work for you. If you have a machine that is always on, **A**
costs nothing extra and keeps everything in your hands.

Whichever you choose, have these ready:

- **Your Canvas address.** Open Canvas and copy what's in the address bar, for
  example `https://yourschool.instructure.com`.
- **A Canvas access token.** In Canvas, go to **Account → Settings → Approved
  Integrations → + New Access Token**. Set the expiry date; it can be up to 90 days, this is a fine choice. Copy the
  token right away, because Canvas only shows it once. Treat it like a
  password.
- **A password you make up** (options A and B), at least 12 characters. Claude
  will ask for it once, when you connect.

### 1. Run the server

#### Option A: Self-hosted

You need a machine that stays on and has [Docker](https://docs.docker.com/get-docker/)
installed.

**Let Claude do it.** If you have Claude Code (or a similar assistant) running
on that machine, tell it:

> claude please deploy this server on this machine
> https://github.com/lennyitb/canvas-viewer-mcp

It follows [DEPLOY.md](DEPLOY.md), which covers the common situations,
including a home machine with no public address. At one point it will stop and
ask you to run a single command yourself. That command is where you type your
Canvas token, so the token goes straight into a private file and the assistant
never sees it.

**Or run the setup script yourself:**

```bash
git clone https://github.com/lennyitb/canvas-viewer-mcp
cd canvas-viewer-mcp && ./scripts/setup.sh
```

It asks four or five questions, starts the server, and prints the address to
use in step 2. [DEPLOY.md](DEPLOY.md) also has a fully manual version.

#### Option B: Railway

[Railway](https://railway.com) is a paid hosting service. It runs the server
for you and gives it a web address. It costs about $5 a month, and you will
need to create a Railway account.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/canvas-viewer-mcp-server?referralCode=fkprGJ&utm_medium=integration&utm_source=template&utm_campaign=generic)

1. Click the button and sign in to Railway.
2. Fill in the three fields: your Canvas address, your Canvas token, and the
   password you made up.
3. Click **Deploy** and wait a few minutes for it to finish.
4. Open the new service and copy its public address. It looks something like
   `https://canvas-viewer-mcp-production-1234.up.railway.app`. You need it for
   step 2.

If the service has no address, open **Settings → Networking → Generate
Domain**.

If the address only ever shows a Railway error ("502", "Application failed to
respond"), the domain is pointing at the wrong port. Open the service's
**Deploy Logs** and find the line `Uvicorn running on http://0.0.0.0:` followed
by a number. Then in **Settings → Networking**, edit the domain and set its
port to that number.

[docs/railway-template.md](docs/railway-template.md) lists exactly what the
button sets up.

#### Option C: This computer only

This runs the server on your own computer, only while you are using it. There
is nothing to host, no password and no bill. It works with Claude Code and
other apps that can start a local program. It does not work with claude.ai in
a browser or with the phone apps.

```bash
pipx install canvas-viewer-mcp   # or: uvx canvas-viewer-mcp
mkdir -p ~/.config/canvas-viewer-mcp
printf 'base_url = "https://yourschool.instructure.com"\n' > ~/.config/canvas-viewer-mcp/config.toml
install -m 600 /dev/null ~/.config/canvas-viewer-mcp/token
read -rs CANVAS_TOKEN && printf '%s' "$CANVAS_TOKEN" > ~/.config/canvas-viewer-mcp/token
canvas-probe whoami   # should print your name
```

The `read -rs` line waits for you to paste your token and keeps it out of your
shell history. Then add the server to Claude Code:

```bash
claude mcp add canvas-viewer -- canvas-viewer-mcp
```

Skip step 2 and go to [step 3](#3-install-the-skills).

### 2. Connect Claude to it

Do this on **claude.ai in a web browser**. The phone apps can't add a new
connector, but they will pick this one up by themselves once it's added.

1. Go to **Settings → Connectors → Add custom connector**.
2. Paste the address from step 1, exactly as it was given to you.
3. A login page opens. Enter the password you made up. (If you self-hosted and
   chose a pairing code instead of a password, enter the code the server
   printed in its log.)

### 3. Install the skills

Skills are instructions that teach Claude how to turn the raw Canvas data into
a useful answer. They are installed separately from the connector.

| Skill | What you get |
| --- | --- |
| [`canvas-week-report`](skills/canvas-week-report/SKILL.md) | A short, prioritized list of what you still have to do. It shows deadlines in your local time, works out real dates for courses that were copied from an earlier term, and checks for discussion replies you still owe. |
| [`canvas-dashboard`](skills/canvas-dashboard/SKILL.md) | The same information as a dashboard page, with recent announcements and a summary of your week. |

**On claude.ai:**

1. Open the [latest release](https://github.com/lennyitb/canvas-viewer-mcp/releases/latest)
   and download `canvas-week-report.zip` and `canvas-dashboard.zip`.
2. Go to **Settings → Capabilities → Skills → Upload skill**.
3. Upload each file, one at a time.

Use the zip files from the release page. Zipping the folders from this page
yourself won't work.

**In Claude Code:**

```bash
git clone https://github.com/lennyitb/canvas-viewer-mcp
cp -r canvas-viewer-mcp/skills/canvas-* ~/.claude/skills/
```

Then ask Claude what's due. You don't need to mention the skills by name.

## Turning it off

In Canvas, go to **Account → Settings → Approved Integrations** and delete the
token you created. Access stops immediately, whatever else is still running.

If you used Railway, also delete the project there so you stop being billed.

---

## Technical reference

Everything below is for people who want to know how it works. You don't need
any of it to use Canvas Viewer.

Canvas Viewer is a read-only [MCP](https://modelcontextprotocol.io) server with
twelve tools. The hosted form runs in a container behind a single-user OAuth
2.1 login. [PLAN.md](PLAN.md) describes how it was built.

### Design

The Canvas dashboard and the `/api/v1/planner/items` endpoint behind it both
filter on `due_at`. This server lists coursework with no date filter and
returns each item with the fields needed to judge it: `due_at`, `unlock_at`,
`lock_at`, submission state, module position, publication state, `created_at`
and `updated_at`.

The server does no classification. It has no "overdue" buckets and no guesses
about stale dates. It returns what Canvas holds, plus assignment bodies, files,
discussions and announcements, and the model (guided by the skills) does the
interpreting.

### Tools

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

Course tabs are often disabled, and Canvas reports that as an error instead of
an empty list. Those tools return `{"unavailable": true, "reason": ...}` so a
sweep across courses is not lost to one locked course.

### Graded discussions

Canvas records a discussion submission when the first post goes up, and has no
field for the replies to classmates that most rubrics also require. A
discussion can read `submitted` or `graded` with replies still owed. `due_at`
usually holds only the post deadline, and the reply deadline is written in the
description. On one real course this applies to 20 of 23 assignments.

How the tools surface this:

- `list_discussion_participation` returns a row per graded discussion with its
  dates, submission state, the sentences of its description that mention
  replies, and `my_participation`: top-level posts, replies to other people,
  and replies to yourself, counted separately. No post bodies.
- `list_assignments` puts a `completion_caveat` and a `discussion_topic_id` on
  every `discussion_topic` row. `get_assignment` repeats the caveat in full.
- `get_discussion` reads the whole thread, nested replies included, and returns
  `my_participation` plus the topic text. Pass `entries="mine"` or
  `entries="all"` to get the posts themselves, and `include_messages=False` to
  get them without bodies.

Canvas's `/entries` endpoint serves only top-level entries, and peer replies
are always nested. On one topic it returned 22 of 77 entries. The server reads
the full thread view, and reports `full_thread: false` when Canvas could only
give it the top level.

### Response size

Everything these tools return lands in a model's context window, so lists are
lean and detail is opt-in:

- List rows omit null fields, except `due_at`, where a null is meaningful.
- `list_assignments` omits bodies and `html_url`; `get_assignment` has both.
- `get_discussion` returns counts and the topic text by default, not the
  thread. On one real topic that is under 1k characters instead of 36k.
- `list_announcements` takes `include_messages=False` and a `course_id`.
- `list_discussion_participation` replaces a `list_assignments` sweep followed
  by `get_assignment` and `get_discussion` per discussion. On a course with 20
  graded discussions that is roughly 14k characters in place of 765k.

### Configuration

Settings come from three sources, highest precedence first: environment
variables, a TOML file at `~/.config/canvas-viewer-mcp/config.toml`, then
defaults. The environment wins so that a leftover file in a home directory
can't redirect a deployed container.

```toml
# ~/.config/canvas-viewer-mcp/config.toml
base_url = "https://yourschool.instructure.com"
# token_file = "/some/other/path"   # optional; defaults to ./token beside this file
```

See [.env.example](.env.example) for the environment form, which is what the
container uses.

**Canvas token.** Read from `CANVAS_TOKEN`, a `CANVAS_TOKEN_FILE` path (how the
container receives it, as a mounted secret), or
`~/.config/canvas-viewer-mcp/token`. It is never read from `config.toml` or
from inside the repository, so `config.toml` is safe to paste into a bug
report.

**Public address.** `PUBLIC_BASE_URL` is the address Claude reaches the server
on. It is read from `RAILWAY_PUBLIC_DOMAIN`, `RENDER_EXTERNAL_URL` or
`FLY_APP_NAME` when the platform sets one, and only needs setting by hand for a
custom domain or a reverse proxy. It is never inferred from the incoming
request: behind a proxy that request arrives as plain http, and the OAuth
metadata would advertise `http://` endpoints that Claude rejects.

**Connector login.** Set `AUTH_PASSWORD`, or an argon2 `AUTH_PASSWORD_HASH` if
you don't want plaintext stored (`canvas-probe hash-password` generates one).
With neither set, the server issues itself a pairing code on first run and
prints it once to its logs. Setting a password later deletes any pairing code
already issued.

The connector password only controls who may authorize a connection. The
Canvas token is what reads your account, which is why deleting the token in
Canvas is the way to cut access.

## License

MIT
