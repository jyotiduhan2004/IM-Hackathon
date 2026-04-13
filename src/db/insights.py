"""Repository for compile_insights — agent meta-observations.

One row per call to `src/compile/compiler.py::log_insight`. Gives the agent
a channel to flag ambiguity, supersession doubts, structural suggestions,
etc. for later human review. `scripts/compile_all.py` surfaces the
top-N at batch-end in `wiki/log.md`.
"""

from __future__ import annotations

from typing import Any

from src.db import connect


def record(
    *,
    run_id: str | None,
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


def list_for_run(run_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return insights for a given compile run, newest first."""
    with connect() as conn:
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
