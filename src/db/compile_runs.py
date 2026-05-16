"""Repository functions for the compile_runs table.

One row per `scripts/compile_all.py` batch invocation. Gives run-level
observability (cost, counts, status) without the `.watch_state.json`-style
sidecar files that Codex flagged. See
`docs/reviews/codex-priority-review-20260413T090000Z.md` §1 PR5.

Lifecycle:

  start_run()  ──>  row status='running'
                        │
                        ├──loop finishes──>  finish_run(status='completed')
                        │
                        ├──exception─────>  finish_run(status='failed')
                        │
                        └──KeyboardInterrupt─>  finish_run(status='killed')
"""

from __future__ import annotations

import uuid
from typing import Any

from src.db import connect


def start_run(model: str | None = None, notes: str | None = None) -> uuid.UUID:
    """Insert a new row in 'running' state and return the generated run_id.

    The DB assigns `run_id` via `gen_random_uuid()` and `started_at` via
    `DEFAULT now()`.
    """
    with connect() as conn, conn.transaction():
        row = conn.execute(
            """
            INSERT INTO compile_runs (model, notes)
            VALUES (%s, %s)
            RETURNING run_id
            """,
            (model, notes),
        ).fetchone()
    # `row` is a dict_row because src.db.connect defaults to dict rows.
    assert row is not None  # INSERT ... RETURNING always produces a row
    return uuid.UUID(str(row["run_id"]))


def finish_run(
    run_id: uuid.UUID,
    status: str,
    emails_processed: int,
    emails_failed: int,
    cost_cents: int | None = None,
) -> None:
    """Mark a run finished — sets finished_at + status + counts.

    Called from a `finally:` block in the compile loop, so it runs on
    success, crash, or KeyboardInterrupt. Invalid status values are
    rejected by the CHECK constraint (psycopg raises).
    """
    with connect() as conn, conn.transaction():
        conn.execute(
            """
            UPDATE compile_runs
               SET status = %s,
                   finished_at = now(),
                   emails_processed = %s,
                   emails_failed = %s,
                   cost_cents = %s
             WHERE run_id = %s
            """,
            (status, emails_processed, emails_failed, cost_cents, run_id),
        )


def list_recent(limit: int = 10) -> list[dict[str, Any]]:
    """Return the N most recent runs, newest first."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT run_id, started_at, finished_at, model, status,
                   emails_processed, emails_failed, cost_cents, notes
              FROM compile_runs
             ORDER BY started_at DESC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()
