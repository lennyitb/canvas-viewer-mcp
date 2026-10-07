---
name: "canvas-dashboard"
description: "A Canvas schoolwork dashboard built from the canvas-viewer connector. Gathers token-efficiently, works out what is really outstanding (stale copied due dates, discussion reply gaps, on-paper work, locks), and publishes one persistent Canvas Dashboard artifact: the due list by day, an on-the-horizon section for big assignments (final projects, papers, exams) and instructor cues to start them early, a last-week-in-review section (grades dated by when they last changed, provisional and hidden grades, work still waiting or missing, instructor feedback), announcements, and a summary led by what changed since the last run. Use whenever the user asks for their Canvas or schoolwork dashboard or to refresh or rebuild it, and also when they ask what's due, what's left this week, what's due tonight or tomorrow, whether they're caught up, or what to work on next for school, even if they don't say Canvas or dashboard. Not for help doing a specific assignment or for picking discussion posts to reply to."
---

# Canvas dashboard

The reader is the student who owns the Canvas account. They know their
courses and instructors, and they want a page they can act on, not a tour of
how Canvas misbehaves. Two things matter: the dashboard is correct about what
is really outstanding, and getting there doesn't burn tokens on data that
never reaches the page. Every run updates one persisted dashboard artifact
rather than making a new one.

Requires the canvas-viewer connector. Publishing uses the `Artifact` tool;
where there isn't one (Claude Code, for instance), write the page to
`canvas-dashboard.html` in the working directory, give its path, and read the
previous snapshot from that file instead.

## 1. Gather (budgeted)

Load every canvas tool in ONE `tool_search` (query "canvas", limit 20), not
one search per tool. The connector has more than 12 tools, and a lower limit
can leave out `list_feedback` and `list_assignments`.

Canvas stamps are UTC. Convert every date and time to the student's local time
zone (from the clock tool, or from the user's location if that's all you
have) before placing it on a day or in a week. "Local" below means this.

**First round.** None of these depends on another; issue them together.

1. **Clock**: the current date and time from the device clock tool if one
   exists; otherwise the current date you were given.
2. **`list_assignments` with no `course_id`.** Rows carry `course_name`,
   submission state, locks, points, and `discussion_topic_id` for every
   course, so `list_courses` and per-course calls add nothing.
3. **`list_announcements` with `days: 7`.** Leave `include_messages` on: the
   full text is where date changes and horizon cues live.
4. **`list_feedback`** with `days` set to days elapsed this week + 8, which
   covers everything since the start of last week. `days` counts back from
   now, not from midnight, so + 7 would miss last Monday morning.
5. **`Artifact` with `action: "list"`**, to find the artifact titled
   `Canvas Dashboard`. Keep its `url` for publishing.
6. **`get_page`** on the home page of each page-organized course (see below).

**Second round**, as the first round's results call for them:

- **The previous dashboard's snapshot.** `Artifact` with `action: "read"` on
  the dashboard's url, then pull out only the snapshot block with a shell
  step, using the entry-file path the read returns:

  ```bash
  python3 - <entry-file> <<'EOF'
  import re, sys
  html = open(sys.argv[1]).read()
  m = re.search(r'<script type="application/json" id="dashboard-snapshot">(.*?)</script>', html, re.S)
  print(m.group(1) if m else "NO SNAPSHOT")
  EOF
  ```

  No artifact, no block, or a block that doesn't parse: carry on without a
  snapshot. Don't read the rest of the old page except as section 3 allows.
- **`list_discussion_participation`**, only if a discussion is in play: main
  post submitted but not yet graded, unsubmitted and due in the window, or
  due last week (last week's reply gaps belong in the review). Pass
  `course_id` when every in-play discussion is in one course.
- **The weekly page** of each page-organized course (see below).

**Reading the discussion sweep.** Each row gives `reply_requirement`
(verbatim sentences from the description) and `my_participation`; that is
everything needed for both the post deadline and the reply deadline. Only
rows due in the window or last week, or ungraded with a post, matter; skip
the rest. In `reply_requirement`, the sentences that matter name a count and
a deadline ("two classmates by Sunday at 11:59 p.m."). Sentences like "As
part of your response, consider..." are keyword noise. If
`counts_are_complete` is false, the counts may be low (see the footer).

**What feedback rows carry** (connector v0.8.1 and later):

- `last_change_at`: the latest of grading, release, and anyone else's
  comment (written or edited), in UTC. Rows come back newest first by it.
- `workflow_state`, only when it isn't `graded`. `pending_review` means the
  score is provisional: part of it, such as a quiz's written answers, is
  still to be graded.
- `graded_at` with no `posted_at`: a hidden grade, entered but not released.
  Canvas withholds its score, rubric marks and pre-release comments.
- No rubric when nothing on it is marked.

`list_assignments` submissions carry `posted_at` too, so a submission reading
`graded` with no `posted_at` is a hidden grade there as well. If feedback
rows come back without `last_change_at` (an older connector), take the
latest of `graded_at`, `posted_at` and the comments' `created_at`.

**Follow-up calls are exceptions**, not routine:

| Situation | Call |
|---|---|
| `reply_requirement` is cut off (`[...]`) before it states a reply count or deadline | `get_assignment` |
| A stale-dated item whose estimated date lands in the window | `get_assignment` (the description sometimes names the real date) |
| An announcement refers to a specific item ambiguously | `get_assignment` |
| A feedback comment is cut off, or refers to an attachment or rubric you need in order to summarize it | `get_assignment` |
| A big assignment cued this week (see "On the horizon") whose description you haven't seen, one per course | `get_assignment`, so its real deadline and deliverables can be stated |

Don't fetch a big assignment nobody has mentioned. `get_discussion` isn't
needed; if you ever do call it, leave `entries` at its default, because
`"all"` returns the whole thread.

Don't call `list_courses`, `list_discussions`, `list_modules`, `list_pages`,
`list_files`, or `read_course_file`. Exception: a course whose dates are all
stale and whose next item you can't otherwise place may justify one
`list_modules`.

**Horizon cues.** Read the text you already have for them: announcement
messages, and the descriptions or `reply_requirement` sentences of this
week's items. Announcements already come back with full text; don't pass
`include_messages: false`.

**Page-organized courses.** Some courses put the weekly introduction on a
wiki page linked from the course home page, not in an announcement, and
that's where instructors tend to mention big upcoming work. These courses
usually hide the Pages tab, so `list_pages` fails, but `get_page` still
returns any published page when given its real slug. For each course listed
below, and only those:

1. `get_page` with the course's home-page slug from the list. Do not pass
   `front_page`: `get_page` requests `/pages/<slug>`, so `front_page` is
   treated as a literal slug and comes back `unavailable`. From the home
   page body, pick the link to the current week's page (use the week table's
   date ranges when there is one, otherwise match the week number to the
   current discussion or assignment titles) and take the slug from the
   link's `/pages/<slug>` path.
2. `get_page` on that slug. Read it for due-list corrections (like an
   announcement) and for horizon cues.

Issue step 1 alongside the other first-round calls. The connector reports a
wrong slug (404) with the same `unavailable` reason as a hidden page, so if
either call comes back `unavailable`, the slug has most likely changed: skip
the course's page check for this run and add the footer note "Weekly page
for <course> couldn't be read (home-page slug may have changed)". Don't fall
back to `list_pages`, and don't guess slugs.

Page-organized courses, one per line as `course_id, short name, home-page
slug`, with a note on how the weekly pages are named if they follow a
pattern:

- *(none yet)*

For example: `- 12345, History, home-page (weekly pages are
week-N-overview-page)`. To add a course, take the link to its home page: the
course id is the number after `/courses/`, and the slug is the last path
segment.

## 2. Triage

**Window.** The week is Monday through Sunday, and the due list covers what
remains of it. On Friday, Saturday or Sunday, add next week as a look-ahead.
If the user names a different window ("tonight", "next two weeks"), use
theirs.

**Drop silently** from the due list:
- Anything submitted or graded that isn't a discussion needing replies.
- Finished courses and non-teaching shells: no real (non-stale) due date
  within about three weeks either side of today and nothing upcoming.
- `submission_types` of `none`, or zero-point items, unless an announcement
  makes them matter.

**Stale dates.** A `due_at` earlier than `created_at` (or many months in the
past in a live course) means the course was copied and the date never
updated. `missing: true` on these is noise. Estimate the real date as the
stale date + 52 weeks, which keeps the weekday, and let a description or
announcement override it. Show the item only if the estimate falls in the
window, marked as an estimate.

**Discussions.** `submitted` means the first post only. Compare
`replies_to_others` with the count in `reply_requirement` and show the gap
as a fraction (`replies 1/2`). `due_at` is usually the post deadline; the
reply deadline lives in the description and is its own due-list entry when
it falls on a different day.

**On-paper items** (`on_paper`). Canvas can't see these, so "unsubmitted"
means nothing. List them when due in the window; never call them late or
missing.

**Late but open.** A real (non-stale) date has passed, but the item is
unsubmitted, not locked, and takes an online submission: still actionable, so
it stays in the due list.

**Announcements.** Use them to correct dates and catch extra requirements,
and attach what matters to the affected item's qualifiers. They are also
reported in their own section: every announcement from the last 7 days, one
entry each, with course short name, title, posted date, a one-sentence gist,
and which due item it affects (if any).

**Qualifiers.** Show only the ones that change what the student does:
- **locks**: `lock_at` equals the deadline, so it can't be done late.
- A time, when it isn't 11:59 PM (5:00 PM, 1:55 PM and 11:30 PM are the ones
  that get missed). For today and tomorrow always give the time.
- Reply fractions for discussions.
- `paper` for on-paper work.
- Unusual weight: an exam, or points well above the course's norm.
- An announcement detail that changes the work ("use the updated starter file").
- `est.` for an estimated stale date.

Combine same-course, same-deadline items. Shorten course names to what the
student would say out loud: "Lin Alg" for "FA26 Linear Algebra (MATH-210-01)".

**Check.** At most three lines, for things that need a human look: a final
grade of 0 or a sharp drop whose last change is in the last 10 days, an
instructor warning in an announcement that may apply to the student, a
stale-dated item you couldn't place. One line each, no advice beyond the
obvious next step. Never a provisional or hidden score, never a low grade the
review already shows, and nothing that was already a Check line on the last
dashboard (the snapshot's `check`).

**On the horizon.** Big assignments beyond the due window that the student
should already be thinking about. Two ways an item gets here:

- **Cued**: an instructor says, anywhere in text you already fetched, that
  it's time to start on, think about, pick a topic for, or plan a larger
  piece of work ("probably a good idea to start thinking about the final
  project"). This is the case that matters most: the cue matters even
  when the assignment has no Canvas row or date yet.
- **Big and approaching**: a row in `list_assignments` that is a final,
  project, paper, research piece, presentation, or exam, or whose points
  are well above the course's norm, due within the next five weeks of the
  real (non-stale) calendar. Estimate stale dates as above.

One entry each: course short name, item name (the instructor's wording if
there's no row), real due date or `no date yet`, weeks out, weight if
unusual, and, for cued items, one sentence in your words on what the
instructor said and where (e.g. "Week 5 announcement"). Mark an item `new`
if the cue appeared in the last 7 days. If a cue names a first step with a
deadline (topic proposal, outline), that step is a normal item in the due
list when it falls in the window.

Leave out: items already in the due list or look-ahead, ordinary weekly
work however many points, and anything submitted. A cue that is only
generic encouragement ("keep up with the reading") is not a cue.

**Last week in review.** "Last week" is the previous Monday–Sunday.

Date every `list_feedback` row by its last change: `last_change_at`,
converted to local time before deciding which day or week it falls in (in
the Americas, an evening release carries the next day's date in UTC). Never
date a row by `graded_at` or `posted_at`; a regrade, the release of a held
grade, or a late comment moves only the last change. A quiz released Sunday
at 1.4/10 pending review and graded to 10/10 on Tuesday is dated Tuesday.

A row stays in the review while its last change is on or after the start of
last week, and that is the only way it leaves. A change of state (provisional
to final, hidden to released) moves a row between lists; it never drops it.

Build three lists:

- **Grades**: every `list_feedback` row in range that isn't provisional or
  hidden (those go under Waiting). Course, title, score as `18/20` plus
  percentage, and the last-change date. Keep the connector's order, newest
  first. Every grade is shown, including the ones that are fine. When
  `graded_at` is more than an hour after `posted_at`, the grade changed after
  release: mark it `regraded`, unless a change chip (below) says more.
- **Waiting**: three kinds.
  - *Not graded yet*: due last week, submitted, no grade. On-paper items due
    last week with no grade go here too (marked paper), never under Missing.
  - *Provisional*: `workflow_state: pending_review`, on a `list_feedback`
    row or a `list_assignments` submission, whatever its due date. Shown as
    `1.4/10 so far · pending review`. It isn't a grade yet: no percentage,
    no low-grade colour, never a Check line.
  - *Hidden*: `graded_at` with no `posted_at` (a `list_feedback` row, or a
    `list_assignments` submission reading `graded` with no `posted_at`),
    whatever its due date. Shown as `graded Sat 9/26 · not released`, using
    the local date of `graded_at`.
  A discussion with the post graded but replies still owed goes under Missing,
  not here.
- **Missing**: due last week, not submitted, online submission. Includes
  discussion reply gaps (`replies 1/2`). Mark each `still open` or `locked`.
  A still-open item also stays in the due list, per the
  late-but-open rule. Stale-dated items are never missing: `missing: true`
  on a copied-course date is noise (see Stale dates). Only count one
  whose *estimated* date fell last week.

- **Feedback**: for each row with comments from someone other than the
  student, show what the instructor actually wrote. Most comments are a few
  sentences, and those go in **verbatim and in full**: trim leading/trailing
  whitespace and collapse runs of blank lines, but keep the instructor's
  wording, typos, and capitals as written. Judge each comment on its own:
  - **Short** (about 75 words or fewer): the full text.
  - **Long** (more than about 75 words): one or two sentences in your words
    on what it says and anything it asks the student to do, marked as a
    summary, with the full text available behind a "Full comment" expander
    (see the layout).
  - **Resubmissions**: when a row's comments span more than one `attempt`,
    the latest attempt's comments are the ones that stand; show those per the
    rules above, and fold earlier attempts into one muted clause ("attempt 1:
    asked for fixes on lines 69 and 229, resubmitted").
  Add any rubric criterion that lost points as `Criterion 10/15`. Combine with
  the item's Grades or Waiting row rather than repeating the item. A commented
  row with no grade still gets a Grades row, with `no grade` in place of the
  score.

**Changes since the last dashboard.** With a snapshot, compare each Grades
and Waiting row with the snapshot entry for the same `assignment_id`, and
give it at most one change chip, the first of these that applies:

1. `released`: the snapshot had it hidden; now it has a score.
2. `updated · was 1.4/10 provisional` (or `was 7/10`): the snapshot had a
   score, and the score or its provisional state is different now. Say what
   it was.
3. `new`: the snapshot had no score for it (no entry, or one still waiting
   to be graded), and its last change is after the snapshot's
   `generated_at`.
4. `new comment`: more comments from others than the snapshot counted.

A change chip replaces `regraded`. Without a snapshot there are no change
chips, and the footer says so.

**Nothing drops silently.** Before writing the page, go through the
snapshot's Grades and Waiting entries. Each one that is still provisional or
hidden, or whose last change (today's `last_change_at` if the row came back,
else the snapshot's) is on or after the start of last week, must appear on
the new page. Waiting entries with no last change follow the due-date rule as
before. If one is missing, the triage is wrong: find out why and fix it
before publishing.

**Overall summary.** 2–4 sentences, led by what changed since the last
dashboard: grades that came in, came back from review, were released or
moved; new instructor feedback, with the gist of what the instructor said;
announcements posted since then. A short word of kudos on a strong new grade
is welcome; keep it to a clause. Then the shape of the week: where the load
is concentrated, the one or two items that will bite if missed (locks,
unusual times, reply gaps), anything under Check, any `new` horizon item, and
anything from last week that needs action (a missing item still open,
feedback that asks for a fix or a resubmission). Without a snapshot, or when
nothing changed, lead with the shape of the week instead. No study tips or
time-management advice beyond the obvious next step. State the true status of
things; don't explain Canvas quirks or how you verified something.

## 3. Build the dashboard

Read the `frontend-design` skill before writing the page. Write a single
self-contained HTML file to `/mnt/user-data/outputs/canvas-dashboard.html`.
`<title>` is `Canvas Dashboard`. Inline all CSS and JS; no external scripts;
no browser storage.

**Refresh or rebuild.** There are two ways to produce the page, and either
way you gather and triage everything first.

- A **rebuild** writes every section fresh from this run's data. You may read
  the old page for its `<style>` block and class names so the look stays
  stable between runs; nothing else comes from it except the snapshot.
- A **refresh** edits the previous page in place. Replace each section whose
  content changed with a freshly written version of the whole section, built
  from this run's data. Always rewrite the header time, the summary, the stat
  row and the snapshot. Leave unchanged sections as they are. Never edit
  individual rows inside a section: a row whose state changed under an old
  date is exactly what row-level patching misses.

Refresh when the page was last rebuilt less than 24 hours ago (the snapshot's
`built_at`). Rebuild instead when:
- the run is a scheduled task: always, however recent the last rebuild;
- the user asks for a full rebuild;
- the last rebuild is 24 hours old or more, or the snapshot is missing or has
  no `built_at`.

The "Nothing drops silently" check applies to both.

Layout, top to bottom, phone-width safe:

1. **Header**: "Canvas Dashboard", the window label, and a small muted line
   `Generated <local time>`.
2. **Summary card**: the overall summary, as prose. If nothing is left in the
   window, say so in one sentence here, name the next due item, and skip the
   day list.
3. **Stat row** (three tiles): items left this week, items due today, count
   of announcements in the last 7 days.
4. **Due list**, grouped by day in order, one row per item: time (per the
   qualifier rules) · `Course: title` · qualifiers. Render
   qualifiers as small chips: `locks` bold/attention colour, `paper`, `est.`
   (stale-date estimate), `late, still open`, and an exam/weight chip for
   unusual weight. Reply fractions inline (`replies 1/2`). Announcement notes
   as a muted second line. Same-course, same-deadline items may share a row.
5. **Check** (only if non-empty): the lines as-is, one per row.
6. **Look-ahead** (Friday to Sunday, or when asked): compact, one row
   per day, items separated by semicolons.
7. **On the horizon** (only if non-empty): one card per item, soonest first
   (`no date yet` items after dated ones) — course chip, item name, due
   date or `no date yet`, `N weeks out`, a weight chip if unusual, and a
   `new` chip in the attention colour when cued this week. The cue sentence
   and its source as a muted second line.
8. **Last week in review**: three sub-blocks.
   - *Grades*: one row each, `Course: title` · score · percentage ·
     last-change date (`Tue 10/6`) · at most one chip. A grade under 70% gets
     the attention colour. Change chips (`released`, `updated · was …`,
     `new comment`, `new`) use the attention colour too, like the horizon's
     `new`; `regraded` is a plain muted chip. Nothing else is coloured. Feedback
     sits under its row as a quiet indented block (thin left border, body
     text size, `white-space: pre-line` so the instructor's line breaks
     survive), one paragraph per comment, then the earlier-attempts clause
     and lost rubric criteria as a muted line. A summarized comment shows
     the summary with a small `summary` label, followed by a native
     `<details><summary>Full comment</summary>…</details>` holding the full
     text; no JS. HTML-escape all comment text.
   - *Waiting*: one row each, with a `paper` chip where it applies.
     Provisional rows read `1.4/10 so far · pending review` with a muted
     `provisional` chip and no percentage; hidden rows read
     `graded Sat 9/26 · not released` with a muted `not released` chip.
     Change chips apply here as in Grades, and feedback on a provisional row
     sits under it the same way.
   - *Missing*: one row each, with `still open` or `locked` chips and reply
     fractions.
   Omit an empty sub-block. If all three are empty, one muted line:
   "Nothing graded, waiting, or missing from last week."
9. **Announcements**: one card each — course chip, title, posted date, gist;
   when it affects an item, a small "→ affects: …" line. Newest first.
   If none, one muted line "No announcements in the last 7 days."
10. Footer line: "Counts may be low" if the discussion counts are incomplete,
    any "Weekly page for <course> couldn't be read" note, and "No previous
    snapshot; change labels start next run" when Gather found none.

**Snapshot.** End the `<body>` with the state this page shows, for the next
run to compare against:

```html
<script type="application/json" id="dashboard-snapshot">
{"v": 1,
 "generated_at": "2026-10-07T13:10:00Z",
 "built_at": "2026-10-07T13:10:00Z",
 "canvas_host": "https://school.instructure.com",
 "check": [],
 "rows": {
  "123456": {"title": "Quiz 5", "section": "grades", "state": "graded",
             "score": 10, "points": 10,
             "last_change_at": "2026-10-06T13:41:03Z", "comments": 0}}}
</script>
```

`generated_at` is this run. `built_at` is the last rebuild: a refresh carries
it forward unchanged, and a rebuild sets it to now. `canvas_host` is the host
the links use, and `check` holds this page's Check lines as shown. `rows` has
one entry per row in Grades, Waiting and Missing, keyed by `assignment_id`
as a string. `section` is `grades`, `waiting` or `missing`; `state` is
`graded`, `pending_review`, `hidden`, `submitted` or `missing`; `score` is
null when there isn't one; `comments` counts comments from others; timestamps
stay in UTC as the connector gives them. Write it with `json.dumps` and
replace `</` with `<\/` so no title can close the tag. It is data only;
nothing on the page reads it.

### Links

The dashboard links things: almost every item,
course, and announcement on the page opens its Canvas page. Build every URL
from IDs already in the fetched data; never make a call just to get a link,
and never guess an ID. If an ID is missing, render that item as plain text.

Canvas host: the scheme and host of any `html_url` in the fetched data
(announcement rows carry one). If this run has none, use the snapshot's
`canvas_host`; if there's neither, render titles as plain text.

| What | Link to |
|---|---|
| Course chip or course short name | `/courses/<course_id>` |
| Assignment row with a `discussion_topic_id` (incl. reply-gap lines) | `/courses/<course_id>/discussion_topics/<discussion_topic_id>` |
| Any other assignment row | `/courses/<course_id>/assignments/<id>` |
| `list_feedback` row (Grades) | `/courses/<course_id>/assignments/<assignment_id>` |
| Announcement | the row's `html_url`, as given |
| Weekly wiki page | `/courses/<course_id>/pages/<slug>` |

Where the link goes on the page:

- **Item titles** everywhere they appear: due list, Check, look-ahead,
  horizon, Grades, Waiting, Missing, and the "→ affects" line. When items
  share a row (or a look-ahead line), each title is its own link.
- **Course chips** link to the course.
- **Announcement titles** link to the announcement.
- **Horizon cue sources**: the source label ("Week 5 announcement",
  "Week 5 overview page") links to that announcement or page. A cued item
  with no Canvas row has no title link; its source is its link.
- **Footer page note** links to the course's home page.
- **Header**: the window label stays plain; add a small muted "Open Canvas"
  link to the host root.

Don't link the summary prose, dates, chips other than course chips, or the
stat tiles. Link the title text, not the whole row, so course chips and
titles stay separate tap targets.

Every link: `target="_blank" rel="noopener"`. Style them to stay quiet:
inherit the text colour, a thin underline at low opacity
(`text-decoration-color` from a muted token, `text-underline-offset: 2px`),
full-strength underline on hover, and a visible `:focus-visible` outline.

Colour tokens on `:root`, dark-mode variants under
`@media (prefers-color-scheme: dark)` guarded by
`:root:not([data-theme="light"])` and again under `:root[data-theme="dark"]`,
explicit `body` background, 16px side gutter, no horizontal scroll. Keep it
quiet: the due list is the content; chips and colour only where they change
what the student does.

## 4. Publish (update in place)

The dashboard is one artifact that gets refreshed, not a new one per run.

- Publish with the `url` found in Gather so the link stays stable. If Gather
  found no `Canvas Dashboard`, publish fresh with `favicon: "📅"` and
  `title: "Canvas Dashboard"`.
- Same file path on every republish.
- The snapshot travels with the published page, so change chips always mean
  "since the last dashboard that was actually published". A publish that
  isn't approved leaves the old snapshot in place, which is what you want.

## 5. Reply

One short message. If the user asked something narrower than the dashboard
("what's due tonight?", "am I caught up?", one course), answer that first in
a sentence. Then the overall summary, then the artifact card. If the counts may
be low or there were Check lines, add one sentence pointing at them. No
description of what was fetched, no offer to help further.

Follow-ups in the same conversation ("and next week?") reuse the data and the
snapshot already fetched: re-triage for the new window and republish. The
review section stays anchored to last week unless the user asks otherwise.
