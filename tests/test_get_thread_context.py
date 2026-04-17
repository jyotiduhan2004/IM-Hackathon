"""Tests for get_thread_context response_format variants (U8).

Covers:
- `"detailed"` (default) preserves the pre-U8 shape exactly — callers
  that never pass the new arg must see no change.
- `"concise"` returns the compact `{summary_lines: ...}` shape with
  one_line strings capped at 120 chars and quote blocks stripped.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from src.compile.compiler import _current_batch_cutoff_date
from src.compile.compiler import get_thread_context


class _FakeCursor:
    """Minimal stand-in for a psycopg cursor returning a preset row list."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.last_sql = sql
        self.last_params = params
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def __enter__(self) -> _FakeCursor:
        return self._cur

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        return self._cur.execute(*args, **kwargs)


def _seed_raw(tmp_path: Path, name: str, body: str) -> str:
    """Write a raw email with minimal frontmatter + body; return absolute path."""
    path = tmp_path / name
    path.write_text(
        f"---\nmessage_id: {name}\n---\n\n{body}",
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture(autouse=True)
def _reset_cutoff() -> Any:
    """Each test runs with no chronological cutoff unless it sets one."""
    token = _current_batch_cutoff_date.set(None)
    try:
        yield
    finally:
        _current_batch_cutoff_date.reset(token)


def test_get_thread_context_detailed_unchanged(tmp_path: Path) -> None:
    """Default `detailed` mode must return the exact pre-U8 shape."""
    raw1 = _seed_raw(tmp_path, "m1.md", "First message body with plenty of context.")
    raw2 = _seed_raw(tmp_path, "m2.md", "Second message body.")
    rows = [
        {
            "message_id": "msg-001",
            "raw_path": raw1,
            "subject": "Sample thread",
            "from_address": "alice@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "compiled",
        },
        {
            "message_id": "msg-002",
            "raw_path": raw2,
            "subject": "Re: Sample thread",
            "from_address": "bob@indiamart.com",
            "date": datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke({"thread_id": "thread-abc"})

    assert set(result.keys()) == {"thread_id", "messages", "truncated", "cutoff_date"}
    assert result["thread_id"] == "thread-abc"
    assert result["truncated"] is False
    assert result["cutoff_date"] is None
    assert len(result["messages"]) == 2

    m1 = result["messages"][0]
    assert set(m1.keys()) == {
        "message_id",
        "subject",
        "from_addr",
        "date",
        "raw_path",
        "first_200_chars",
        "compile_state",
    }
    assert m1["message_id"] == "msg-001"
    assert m1["subject"] == "Sample thread"
    assert m1["from_addr"] == "alice@indiamart.com"
    assert m1["date"] == "2026-04-10T12:00:00+00:00"
    assert m1["compile_state"] == "compiled"
    assert m1["first_200_chars"].startswith("First message body")


def test_get_thread_context_concise_format(tmp_path: Path) -> None:
    """Concise mode returns compact summary_lines with stripped quotes."""
    # Message 1: clean body.
    raw1 = _seed_raw(tmp_path, "m1.md", "Launch plan: ship MVP Friday.")
    # Message 2: reply with long quote block that must be stripped.
    raw2_body = (
        "Agreed, shipping Friday works for me.\n"
        "----- Forwarded message -----\n"
        "original noise that must be dropped\n"
        "more noise\n"
    )
    raw2 = _seed_raw(tmp_path, "m2.md", raw2_body)
    # Message 3: reply with angle-prefixed quotes on top, substantive line below.
    raw3_body = (
        "> previous thread line\n"
        "> another quoted line\n"
        "\n"
        "Confirmed on my end — deployed to staging."
    )
    raw3 = _seed_raw(tmp_path, "m3.md", raw3_body)
    # Message 4: body exceeds 120 chars — must be truncated.
    long_first_line = "A" * 200
    raw4 = _seed_raw(tmp_path, "m4.md", long_first_line)

    rows = [
        {
            "message_id": "msg-001",
            "raw_path": raw1,
            "subject": "Launch",
            "from_address": "alice@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "compiled",
        },
        {
            "message_id": "msg-002",
            "raw_path": raw2,
            "subject": "Re: Launch",
            "from_address": "bob@indiamart.com",
            "date": datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
        {
            "message_id": "msg-003",
            "raw_path": raw3,
            "subject": "Re: Launch",
            "from_address": "carol@indiamart.com",
            "date": datetime(2026, 4, 12, 10, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
        {
            "message_id": "msg-004",
            "raw_path": raw4,
            "subject": "Re: Launch",
            "from_address": "dave@indiamart.com",
            "date": datetime(2026, 4, 13, 10, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke(
            {"thread_id": "thread-abc", "response_format": "concise"}
        )

    assert set(result.keys()) == {
        "thread_id",
        "message_count",
        "cutoff_date",
        "summary_lines",
        "truncated",
    }
    assert result["thread_id"] == "thread-abc"
    assert result["message_count"] == 4
    assert result["truncated"] is False
    assert result["cutoff_date"] is None

    sl = result["summary_lines"]
    assert len(sl) == 4

    # Shape per entry.
    for entry in sl:
        assert set(entry.keys()) == {"message_id", "date", "from_addr", "one_line"}
        assert len(entry["one_line"]) <= 120

    # Clean body: first line preserved.
    assert sl[0]["one_line"] == "Launch plan: ship MVP Friday."

    # Forwarded marker: content before marker returned, noise dropped.
    assert sl[1]["one_line"] == "Agreed, shipping Friday works for me."
    assert "Forwarded" not in sl[1]["one_line"]
    assert "noise" not in sl[1]["one_line"]

    # Quoted-reply lines skipped; first non-quote line wins.
    assert sl[2]["one_line"] == "Confirmed on my end — deployed to staging."
    assert not sl[2]["one_line"].startswith(">")

    # 200-char first line truncated to 120.
    assert len(sl[3]["one_line"]) == 120
    assert sl[3]["one_line"] == "A" * 120


def test_get_thread_context_concise_missing_raw_graceful(tmp_path: Path) -> None:
    """A row whose raw file is missing yields empty one_line, not a crash."""
    rows = [
        {
            "message_id": "msg-missing",
            "raw_path": str(tmp_path / "does_not_exist.md"),
            "subject": "Gone",
            "from_address": "ghost@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke(
            {"thread_id": "thread-abc", "response_format": "concise"}
        )

    assert result["summary_lines"][0]["one_line"] == ""


def test_get_thread_context_concise_respects_cutoff(tmp_path: Path) -> None:
    """Concise mode must still honour _current_batch_cutoff_date."""
    cur = _FakeCursor([])
    token = _current_batch_cutoff_date.set("2026-01-09T00:00:00+00:00")
    try:
        with patch("src.db.connect", return_value=_FakeConn(cur)):
            result = get_thread_context.invoke(
                {"thread_id": "thread-abc", "response_format": "concise"}
            )
    finally:
        _current_batch_cutoff_date.reset(token)

    assert result["cutoff_date"] == "2026-01-09T00:00:00+00:00"
    assert "date::date <= %s::date" in (cur.last_sql or "")
