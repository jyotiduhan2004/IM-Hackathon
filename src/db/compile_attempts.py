"""Repository for compile_attempts — append-only per-model invocation log.

One row per model invocation for a (message, run) pair. Written at batch
dispatch (``record_start``) so orphaned claims stay visible; stamped with
an outcome at batch completion (``record_outcome``). Used by the run-start
``_healthy_pool`` guard in ``scripts/compile_all.py`` to auto-exclude
chronically-failing models.

Replaces the lossy ``messages.compile_model`` field which was overwritten
by ``COALESCE`` on retry — failure history vanished from the catalog.

Both functions accept the connection so callers can ride the same
transaction as the surrounding compile-state UPDATE.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import structlog

import src.db as db_pkg

logger = structlog.get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS compile_attempts (
  id              bigserial PRIMARY KEY,
  message_id      text NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
  run_id          uuid REFERENCES compile_runs(run_id) ON DELETE CASCADE,
  compile_model   text,
  outcome         text CHECK (outcome IN ('compiled', 'failed', 'timeout', 'skipped')),
  error           text,
  attempted_at    timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);

CREATE INDEX IF NOT EXISTS compile_attempts_health_stats_idx
  ON compile_attempts (compile_model, attempted_at DESC)
  WHERE compile_model IS NOT NULL AND finished_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS compile_attempts_message_idx
  ON compile_attempts (message_id);

CREATE INDEX IF NOT EXISTS compile_attempts_run_idx
  ON compile_attempts (run_id);
"""


def _row_get(row: Any, key: str, fallback_index: int) -> Any:
    """Fetch a column from either dict-style or tuple-style psycopg rows."""
    if isinstance(row, dict):
        return row[key]
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[fallback_index]


def ensure_schema() -> None:
    """Create compile_attempts + indexes if this environment is behind."""
    with db_pkg.connect() as conn:
        conn.execute(_SCHEMA_SQL)
        conn.commit()


def record_start(
    conn: psycopg.Connection,
    *,
    message_id: str,
    run_id: uuid.UUID | None,
    compile_model: str | None,
) -> int:
    """Insert an in-flight attempt row. Returns the new id.

    ``outcome`` and ``finished_at`` are left NULL — ``record_outcome``
    stamps them when the batch terminates.
    """
    row = conn.execute(
        """
        INSERT INTO compile_attempts (message_id, run_id, compile_model)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (message_id, run_id, compile_model),
    ).fetchone()
    if row is None:  # pragma: no cover — unreachable if INSERT succeeds
        raise RuntimeError("INSERT compile_attempts returned no row")
    return int(_row_get(row, "id", 0))


def record_outcome(
    conn: psycopg.Connection,
    *,
    attempt_id: int,
    outcome: str,
    error: str | None = None,
) -> None:
    """Stamp ``finished_at = now()`` and the outcome on an in-flight attempt.

    ``outcome`` must be one of ``'compiled'``, ``'failed'``, ``'timeout'``
    — the DB CHECK will reject others. ``error`` is trimmed by the caller
    (typical convention: 500 chars) to avoid bloating rows.

    Logs a warning if the UPDATE matched no row — bug or race condition,
    not a hard failure (the in-flight row will eventually time out).
    """
    cur = conn.execute(
        """
        UPDATE compile_attempts
           SET outcome = %s,
               error = %s,
               finished_at = now()
         WHERE id = %s
        """,
        (outcome, error, attempt_id),
    )
    if cur.rowcount != 1:
        logger.warning(
            "compile_attempts.record_outcome no matching row",
            attempt_id=attempt_id,
            outcome=outcome,
            rowcount=cur.rowcount,
        )
