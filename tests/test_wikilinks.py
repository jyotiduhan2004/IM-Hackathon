"""Regression tests for shared wikilink parser."""

from __future__ import annotations

from src.utils.wikilinks import WIKILINK_RE
from src.utils.wikilinks import parse_wikilink_target


def test_bare_slug() -> None:
    assert parse_wikilink_target("topic/foo") == "topic/foo"


def test_pipe_display() -> None:
    assert parse_wikilink_target("topic/foo|Display Text") == "topic/foo"


def test_hash_anchor() -> None:
    assert parse_wikilink_target("topic/foo#section") == "topic/foo"


def test_pipe_and_hash() -> None:
    assert parse_wikilink_target("topic/foo|Display#section") == "topic/foo"


def test_strips_whitespace() -> None:
    assert parse_wikilink_target("  topic/foo  ") == "topic/foo"
    assert parse_wikilink_target("  topic/foo  |  disp  ") == "topic/foo"


def test_empty_returns_empty() -> None:
    assert parse_wikilink_target("") == ""
    assert parse_wikilink_target("|only display") == ""


def test_regex_finds_bare() -> None:
    assert WIKILINK_RE.findall("see [[topic/foo]]") == ["topic/foo"]


def test_regex_finds_pipe_display() -> None:
    assert WIKILINK_RE.findall("see [[topic/foo|Foo Display]]") == ["topic/foo"]


def test_regex_finds_hash_anchor() -> None:
    assert WIKILINK_RE.findall("see [[topic/foo#section]]") == ["topic/foo"]


def test_regex_finds_pipe_and_hash() -> None:
    assert WIKILINK_RE.findall("see [[topic/foo|Display#section]]") == ["topic/foo"]


def test_regex_finds_multiple_nested_brackets() -> None:
    # Two adjacent wikilinks — the regex must not be greedy across them.
    body = "[[a|x]][[b]]"
    assert WIKILINK_RE.findall(body) == ["a", "b"]


def test_regex_rejects_unbalanced_brackets() -> None:
    # Opening `[[` inside a link target should not match.
    assert WIKILINK_RE.findall("[[topic/[[nested]]") == ["nested"]


def test_regex_plus_target_parsing_end_to_end() -> None:
    body = "See [[topic/foo|Display Text#anchor]] for details."
    matches = WIKILINK_RE.findall(body)
    assert [parse_wikilink_target(m) for m in matches] == ["topic/foo"]


def test_regex_finds_hash_before_pipe() -> None:
    """Obsidian form: anchor on the page path, display text after."""
    assert WIKILINK_RE.findall("see [[topic/foo#section|Label]]") == ["topic/foo"]


def test_regex_fullmatch_bare_wikilink() -> None:
    m = WIKILINK_RE.fullmatch("[[topic/foo]]")
    assert m is not None and m.group(1) == "topic/foo"


def test_regex_fullmatch_no_match_for_bare_slug() -> None:
    assert WIKILINK_RE.fullmatch("topic/foo") is None
