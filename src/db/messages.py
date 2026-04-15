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


def list_uncompiled_with_filters(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    thread_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return uncompiled messages matching optional filters.

    Filters are ANDed; omitted filters are not applied. Uses ILIKE for
    sender_contains/subject_contains. Still limited to 'pending' and 'failed'
    states like list_uncompiled. Parameterized SQL — no string interpolation.
    """
    conditions: list[str] = ["compile_state IN ('pending', 'failed')"]
    params: list[Any] = []
    if date_from is not None:
        conditions.append("date >= %s::date")
        params.append(date_from)
    if date_to is not None:
        # `messages.date` is TIMESTAMPTZ; casting YYYY-MM-DD to ::date yields
        # midnight, so `<=` silently excludes emails later that same day
        # (e.g. date_to=2026-04-01 would drop a 15:00 row). Use an exclusive
        # next-day bound so the upper bound is genuinely inclusive.
        conditions.append("date < (%s::date + interval '1 day')")
        params.append(date_to)
    if sender_contains is not None:
        conditions.append("from_address ILIKE '%' || %s || '%'")
        params.append(sender_contains)
    if subject_contains is not None:
        conditions.append("subject ILIKE '%' || %s || '%'")
        params.append(subject_contains)
    if thread_id is not None:
        conditions.append("thread_id = %s")
        params.append(thread_id)
    sql = (
        "SELECT message_id, raw_path, thread_id, subject, from_address, date\n"
        "  FROM messages\n"
        f" WHERE {' AND '.join(conditions)}\n"
        " ORDER BY date ASC NULLS LAST, message_id ASC\n"
        " LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])
    with connect() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


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


def finish_message_compile(message_id: str, compile_model: str | None = None) -> None:
    """Mark a message as successfully compiled. Idempotent.

    `compile_model` records which model in the A/B pool produced this
    compile. Pass None for the legacy single-model flow (column stays
    NULL — won't break any existing reports).
    """
    with connect() as conn, conn.transaction():
        conn.execute(
            """
            UPDATE messages
               SET compile_state = 'compiled',
                   compiled_at = now(),
                   last_error = NULL,
                   compile_model = COALESCE(%s, compile_model)
             WHERE message_id = %s
            """,
            (compile_model, message_id),
        )


def fail_message_compile(message_id: str, error: str, compile_model: str | None = None) -> None:
    """Mark a message as failed. The next claim cycle will retry it.

    Records the model that failed so the A/B rollup can score
    failure rates per model.
    """
    with connect() as conn, conn.transaction():
        conn.execute(
            """
            UPDATE messages
               SET compile_state = 'failed',
                   last_error = %s,
                   compile_model = COALESCE(%s, compile_model)
             WHERE message_id = %s
            """,
            (error, compile_model, message_id),
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


# TODO(refactor): move to src/db/compile_attempts.py once the import graph
# tolerates it. Kept here for this PR to minimize churn on the compile_all.py
# side (already imports from src.db.messages).
def model_health_stats(*, since_hours: int = 24) -> list[dict[str, Any]]:
    """Per-model attempt outcomes over the last ``since_hours``.

    Used by ``scripts/compile_all.py::_healthy_pool`` at run-start to drop
    models that have been failing consistently. Sourced from the
    append-only ``compile_attempts`` table because ``messages.compile_model``
    is overwritten by ``COALESCE`` on retry and so loses failure history.

    Returns a list of dicts: ``{compile_model, total, failed, fail_rate}``.
    Only counts attempts where ``finished_at IS NOT NULL`` (excludes
    in-flight rows that haven't resolved yet). ``timeout`` outcomes are
    counted alongside ``failed`` — they're both "the model didn't
    produce a usable compile".
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT compile_model,
                   count(*)::int AS total,
                   count(*) FILTER (
                     WHERE outcome IN ('failed', 'timeout')
                   )::int AS failed
              FROM compile_attempts
             WHERE compile_model IS NOT NULL
               AND finished_at IS NOT NULL
               AND attempted_at >= now() - make_interval(hours => %s)
             GROUP BY compile_model
            """,
            (since_hours,),
        ).fetchall()
    return [
        {
            "compile_model": r["compile_model"],
            "total": r["total"],
            "failed": r["failed"],
            "fail_rate": (r["failed"] / r["total"]) if r["total"] else 0.0,
        }
        for r in rows
    ]
