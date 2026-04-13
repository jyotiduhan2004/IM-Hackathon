"""Tests for src/db/threads.py — upsert idempotency, aggregate recompute, count.

Isolation: see tests/conftest.py.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import psycopg
from src.db import messages as msg_repo
from src.db import threads as repo


def test_upsert_thread_inserts_then_no_op_on_repeat(db_conn: psycopg.Connection) -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    first = repo.upsert_thread(db_conn, thread_id="t1", first_message_at=when, last_message_at=when)
    db_conn.commit()
    assert first is True

    second = repo.upsert_thread(
        db_conn, thread_id="t1", first_message_at=when, last_message_at=when
    )
    db_conn.commit()
    assert second is False

    assert repo.count_threads() == 1


def test_upsert_thread_widens_first_last_window(db_conn: psycopg.Connection) -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)
    middle = datetime(2026, 6, 1, tzinfo=UTC)
    late = datetime(2026, 12, 1, tzinfo=UTC)

    repo.upsert_thread(db_conn, thread_id="t1", first_message_at=middle, last_message_at=middle)
    db_conn.commit()

    # Re-upsert with a wider window — should widen, not overwrite.
    repo.upsert_thread(db_conn, thread_id="t1", first_message_at=early, last_message_at=late)
    db_conn.commit()

    row = db_conn.execute(
        "SELECT first_message_at, last_message_at FROM threads WHERE thread_id = %s",
        ("t1",),
    ).fetchone()
    assert row is not None
    assert row["first_message_at"] == early
    assert row["last_message_at"] == late


def test_update_thread_aggregates_recomputes_from_messages(
    db_conn: psycopg.Connection,
) -> None:
    repo.upsert_thread(db_conn, thread_id="t1")
    d_first = datetime(2026, 1, 1, tzinfo=UTC)
    d_mid = datetime(2026, 6, 1, tzinfo=UTC)
    d_last = datetime(2026, 9, 1, tzinfo=UTC)
    for mid, d in [("m1", d_first), ("m2", d_mid), ("m3", d_last)]:
        msg_repo.insert_message(
            db_conn,
            message_id=mid,
            raw_path=f"raw/{mid}.md",
            thread_id="t1",
            subject="s",
            from_address="a@b.c",
            date=d,
        )
    db_conn.commit()

    updated = repo.update_thread_aggregates("t1")
    assert updated is not None
    assert updated["message_count"] == 3
    assert updated["first_message_at"] == d_first
    assert updated["last_message_at"] == d_last


def test_count_threads(db_conn: psycopg.Connection) -> None:
    repo.upsert_thread(db_conn, thread_id="t1")
    repo.upsert_thread(db_conn, thread_id="t2")
    db_conn.commit()

    assert repo.count_threads() == 2
