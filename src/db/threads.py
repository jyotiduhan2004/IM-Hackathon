"""Repository functions for the threads table.

One row per Gmail thread_id. The `first_message_at` / `last_message_at` /
`message_count` aggregates can be set directly at upsert time (during
backfill, where we already have all the dates in hand) or recomputed
later via `update_thread_aggregates` once new messages arrive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg

from src.db import connect


def upsert_thread(
    conn: psycopg.Connection,
    *,
    thread_id: str,
    first_message_at: datetime | None = None,
    last_message_at: datetime | None = None,
) -> bool:
    """Insert or refresh a thread row. Returns True when created.

    On conflict we widen the (first, last) window — earliest first_message_at
    and latest last_message_at win — so backfilling out of order still
    converges to the right values.
    """
    cur = conn.execute(
        """
        INSERT INTO threads (thread_id, first_message_at, last_message_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (thread_id) DO UPDATE
          SET first_message_at = LEAST(
                threads.first_message_at, EXCLUDED.first_message_at
              ),
              last_message_at = GREATEST(
                threads.last_message_at, EXCLUDED.last_message_at
              )
        RETURNING (xmax = 0) AS inserted
        """,
        (thread_id, first_message_at, last_message_at),
    )
    row = cur.fetchone()
    return bool(row and row["inserted"])


def update_thread_aggregates(thread_id: str) -> dict[str, Any] | None:
    """Recompute first/last message timestamps + count from messages.

    Used after bulk inserts and as the source-of-truth refresh after new
    messages land. Returns the updated row, or None if the thread has no
    messages (count stays 0, timestamps stay null).
    """
    with connect() as conn, conn.transaction():
        return conn.execute(
            """
            UPDATE threads t
               SET first_message_at = agg.first_at,
                   last_message_at = agg.last_at,
                   message_count = agg.n
              FROM (
                SELECT min(date) AS first_at,
                       max(date) AS last_at,
                       count(*)::int AS n
                  FROM messages
                 WHERE thread_id = %s
              ) AS agg
             WHERE t.thread_id = %s
            RETURNING t.thread_id, t.first_message_at, t.last_message_at, t.message_count
            """,
            (thread_id, thread_id),
        ).fetchone()


def count_threads() -> int:
    """Total number of threads — used by backfill smoke checks."""
    with connect() as conn:
        row = conn.execute("SELECT count(*)::int AS n FROM threads").fetchone()
    return int(row["n"]) if row else 0
