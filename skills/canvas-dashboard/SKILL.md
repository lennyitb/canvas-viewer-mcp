---
name: canvas-dashboard
description: Build a visual Canvas dashboard from the canvas-viewer connector — what's due this week grouped by day, recent announcements, and a written summary of the week — as a single self-contained HTML artifact. Use when the user asks for their Canvas or schoolwork dashboard, a dashboard or visual view of what's due, or to refresh or update it; a plain "what's due" question stays with canvas-week-report.
---

# Canvas dashboard

This skill is a wrapper. The rules for gathering from the canvas-viewer
connector and triaging what is really outstanding live in
`canvas-week-report`; follow that skill for steps 1 and 2 and do not re-derive
them here.

What this skill adds is the output. Where `canvas-week-report` writes a short
chat message, this one renders the same triaged data as one dashboard page,
and includes the two things the chat report deliberately leaves out: an
announcements section and a written summary of the week.

Requires the canvas-viewer connector and a client that can produce HTML
artifacts. Without artifacts, fall back to `canvas-week-report` and say why in
one line.

## 1. Gather and triage

Exactly as `canvas-week-report` specifies: the same budgeted calls, the same
UTC-to-local conversion, the same rules for stale dates, discussions, on-paper
items and what to drop.

Two changes to that skill's rules, both additive:

1. **Announcements are reported here.** The base skill uses them only to
   correct dates and then discards them. Keep them: every announcement from
   the last 7 days gets an entry with its course, title, local posted date, a
   one-sentence gist, and which due item it affects, if any. Still attach the
   consequence to the affected item as a note, as the base skill says.
2. **Write a summary.** Two to four sentences in your own voice on the shape
   of the week: where the load is concentrated, the one or two items that will
   bite if missed (locks, unusual times, reply gaps), and anything that needs
   a human look. No encouragement, no advice beyond the obvious next step.

Tell the user in one short line what you are doing before the first call, then
stay quiet until the dashboard is up.

## 2. Build the dashboard

One self-contained HTML page, titled `Canvas Dashboard`. All CSS and JS
inline; no external scripts; no browser storage. It has to read on a phone:
16px side gutter, no horizontal scroll.

Layout, top to bottom:

1. **Header** — "Canvas Dashboard", the window label (e.g. "Week of Sep 21"),
   and a small muted line giving the local generation time.
2. **Summary** — the summary from step 1, as prose. If nothing is left in the
   window, put that sentence here with the next due item and skip the day list
   entirely.
3. **Stat row**, three tiles — items left this week, items due today,
   announcements in the last 7 days.
4. **Due list**, grouped by day in order, one row per item: the time (when it
   isn't the unremarkable 11:59 PM, and always for today and tomorrow), then
   `Course: title`, then qualifiers. Render qualifiers as small chips —
   `locks` in an attention colour, `paper`, `est.` for an estimated date,
   `late, still open`, and a weight chip for an exam or an unusually heavy
   item. Reply fractions inline (`replies 1/2`). Notes as a muted second line.
   Same-course, same-deadline items may share a row.
5. **Check**, only when non-empty — at most three lines, one per row.
6. **Look-ahead**, only when the base skill says to include it — compact, one
   row per day, items separated by semicolons.
7. **Announcements** — one card each, newest first: course chip, title, posted
   date, gist, and when it affects a due item, a small "→ affects: …" line. If
   there are none, a single muted line saying so.

Define colour tokens on `:root`, redefine them for dark mode under
`@media (prefers-color-scheme: dark)` guarded by
`:root:not([data-theme="light"])` and again under `:root[data-theme="dark"]`,
and give `body` an explicit background.

Keep it quiet. The due list is the content; colour and chips are for the
things that change what the student does today, not decoration. If your client
offers a design or data-visualization skill for artifacts, load it before
writing the page.

## 3. Reply

One short message: the summary verbatim, then the dashboard. If counts might
be low, or there are Check lines, add one sentence pointing at them. No
account of what you fetched, no offer to help further.

On a follow-up in the same conversation ("and next week?"), re-run the gather
for the new window and update the existing dashboard rather than starting a
second one.
