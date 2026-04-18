"""Tests for get_thread_context response_format variants (U8 + v10-U6).

Covers:
- `"detailed"` preserves the pre-U8 shape exactly — callers that opt
  into detailed must see a full per-message list.
- `"concise"` (the v10-U6 default) returns the aggregate shape
  `{thread_id, message_count, first_subject, latest_date, cutoff_date,
  truncated}` — no per-message bodies.
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
    """Explicit `detailed` mode must return the full per-message shape."""
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
        result = get_thread_context.invoke(
            {"thread_id": "thread-abc", "response_format": "detailed"}
        )

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


def test_get_thread_context_default_is_concise_aggregate(tmp_path: Path) -> None:
    """Default (no arg) returns the aggregate concise shape — no per-message bodies."""
    raw1 = _seed_raw(tmp_path, "m1.md", "Launch plan: ship MVP Friday.")
    raw2 = _seed_raw(tmp_path, "m2.md", "Second message body — later in thread.")
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
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke({"thread_id": "thread-abc"})

    assert set(result.keys()) == {
        "thread_id",
        "message_count",
        "first_subject",
        "latest_date",
        "cutoff_date",
        "truncated",
    }
    assert result["thread_id"] == "thread-abc"
    assert result["message_count"] == 2
    assert result["first_subject"] == "Launch"
    assert result["latest_date"] == "2026-04-11T09:00:00+00:00"
    assert result["cutoff_date"] is None
    assert result["truncated"] is False
    # Concise must NOT include per-message bodies.
    assert "messages" not in result
    assert "summary_lines" not in result


def test_get_thread_context_concise_empty_thread() -> None:
    """Concise mode on an unknown thread returns message_count=0 + empty subject."""
    cur = _FakeCursor([])

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke(
            {"thread_id": "thread-nope", "response_format": "concise"}
        )

    assert result["thread_id"] == "thread-nope"
    assert result["message_count"] == 0
    assert result["first_subject"] == ""
    assert result["latest_date"] is None
    assert result["truncated"] is False


def test_get_thread_context_concise_respects_cutoff() -> None:
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
