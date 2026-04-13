"""Tests for src.db.cursors — ingest_cursors table repo.

Isolation: see tests/conftest.py. Each test runs with an empty
ingest_cursors table in a schema-namespaced test DB.
"""

from __future__ import annotations

import time

from src.db import cursors


def test_read_missing_returns_none() -> None:
    assert cursors.read_cursor("does-not-exist") is None


def test_write_then_read_roundtrip() -> None:
    cursors.write_cursor("gmail", "h1")

    row = cursors.read_cursor("gmail")
    assert row is not None
    assert row["cursor_name"] == "gmail"
    assert row["history_id"] == "h1"
    assert row["updated_at"] is not None


def test_second_write_updates_same_row() -> None:
    cursors.write_cursor("gmail", "h1")
    cursors.write_cursor("gmail", "h2")

    # Value updated, and there is still exactly one row.
    row = cursors.read_cursor("gmail")
    assert row is not None
    assert row["history_id"] == "h2"

    from src.db import connect

    with connect() as conn:
        count = conn.execute(
            "SELECT count(*)::int AS n FROM ingest_cursors WHERE cursor_name = %s",
            ("gmail",),
        ).fetchone()
    assert count is not None
    assert count["n"] == 1


def test_update_refreshes_updated_at() -> None:
    cursors.write_cursor("gmail", "h1")
    first = cursors.read_cursor("gmail")
    assert first is not None
    first_ts = first["updated_at"]

    # BEFORE UPDATE trigger fires on re-write. Sleep a hair so the
    # timestamps are distinguishable even on fast machines.
    time.sleep(0.01)
    cursors.write_cursor("gmail", "h2")

    second = cursors.read_cursor("gmail")
    assert second is not None
    assert second["updated_at"] > first_ts
