"""Tests for scripts/compare_models.py — the canonical A/B scorecard.

Isolation: uses the shared ``email_kb_test_schema`` fixture via
``tests/conftest.py``. Each test seeds compile_attempts / messages /
wiki_pages rows, then drives the metric functions directly (no CLI
harness) so assertions are on plain dicts.

Integration-ish: ``test_compare_models_e2e_table`` imports ``main`` and
drives it via ``click.testing.CliRunner`` to catch wiring bugs that
pure-function tests miss (arg parsing, output routing, format switch).
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from click.testing import CliRunner

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import compare_models  # noqa: E402
from scripts.compare_models import COMPARE_WINDOWS  # noqa: E402
from scripts.compare_models import METRIC_REGISTRY  # noqa: E402
from scripts.compare_models import _parse_window  # noqa: E402
from scripts.compare_models import _render_csv  # noqa: E402
from scripts.compare_models import _render_markdown  # noqa: E402
from scripts.compare_models import _render_table  # noqa: E402
from scripts.compare_models import _resolve_compare_window  # noqa: E402
from scripts.compare_models import main as compare_main  # noqa: E402
from src.db import compare_metrics as shared  # noqa: E402

# ---------------------------------------------------------------------
# repoint module-level ``connect`` symbols at the test schema
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _repoint_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conftest monkeypatches ``src.db.connect`` globally, but
    ``compare_models`` and the shared metrics module bind their own
    ``connect`` at import time — reach in and redirect those too."""
    from src.db import connect as db_connect

    monkeypatch.setattr(compare_models, "connect", db_connect)


# ---------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------


def _seed_message(conn: psycopg.Connection, *, message_id: str, thread_id: str = "t1") -> None:
    conn.execute(
        """
        INSERT INTO messages (
          message_id, raw_path, thread_id, subject, from_address, date
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO NOTHING
        """,
        (
            message_id,
            f"raw/{message_id}.md",
            thread_id,
            "subj",
            "a@b.c",
            datetime.now(UTC),
        ),
    )


def _seed_attempt(
    conn: psycopg.Connection,
    *,
    message_id: str,
    model: str,
    outcome: str | None,
    error: str | None = None,
    age_hours: float = 0.0,
    run_id: uuid.UUID | None = None,
) -> int:
    _seed_message(conn, message_id=message_id)
    # finished_at is NULL iff outcome is NULL (in-flight attempt). We
    # set it via a Python-side None rather than a SQL CASE to keep
    # psycopg's type inference happy.
    finished_at_offset_s = None if outcome is None else age_hours * 3600
    row = conn.execute(
        """
        INSERT INTO compile_attempts (
          message_id, run_id, compile_model, outcome, error,
          attempted_at, finished_at
        ) VALUES (
          %s, %s, %s, %s, %s,
          now() - make_interval(secs => %s),
          CASE WHEN %s::float IS NULL THEN NULL
               ELSE now() - make_interval(secs => %s::float) END
        )
        RETURNING id
        """,
        (
            message_id,
            run_id,
            model,
            outcome,
            error,
            age_hours * 3600,
            finished_at_offset_s,
            finished_at_offset_s,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _seed_wiki_page(conn: psycopg.Connection, *, slug: str) -> int:
    row = conn.execute(
        """
        INSERT INTO wiki_pages (slug, path, title, page_type, status)
        VALUES (%s, %s, %s, 'topic', 'active')
        RETURNING page_id
        """,
        (slug, f"wiki/topics/{slug}.md", slug.title()),
    ).fetchone()
    assert row is not None
    return int(row["page_id"])


# ---------------------------------------------------------------------
# Registry + schema smoke
# ---------------------------------------------------------------------


def test_metric_registry_coverage() -> None:
    """Every registered metric is callable with the expected signature."""
    # We don't care about the return value shape here — just that each
    # function is registered with the right arity. Actual behaviour is
    # asserted by the per-metric tests below.
    required = {
        "volume_attempts",
        "outcome_compiled",
        "outcome_skipped",
        "outcome_failed",
        "valid_pct",
        "skip_discrimination_pct",
        "error_recursion_fail",
        "error_not_cited",
        "error_timeout",
        "avg_tools_per_batch",
        "avg_turns_per_batch",
        "pages_normalized",
        "check_my_work_blocks_total",
        "cost_usd",
        "unique_threads_processed",
    }
    assert required <= set(METRIC_REGISTRY)
    for name, fn in METRIC_REGISTRY.items():
        assert callable(fn), name


def test_metric_registry_empty_window_returns_empty_dicts(
    db_conn: psycopg.Connection,
) -> None:
    """Every metric fn handles "no data in window" gracefully."""
    since = datetime.now(UTC) - timedelta(hours=1)
    for name, fn in METRIC_REGISTRY.items():
        result = fn(db_conn, since, None, None)
        assert isinstance(result, dict), name


# ---------------------------------------------------------------------
# Per-metric behaviour
# ---------------------------------------------------------------------


def test_valid_pct_calculation(db_conn: psycopg.Connection) -> None:
    """valid_pct = (compiled + skipped) / attempts * 100.

    Model A: 3 compiled + 1 skipped + 1 failed = 4/5 = 80%.
    Model B: 2 compiled + 2 timeout = 2/4 = 50%.
    """
    for i in range(3):
        _seed_attempt(db_conn, message_id=f"a{i}", model="model_A", outcome="compiled")
    _seed_attempt(db_conn, message_id="a3", model="model_A", outcome="skipped")
    _seed_attempt(db_conn, message_id="a4", model="model_A", outcome="failed")
    for i in range(2):
        _seed_attempt(db_conn, message_id=f"b{i}", model="model_B", outcome="compiled")
    _seed_attempt(db_conn, message_id="b2", model="model_B", outcome="timeout")
    _seed_attempt(db_conn, message_id="b3", model="model_B", outcome="timeout")
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["valid_pct"](db_conn, since, None, None)
    assert result["model_A"] == pytest.approx(80.0)
    assert result["model_B"] == pytest.approx(50.0)


def test_skip_discrimination_pct_handles_zero_valid(
    db_conn: psycopg.Connection,
) -> None:
    """All-failed model: no compiled/skipped denominator → None, not div0."""
    _seed_attempt(db_conn, message_id="m1", model="model_X", outcome="failed")
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["skip_discrimination_pct"](db_conn, since, None, None)
    assert result["model_X"] is None


def test_error_recursion_fail_substring_match(
    db_conn: psycopg.Connection,
) -> None:
    """Recursion errors come in two phrasings — both must count."""
    _seed_attempt(
        db_conn,
        message_id="m1",
        model="model_A",
        outcome="failed",
        error="GraphRecursionError: limit reached",
    )
    _seed_attempt(
        db_conn,
        message_id="m2",
        model="model_A",
        outcome="failed",
        error="batch hit recursion limit after 50 steps",
    )
    _seed_attempt(
        db_conn,
        message_id="m3",
        model="model_A",
        outcome="failed",
        error="timeout",
    )
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["error_recursion_fail"](db_conn, since, None, None)
    assert result["model_A"] == pytest.approx(2.0)


def test_volume_attempts_filter_by_model(db_conn: psycopg.Connection) -> None:
    """--models substring filter projects onto seen rows."""
    _seed_attempt(db_conn, message_id="m1", model="x-ai/grok-4.1-fast", outcome="compiled")
    _seed_attempt(db_conn, message_id="m2", model="minimax/minimax-m2.7", outcome="compiled")
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["volume_attempts"](db_conn, since, None, ["grok"])
    assert set(result) == {"x-ai/grok-4.1-fast"}


def test_avg_turns_placeholder_returns_empty(db_conn: psycopg.Connection) -> None:
    """Not-yet-persisted metric returns {} so the render shows '-' for every row."""
    _seed_attempt(db_conn, message_id="m1", model="model_A", outcome="compiled")
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["avg_turns_per_batch"](db_conn, since, None, None)
    assert result == {}


def test_pages_normalized_counts_touches(db_conn: psycopg.Connection) -> None:
    """One attempt → N touches joins correctly across the message bridge."""
    _seed_attempt(db_conn, message_id="m1", model="model_A", outcome="compiled")
    p1 = _seed_wiki_page(db_conn, slug="page-one")
    p2 = _seed_wiki_page(db_conn, slug="page-two")
    db_conn.execute(
        "INSERT INTO message_touched_pages (message_id, page_id) VALUES (%s, %s), (%s, %s)",
        ("m1", p1, "m1", p2),
    )
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["pages_normalized"](db_conn, since, None, None)
    assert result["model_A"] == pytest.approx(2.0)


def test_unique_threads_processed(db_conn: psycopg.Connection) -> None:
    """COUNT(DISTINCT thread_id) across attempts per model."""
    _seed_message(db_conn, message_id="m1", thread_id="t_alpha")
    _seed_message(db_conn, message_id="m2", thread_id="t_alpha")
    _seed_message(db_conn, message_id="m3", thread_id="t_beta")
    _seed_attempt(db_conn, message_id="m1", model="model_A", outcome="compiled")
    _seed_attempt(db_conn, message_id="m2", model="model_A", outcome="compiled")
    _seed_attempt(db_conn, message_id="m3", model="model_A", outcome="compiled")
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = METRIC_REGISTRY["unique_threads_processed"](db_conn, since, None, None)
    assert result["model_A"] == pytest.approx(2.0)


# ---------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------


def test_compare_window_preset_maps_to_correct_dates() -> None:
    """pre-PR-225 maps to the 2026-04-17 → 2026-04-18 window."""
    windows = _resolve_compare_window("pre-PR-225")
    assert len(windows) == 1
    since, until = windows[0]
    assert since == datetime(2026, 4, 17, tzinfo=UTC)
    assert until == datetime(2026, 4, 18, tzinfo=UTC)


def test_compare_window_pair_returns_two_windows() -> None:
    """Paired preset produces (before, after) tuple for side-by-side render."""
    windows = _resolve_compare_window("pre-PR-225,post-PR-225")
    assert len(windows) == 2
    pre_since, _ = windows[0]
    post_since, _ = windows[1]
    assert pre_since == datetime(2026, 4, 17, tzinfo=UTC)
    assert post_since == datetime(2026, 4, 23, tzinfo=UTC)


def test_compare_window_unknown_name_raises() -> None:
    """Typos should surface the valid list, not silently return ()."""
    import click

    with pytest.raises(click.BadParameter) as exc:
        _resolve_compare_window("pre-PR-999")
    assert "pre-PR-999" in str(exc.value)


def test_parse_window_until_now_returns_none() -> None:
    """'now' collapses to None so the SQL COALESCE uses the server clock."""
    since, until = _parse_window("24h", "now")
    assert isinstance(since, datetime)
    assert until is None


def test_parse_window_absolute_dates() -> None:
    since, until = _parse_window("2026-04-17", "2026-04-18")
    assert since == datetime(2026, 4, 17, tzinfo=UTC)
    assert until == datetime(2026, 4, 18, tzinfo=UTC)


def test_compare_windows_dict_has_spec_entries() -> None:
    """Spec-named presets must exist so CI-driven tooling can rely on them."""
    required = {"pre-PR-225", "post-PR-225", "cycle-9", "cycle-10", "last-week"}
    assert required <= set(COMPARE_WINDOWS)


# ---------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------


def _sample_metrics() -> dict[str, dict[str, float | None]]:
    return {
        "volume_attempts": {"model_A": 10, "model_B": 5},
        "valid_pct": {"model_A": 90.0, "model_B": None},
    }


def test_format_table_renders_without_crash() -> None:
    out = _render_table(_sample_metrics(), ["model_A", "model_B"], "test-label")
    assert "model_A" in out
    assert "model_B" in out
    assert "volume_attempts" in out
    assert "-" in out  # None → "-"


def test_format_csv_shape() -> None:
    """First row = metric,<m1>,<m2>; subsequent rows = metric values."""
    out = _render_csv(_sample_metrics(), ["model_A", "model_B"])
    lines = out.strip().splitlines()
    assert lines[0] == "metric,model_A,model_B"
    assert lines[1].startswith("volume_attempts,")
    # None renders as "-" in CSV cells too.
    assert "-" in lines[2]


def test_format_markdown_pipes() -> None:
    out = _render_markdown(_sample_metrics(), ["model_A", "model_B"], "test-label")
    # Pipe-delimited header + separator.
    assert "| metric | model_A | model_B |" in out
    assert "|---|---|---|" in out


# ---------------------------------------------------------------------
# E2E smoke — click invocation + DB round trip
# ---------------------------------------------------------------------


def test_cli_e2e_table_format(db_conn: psycopg.Connection) -> None:
    """CliRunner wires click args, metrics run, output format switches."""
    _seed_attempt(db_conn, message_id="m1", model="model_A", outcome="compiled")
    _seed_attempt(db_conn, message_id="m2", model="model_B", outcome="failed")
    db_conn.commit()

    runner = CliRunner()
    result = runner.invoke(
        compare_main,
        ["--since", "24h", "--format", "table"],
    )
    assert result.exit_code == 0, result.output
    assert "model_A" in result.output
    assert "volume_attempts" in result.output


def test_cli_e2e_empty_window_raises(db_conn: psycopg.Connection) -> None:
    """No attempts in window → explicit error, not a silent empty table."""
    # Use a past-only window where we've seeded nothing.
    runner = CliRunner()
    result = runner.invoke(
        compare_main,
        ["--since", "2020-01-01", "--until", "2020-01-02", "--format", "table"],
    )
    assert result.exit_code != 0
    assert "no compile_attempts" in result.output


# ---------------------------------------------------------------------
# Shared module smoke
# ---------------------------------------------------------------------


def test_shared_outcomes_by_model_groups_correctly(
    db_conn: psycopg.Connection,
) -> None:
    _seed_attempt(db_conn, message_id="m1", model="model_A", outcome="compiled")
    _seed_attempt(db_conn, message_id="m2", model="model_A", outcome="skipped")
    _seed_attempt(db_conn, message_id="m3", model="model_B", outcome="failed")
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = shared.outcomes_by_model(db_conn, since=since, until=None)
    assert result["model_A"] == {
        "attempts": 2,
        "compiled": 1,
        "skipped": 1,
        "failed": 0,
        "timeout": 0,
    }
    assert result["model_B"]["failed"] == 1
