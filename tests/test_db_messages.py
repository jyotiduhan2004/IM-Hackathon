"""Tests for the messages catalog repo functions (src/db/messages.py).

Isolation: see tests/conftest.py. Each test runs against the dedicated
`email_kb_test` database and starts with an empty messages table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import psycopg

from src.db import messages as repo


def _insert(
    conn: psycopg.Connection,
    *,
    message_id: str,
    raw_path: str | None = None,
    thread_id: str | None = "t-1",
    subject: str | None = "s",
    from_address: str | None = "a@b.c",
    date: datetime | None = None,
    compile_state: str = "pending",
) -> bool:
    """Shortcut: insert via the repo using the test connection."""
    return repo.insert_message(
        conn,
        message_id=message_id,
        raw_path=raw_path or f"raw/{message_id}.md",
        thread_id=thread_id,
        subject=subject,
        from_address=from_address,
        date=date,
        compile_state=compile_state,
    )


def _fetch_one(conn: psycopg.Connection, message_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM messages WHERE message_id = %s", (message_id,)
    ).fetchone()
    assert row is not None, f"no row for {message_id}"
    return row


# ---------------------------------------------------------------------------
# insert_message
# ---------------------------------------------------------------------------


def test_insert_message_basic(db_conn: psycopg.Connection) -> None:
    first = _insert(db_conn, message_id="m1")
    db_conn.commit()
    assert first is True

    # Re-insert: ON CONFLICT DO NOTHING → returns False, row unchanged.
    second = _insert(db_conn, message_id="m1", subject="NEW SUBJECT")
    db_conn.commit()
    assert second is False

    row = _fetch_one(db_conn, "m1")
    assert row["subject"] == "s"  # original value preserved


# ---------------------------------------------------------------------------
# list_uncompiled
# ---------------------------------------------------------------------------


def test_list_uncompiled_orders_by_date_asc_nulls_last(
    db_conn: psycopg.Connection,
) -> None:
    d_old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d_new = datetime(2026, 6, 1, tzinfo=timezone.utc)

    _insert(db_conn, message_id="m_new", date=d_new)
    _insert(db_conn, message_id="m_null", date=None)
    _insert(db_conn, message_id="m_old", date=d_old)
    db_conn.commit()

    rows = repo.list_uncompiled()
    ids = [r["message_id"] for r in rows]
    # Oldest first, then newer, NULL dates last.
    assert ids == ["m_old", "m_new", "m_null"]


# ---------------------------------------------------------------------------
# claim_next_message + finish_message_compile (happy path)
# ---------------------------------------------------------------------------


def test_claim_finish_happy_path(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    run_id = uuid.uuid4()
    claimed = repo.claim_next_message(run_id)
    assert claimed is not None
    assert claimed["message_id"] == "m1"
    assert claimed["compile_attempts"] == 1

    row = _fetch_one(db_conn, "m1")
    assert row["compile_state"] == "claimed"
    assert row["compile_run_id"] == run_id
    assert row["claimed_at"] is not None
    assert row["is_compiled"] is False  # generated column

    repo.finish_message_compile("m1")

    row = _fetch_one(db_conn, "m1")
    assert row["compile_state"] == "compiled"
    assert row["compiled_at"] is not None
    assert row["is_compiled"] is True  # generated column reflects state
    assert row["last_error"] is None


# ---------------------------------------------------------------------------
# claim_next_message concurrency (no double-claim)
# ---------------------------------------------------------------------------


def test_claim_skip_locked_no_double_claim(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db_conn, message_id="m2", date=datetime(2026, 2, 1, tzinfo=timezone.utc))
    db_conn.commit()

    run_a = uuid.uuid4()
    run_b = uuid.uuid4()

    first = repo.claim_next_message(run_a)
    second = repo.claim_next_message(run_b)

    assert first is not None
    assert second is not None
    assert first["message_id"] != second["message_id"], (
        "two workers must not claim the same row"
    )
    assert {first["message_id"], second["message_id"]} == {"m1", "m2"}

    # Third claim: queue empty → None.
    third = repo.claim_next_message(uuid.uuid4())
    assert third is None


# ---------------------------------------------------------------------------
# fail_message_compile → re-claim increments attempts, clears last_error
# ---------------------------------------------------------------------------


def test_fail_then_reclaim_increments_attempts(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    run_id = uuid.uuid4()
    first = repo.claim_next_message(run_id)
    assert first is not None
    assert first["compile_attempts"] == 1

    repo.fail_message_compile("m1", error="boom")
    row = _fetch_one(db_conn, "m1")
    assert row["compile_state"] == "failed"
    assert row["last_error"] == "boom"

    # Re-claim should pick the failed row up again.
    second = repo.claim_next_message(uuid.uuid4())
    assert second is not None
    assert second["message_id"] == "m1"
    assert second["compile_attempts"] == 2

    row = _fetch_one(db_conn, "m1")
    assert row["last_error"] is None  # cleared on re-claim
    assert row["compile_state"] == "claimed"


# ---------------------------------------------------------------------------
# Stale-claim recovery
# ---------------------------------------------------------------------------


def test_stale_claim_recovery(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db_conn.commit()

    first = repo.claim_next_message(uuid.uuid4())
    assert first is not None
    assert first["compile_attempts"] == 1

    # A second claim right away finds nothing — it's still claimed and fresh.
    fresh = repo.claim_next_message(uuid.uuid4(), stale_after_minutes=30)
    assert fresh is None

    # Simulate a crashed worker: backdate claimed_at by 1 hour.
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    db_conn.execute(
        "UPDATE messages SET claimed_at = %s WHERE message_id = %s",
        (stale_ts, "m1"),
    )
    db_conn.commit()

    # Now with the default 30-minute staleness threshold, it should be reclaimable.
    stolen = repo.claim_next_message(uuid.uuid4(), stale_after_minutes=30)
    assert stolen is not None
    assert stolen["message_id"] == "m1"
    assert stolen["compile_attempts"] == 2


# ---------------------------------------------------------------------------
# count_by_state
# ---------------------------------------------------------------------------


def test_count_by_state(db_conn: psycopg.Connection) -> None:
    # Distribution: 3 pending, 2 compiled, 1 failed.
    _insert(db_conn, message_id="p1")
    _insert(db_conn, message_id="p2")
    _insert(db_conn, message_id="p3")
    _insert(
        db_conn,
        message_id="c1",
        compile_state="compiled",
    )
    _insert(
        db_conn,
        message_id="c2",
        compile_state="compiled",
    )
    _insert(db_conn, message_id="f1", compile_state="failed")
    db_conn.commit()

    counts = repo.count_by_state()
    assert counts == {"pending": 3, "compiled": 2, "failed": 1}


# ---------------------------------------------------------------------------
# remaining_uncompiled_count
# ---------------------------------------------------------------------------


def test_remaining_uncompiled_count(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="p1")
    _insert(db_conn, message_id="p2")
    _insert(db_conn, message_id="f1", compile_state="failed")
    _insert(db_conn, message_id="c1", compile_state="compiled")
    db_conn.commit()

    remaining = repo.remaining_uncompiled_count()
    uncompiled_rows = repo.list_uncompiled()

    assert remaining == len(uncompiled_rows)
    assert remaining == 3  # two pending + one failed


# ---------------------------------------------------------------------------
# find_by_raw_path
# ---------------------------------------------------------------------------


def test_find_by_raw_path(db_conn: psycopg.Connection) -> None:
    _insert(db_conn, message_id="m1", raw_path="raw/2026-04-10-xyz.md")
    db_conn.commit()

    row = repo.find_by_raw_path("raw/2026-04-10-xyz.md")
    assert row is not None
    assert row["message_id"] == "m1"
    assert row["compile_state"] == "pending"

    assert repo.find_by_raw_path("raw/does-not-exist.md") is None
