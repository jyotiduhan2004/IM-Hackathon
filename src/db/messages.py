"""Repository functions for the messages table.

Replaces the raw-frontmatter scan in src.compile.compiler. The compile
state machine lives here:

  pending  ──claim──>  claimed  ──finish──>  compiled
                          │
                          ├──fail──>  failed  ──claim──>  claimed
                          │
                          └──stale (>30m)──>  re-claimable

Stale-claim recovery: if a worker crashes mid-compile, the row stays
'claimed'. The next claim cycle steals it back after stale_after_minutes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import psycopg

from src.db import connect


def insert_message(
    conn: psycopg.Connection,
    *,
    message_id: str,
    raw_path: str,
    thread_id: str | None,
    subject: str | None,
    from_address: str | None,
    date: datetime | None,
    compile_state: str = "pending",
    compiled_at: datetime | None = None,
) -> bool:
    """Upsert one message row. Returns True if inserted, False if it already existed.

    Used by backfill — INSERT ... ON CONFLICT DO NOTHING so re-running
    the script is safe.
    """
    cur = conn.execute(
        """
        INSERT INTO messages (
          message_id, raw_path, thread_id, subject, from_address, date,
          compile_state, compiled_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO NOTHING
        RETURNING message_id
        """,
        (
            message_id,
            raw_path,
            thread_id,
            subject,
            from_address,
            date,
            compile_state,
            compiled_at,
        ),
    )
    return cur.fetchone() is not None


def list_uncompiled(limit: int = 1000) -> list[dict[str, Any]]:
    """Return uncompiled messages ordered by date (oldest first).

    'pending' and 'failed' rows are returned. 'claimed' rows are NOT —
    a worker is allegedly handling them; the stale-claim sweep in
    claim_next_message will recover them if the worker died.
    """
    with connect() as conn:
        return conn.execute(
            """
            SELECT message_id, raw_path, thread_id, subject, from_address, date
              FROM messages
             WHERE compile_state IN ('pending', 'failed')
             ORDER BY date ASC NULLS LAST, message_id ASC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()


def claim_next_message(
    run_id: uuid.UUID,
    *,
    stale_after_minutes: int = 30,
) -> dict[str, Any] | None:
    """Atomically claim the oldest pending/failed/stale-claimed message.

    `FOR UPDATE SKIP LOCKED` lets multiple workers claim in parallel
    without blocking each other. Returns None when the queue is empty.
    """
    with connect() as conn, conn.transaction():
        return conn.execute(
            """
            WITH next AS (
              SELECT message_id
                FROM messages
               WHERE compile_state IN ('pending', 'failed')
                  OR (compile_state = 'claimed'
                      AND claimed_at < now() - make_interval(mins => %s))
               ORDER BY date ASC NULLS LAST, message_id ASC
               FOR UPDATE SKIP LOCKED
               LIMIT 1
            )
            UPDATE messages m
               SET compile_state = 'claimed',
                   compile_run_id = %s,
                   claimed_at = now(),
                   compile_attempts = compile_attempts + 1,
                   last_error = NULL
              FROM next
             WHERE m.message_id = next.message_id
            RETURNING m.message_id, m.raw_path, m.thread_id, m.subject,
                      m.from_address, m.date, m.compile_attempts
            """,
            (stale_after_minutes, run_id),
        ).fetchone()


def finish_message_compile(message_id: str) -> None:
    """Mark a message as successfully compiled. Idempotent."""
    with connect() as conn, conn.transaction():
        conn.execute(
            """
            UPDATE messages
               SET compile_state = 'compiled',
                   compiled_at = now(),
                   last_error = NULL
             WHERE message_id = %s
            """,
            (message_id,),
        )


def fail_message_compile(message_id: str, error: str) -> None:
    """Mark a message as failed. The next claim cycle will retry it."""
    with connect() as conn, conn.transaction():
        conn.execute(
            """
            UPDATE messages
               SET compile_state = 'failed',
                   last_error = %s
             WHERE message_id = %s
            """,
            (error, message_id),
        )


def find_by_raw_path(raw_path: str) -> dict[str, Any] | None:
    """Lookup a message by its raw markdown path. Bridge for the existing
    `mark_as_compiled(file_path)` agent tool until callers carry message_id."""
    with connect() as conn:
        return conn.execute(
            "SELECT message_id, compile_state FROM messages WHERE raw_path = %s",
            (raw_path,),
        ).fetchone()


def count_by_state() -> dict[str, int]:
    """Compile state distribution — used by backfill smoke check + tests."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT compile_state, count(*)::int AS n FROM messages GROUP BY 1"
        ).fetchall()
    return {r["compile_state"]: r["n"] for r in rows}


def remaining_uncompiled_count() -> int:
    """Cheap counter for the agent's stop signal."""
    with connect() as conn:
        row = conn.execute(
            "SELECT count(*)::int AS n FROM messages WHERE compile_state IN ('pending', 'failed')"
        ).fetchone()
    return int(row["n"]) if row else 0


def reset_to_pending() -> int:
    """Flip ALL compiled messages back to pending. Returns rowcount.

    Used by snapshot_wiki.py --reset-raw-compiled to force a full recompile
    (previously done by rewriting raw/*.md frontmatter — now DB is truth).
    """
    with connect() as conn, conn.transaction():
        cur = conn.execute(
            "UPDATE messages SET compile_state='pending', compiled_at=NULL, "
            "last_error=NULL WHERE compile_state='compiled'"
        )
        return cur.rowcount


def reset_to_pending_by_path(raw_paths: list[str]) -> int:
    """Targeted reset for a list of raw markdown paths. Returns rowcount.

    Used by backfill_stubs.py --recompile after it rewrites provenance on
    wiki pages that cite those raws.
    """
    if not raw_paths:
        return 0
    with connect() as conn, conn.transaction():
        cur = conn.execute(
            "UPDATE messages SET compile_state='pending', compiled_at=NULL, "
            "last_error=NULL WHERE raw_path = ANY(%s)",
            (list(raw_paths),),
        )
        return cur.rowcount
