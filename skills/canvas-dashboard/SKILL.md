---
name: canvas-dashboard
description: Run the canvas-week-report gather+triage in a Sonnet (medium effort) subagent, then publish the result as a Canvas Dashboard artifact with the due list, announcements, and the model's overall summary. Use when the user asks for their Canvas/schoolwork dashboard, a dashboard view of what's due, or to refresh/update it; plain "what's due" questions stay with canvas-week-report.
---

# Canvas dashboard

This skill is a wrapper. The rules for gathering from the canvas-viewer MCP and
triaging what is really outstanding live in `canvas-week-report`; do not
re-derive them here. This skill does three things: delegate that work to a
Sonnet subagent at medium effort, add the two things the base report leaves
out (an announcements section and an overall summary written by the subagent),
and render everything as one persisted dashboard artifact.

The parent (you) does no Canvas calls. All Canvas tool use happens inside the
subagent, so the parent context stays small.

Requires the `canvas-viewer` MCP server, plus a client that has both the
`Agent` tool and the `Artifact` tool. Without them, fall back to
`canvas-week-report` and say why.

## 1. Delegate to Sonnet

Call the `Agent` tool once with `subagent_type: "general-purpose"`,
`model: "sonnet"`, and a prompt built from the template below. The Agent tool
has no effort parameter, so the prompt states the effort level explicitly.
If a `.claude/agents/canvas-report.md` definition exists with
`model: sonnet` and `effort: medium` in its frontmatter, use that agent type
instead.

Prompt template (fill the bracketed parts; keep the rest verbatim):

```
Effort: medium. Be careful about time-zone conversion and discussion reply
counts; do not over-explore beyond the budgeted calls.

Invoke the skill `canvas-viewer:canvas-week-report` (it may be listed as
`canvas-week-report`) and follow it exactly for gathering and triage: one
ToolSearch for the canvas tools, then list_assignments (no course_id),
list_announcements (days: 7), and list_discussion_participation only if a
discussion is in play. Follow-up calls only for the exceptions the skill lists.

Window: [remainder of this week, Mon–Sun local / or the window the user named].
Current local time: [time and zone from the device clock, or the zone the user
has stated].

Two additions to the base skill:

1. Announcements ARE reported here. Include every announcement from the last
   7 days, one entry each: course short name, title, posted date (local),
   a one-sentence gist, and `affects` (the due item it changes, or null).
   Still attach the consequence to the affected item's qualifiers as usual.

2. Write an `overall_summary`: 2–4 sentences in your own voice on the shape of
   the week — where the load is concentrated, the one or two items that will
   bite if missed (locks, unusual times, reply gaps), and anything under Check.
   No encouragement, no advice beyond the obvious next step.

Return ONLY this JSON, no prose around it:

{
  "model": "<your model name>",
  "generated_at": "<local ISO datetime>",
  "window": {"label": "<e.g. Week of Sep 21>", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "days": [
    {"label": "Today|Tomorrow|Tue 9/22", "date": "YYYY-MM-DD",
     "items": [{"course": "Circuits", "title": "Quiz 3 Parallel Circuits",
                "time": "11:59 PM", "show_time": true,
                "flags": ["locks"|"paper"|"estimate"|"late_open"|"heavy"],
                "replies": "1/2" | null,
                "note": "use the updated starter file" | null}]}
  ],
  "lookahead": [ same shape as days, only when the base skill says to include it ],
  "check": ["one line each, max 3"],
  "announcements": [{"course": "...", "title": "...", "posted": "Sat 9/19",
                      "gist": "...", "affects": "Microcontrollers: Week 4 Lab" | null}],
  "nothing_left": null | "one sentence plus the next due item",
  "overall_summary": "...",
  "counts_may_be_low": false
}
```

If the subagent returns prose instead of JSON, extract the JSON block; do not
re-run the gather yourself.

## 2. Build the dashboard

Load the `artifact-design` skill before writing the page (and `dataviz` if you
add stat tiles). Write a single self-contained HTML file to the scratchpad,
e.g. `canvas-dashboard.html`. `<title>` is `Canvas Dashboard`. Inline all CSS
and JS; no external scripts; no browser storage.

Layout, top to bottom, phone-width safe:

1. **Header**: "Canvas Dashboard", the window label, and a small muted line
   `Generated <local time> · <model>` so the student can see which model wrote
   it.
2. **Summary card**: `overall_summary` verbatim, as prose. If `nothing_left`
   is set, show that sentence here and skip the day list.
3. **Stat row** (three tiles): items left this week, items due today, count
   of announcements in the last 7 days.
4. **Due list**, grouped by day in order, one row per item:
   time (when `show_time`) · `Course: title` · qualifiers. Render flags as
   small chips: `locks` bold/attention colour, `paper`, `est.` (for
   `estimate`), `late, still open`, and an exam/weight chip for `heavy`.
   Reply fractions inline (`replies 1/2`). Notes as a muted second line.
   Same-course, same-deadline items may share a row.
5. **Check** (only if non-empty): the lines as-is, one per row.
6. **Look-ahead** (only if present): compact, one row per day, items
   separated by semicolons.
7. **Announcements**: one card each — course chip, title, posted date, gist;
   when `affects` is set, a small "→ affects: …" link-style line. Newest first.
   If none, one muted line "No announcements in the last 7 days."
8. Footer line: "Counts may be low" if `counts_may_be_low` is true.

Colour tokens on `:root`, dark-mode variants under
`@media (prefers-color-scheme: dark)` guarded by
`:root:not([data-theme="light"])` and again under `:root[data-theme="dark"]`,
explicit `body` background, 16px side gutter, no horizontal scroll. Keep it
quiet: the due list is the content; chips and colour only where they change
what the student does.

## 3. Publish (update in place)

The dashboard is one artifact that gets refreshed, not a new one per run.

- First, `Artifact` with `action: "list"` and look for a title
  `Canvas Dashboard`. If found, `read` it, then publish with that `url` so the
  link stays stable. If not found, publish fresh with `icon: "calendar"` and
  `description: "What's left this week on Canvas, with announcements and a
  model-written summary."`.
- Same scratchpad file path on every republish within a session.

## 4. Reply

One short message: the `overall_summary` (verbatim, it is the model's
summary), then the artifact card. If the subagent flagged `counts_may_be_low`
or there were Check lines, add one sentence pointing at them. No description of
what was fetched, no offer to help further.

Follow-ups in the same conversation ("and next week?") re-run only step 1
with the new window and republish; the subagent may reuse nothing across
calls, so accept the repeated fetch.
