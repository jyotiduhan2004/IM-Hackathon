"""Repository for compile_insights — agent meta-observations.

One row per call to `src/compile/compiler.py::log_insight`. Gives the agent
a channel to flag ambiguity, supersession doubts, structural suggestions,
etc. for later human review. `scripts/compile_all.py` surfaces the
top-N at batch-end in `wiki/log.md`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.db import connect


def record(
    *,
    run_id: UUID | None,
    category: str,
    message: str,
    email_path: str | None = None,
    suggested_action: str | None = None,
) -> int:
    """Insert one insight row. Returns the new id.

    Raises psycopg.errors.CheckViolation on invalid category — the caller
    (`log_insight` tool) validates first so the agent gets a structured
    error, but the DB CHECK is the durable guard.
    """
    with connect() as conn, conn.transaction():
        row = conn.execute(
            """
            INSERT INTO compile_insights (
              run_id, category, message, email_path, suggested_action
            ) VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, category, message, email_path, suggested_action),
        ).fetchone()
    return int(row["id"]) if row else 0


def list_for_run(
    run_id: UUID, limit: int = 50, *, since_id: int | None = None
) -> list[dict[str, Any]]:
    """Return insights for a given compile run, newest first.

    When `since_id` is supplied, only rows with `id > since_id` are returned —
    lets the batch digest show only the current batch's new insights instead
    of re-echoing every insight from previous batches in the run.
    """
    with connect() as conn:
        if since_id is not None:
            return conn.execute(
                """
                SELECT id, category, message, email_path, suggested_action, created_at
                  FROM compile_insights
                 WHERE run_id = %s AND id > %s
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                (run_id, since_id, limit),
            ).fetchall()
        return conn.execute(
            """
            SELECT id, category, message, email_path, suggested_action, created_at
              FROM compile_insights
             WHERE run_id = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (run_id, limit),
        ).fetchall()


def max_id_for_run(run_id: UUID) -> int:
    """Return the largest `id` recorded for this run, or 0 if none. Used as a
    cursor so a batch digest can show only rows that landed during this batch.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM compile_insights WHERE run_id = %s",
            (run_id,),
        ).fetchone()
    return int(row["max_id"]) if row else 0
