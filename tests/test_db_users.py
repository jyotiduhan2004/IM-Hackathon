"""Tests for src/db/users.py — upsert idempotency, name override, count.

Isolation: see tests/conftest.py — uses the per-session test schema.
"""

from __future__ import annotations

import psycopg
from src.db import users as repo


def test_upsert_user_inserts_then_no_op_on_repeat(db_conn: psycopg.Connection) -> None:
    first = repo.upsert_user(db_conn, email="amit@indiamart.com", display_name="Amit Jain")
    db_conn.commit()
    assert first is True

    second = repo.upsert_user(db_conn, email="amit@indiamart.com", display_name="Amit Jain")
    db_conn.commit()
    assert second is False  # row already exists

    # Total count is still 1.
    assert repo.count_users() == 1


def test_upsert_user_overrides_name_when_new_value_provided(
    db_conn: psycopg.Connection,
) -> None:
    repo.upsert_user(db_conn, email="amit@indiamart.com", display_name="Amit")
    db_conn.commit()

    repo.upsert_user(db_conn, email="amit@indiamart.com", display_name="Amit Jain (IM)")
    db_conn.commit()

    row = repo.find_by_email("amit@indiamart.com")
    assert row is not None
    assert row["display_name"] == "Amit Jain (IM)"


def test_upsert_user_keeps_existing_name_when_new_is_null(
    db_conn: psycopg.Connection,
) -> None:
    repo.upsert_user(db_conn, email="amit@indiamart.com", display_name="Amit Jain")
    db_conn.commit()

    # Bare-email frontmatter shows up — don't blow away the good name.
    repo.upsert_user(db_conn, email="amit@indiamart.com", display_name=None)
    db_conn.commit()

    row = repo.find_by_email("amit@indiamart.com")
    assert row is not None
    assert row["display_name"] == "Amit Jain"


def test_find_by_email_returns_none_for_unknown(db_conn: psycopg.Connection) -> None:
    assert repo.find_by_email("nobody@example.com") is None


def test_count_users(db_conn: psycopg.Connection) -> None:
    repo.upsert_user(db_conn, email="a@example.com", display_name="A")
    repo.upsert_user(db_conn, email="b@example.com", display_name="B")
    repo.upsert_user(db_conn, email="c@example.com", display_name=None)
    db_conn.commit()

    assert repo.count_users() == 3
