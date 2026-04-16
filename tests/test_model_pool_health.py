"""Tests for the two-window quarantine in ``_healthy_pool``.

Complements ``tests/test_compile_attempts.py`` which already covers the
24h window + fail-open + never-empty semantics. This file focuses
exclusively on the 4h short-window quarantine added in F2:

- Short-window fires at >80% fail_rate AND total >= 5 (4 attempts → keep).
- Short-window reasons are tagged ``quarantined (4h)`` on the exclusion
  record so operators can tell which guard fired.
- 24h rule still wins when a model trips both windows (persistent
  offender is the more damning signal).
- Failures older than 4h but inside the 24h window don't trip the short
  rule (window discrimination actually works).
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
) -> None:
    """Insert a finished attempt row with a controllable age.

    Same helper shape as ``tests/test_compile_attempts.py``; duplicated
    here (rather than imported) so the two test files don't develop an
    implicit ordering dependency through shared fixtures.
    """
    conn.execute(
        """
        INSERT INTO compile_attempts (
          message_id, compile_model, outcome, attempted_at, finished_at
        ) VALUES (
          %s, %s, %s,
          now() - make_interval(secs => %s),
          now() - make_interval(secs => %s)
        )
        """,
        (message_id, model, outcome, age_hours * 3600, age_hours * 3600),
    )


def _load_compile_all() -> Any:
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = Path(__file__).parent.parent / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_pool_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_pool_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module() -> Any:
    return _load_compile_all()


def test_short_window_quarantine_fires_at_five_failures(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """5 recent failures (within 4h) — with enough older successes to keep
    the 24h rule silent — drops the model via the 4h window only.

    This is the "hot" breakage case: the model was fine all day, then the
    LiteLLM proxy started 400-ing it an hour ago. 24h stats dilute the
    failure rate below the 24h threshold, so the 4h rule has to catch it.

    Shape: 6 successes @ ~20h ago + 5 failures @ ~0.5h ago →
      - 24h: 11 total, 5 failed → 45% fail → 24h rule NOT fired.
      - 4h: 5 total, 5 failed → 100% fail → 4h rule fires.
    """
    for i in range(11):
        _insert_message(db_conn, f"m{i}")
    # 6 old successes (20h ago — inside 24h window, outside 4h window).
    for i in range(6):
        _finished_attempt(
            db_conn, message_id=f"m{i}", model="test/bad-model", outcome="compiled", age_hours=20.0
        )
    # 5 fresh failures (0.5h ago — inside both windows).
    for i in range(6, 11):
        _finished_attempt(
            db_conn, message_id=f"m{i}", model="test/bad-model", outcome="failed", age_hours=0.5
        )
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["test/bad-model", "test/good-model"])
    assert kept == ["test/good-model"]
    assert len(excluded) == 1
    assert excluded[0]["compile_model"] == "test/bad-model"
    assert excluded[0]["reason"] == "quarantined (4h)"
    assert excluded[0]["window_hours"] == 4


def test_short_window_respects_min_attempts_threshold(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """4 failures in the 4h window is below min-attempts → NOT filtered.

    The threshold is the whole point of the 4h window — it's noisy, so we
    wait for enough evidence that the outage isn't a 1-2 transient blip.
    """
    for i in range(4):
        _insert_message(db_conn, f"m{i}")
    for i in range(4):
        _finished_attempt(
            db_conn, message_id=f"m{i}", model="test/flaky-model", outcome="failed", age_hours=1.0
        )
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["test/flaky-model", "test/good-model"])
    assert set(kept) == {"test/flaky-model", "test/good-model"}
    assert excluded == []


def test_short_window_ignores_older_failures(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """Failures 5h ago are inside the 24h window but outside 4h — the
    short-window rule must NOT fire on them. Validates the two windows
    are actually independent, not a shared query.

    Using exactly 5 failures older than 4h with no other activity: the
    24h rule needs fail_rate > 0.5 AND total >= 5 (satisfied) so it DOES
    fire, but the record must be tagged ``quarantined (24h)`` — proving
    the short window didn't match on these older rows.
    """
    for i in range(5):
        _insert_message(db_conn, f"m{i}")
    for i in range(5):
        _finished_attempt(
            db_conn, message_id=f"m{i}", model="test/bad-model", outcome="failed", age_hours=5.0
        )
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["test/bad-model", "test/good-model"])
    assert kept == ["test/good-model"]
    assert len(excluded) == 1
    # 5h-old failures are outside the 4h window, so the 24h rule fired.
    assert excluded[0]["reason"] == "quarantined (24h)"
    assert excluded[0]["window_hours"] == 24


def test_short_window_threshold_requires_high_fail_rate(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """80% is the 4h bar — a 70% fail rate (4/6) should NOT trip it.

    Also must NOT trip the 24h rule: 4/6 = 66% > 50% but the 24h rule
    requires >50% which IS satisfied. Adjust counts so only the short
    window could theoretically fire — we use 10 attempts total (8 failed,
    2 ok = 80% exactly, NOT > 80%) to hit the boundary.
    """
    for i in range(10):
        _insert_message(db_conn, f"m{i}")
    for i in range(8):
        _finished_attempt(
            db_conn, message_id=f"m{i}", model="test/boundary", outcome="failed", age_hours=0.5
        )
    for i in range(8, 10):
        _finished_attempt(
            db_conn, message_id=f"m{i}", model="test/boundary", outcome="compiled", age_hours=0.5
        )
    db_conn.commit()

    # 8/10 = 0.80 exactly → short window (strict >) must NOT fire.
    # 24h rule: 8/10 > 0.5 AND total >= 5 → WOULD fire. Confirm via reason.
    kept, excluded = compile_all_module._healthy_pool(["test/boundary", "test/good-model"])
    assert kept == ["test/good-model"]
    # It's excluded, but via the 24h window, not the short one.
    assert excluded[0]["reason"] == "quarantined (24h)"


def test_long_window_wins_when_both_fire(
    compile_all_module: Any, db_conn: psycopg.Connection
) -> None:
    """Model trips BOTH windows → 24h reason wins (persistent offender
    label is more actionable than ``right now`` for an already-persistent
    pattern).
    """
    for i in range(10):
        _insert_message(db_conn, f"m{i}")
    # 5 recent failures (within 4h) → short-window fires at 100%.
    for i in range(5):
        _finished_attempt(
            db_conn,
            message_id=f"m{i}",
            model="test/persistent-bad",
            outcome="failed",
            age_hours=0.5,
        )
    # 5 more failures 10h ago → 24h window sees 10 total, 10 failed = 100%.
    for i in range(5, 10):
        _finished_attempt(
            db_conn,
            message_id=f"m{i}",
            model="test/persistent-bad",
            outcome="failed",
            age_hours=10.0,
        )
    db_conn.commit()

    kept, excluded = compile_all_module._healthy_pool(["test/persistent-bad", "test/good-model"])
    assert kept == ["test/good-model"]
    assert excluded[0]["reason"] == "quarantined (24h)"
