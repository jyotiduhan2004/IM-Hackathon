"""Repository functions for the users table.

One row per distinct email address. `display_name` is best-effort — Gmail
gives us different display names across messages (`Amit Jain` vs
`Amit Jain (IM)`); upsert keeps the most recently observed non-null value.
"""

from __future__ import annotations

from typing import Any

import psycopg

from src.db import connect


def upsert_user(
    conn: psycopg.Connection,
    *,
    email: str,
    display_name: str | None = None,
) -> bool:
    """Insert or refresh a user row. Returns True if the row was created.

    On conflict we keep the existing row but overwrite `display_name` when
    a non-null value comes in — the latest header tends to be the most
    descriptive. `last_seen_at` / `first_seen_at` are populated by the
    backfill once it knows the message dates.
    """
    cur = conn.execute(
        """
        INSERT INTO users (email, display_name)
        VALUES (%s, %s)
        ON CONFLICT (email) DO UPDATE
          SET display_name = COALESCE(EXCLUDED.display_name, users.display_name)
        RETURNING (xmax = 0) AS inserted
        """,
        (email, display_name),
    )
    row = cur.fetchone()
    return bool(row and row["inserted"])


def find_by_email(email: str) -> dict[str, Any] | None:
    """Lookup a user row by email. Returns None when the user is unknown."""
    with connect() as conn:
        return conn.execute(
            "SELECT email, display_name, first_seen_at, last_seen_at FROM users WHERE email = %s",
            (email,),
        ).fetchone()


def count_users() -> int:
    """Total number of distinct users — used by backfill smoke checks."""
    with connect() as conn:
        row = conn.execute("SELECT count(*)::int AS n FROM users").fetchone()
    return int(row["n"]) if row else 0
