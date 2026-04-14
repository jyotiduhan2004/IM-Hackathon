"""Tests for `find_new_sources` tool and `list_uncompiled_with_filters` repo fn.

Mocks `src.db.messages.connect` so tests don't hit Postgres. Verifies the
generated SQL wires up the right WHERE clauses and that parameters are
passed positionally (no user input is ever spliced into the SQL string).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from src.compile import compiler
from src.db import messages as repo


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    """Records execute() calls so tests can inspect the SQL + params."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._rows = rows or []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.calls.append((sql, params))
        return _FakeCursor(self._rows)


@pytest.fixture
def fake_conn(monkeypatch: pytest.MonkeyPatch) -> _FakeConn:
    """Patch `src.db.messages.connect` to yield a _FakeConn."""
    conn = _FakeConn()

    @contextmanager
    def _connect() -> Any:
        yield conn

    monkeypatch.setattr(repo, "connect", _connect)
    return conn


# ---------------------------------------------------------------------------
# list_uncompiled_with_filters — SQL shape
# ---------------------------------------------------------------------------


def test_all_filters_set_builds_all_where_clauses(fake_conn: _FakeConn) -> None:
    repo.list_uncompiled_with_filters(
        date_from="2026-01-01",
        date_to="2026-04-01",
        sender_contains="alice",
        subject_contains="urgent",
        thread_id="t-123",
        limit=10,
        offset=5,
    )

    assert len(fake_conn.calls) == 1
    sql, params = fake_conn.calls[0]

    # Base predicate always present.
    assert "compile_state IN ('pending', 'failed')" in sql
    # All filter clauses wired up.
    assert "date >= %s::date" in sql
    assert "date <= %s::date" in sql
    assert "from_address ILIKE" in sql
    assert "subject ILIKE" in sql
    assert "thread_id = %s" in sql
    assert "LIMIT %s OFFSET %s" in sql

    # Params are positional + ordered: filters first (in the order filters
    # are appended), then limit/offset last.
    assert params == (
        "2026-01-01",
        "2026-04-01",
        "alice",
        "urgent",
        "t-123",
        10,
        5,
    )


def test_only_date_from_emits_date_lower_bound_no_ilike(fake_conn: _FakeConn) -> None:
    repo.list_uncompiled_with_filters(date_from="2026-02-01")

    sql, params = fake_conn.calls[0]

    assert "date >= %s::date" in sql
    assert "date <= %s::date" not in sql
    assert "ILIKE" not in sql
    assert "thread_id = %s" not in sql
    # Only date_from + default limit/offset.
    assert params == ("2026-02-01", 50, 0)


def test_no_filters_only_emits_state_predicate(fake_conn: _FakeConn) -> None:
    repo.list_uncompiled_with_filters()

    sql, params = fake_conn.calls[0]

    assert "compile_state IN ('pending', 'failed')" in sql
    assert "date >= %s::date" not in sql
    assert "date <= %s::date" not in sql
    assert "ILIKE" not in sql
    assert "thread_id = %s" not in sql
    # Only the default limit/offset params should be present.
    assert params == (50, 0)


def test_custom_limit_honored(fake_conn: _FakeConn) -> None:
    repo.list_uncompiled_with_filters(limit=200)

    _sql, params = fake_conn.calls[0]
    # Limit is the second-to-last param; offset last.
    assert params[-2] == 200
    assert params[-1] == 0


def test_sql_is_parameterized_no_interpolation(fake_conn: _FakeConn) -> None:
    """User-supplied filter values must NOT appear verbatim in the SQL."""
    repo.list_uncompiled_with_filters(
        date_from="2026-01-01",
        sender_contains="'; DROP TABLE messages; --",
        subject_contains="urgent",
        thread_id="t-xyz",
    )

    sql, params = fake_conn.calls[0]

    # None of the user inputs should be spliced into the SQL string.
    assert "2026-01-01" not in sql
    assert "DROP TABLE" not in sql
    assert "urgent" not in sql
    assert "t-xyz" not in sql
    # But they MUST appear in params.
    assert "2026-01-01" in params
    assert "'; DROP TABLE messages; --" in params
    assert "urgent" in params
    assert "t-xyz" in params


# ---------------------------------------------------------------------------
# find_new_sources tool — output shape
# ---------------------------------------------------------------------------


def test_find_new_sources_returns_correct_dict_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the tool maps rows to the {path,date,subject,from,thread_id} shape."""
    rows: list[dict[str, Any]] = [
        {
            "message_id": "m1",
            "raw_path": "raw/2026-01-01-a.md",
            "thread_id": "t-1",
            "subject": "hello",
            "from_address": "alice@example.com",
            "date": datetime(2026, 1, 1, tzinfo=UTC),
        },
        # Row with all-None nullable fields — tool must handle without crashing.
        {
            "message_id": "m2",
            "raw_path": "raw/2026-01-02-b.md",
            "thread_id": None,
            "subject": None,
            "from_address": None,
            "date": None,
        },
    ]

    mock_fn = MagicMock(return_value=rows)
    monkeypatch.setattr(
        "src.db.messages.list_uncompiled_with_filters",
        mock_fn,
    )

    result = compiler.find_new_sources.invoke({"sender_contains": "alice"})

    assert result == [
        {
            "path": "raw/2026-01-01-a.md",
            "date": "2026-01-01T00:00:00+00:00",
            "subject": "hello",
            "from": "alice@example.com",
            "thread_id": "t-1",
        },
        {
            "path": "raw/2026-01-02-b.md",
            "date": "",
            "subject": "",
            "from": "",
            "thread_id": "",
        },
    ]

    # Tool forwards kwargs to the repo function.
    mock_fn.assert_called_once_with(
        date_from=None,
        date_to=None,
        sender_contains="alice",
        subject_contains=None,
        thread_id=None,
        limit=50,
        offset=0,
    )


@pytest.mark.parametrize(
    "bad",
    ["2026/04/13", "abc", "2026-13-01", "2026-02-30", ""],
)
def test_find_new_sources_rejects_malformed_date_from(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-ISO date_from values return an error dict (no DB call, no crash)."""
    mock_fn = MagicMock()
    monkeypatch.setattr("src.db.messages.list_uncompiled_with_filters", mock_fn)

    result = compiler.find_new_sources.invoke({"date_from": bad})
    assert isinstance(result, dict) and "error" in result
    assert "date_from" in result["error"]
    mock_fn.assert_not_called()


def test_find_new_sources_rejects_malformed_date_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_fn = MagicMock()
    monkeypatch.setattr("src.db.messages.list_uncompiled_with_filters", mock_fn)

    result = compiler.find_new_sources.invoke({"date_to": "2026-02-30"})
    assert isinstance(result, dict) and "date_to" in result["error"]
    mock_fn.assert_not_called()


def test_find_new_sources_caps_unbounded_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requesting limit=10000 gets silently capped at 200 so the tool can't
    drag a huge result set back. The agent still gets data, just paginated."""
    mock_fn = MagicMock(return_value=[])
    monkeypatch.setattr("src.db.messages.list_uncompiled_with_filters", mock_fn)

    compiler.find_new_sources.invoke({"limit": 10_000})
    assert mock_fn.call_args.kwargs["limit"] == 200


@pytest.mark.parametrize("bad_kwargs", [{"limit": 0}, {"limit": -5}, {"offset": -1}])
def test_find_new_sources_rejects_invalid_limit_offset(
    bad_kwargs: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_fn = MagicMock()
    monkeypatch.setattr("src.db.messages.list_uncompiled_with_filters", mock_fn)

    result = compiler.find_new_sources.invoke(bad_kwargs)
    assert isinstance(result, dict) and "error" in result
    mock_fn.assert_not_called()
