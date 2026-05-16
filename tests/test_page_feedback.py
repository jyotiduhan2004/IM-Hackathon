"""Tests for src/db/page_feedback.py.

Covers round-trip insert/read, the DISTINCT-ON-source latest-per-source
rollup, run-scoped listing, and the severity CHECK constraint.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from uuid import UUID
from uuid import uuid4

import psycopg
import pytest
from src.db import page_feedback as repo

_VERSION = "2026-04-23T12:00:00Z"


def _insert(
    conn: psycopg.Connection,
    *,
    run_id: UUID,
    page_slug: str,
    source: str = "scorer",
    score: float | None = 5.0,
    finding: str = "f",
    severity: repo.Severity = "info",
    captured_by: str = "heuristic",
    raw_json: dict[str, Any] | None = None,
) -> int:
    """Thin wrapper so tests stay focused on the column(s) they assert on."""
    return repo.insert_feedback(
        conn,
        run_id=run_id,
        page_slug=page_slug,
        page_version=_VERSION,
        source=source,
        score=score,
        finding=finding,
        severity=severity,
        captured_by=captured_by,
        raw_json=raw_json if raw_json is not None else {},
    )


# ---------------------------------------------------------------------------
# insert_feedback — round-trip.
# ---------------------------------------------------------------------------


def test_insert_feedback_returns_id_and_persists(db_conn: psycopg.Connection) -> None:
    run_id = uuid4()
    new_id = _insert(
        db_conn,
        run_id=run_id,
        page_slug="seller-isq",
        score=7.5,
        finding="Missing Why paragraph",
        severity="warning",
        raw_json={"rule": "lead_paragraph_missing"},
    )
    db_conn.commit()

    assert new_id > 0

    rows = repo.list_feedback_by_run(db_conn, run_id=run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["page_slug"] == "seller-isq"
    assert row["source"] == "scorer"
    assert float(row["score"]) == 7.5
    assert row["severity"] == "warning"
    assert row["captured_by"] == "heuristic"
    assert row["raw_json"] == {"rule": "lead_paragraph_missing"}


def test_insert_feedback_accepts_null_score(db_conn: psycopg.Connection) -> None:
    """Judge-persona prose findings have no numeric score."""
    run_id = uuid4()
    new_id = _insert(
        db_conn,
        run_id=run_id,
        page_slug="seller-isq",
        source="judge-newbie",
        score=None,
        captured_by="newbie",
    )
    db_conn.commit()
    assert new_id > 0

    rows = repo.list_feedback_by_run(db_conn, run_id=run_id)
    assert rows[0]["score"] is None


# ---------------------------------------------------------------------------
# list_recent_feedback_for_page — DISTINCT ON (source).
# ---------------------------------------------------------------------------


def test_list_recent_returns_latest_per_source(db_conn: psycopg.Connection) -> None:
    """Two sources, two inserts each; caller sees one row per source."""
    old_run = uuid4()
    new_run = uuid4()

    _insert(db_conn, run_id=old_run, page_slug="buylead", score=4.0, finding="older scorer run")
    db_conn.commit()
    _insert(db_conn, run_id=new_run, page_slug="buylead", score=6.0, finding="newer scorer run")
    _insert(
        db_conn,
        run_id=new_run,
        page_slug="buylead",
        source="judge-pm",
        score=None,
        finding="No 'why now' framing",
        severity="warning",
        captured_by="pm",
    )
    db_conn.commit()

    rows = repo.list_recent_feedback_for_page(db_conn, page_slug="buylead")
    assert len(rows) == 2
    by_source = {r["source"]: r for r in rows}
    assert set(by_source) == {"scorer", "judge-pm"}
    # Latest scorer wins — the older 4.0 row was skipped.
    assert by_source["scorer"]["finding"] == "newer scorer run"
    assert float(by_source["scorer"]["score"]) == 6.0


def test_list_recent_unknown_slug_returns_empty(db_conn: psycopg.Connection) -> None:
    assert repo.list_recent_feedback_for_page(db_conn, page_slug="ghost-page") == []


# ---------------------------------------------------------------------------
# list_feedback_by_run.
# ---------------------------------------------------------------------------


def test_list_by_run_returns_all_rows_sorted(db_conn: psycopg.Connection) -> None:
    run_id = uuid4()
    _insert(db_conn, run_id=run_id, page_slug="zzz-page")
    _insert(db_conn, run_id=run_id, page_slug="aaa-page", source="judge-ia", score=None)
    _insert(db_conn, run_id=run_id, page_slug="aaa-page", score=8.0)
    db_conn.commit()

    rows = repo.list_feedback_by_run(db_conn, run_id=run_id)
    assert [(r["page_slug"], r["source"]) for r in rows] == [
        ("aaa-page", "judge-ia"),
        ("aaa-page", "scorer"),
        ("zzz-page", "scorer"),
    ]


def test_list_by_run_unknown_run_returns_empty(db_conn: psycopg.Connection) -> None:
    assert repo.list_feedback_by_run(db_conn, run_id=uuid4()) == []


# ---------------------------------------------------------------------------
# list_recent_feedback_by_source.
# ---------------------------------------------------------------------------


def test_list_by_source_since_filter(db_conn: psycopg.Connection) -> None:
    """`since` is a floor — rows older than it are excluded."""
    run_id = uuid4()
    _insert(db_conn, run_id=run_id, page_slug="page-a")
    db_conn.commit()

    future = datetime.now(UTC) + timedelta(days=1)
    assert repo.list_recent_feedback_by_source(db_conn, source="scorer", since=future) == []

    past = datetime.now(UTC) - timedelta(days=1)
    rows = repo.list_recent_feedback_by_source(db_conn, source="scorer", since=past)
    assert len(rows) == 1
    assert rows[0]["page_slug"] == "page-a"


def test_list_by_source_no_since_returns_all(db_conn: psycopg.Connection) -> None:
    """With `since=None` the query should not filter by captured_at."""
    run_id = uuid4()
    _insert(db_conn, run_id=run_id, page_slug="page-a")
    db_conn.commit()

    rows = repo.list_recent_feedback_by_source(db_conn, source="scorer")
    assert len(rows) == 1


def test_list_by_source_respects_limit(db_conn: psycopg.Connection) -> None:
    run_id = uuid4()
    for i in range(5):
        _insert(db_conn, run_id=run_id, page_slug=f"p-{i}", score=float(i))
    db_conn.commit()

    assert len(repo.list_recent_feedback_by_source(db_conn, source="scorer", limit=2)) == 2
    assert len(repo.list_recent_feedback_by_source(db_conn, source="scorer", limit=10)) == 5


# ---------------------------------------------------------------------------
# severity CHECK constraint.
# ---------------------------------------------------------------------------


def test_invalid_severity_violates_check(db_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(
            db_conn,
            run_id=uuid4(),
            page_slug="any",
            severity="bogus",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# list_feedback_by_run limit guard (C7).
# ---------------------------------------------------------------------------


def test_list_by_run_respects_limit(db_conn: psycopg.Connection) -> None:
    """Bulk runs must not flood the caller — limit is an opt-in floor."""
    run_id = uuid4()
    for i in range(5):
        _insert(db_conn, run_id=run_id, page_slug=f"p-{i}", score=float(i))
    db_conn.commit()

    assert len(repo.list_feedback_by_run(db_conn, run_id=run_id, limit=3)) == 3
    assert len(repo.list_feedback_by_run(db_conn, run_id=run_id, limit=100)) == 5


# ---------------------------------------------------------------------------
# list_recent_feedback_for_page default limit surfaces all 5 expected sources
# (C4 regression).
# ---------------------------------------------------------------------------


def test_list_recent_default_limit_shows_all_expected_sources(
    db_conn: psycopg.Connection,
) -> None:
    """The default limit must be big enough for scorer + 3 judges + human."""
    run_id = uuid4()
    for source in ("scorer", "judge-newbie", "judge-pm", "judge-ia", "human"):
        _insert(
            db_conn,
            run_id=run_id,
            page_slug="central-api",
            source=source,
            score=5.0 if source == "scorer" else None,
        )
    db_conn.commit()

    rows = repo.list_recent_feedback_for_page(db_conn, page_slug="central-api")
    assert {r["source"] for r in rows} == {
        "scorer",
        "judge-newbie",
        "judge-pm",
        "judge-ia",
        "human",
    }
