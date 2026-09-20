"""Discussion topic and reply flattening.

Two things Canvas declines to tell you straight, both reconstructed in
``content.py`` and both easy to regress because the payloads that expose them
only turn up on some endpoints:

* no topic payload carries a ``course_id``; and
* the author's name arrives under a different key per endpoint, with the
  others present but empty.
"""

from __future__ import annotations

from canvas_viewer_mcp.canvas.content import flatten_reply, flatten_topic

COURSE_TOPIC = {
    "id": 501,
    "title": "Week 4 Discussion",
    "posted_at": "2026-09-14T15:00:00Z",
    "html_url": "https://school.instructure.com/courses/68894/discussion_topics/501",
}


# ---- the course id Canvas omits ----------------------------------------------


def test_course_id_comes_from_the_request_when_canvas_omits_it() -> None:
    """Canvas never returns course_id on a topic; the caller always knows it."""
    topic = flatten_topic({"id": 1, "title": "t"}, course_id=68894)
    assert topic.course_id == 68894


def test_course_id_is_read_from_an_announcement_context_code() -> None:
    """The announcements endpoint spans courses, so no single id can be passed
    in; the payload names the course itself."""
    topic = flatten_topic({"id": 1, "title": "t", "context_code": "course_68894"})
    assert topic.course_id == 68894


def test_course_id_is_recovered_from_the_topic_link() -> None:
    """Last resort before the caller's own id: the links a topic carries sit
    under /courses/{id}/."""
    topic = flatten_topic(COURSE_TOPIC)
    assert topic.course_id == 68894


def test_canvas_course_id_wins_over_the_passed_one() -> None:
    """A payload that states the course outranks the request path."""
    topic = flatten_topic({"id": 1, "title": "t", "course_id": 111}, course_id=222)
    assert topic.course_id == 111


def test_the_caller_supplies_what_nothing_else_can() -> None:
    """A course topic with no id, no context code and no link still belongs to
    the course that was asked about."""
    topic = flatten_topic({"id": 1, "title": "t"}, course_id=68894)
    assert topic.course_id == 68894


def test_course_id_stays_null_when_nothing_can_supply_it() -> None:
    assert flatten_topic({"id": 1, "title": "t"}).course_id is None


def test_unparseable_context_code_does_not_crash_or_fabricate() -> None:
    topic = flatten_topic({"id": 1, "title": "t", "context_code": "group_42"})
    assert topic.course_id is None


# ---- the author Canvas hides in a different key per endpoint -----------------


def test_empty_author_object_falls_through_to_user_name() -> None:
    """The bug this guards: `author` is `{}` far more often than it is absent,
    and an empty dict used to settle the question and drop a named author."""
    topic = flatten_topic({"id": 1, "title": "t", "author": {}, "user_name": "Dana Reyes"})
    assert topic.author == "Dana Reyes"


def test_announcement_author_is_read_from_the_author_object() -> None:
    topic = flatten_topic({"id": 1, "title": "t", "author": {"display_name": "Prof. Vance"}})
    assert topic.author == "Prof. Vance"


def test_unattributed_course_topic_reports_no_author() -> None:
    """Instructor-created topics are attributed to the course, not a person.
    Null is the honest report; the course name would be a fabrication."""
    topic = flatten_topic({"id": 1, "title": "t", "author": {}, "user": {}})
    assert topic.author is None


def test_reply_author_is_read_from_the_user_object() -> None:
    """Replies name the writer under `user`, where topics use `author`."""
    assert flatten_reply({"id": 2, "user": {"display_name": "Sam Ortiz"}}).author == "Sam Ortiz"


def test_reply_falls_back_to_user_name() -> None:
    """The thread view carries no user object; the participant map is injected
    as user_name instead, and must still be found."""
    assert flatten_reply({"id": 2, "user": {}, "user_name": "Sam Ortiz"}).author == "Sam Ortiz"


def test_reply_with_no_named_writer_reports_none() -> None:
    assert flatten_reply({"id": 2}).author is None
