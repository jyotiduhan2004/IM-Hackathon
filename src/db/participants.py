"""Repository functions for message_participants — (message, user, role) join.

A message can have many participants; a participant can appear once per
role (`from`, `to`, `cc`). The composite primary key (message_id,
user_email, role) makes ON CONFLICT DO NOTHING the natural idempotency
guarantee for backfill.
"""

from __future__ import annotations

import psycopg

from src.db import connect

_VALID_ROLES = ("from", "to", "cc")


def insert_participant(
    conn: psycopg.Connection,
    *,
    message_id: str,
    user_email: str,
    role: str,
    display_name: str | None = None,
) -> bool:
    """Insert one participant row. Returns True when actually inserted.

    Idempotent: re-running over the same (message_id, user_email, role)
    is a no-op. The CHECK constraint on `role` will reject anything other
    than 'from'/'to'/'cc', so we don't validate on the Python side.
    """
    cur = conn.execute(
        """
        INSERT INTO message_participants (message_id, user_email, role, display_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (message_id, user_email, role) DO NOTHING
        RETURNING message_id
        """,
        (message_id, user_email, role, display_name),
    )
    return cur.fetchone() is not None


def count_participants_by_role() -> dict[str, int]:
    """Distribution of participant rows by role — used by backfill smoke check.

    Always returns all three roles (zero-filled for any role that hasn't
    appeared yet) so callers can index by name without KeyError.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, count(*)::int AS n FROM message_participants GROUP BY 1"
        ).fetchall()
    counts: dict[str, int] = dict.fromkeys(_VALID_ROLES, 0)
    for r in rows:
        counts[r["role"]] = r["n"]
    return counts
