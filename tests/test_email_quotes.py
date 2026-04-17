"""Tests for the shared quote-strip helper.

Used by both ingest (trivial-ack filter) and compile (thread-context
summarization). The behavior must match filter_trivial's pre-U8 private
helper exactly so the is_trivial judgment doesn't drift.
"""

from __future__ import annotations

from src.utils.email_quotes import strip_quoted


def test_strip_quoted_drops_angle_prefix_lines() -> None:
    # Quoted lines are *skipped*, not replaced with blanks — result is the
    # remaining non-quoted lines joined by newlines, nothing more.
    body = "> quoted line\ncontent"
    assert strip_quoted(body) == "content"


def test_strip_quoted_stops_at_forwarded_marker() -> None:
    body = "body\n----- Forwarded message -----\nquoted history"
    assert strip_quoted(body) == "body"


def test_strip_quoted_stops_at_original_message_marker() -> None:
    body = "reply text\nOriginal Message\nfrom: someone"
    assert strip_quoted(body) == "reply text"


def test_strip_quoted_marker_is_case_insensitive() -> None:
    body = "hello\n----- FORWARDED MESSAGE -----\nquoted"
    assert strip_quoted(body) == "hello"


def test_strip_quoted_handles_leading_whitespace_on_quote_lines() -> None:
    # Gmail indents quoted blocks — leading whitespace before `>` still counts.
    body = "  > indented quote\nreal content"
    assert strip_quoted(body) == "real content"


def test_strip_quoted_preserves_content_without_quotes() -> None:
    body = "line one\nline two\nline three"
    assert strip_quoted(body) == "line one\nline two\nline three"


def test_strip_quoted_empty_body() -> None:
    assert strip_quoted("") == ""


def test_strip_quoted_matches_filter_trivial_backcompat_alias() -> None:
    # Belt-and-suspenders: the legacy private name in filter_trivial
    # should still resolve to the shared helper. Guards against someone
    # reintroducing a divergent private copy.
    from src.ingest.filter_trivial import _strip_quoted

    assert _strip_quoted is strip_quoted
