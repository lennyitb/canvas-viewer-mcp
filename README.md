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

It reads your instructors' feedback too: the comments on your submissions and
how each part of the rubric was marked. A comment like "resubmit by Friday"
is work that no due date shows.

It also checks discussion boards properly. Canvas marks a discussion as
submitted as soon as you make your first post, even when you still owe replies
to classmates. Claude can count what you have actually posted and compare it
with what the assignment asks for.

Things you can ask once it's set up:

- "What's due this week?"
- "Am I caught up in all my classes?"
- "Do I still owe any discussion replies?"
- "What did my instructors announce this week?"
- "Did I get any feedback this week?"
- "Please help me develop a reply to Chris in the Week 4 history discussion"
- "Please fetch today's chemistry lab, put a copy in my folder, and summarize the procedure"

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
fifteen tools. The hosted form runs in a container behind a single-user OAuth
2.1 login. [PLAN.md](PLAN.md) describes how it was built.

### Design

The Canvas dashboard and the `/api/v1/planner/items` endpoint behind it both
filter on `due_at`. This server lists coursework with no date filter and
returns each item with the fields needed to judge it: `due_at`, `unlock_at`,
`lock_at`, submission state, module position, publication state, `created_at`
and `updated_at`.

The server does no classification. It has no "overdue" buckets and no guesses
about stale dates. It returns what Canvas holds, plus assignment bodies, files,
discussions, announcements and instructor feedback, and the model (guided by the skills) does the
interpreting.

### Tools

| Tool | Returns |
| --- | --- |
| `list_courses` | Active courses, term, current score |
| `list_assignments` | Every assignment, no date filtering, bodies omitted |
| `get_assignment` | One assignment including its full description and feedback: comments, rubric marks, grade |
| `list_feedback` | Assignments graded, released or commented on by someone else in the last N days, newest first, with the comments and rubric marks |
| `list_announcements` | Recent announcements across all courses, with text (or titles only) |
| `list_discussions` / `get_discussion` | Topics, and one topic with the user's own participation counted; the reply tree on request |
| `list_discussion_participation` | Every graded discussion with the reply requirement and the user's post counts, in one call |
| `get_syllabus` | The course's Syllabus tab |
| `list_modules` | Modules and their items, in instructor-intended order |
| `list_pages` / `get_page` | Wiki pages, and one page's body |
| `list_files` | Course files, or the files the course links to when its Files tab is hidden |
| `read_course_file` | The text of one file: PDF, Word, PowerPoint, Excel or plain text |
| `download_course_file` | A download link for one file of any type; images also shown inline |

### Course structure

Courses differ in where they keep their material. Some put everything in
modules. Some publish a page per week and link the pages from the course home
page. Some keep the syllabus on the Syllabus tab, some upload it as a file and
link it from there, and some link a handout only from the assignment that uses
it. The server has no model of this and does not impose one. Each tool reports
what it found, says when Canvas refused, and names where else to look; choosing
where to look is left to the model. The problems met on real courses, and what
was done about each:

**Hidden tabs.** Instructors disable course tabs, and Canvas reports a disabled
tab as a 403 (files) or 404 (pages) rather than an empty list. On a real
account with eleven active courses, including non-teaching shells, the file
list was refused in five and the page list in seven. A tool that raised on a
refusal would end a whole-account sweep at the first such course, so every tool
returns `{"unavailable": true, "reason": ...}` instead and the sweep continues.

**Hidden Pages tab.** A course that hides the Pages list usually still
publishes the pages themselves, typically one per week, linked from the course
home page. Without a pointer, a caller reads the refusal as "this course has no
pages". The `list_pages` refusal therefore carries a `hint`: call `get_page`
with `page_url` set to `front_page`, follow the `/courses/<id>/pages/<slug>`
links in its body by passing each slug to `get_page`, and check
`list_modules`, which may link the same pages. Canvas serves the home page from
its own endpoint rather than as a slug, so `get_page` accepts `front_page` as
a name.

**Hidden Files tab.** Canvas refuses the file list, but `files/:id` still
serves each file to anyone enrolled. Two of the account's five teaching courses
hid the tab, and in both the syllabus was a file found only through a link: one
from a module item, the other a `.docx` linked from the Syllabus tab.
`list_files` in such a course rebuilds the list from every place the course
links to a file: module items, the Syllabus tab, the front page, assignment
descriptions, pages, discussion topics and the last year of announcements. If
the Pages tab is hidden too, the pages the modules name are read one by one,
up to 40. Each row's `linked_from` names where the file was found. The result
is marked `files_tab_hidden`, carries a note that a file linked from nowhere
cannot appear and that a link can outlive its file, lists any source Canvas
refused in `sources_unavailable`, and any source read only in part in
`partial`. If nothing at all is found, the refusal carries a hint saying where
file ids turn up.

**File ids in HTML.** Canvas bodies link to files in several forms:
`/courses/1/files/2?wrap=1`, `/files/2/download`,
`/api/v1/courses/1/files/2`, `/users/3/files/2/preview`. Converted to
Markdown, the id is still present but inside a URL, which reads as a web link
rather than a file to fetch. Every tool that returns a body also returns
`linked_files`: the file ids found in its links, images and embeds, each with
a name taken from the link's `title` attribute (where the rich content editor
writes the real filename), then `alt`, then the link text. This applies to
`get_page`, `get_syllabus`, `get_assignment`, `get_discussion` and
`list_announcements`, and discussion attachments are included. A module item
of type File carries its file id as `file_id` rather than the generic
`content_id`, and a file attached to a feedback comment carries its `id`. Ids
from any of these work with `read_course_file` and `download_course_file`
whether or not the Files tab is hidden.

**Syllabus.** The Syllabus tab often holds the schedule, the grading breakdown
and the deadline policy that assignment metadata leaves out, or it holds
nothing and the syllabus is a file. `get_syllabus` returns the tab's body with
its `linked_files`, and a `note` when the tab is empty. Its description names
`list_modules` and `list_files` as the next places to look.

**Modules.** Modules are available in nearly every course, hidden tabs or
not, and hold the order the instructor intends the work to be done in. On one
real course whose due dates had not been rolled forward, the module order
matched the order of the stale dates exactly, offset by about a year; it is
the ordering signal that survives stale dates. `list_modules` returns each
module's items in position, with `file_id` for a File item, `page_url` for a
Page item, `content_id` for an assignment or discussion, `html_url`, and the
completion requirement and its state where Canvas sets one.

**Ids across objects.** A graded discussion is two Canvas objects with
different ids, an assignment and a topic. Assignment rows for discussions
carry `discussion_topic_id`, which `get_discussion` takes, and topics carry
`assignment_id`, which `get_assignment` takes. With `file_id`, `page_url` and
`linked_files`, every object the tools return names the id another tool
needs, so whichever objects a course exposes lead to the rest.

**Announcements without a course.** The account-wide announcements endpoint,
which fetches every course's announcements in one request, does not return
`course_id`; it names the course in `context_code` as `course_<id>`. Reading
only `course_id` there left every announcement with a null course, the one
field needed to act on it. The course id is now taken from `course_id` where
present, else from `context_code`, else from the topic's own URL.

**Reading a file.** `read_course_file` extracts text from PDF, Word,
PowerPoint, Excel and text-based files, as Markdown with headings, lists and
tables where the format has them; PowerPoint text is per slide with speaker
notes, Excel per sheet as a table. Canvas sometimes labels an Office upload
`application/octet-stream` or `application/zip`, in which case the real type
is taken from the filename. Limits: 25 MB downloaded, 50 PDF pages, 200 rows
by 30 columns per sheet, 20,000 characters of text, and an Office file is
refused if its zip inflates past 100 MB. Every limit that applies is reported
in a `note` with what was cut. A scanned PDF is reported as having no
extractable text rather than returned empty. `download_url` is returned
alongside the text, so a file that cannot be read can still be downloaded.

**Downloading a file.** Getting a file rather than its text used to mean
copying the `url` off a `list_files` row, which no tool description mentioned
and which failed in any course hiding the tab. `download_course_file` returns
the file's own Canvas link for any file id. Canvas file links are pre-signed
and carry their credentials in the query string, so the link works without a
Canvas session; the response says so, and gives a `curl` line for a shell.
PNG, JPEG, GIF and WebP images up to 3.75 MB (5 MB once base64-encoded) also
come back inline. A locked file comes back `locked` with its `unlock_at`;
handouts are commonly uploaded at the start of term and unlocked a week at a
time. Checked live: the link for a `.pptx` fetched 2,652,719 bytes, the size
Canvas reports for it.

**Hints in the tool descriptions.** Each tool's description says what its
result does not settle and which tool comes next. `list_assignments` says a
discussion row is not done on its submission state and points at
`list_discussion_participation`; `get_syllabus` says a syllabus is often a
linked file that `read_course_file` reads; `list_files` says its rows carry
no download link and `download_course_file` does; `list_modules` says File
items carry a `file_id`. The server also sends the client a short set of
instructions at connection time covering stale dates, graded discussions,
feedback and files, so a model has them before its first call. Clients show
only the first 2,048 characters of those, so they stay under that and leave
the detail to the tool descriptions.

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

### Grades and feedback

A score is not always final, and not always visible. A quiz with written
questions is released with only its auto-graded part scored: one real quiz
read 1.4/10 for 41 hours before the written answers took it to 10/10. A
course that posts grades by hand holds each one after grading, for up to 22
hours on the real account. While it is held, Canvas leaves out the score,
the rubric marks and any comments written before release, and when it is
released the grading and comment times stay where they were. Only
`posted_at` moves.

How the tools surface this:

- `list_feedback` keeps a submission when its latest change falls in the
  window: grading, release, or anyone else's comment, new or edited. That
  change is `last_change_at`, and rows come newest first across courses.
- A row whose score is still provisional carries `workflow_state:
  pending_review`. Graded rows, the usual case, leave the field out.
- A grade entered but held has `graded_at` but no score and no `posted_at`,
  both in `list_feedback` and on the `submission` in `list_assignments`.
- A `list_feedback` row leaves its rubric out when Canvas sent no marks for
  it, rather than listing every criterion unmarked. `get_assignment` keeps
  the rubric, since before grading it is where the criteria are.

### Response size

Everything these tools return lands in a model's context window, so lists are
lean and detail is opt-in:

- List rows omit null fields, except `due_at`, where a null is meaningful.
- `list_assignments` omits bodies and `html_url`; `get_assignment` has both.
- `get_discussion` returns counts and the topic text by default, not the
  thread. On one real topic that is under 1k characters instead of 36k.
- `list_announcements` takes `include_messages=False` and a `course_id`.
- `list_feedback` returns only recently graded, released or commented work,
  and leaves out the user's own comments unless asked. Comments are capped at
  2k characters with a visible truncation marker.
- `list_files` rows omit the download link, the stored filename, the creation
  date and the folder; on a 97-file course that is 17k characters instead of
  39k. `download_course_file` gives the link for the one file wanted.
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

### Overrides

Sometimes Canvas is wrong and stays wrong: a revised syllabus handed out in
class that never reached the Syllabus tab, or library files the instructor has
since replaced. An overrides file fixes that one item at a time. Each entry
either hides an item, so the tools act as if Canvas had refused it, or serves a
local file in its place. The replacement has the same shape Canvas content
would, with nothing marking it as an override.

The file is `overrides.toml` beside `config.toml`, or wherever
`CANVAS_OVERRIDES_FILE` points. With docker compose it is
`overrides/overrides.toml` next to `compose.yaml`. Paths in `with` are relative
to the file's own folder.

```toml
# The Syllabus tab of course 12345, replaced by a local PDF (or .docx, .md, .txt).
[[override]]
course = 12345
kind = "syllabus"
with = "syllabus-rev2.pdf"

# One course file, by id, replaced by a local copy.
[[override]]
course = 67890
kind = "file"
id = 555
with = "lib/uart.c"

# Every file in the course whose name matches, hidden.
[[override]]
course = 67890
kind = "file"
name = "stm32_hal_v1*.h"
action = "hide"

# A wiki page, by slug (or `title = "..."` glob).
[[override]]
course = 67890
kind = "page"
url = "week-3-notes"
with = "week3.md"
```

Hidden files and pages also drop out of `list_modules`. Edits apply without a
restart, and an edit that doesn't parse keeps the previous set. Since the
tools give no sign of an override, check what each rule matched with:

```bash
canvas-probe overrides
```

It prints every rule, the Canvas item it matched (or `MATCHED NOTHING`), and
whether the replacement file can be read.

## License

MIT
