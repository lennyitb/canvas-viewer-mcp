---
name: canvas-week-report
description: Produce a short, prioritized "what schoolwork is left" report from the canvas-viewer connector with as few tool calls and tokens as possible. Use this whenever the user asks what's due, what's left this week, what's due tonight or tomorrow, whether they're caught up, what they should work on next for school, or asks for a Canvas / homework / assignment summary or report, even if they don't say "Canvas" or "report". Also use it for follow-ups like "what about next week". Do NOT use it for help doing a specific assignment or for picking discussion posts to reply to.
---

# Canvas week report

The reader is the student. They know their own courses and instructors, and
they know Canvas misbehaves. They want a to-do list they can act on in ten
seconds, not a status dashboard. Two things matter: the report is correct about
what is really outstanding, and getting there doesn't burn tokens on data that
never reaches the report.

Requires the canvas-viewer connector (or MCP server). Its tools may appear
under a prefix such as `canvas_viewer_mcp__list_assignments`; the bare names
are used throughout this skill.

## 1. Gather (budgeted)

If your client loads tools on demand rather than listing them all up front,
fetch every Canvas tool you need in ONE search (query "canvas"), not one
search per tool.

A normal run is at most these four calls:

1. **Clock**: current local time and zone from a clock tool if one exists.
   Otherwise use the timezone the user has already stated; if none is known,
   say in one short line which zone you assumed. Canvas returns UTC and
   nothing in the report is right until it is converted.
2. **`list_assignments` with no `course_id`**, once. Rows carry `course_name`,
   submission state, locks, points, and `discussion_topic_id` for every course,
   so `list_courses` and per-course calls add nothing.
3. **`list_announcements` with `days: 7`.**
4. **`list_discussion_participation`**, only if step 2 shows a discussion in
   play: main post submitted but not yet graded, or unsubmitted and due in the
   window. Pass `course_id` when every in-play discussion is in one course.
   Each row gives `reply_requirement` (verbatim sentences from the description)
   and `my_participation`; that is everything the report needs, for both the
   post deadline and the reply deadline.

Calls 2 and 3 don't depend on each other; issue them together when you can.

Reading the sweep: only rows that are ungraded-with-a-post or due in the window
matter; skip graded and far-future rows. In `reply_requirement`, the sentences
that matter name a count and a deadline ("two classmates by Sunday at 11:59
p.m."). Sentences like "As part of your response, consider..." are keyword
noise. If `counts_are_complete` is false, say the count may be low.

Follow-up calls are exceptions, not routine:

| Situation | Call |
|---|---|
| `reply_requirement` is cut off (`[...]`) before it states a reply count or deadline | `get_assignment` |
| Stale-dated item whose estimated date lands in the window | `get_assignment` (description sometimes names the real date) |
| An announcement refers to a specific item ambiguously | `get_assignment` |
| A grade of 0 or a sharp drop posted in the last 10 days | `list_feedback` with `days: 10` and that `course_id` (the comment may offer a resubmission, which is work to list) |

`get_discussion` isn't needed for this report. If you ever do call it, leave
`entries` at its default; `"all"` returns the whole thread.

Don't call `list_courses`, `list_discussions`, `list_modules`, `list_pages`,
`list_files`, or `read_course_file` here. Exception: a course whose dates are
all stale and whose next item you can't otherwise place may justify one
`list_modules`.

In a follow-up in the same conversation ("and next week?"), reuse what you
already fetched.

## 2. Triage

**Time.** Every Canvas timestamp is UTC. Convert to local time before deciding
what day something is due. In US Eastern, `03:59:59Z` is 11:59 PM the
*previous* evening; getting this wrong shifts the whole report by a day.

**Window.** The week is Monday through Sunday, local. Report what remains of it.
If asked on Friday, Saturday, or Sunday, add next week as a compact look-ahead.
If the user names a different window ("tonight", "next two weeks"), use theirs.

**Drop silently:**
- Anything submitted or graded that isn't a discussion needing replies.
- Finished courses and non-teaching shells: no real (non-stale) due date within
  about three weeks either side of today and nothing upcoming.
- `submission_types` of `none`, or zero-point items, unless an announcement
  makes them matter.

**Stale dates.** `due_at` earlier than `created_at` (or many months in the past
in a live course) means the course was copied and the date never updated.
`missing: true` on these is noise. Estimate the real date as stale date + 52
weeks, which preserves the weekday, and let a description or announcement
override it. Report the item only if the estimate falls in the window, marked
as an estimate.

**Discussions.** `submitted` means the first post only. Compare
`replies_to_others` to the count in `reply_requirement` and report the gap as
a fraction ("replies 1/2"). `due_at` is usually the post deadline; the reply
deadline lives in the description and is a separate line in the report if it
falls on a different day.

**On-paper items** (`on_paper`). Canvas can't see these, so "unsubmitted" means
nothing. List them when due in the window; never call them late or missing.

**Late but open.** Valid date passed, unsubmitted, not locked, online
submission: still actionable, so include it.

**Announcements.** Use them to correct dates and catch extra requirements.
Attach what matters to the item it affects. No announcements section.

## 3. Write the report

Lead with the list. No greeting, no description of what you checked, no closing
offer. Group by day, soonest first; use **Today** and **Tomorrow** for those
days and `Tue 9/22` style after. One line per item:

`Course short name: item` plus only the qualifiers that change what they do.

Qualifiers worth the words:
- **locks**: `lock_at` equals the deadline, so it can't be done late. Bold it.
- A time, when it isn't 11:59 PM (5:00 PM, 1:55 PM, 11:30 PM are the ones that
  get missed). For Today and Tomorrow always give the time. Otherwise omit
  11:59 PM.
- Reply fractions for discussions.
- "paper" for on-paper work.
- Unusual weight: an exam, or points well above the course's norm.
- An announcement detail that changes the work ("use the updated starter file").
- "est., Canvas date stale" for estimated dates.

Combine same-course, same-deadline items on one line. Shorten course names to
what the student would say out loud — "Circuits", not "ELEC 2110 Circuit
Analysis I (Fall 2025)".

After the dated list, an optional **Check** section, at most three lines, for
things that need a human look: a grade of 0 or a sharp drop posted in the last
10 days, an instructor warning in an announcement that may apply to them, a
stale-dated item you couldn't place. One line each, no advice beyond the
obvious next step.

Look-ahead (when included): one line per day, items separated by semicolons,
same qualifiers.

### Leave out
- Completed work, "already done" lists, current grades that are fine.
- Explanations of Canvas quirks or of how you verified something. State the
  true status; the student knows why Canvas is wrong.
- Submission type, points, or links, unless they're one of the qualifiers above.
- Restating the question, the date range, or that Sunday ends the week.
- Encouragement, study tips, time-management advice, offers to help further.
- Anything flagged under Check that isn't new; they have seen it.

If nothing is left in the window, say so in one sentence and give the next due
item.

### Example

Shape only — the course names are invented.

```
**Today**
- 5:00 PM · Microcontrollers: Week 4 HW; Week 4 Quiz
- 11:59 PM · Circuits: Quiz 3 Parallel Circuits (**locks**)
- 11:59 PM · History Week 4 replies: Discussion 1/2, Topic 0/1, Project 0/1

**Next week**
- Tue 9/22 · Circuits: HW 3 (paper); Microcontrollers: Week 4 Lab (use the updated starter file from the announcement)
- Wed 9/23 · History: Week 5 Discussion question; Design: PCB Layout #2 (est., Canvas date stale)
- Thu 9/24 · Linear Algebra: Quiz 5, 1:55 PM; History: Week 5 topic discussion
- Fri 9/25 · History: Week 5 argument
- Sat 9/26 · Circuits: Lab 3
- Sun 9/27 · Microcontrollers: Week 5 HW, 11:30 PM; History Week 5 replies

**Check**
- Circuits HW 1 (paper) was graded 0 on 9/12.
```
