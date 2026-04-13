"""Repository functions for the ingest_cursors table.

Durable replacement for `.watch_state.json` — one row per named ingest
loop (`gmail_history` today). `history_id` is the opaque resume token
the source API exposes; callers don't need to know whether it's a Gmail
historyId, an ISO timestamp, or anything else.

Keeping the surface deliberately tiny (read + upsert) — there's only one
writer at a time (the watcher) so we don't need locking or versioning.
"""

from __future__ import annotations

from typing import Any

from src.db import connect


def read_cursor(name: str) -> dict[str, Any] | None:
    """Return `{cursor_name, history_id, updated_at}` for `name`, or None."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT cursor_name, history_id, updated_at
              FROM ingest_cursors
             WHERE cursor_name = %s
            """,
            (name,),
        ).fetchone()


def write_cursor(name: str, history_id: str) -> None:
    """Upsert the cursor's history_id. Idempotent on re-run with same value."""
    with connect() as conn, conn.transaction():
        conn.execute(
            """
            INSERT INTO ingest_cursors (cursor_name, history_id)
            VALUES (%s, %s)
            ON CONFLICT (cursor_name) DO UPDATE
               SET history_id = EXCLUDED.history_id
            """,
            (name, history_id),
        )
