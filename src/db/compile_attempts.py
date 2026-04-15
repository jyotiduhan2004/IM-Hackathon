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

import psycopg


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
    assert row is not None  # INSERT ... RETURNING always produces a row
    return int(row["id"])


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
    """
    conn.execute(
        """
        UPDATE compile_attempts
           SET outcome = %s,
               error = %s,
               finished_at = now()
         WHERE id = %s
        """,
        (outcome, error, attempt_id),
    )
