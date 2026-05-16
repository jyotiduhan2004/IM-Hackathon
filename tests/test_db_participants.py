"""Tests for src/db/participants.py — role enum, FK cascade, count by role.

Isolation: see tests/conftest.py.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import psycopg
import pytest
from src.db import messages as msg_repo
from src.db import participants as repo
from src.db import users as user_repo


def _seed_message(conn: psycopg.Connection, mid: str = "m1") -> None:
    """Create a message + a single user so participant inserts have FKs to point at."""
    msg_repo.insert_message(
        conn,
        message_id=mid,
        raw_path=f"raw/{mid}.md",
        thread_id="t1",
        subject="s",
        from_address="a@b.c",
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    user_repo.upsert_user(conn, email="amit@indiamart.com", display_name="Amit")
    conn.commit()


def test_insert_participant_basic_and_idempotent(db_conn: psycopg.Connection) -> None:
    _seed_message(db_conn)

    first = repo.insert_participant(
        db_conn,
        message_id="m1",
        user_email="amit@indiamart.com",
        role="from",
        display_name="Amit Jain",
    )
    db_conn.commit()
    assert first is True

    # Repeat → ON CONFLICT DO NOTHING, returns False.
    second = repo.insert_participant(
        db_conn,
        message_id="m1",
        user_email="amit@indiamart.com",
        role="from",
        display_name="Amit Jain",
    )
    db_conn.commit()
    assert second is False


def test_insert_participant_all_three_roles(db_conn: psycopg.Connection) -> None:
    _seed_message(db_conn)
    user_repo.upsert_user(db_conn, email="other@example.com")
    user_repo.upsert_user(db_conn, email="cc@example.com")
    db_conn.commit()

    repo.insert_participant(db_conn, message_id="m1", user_email="amit@indiamart.com", role="from")
    repo.insert_participant(db_conn, message_id="m1", user_email="other@example.com", role="to")
    repo.insert_participant(db_conn, message_id="m1", user_email="cc@example.com", role="cc")
    db_conn.commit()

    counts = repo.count_participants_by_role()
    assert counts == {"from": 1, "to": 1, "cc": 1}


def test_insert_participant_rejects_invalid_role(db_conn: psycopg.Connection) -> None:
    _seed_message(db_conn)

    # The CHECK constraint must reject anything outside (from, to, cc).
    # psycopg surfaces this as a CheckViolation; we just want a database error.
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.insert_participant(
            db_conn,
            message_id="m1",
            user_email="amit@indiamart.com",
            role="bcc",  # not in the enum
        )
    db_conn.rollback()


def test_fk_cascade_delete_message_drops_participants(
    db_conn: psycopg.Connection,
) -> None:
    _seed_message(db_conn)
    repo.insert_participant(db_conn, message_id="m1", user_email="amit@indiamart.com", role="from")
    db_conn.commit()
    assert repo.count_participants_by_role()["from"] == 1

    db_conn.execute("DELETE FROM messages WHERE message_id = %s", ("m1",))
    db_conn.commit()

    # ON DELETE CASCADE — participant row should be gone, user row stays.
    assert repo.count_participants_by_role() == {"from": 0, "to": 0, "cc": 0}
    assert user_repo.find_by_email("amit@indiamart.com") is not None


def test_count_participants_by_role_zero_fills_missing(
    db_conn: psycopg.Connection,
) -> None:
    # Empty table — every role should still appear with count 0.
    counts = repo.count_participants_by_role()
    assert counts == {"from": 0, "to": 0, "cc": 0}
