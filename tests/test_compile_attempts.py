"""Tests for compile_attempts catalog repo + _healthy_pool auto-exclusion.

Isolation: see tests/conftest.py. Each test runs against the dedicated
`email_kb_test_schema` schema and starts with an empty compile_attempts
table. `_healthy_pool` tests also exercise `model_health_stats` since the
guard is the only consumer.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
import src.db as db_pkg
from src.db import compile_attempts as repo
from src.db import compile_runs as runs_repo
from src.db.messages import model_health_stats

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _insert_message(conn: psycopg.Connection, message_id: str) -> None:
    conn.execute(
        """
        INSERT INTO messages (
          message_id, raw_path, thread_id, subject, from_address, date
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            message_id,
            f"raw/{message_id}.md",
            "t1",
            "subj",
            "a@b.c",
            datetime.now(UTC),
        ),
    )


def _finished_attempt(
    conn: psycopg.Connection,
    *,
    message_id: str,
    model: str,
    outcome: str,
    age_hours: float = 0.0,
) -> int:
    """Insert a finished attempt row directly (bypassing record_start/outcome)
    so we can control ``attempted_at`` precisely for window-scan tests.

    ``age_hours`` shifts both ``attempted_at`` and ``finished_at`` backwards
    by that many hours (so a 25h-old row is outside a 24h window).
    """
    row = conn.execute(
        """
        INSERT INTO compile_attempts (
          message_id, compile_model, outcome, attempted_at, finished_at
        ) VALUES (
          %s, %s, %s,
          now() - make_interval(secs => %s),
          now() - make_interval(secs => %s)
        )
        RETURNING id
        """,
        (message_id, model, outcome, age_hours * 3600, age_hours * 3600),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _load_compile_all() -> Any:
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = Path(__file__).parent.parent / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module() -> Any:
    return _load_compile_all()


# ---------------------------------------------------------------------------
# record_start / record_outcome
# ---------------------------------------------------------------------------


def test_record_start_returns_id_with_null_outcome(db_conn: psycopg.Connection) -> None:
    _insert_message(db_conn, "m1")
    db_conn.commit()

    run_id = runs_repo.start_run(model="m")
    with db_pkg.connect() as conn:
        attempt_id = repo.record_start(
            conn,
            message_id="m1",
            run_id=run_id,
            compile_model="minimax/minimax-m2.7",
        )
        conn.commit()

    assert attempt_id > 0
    row = db_conn.execute(
        "SELECT outcome, finished_at, compile_model FROM compile_attempts WHERE id = %s",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    assert row["outcome"] is None
    assert row["finished_at"] is None
    assert row["compile_model"] == "minimax/minimax-m2.7"


def test_record_outcome_stamps_finished_at(db_conn: psycopg.Connection) -> None:
    _insert_message(db_conn, "m1")
    db_conn.commit()

    with db_pkg.connect() as conn:
        attempt_id = repo.record_start(
            conn,
            message_id="m1",
            run_id=None,
            compile_model="z-ai/glm-5",
        )
        conn.commit()

    with db_pkg.connect() as conn:
        repo.record_outcome(conn, attempt_id=attempt_id, outcome="compiled")
        conn.commit()

    row = db_conn.execute(
        "SELECT outcome, finished_at, error FROM compile_attempts WHERE id = %s",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    assert row["outcome"] == "compiled"
    assert row["finished_at"] is not None
    assert row["error"] is None


def test_record_outcome_persists_error(db_conn: psycopg.Connection) -> None:
    _insert_message(db_conn, "m1")
    db_conn.commit()

    with db_pkg.connect() as conn:
        attempt_id = repo.record_start(
            conn,
            message_id="m1",
            run_id=None,
            compile_model="z-ai/glm-5",
        )
        conn.commit()

    with db_pkg.connect() as conn:
        repo.record_outcome(
            conn,
            attempt_id=attempt_id,
            outcome="failed",
            error="recursion limit hit",
        )
        conn.commit()

    row = db_conn.execute(
        "SELECT outcome, error FROM compile_attempts WHERE id = %s",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    assert row["outcome"] == "failed"
    assert row["error"] == "recursion limit hit"


def test_record_outcome_invalid_raises_check_violation(db_conn: psycopg.Connection) -> None:
    _insert_message(db_conn, "m1")
    db_conn.commit()

    with db_pkg.connect() as conn:
        attempt_id = repo.record_start(
            conn,
            message_id="m1",
            run_id=None,
            compile_model="m",
        )
        conn.commit()

    with pytest.raises(psycopg.errors.CheckViolation), db_pkg.connect() as conn:
        repo.record_outcome(conn, attempt_id=attempt_id, outcome="invalid")
        conn.commit()


def test_record_outcome_warns_on_missing_attempt_id(
    db_conn: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bug or race: UPDATE matched no row → warn but don't raise.

    structlog routes through its own pipeline (not stdlib `caplog`) so we
    assert against captured stdout instead.
    """
    with db_pkg.connect() as conn:
        repo.record_outcome(conn, attempt_id=999_999, outcome="compiled")
        conn.commit()
    out = capsys.readouterr().out
    assert "compile_attempts.record_outcome no matching row" in out
    # `attempt_id` and `999999` are emitted as kv pairs, ANSI-colored in
    # interactive runs. Don't tie the assertion to terminal escape codes.
    assert "999999" in out


# ---------------------------------------------------------------------------
# model_health_stats
# ---------------------------------------------------------------------------


def test_model_health_stats_counts_only_finished(db_conn: psycopg.Connection) -> None:
    """In-flight attempts (outcome IS NULL) must be excluded from the
    health rollup — otherwise a stuck worker inflates totals."""
    _insert_message(db_conn, "m1")
    _insert_message(db_conn, "m2")
    _insert_message(db_conn, "m3")
    # One in-flight (NULL outcome, NULL finished_at).
    db_conn.execute(
        """
        INSERT INTO compile_attempts (message_id, compile_model)
        VALUES (%s, %s)
        """,
        ("m1", "model_X"),
    )
    _finished_attempt(db_conn, message_id="m2", model="model_X", outcome="compiled")
    _finished_attempt(db_conn, message_id="m3", model="model_X", outcome="failed")
    db_conn.commit()

    stats = model_health_stats(since_hours=24)
    by_model = {s["compile_model"]: s for s in stats}
    assert by_model["model_X"]["total"] == 2
    assert by_model["model_X"]["failed"] == 1
    assert by_model["model_X"]["fail_rate"] == 0.5


def test_model_health_stats_timeout_counts_as_failure(db_conn: psycopg.Connection) -> None:
    """Timeout is functionally a failure from the pool-health perspective
    — the model didn't produce a usable compile."""
    _insert_message(db_conn, "m1")
    _insert_message(db_conn, "m2")
    _finished_attempt(db_conn, message_id="m1", model="model_X", outcome="timeout")
    _finished_attempt(db_conn, message_id="m2", model="model_X", outcome="compiled")
    db_conn.commit()

    stats = model_health_stats(since_hours=24)
    by_model = {s["compile_model"]: s for s in stats}
    assert by_model["model_X"]["total"] == 2
    assert by_model["model_X"]["failed"] == 1


def test_model_health_stats_respects_window(db_conn: psycopg.Connection) -> None:
    _insert_message(db_conn, "m1")
    _insert_message(db_conn, "m2")
    # 25h ago → outside the 24h window.
    _finished_attempt(db_conn, message_id="m1", model="model_X", outcome="failed", age_hours=25)
    # 1h ago → inside the window.
    _finished_attempt(db_conn, message_id="m2", model="model_X", outcome="compiled", age_hours=1)
    db_conn.commit()

    stats = model_health_stats(since_hours=24)
    by_model = {s["compile_model"]: s for s in stats}
    assert by_model["model_X"]["total"] == 1
    assert by_model["model_X"]["failed"] == 0


def test_model_health_stats_ignores_null_compile_model(db_conn: psycopg.Connection) -> None:
    """Legacy rows with NULL compile_model (pre-A/B) must not show up in
    health stats as a phantom 'None' model."""
    _insert_message(db_conn, "m1")
    db_conn.execute(
        """
        INSERT INTO compile_attempts (
          message_id, compile_model, outcome, finished_at
        ) VALUES (%s, NULL, 'compiled', now())
        """,
        ("m1",),
    )
    db_conn.commit()

    stats = model_health_stats(since_hours=24)
    assert stats == []


# ---------------------------------------------------------------------------
# _healthy_pool (uses model_health_stats)
# ---------------------------------------------------------------------------


def test_healthy_pool_drops_high_fail_rate_model(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """fail_rate > 0.5 AND total ≥ 5 → drop."""
    for i in range(6):
        _insert_message(db_conn, f"m{i}")
    # 4 failures, 2 successes → 67% fail rate, total 6.
    for i in range(4):
        _finished_attempt(db_conn, message_id=f"m{i}", model="model_A", outcome="failed")
    for i in (4, 5):
        _finished_attempt(db_conn, message_id=f"m{i}", model="model_A", outcome="compiled")
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["model_A", "model_B"])
    assert kept == ["model_B"]
    assert len(excluded) == 1
    assert excluded[0]["compile_model"] == "model_A"


def test_healthy_pool_drops_on_absolute_failure_cap(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """failed ≥ 10 drops the model regardless of fail_rate — covers models
    that failed 10 of 50 calls (20%) but clearly have a systemic issue
    with enough volume to matter."""
    # 10 fails + 50 successes → ~17% fail rate but absolute cap hit.
    for i in range(60):
        _insert_message(db_conn, f"m{i}")
    for i in range(10):
        _finished_attempt(db_conn, message_id=f"m{i}", model="model_A", outcome="failed")
    for i in range(10, 60):
        _finished_attempt(db_conn, message_id=f"m{i}", model="model_A", outcome="compiled")
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["model_A"])
    assert kept == ["model_A"]  # would empty → falls open
    assert len(excluded) == 1  # still reports what it WOULD have dropped

    kept, excluded = compile_all_module._healthy_pool(["model_A", "model_B"])
    assert kept == ["model_B"]
    assert excluded and excluded[0]["compile_model"] == "model_A"


def test_healthy_pool_keeps_low_attempt_model(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """total < 5 is too little data to exclude, even at 100% fail rate —
    we wait until we have enough evidence that it's not a blip."""
    for i in range(4):
        _insert_message(db_conn, f"m{i}")
    for i in range(4):
        _finished_attempt(db_conn, message_id=f"m{i}", model="model_A", outcome="failed")
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["model_A", "model_B"])
    assert set(kept) == {"model_A", "model_B"}
    assert excluded == []


def test_healthy_pool_fails_open_on_db_error(
    compile_all_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DB blip during run-start must not brick compile — return original pool."""

    def _boom(**_kwargs: Any) -> list[dict[str, Any]]:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(compile_all_module, "model_health_stats", _boom)
    kept, excluded = compile_all_module._healthy_pool(["model_A", "model_B"])
    assert kept == ["model_A", "model_B"]
    assert excluded == []


def test_healthy_pool_never_empties_pool(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """If every model would be excluded, fall open — a compile with a
    legit-but-flaky model beats a compile that can't pick any model.

    Uses the same 6 message rows for both models since ``compile_attempts``
    has no unique constraint on ``(message_id, compile_model)`` — only on
    its own ``id``.
    """
    for i in range(6):
        _insert_message(db_conn, f"m{i}")
    for mdl in ("model_A", "model_B"):
        for i in range(6):
            _finished_attempt(db_conn, message_id=f"m{i}", model=mdl, outcome="failed")
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["model_A", "model_B"])
    # Empty result → fall open to full pool.
    assert set(kept) == {"model_A", "model_B"}
    # But we still report what we'd have dropped, so the operator sees it.
    assert len(excluded) == 2
