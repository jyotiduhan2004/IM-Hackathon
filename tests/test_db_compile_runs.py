"""Tests for the compile_runs catalog repo functions (src/db/compile_runs.py).

Isolation: see tests/conftest.py. Each test runs against the dedicated
`email_kb_test_schema` schema and starts with an empty compile_runs table.
"""

from __future__ import annotations

import time
import uuid

import psycopg
import pytest
from src.db import compile_runs as repo


def test_start_run_returns_uuid_and_row_is_running(db_conn: psycopg.Connection) -> None:
    run_id = repo.start_run(model="gpt-4o-mini", notes="limit=1 batch_size=1")

    assert isinstance(run_id, uuid.UUID)

    row = db_conn.execute(
        "SELECT * FROM compile_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "running"
    assert row["model"] == "gpt-4o-mini"
    assert row["notes"] == "limit=1 batch_size=1"
    assert row["emails_processed"] == 0
    assert row["emails_failed"] == 0
    assert row["cost_cents"] is None
    assert row["finished_at"] is None
    assert row["started_at"] is not None


def test_finish_run_updates_status_counts_and_finished_at(
    db_conn: psycopg.Connection,
) -> None:
    run_id = repo.start_run(model="gpt-4o")

    repo.finish_run(
        run_id,
        status="completed",
        emails_processed=42,
        emails_failed=3,
        cost_cents=1250,
    )

    row = db_conn.execute(
        "SELECT * FROM compile_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "completed"
    assert row["emails_processed"] == 42
    assert row["emails_failed"] == 3
    assert row["cost_cents"] == 1250
    assert row["finished_at"] is not None


def test_finish_run_allows_null_cost(db_conn: psycopg.Connection) -> None:
    # Budget fetch may fail (proxy down) — cost_cents must remain NULL-able.
    run_id = repo.start_run()
    repo.finish_run(
        run_id,
        status="failed",
        emails_processed=0,
        emails_failed=0,
        cost_cents=None,
    )

    row = db_conn.execute(
        "SELECT status, cost_cents FROM compile_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["cost_cents"] is None


def test_list_recent_orders_by_started_at_desc() -> None:
    # Insert three runs with small sleeps so started_at is strictly ordered.
    ids: list[uuid.UUID] = []
    for _ in range(3):
        ids.append(repo.start_run(model="m"))
        # 10ms is enough for TIMESTAMPTZ now() to advance on Postgres.
        time.sleep(0.01)

    rows = repo.list_recent(limit=10)
    ordered_ids = [row["run_id"] for row in rows]
    # Newest-first: reversed insertion order.
    assert ordered_ids == list(reversed(ids))


def test_list_recent_respects_limit() -> None:
    for _ in range(5):
        repo.start_run()
        time.sleep(0.01)

    rows = repo.list_recent(limit=2)
    assert len(rows) == 2


def test_finish_run_invalid_status_raises_check_violation() -> None:
    run_id = repo.start_run()
    # Any status outside the CHECK set is rejected by Postgres.
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.finish_run(
            run_id,
            status="bogus",
            emails_processed=0,
            emails_failed=0,
        )
