"""Unit tests for Tier A telemetry signals in the scorecard + audit scripts.

Pure-function tests — they construct synthetic trace payloads matching
the shape Langfuse returns and exercise the extractors / summarizers
directly. No DB, no Langfuse CLI, no subprocess.

The three signals under test are passive: before Tier A's
PathAutoHealMiddleware / reviewer subagent / todo nudging land, every
trace should score all-off (False/None/False) and the aggregate rates
should be zero — the scorecard must not break on pre-Tier-A data.

Also covers the E3 migration metrics
(``pages_migrated_per_run`` / ``migration_inflight_pct``) which are
DB-backed — those tests use the ``db_conn`` fixture and seed wiki_pages
rows directly.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

import psycopg
import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import trace_scorecard  # noqa: E402
from scripts.nightly_trace_audit import _extract_tier_a_signals  # noqa: E402
from scripts.nightly_trace_audit import _score_trace  # noqa: E402
from scripts.nightly_trace_audit import _summarize  # noqa: E402
from scripts.trace_scorecard import Attempt  # noqa: E402
from scripts.trace_scorecard import TraceMetrics  # noqa: E402
from scripts.trace_scorecard import _build_row  # noqa: E402
from scripts.trace_scorecard import _extract_trace_metrics  # noqa: E402
from scripts.trace_scorecard import _fmt_verdicts  # noqa: E402
from scripts.trace_scorecard import _migration_inflight_pct  # noqa: E402
from scripts.trace_scorecard import _pages_migrated_per_run  # noqa: E402


def _mk_tool_obs(
    name: str, output: str = "", level: str = "DEFAULT", inputs: str = ""
) -> dict[str, Any]:
    return {"type": "TOOL", "name": name, "output": output, "level": level, "input": inputs}


def _mk_trace(observations: list[dict[str, Any]], trace_id: str = "t0") -> dict[str, Any]:
    return {
        "body": {
            "id": trace_id,
            "metadata": {"compile_model": "m"},
            "observations": observations,
        }
    }


# ----------------------- extraction: scorecard -----------------------


def test_scorecard_extract_all_signals_present() -> None:
    """write_todos at index 0, auto-correct annotation, verdict=revise."""
    trace = _mk_trace(
        [
            _mk_tool_obs("write_todos", "[]"),
            _mk_tool_obs(
                "read_file",
                "(auto_corrected_from='/.claude/raw/x.md' -> auto_corrected_to='/raw/x.md')",
            ),
            _mk_tool_obs("task", '{"verdict": "revise", "blockers": []}'),
        ]
    )
    m = _extract_trace_metrics(trace)
    assert m.auto_corrected is True
    assert m.reviewer_verdict == "revise"
    assert m.wrote_todos_early is True


def test_scorecard_extract_passive_defaults() -> None:
    """No annotations anywhere — all three stay at their off value."""
    trace = _mk_trace(
        [
            _mk_tool_obs("ls", "[]"),
            _mk_tool_obs("read_file", "raw content"),
            _mk_tool_obs("write_file", "ok"),
        ]
    )
    m = _extract_trace_metrics(trace)
    assert m.auto_corrected is False
    assert m.reviewer_verdict is None
    assert m.wrote_todos_early is False


def test_scorecard_extract_auto_correct_in_input_side() -> None:
    """If middleware annotates the input dict (not output), still detected."""
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "read_file",
                output="file contents",
                inputs="auto_corrected_from='/.claude/raw/x.md'",
            ),
        ]
    )
    m = _extract_trace_metrics(trace)
    assert m.auto_corrected is True


def test_scorecard_fmt_verdicts_all_zero_renders_dash() -> None:
    """All-zero counts mean reviewer hasn't run yet — render '—' not 'p=0…'."""
    assert _fmt_verdicts({"pass": 0, "revise": 0, "block": 0, "none": 0}) == "—"
    assert _fmt_verdicts({}) == "—"
    assert _fmt_verdicts(None) == "—"
    assert _fmt_verdicts({"pass": 1, "revise": 0, "block": 0, "none": 0}) != "—"


def test_scorecard_extract_write_todos_outside_window() -> None:
    """write_todos at index 3 doesn't count as 'early'."""
    trace = _mk_trace(
        [
            _mk_tool_obs("ls"),
            _mk_tool_obs("read_file"),
            _mk_tool_obs("read_file"),
            _mk_tool_obs("write_todos", "[]"),  # index 3
        ]
    )
    m = _extract_trace_metrics(trace)
    assert m.wrote_todos_early is False


def test_scorecard_extract_write_todos_at_boundary() -> None:
    """Fencepost: write_todos at index 2 (last position inside window) counts."""
    trace = _mk_trace(
        [
            _mk_tool_obs("ls"),
            _mk_tool_obs("read_file"),
            _mk_tool_obs("write_todos", "[]"),  # index 2 — boundary
        ]
    )
    m = _extract_trace_metrics(trace)
    assert m.wrote_todos_early is True


def test_scorecard_extract_first_verdict_wins() -> None:
    """Reviewer can run twice; we record only the first verdict."""
    trace = _mk_trace(
        [
            _mk_tool_obs("task", '{"verdict": "block"}'),
            _mk_tool_obs("task", '{"verdict": "pass"}'),
        ]
    )
    m = _extract_trace_metrics(trace)
    assert m.reviewer_verdict == "block"


def test_scorecard_extract_verdict_case_insensitive() -> None:
    """Verdict matching is case-insensitive but normalized to lower."""
    trace = _mk_trace([_mk_tool_obs("task", '{"verdict": "PASS"}')])
    m = _extract_trace_metrics(trace)
    assert m.reviewer_verdict == "pass"


# ----------------------- aggregation: scorecard ----------------------


def test_scorecard_build_row_aggregate_rates() -> None:
    """Three traces: 2 auto-corrected, 2 wrote-early, mixed verdicts."""
    model = "test-model"
    attempts = [
        Attempt(
            message_id=f"m{i}",
            run_id=uuid.uuid4(),
            thread_id=f"t{i}",
            compile_model=model,
            outcome="compiled",
        )
        for i in range(3)
    ]
    traces = [
        TraceMetrics(
            trace_id="tr1",
            model=model,
            tool_calls=4,
            auto_corrected=True,
            reviewer_verdict="pass",
            wrote_todos_early=True,
        ),
        TraceMetrics(
            trace_id="tr2",
            model=model,
            tool_calls=5,
            auto_corrected=False,
            reviewer_verdict="revise",
            wrote_todos_early=False,
        ),
        TraceMetrics(
            trace_id="tr3",
            model=model,
            tool_calls=6,
            auto_corrected=True,
            reviewer_verdict=None,
            wrote_todos_early=True,
        ),
    ]
    row = _build_row(model, attempts, traces)
    assert abs(row.auto_correction_rate - 2 / 3) < 1e-9
    assert abs(row.todo_adoption_rate - 2 / 3) < 1e-9
    assert row.reviewer_verdicts_dist == {
        "pass": 1,
        "revise": 1,
        "block": 0,
        "none": 1,
    }


def test_scorecard_build_row_no_traces_zeros() -> None:
    """No traces → all rates are zero, dist is all-zero."""
    attempts = [
        Attempt(
            message_id="m1",
            run_id=None,
            thread_id=None,
            compile_model="m",
            outcome=None,
        )
    ]
    row = _build_row("m", attempts, [])
    assert row.auto_correction_rate == 0.0
    assert row.todo_adoption_rate == 0.0
    assert row.reviewer_verdicts_dist == {
        "pass": 0,
        "revise": 0,
        "block": 0,
        "none": 0,
    }


def test_fmt_verdicts_compact_render() -> None:
    assert _fmt_verdicts({"pass": 4, "revise": 1, "block": 0, "none": 2}) == "p=4 r=1 b=0 n=2"
    assert _fmt_verdicts(None) == "—"
    assert _fmt_verdicts({}) == "—"


# ----------------------- nightly audit -------------------------------


def test_audit_extract_tier_a_signals_matches_scorecard() -> None:
    """The audit's helper should produce the same signals as the scorecard."""
    obs = [
        _mk_tool_obs("write_todos", "[]"),
        _mk_tool_obs("read_file", "auto_corrected_from=blah"),
        _mk_tool_obs("task", '{"verdict": "block"}'),
    ]
    auto, verdict, todos = _extract_tier_a_signals(obs)
    assert auto is True
    assert verdict == "block"
    assert todos is True


def test_audit_extract_tier_a_signals_input_side_auto_correct() -> None:
    """If middleware annotates input rather than output, audit also detects."""
    obs = [
        _mk_tool_obs("read_file", output="contents", inputs="auto_corrected_from='/.claude/x.md'"),
    ]
    auto, _verdict, _todos = _extract_tier_a_signals(obs)
    assert auto is True


def test_audit_extract_tier_a_signals_skips_unnamed_tool_events() -> None:
    """Unnamed TOOL observations don't shift the write_todos window position.

    Mirrors `_extract_trace_metrics` in scorecard, so audit and scorecard
    agree on `wrote_todos_early` for malformed traces (Codex P2 on #98).
    """
    obs = [
        {"type": "TOOL", "name": "", "output": "", "input": ""},  # unnamed
        {"type": "TOOL", "name": "", "output": "", "input": ""},  # unnamed
        _mk_tool_obs("write_todos", "[]"),  # would be index 2 if unnamed counted
    ]
    _auto, _verdict, todos = _extract_tier_a_signals(obs)
    # Unnamed events skipped → write_todos at index 0 → counted as early
    assert todos is True


def test_skip_null_name_tool_events_in_both_paths() -> None:
    """JSON null `name` (vs missing key) must also be treated as unnamed.

    Without `obs.get("name") or ""`, str(None) = "None" (truthy) bypasses
    the unnamed-skip guard. Codex P2 (round 2) on #98.
    """
    null_name_obs = {"type": "TOOL", "name": None, "output": "", "input": ""}
    obs = [
        dict(null_name_obs),
        dict(null_name_obs),
        _mk_tool_obs("write_todos", "[]"),  # would be index 2 if null counted
    ]
    # Scorecard
    trace = _mk_trace(obs)
    m = _extract_trace_metrics(trace)
    assert m.wrote_todos_early is True
    assert m.tool_calls == 1  # null-name observations not counted
    # Audit
    _auto, _verdict, todos = _extract_tier_a_signals(obs)
    assert todos is True


def test_audit_score_trace_populates_signals() -> None:
    """_score_trace surfaces the signals on the TraceRubric."""
    trace = _mk_trace(
        [
            _mk_tool_obs("write_todos", "[]"),
            _mk_tool_obs("task", '{"verdict": "pass"}'),
        ]
    )
    trace["body"]["output"] = "## Summary\nTL;DR\nok"
    rubric = _score_trace(trace, tool_call_median=3.0)
    assert rubric["auto_corrected"] is False
    assert rubric["reviewer_verdict"] == "pass"
    assert rubric["wrote_todos_early"] is True


def test_audit_score_trace_fetch_error_defaults_to_off() -> None:
    """A trace that failed to fetch should have all signals set to off."""
    rubric = _score_trace({"tid": "x", "error": "timeout"}, tool_call_median=3.0)
    assert rubric["auto_corrected"] is False
    assert rubric["reviewer_verdict"] is None
    assert rubric["wrote_todos_early"] is False
    assert rubric["grade"] == "F"


def test_audit_summarize_emits_tier_a_rates() -> None:
    """Summary emits all three Tier A fields aggregated over the list."""
    rubrics = [
        _score_trace(
            _mk_trace(
                [
                    _mk_tool_obs("write_todos"),
                    _mk_tool_obs("read_file", "auto_corrected_from=x"),
                    _mk_tool_obs("task", '{"verdict": "pass"}'),
                ],
                trace_id="a",
            ),
            tool_call_median=3.0,
        ),
        _score_trace(
            _mk_trace(
                [
                    _mk_tool_obs("ls"),
                    _mk_tool_obs("task", '{"verdict": "revise"}'),
                ],
                trace_id="b",
            ),
            tool_call_median=3.0,
        ),
        _score_trace(_mk_trace([_mk_tool_obs("ls")], trace_id="c"), tool_call_median=3.0),
    ]
    summary = _summarize(rubrics)
    # 1/3 auto-corrected, 1/3 early-todos, verdicts: pass=1 revise=1 none=1
    assert abs(summary["auto_correction_rate"] - 1 / 3) < 1e-9
    assert abs(summary["todo_adoption_rate"] - 1 / 3) < 1e-9
    assert summary["reviewer_verdicts_dist"] == {
        "pass": 1,
        "revise": 1,
        "block": 0,
        "none": 1,
    }


def test_audit_summarize_empty_list_safe() -> None:
    """No rubrics → rates are 0, dist is all zero. No div-by-zero."""
    summary = _summarize([])
    assert summary["auto_correction_rate"] == 0.0
    assert summary["todo_adoption_rate"] == 0.0
    assert summary["reviewer_verdicts_dist"] == {
        "pass": 0,
        "revise": 0,
        "block": 0,
        "none": 0,
    }
    # E3: migration fields default to None when caller didn't supply them.
    assert summary["pages_migrated_per_run"] is None
    assert summary["migration_inflight_pct"] is None


def test_audit_summarize_accepts_migration_metrics() -> None:
    """The audit summary surfaces the DB-derived migration metrics."""
    summary = _summarize(
        [],
        pages_migrated_per_run=12,
        migration_inflight_pct=0.37,
    )
    assert summary["pages_migrated_per_run"] == 12
    assert summary["migration_inflight_pct"] == pytest.approx(0.37)


# ----------------------- E3 migration metrics ------------------------


@pytest.fixture(autouse=True)
def _repoint_scorecard_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repoint ``trace_scorecard.connect`` at the test-schema connect.

    The scorecard does ``from src.db import connect`` at module load, so
    the conftest's ``monkeypatch.setattr(db_pkg, "connect", …)`` doesn't
    reach it — the binding is already captured. Without this the DB
    queries fall through to the production schema and find unrelated
    rows. Harmless for the non-DB tests in this file.
    """
    from src.db import connect as db_connect

    monkeypatch.setattr(trace_scorecard, "connect", db_connect)


def _seed_wiki_page(
    conn: psycopg.Connection,
    *,
    slug: str,
    page_type: str,
    status: str,
    updated_at: datetime | None = None,
) -> None:
    """Insert a wiki_pages row, optionally backdating ``updated_at``.

    Direct INSERT (not ``upsert_wiki_page``) because the BEFORE UPDATE
    trigger ``wiki_pages_set_updated_at`` rewrites ``updated_at`` to
    ``now()``, defeating any post-insert UPDATE. INSERT fires the same
    trigger but most variants fire BEFORE UPDATE only; the schema.sql
    here uses BEFORE UPDATE so an explicit ``updated_at`` on INSERT is
    preserved.
    """
    conn.execute(
        """
        INSERT INTO wiki_pages
          (slug, path, title, page_type, status, updated_at)
        VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
        """,
        (
            slug,
            f"wiki/{page_type}s/{slug}.md",
            slug.replace("-", " ").title(),
            page_type,
            status,
            updated_at,
        ),
    )
    conn.commit()


def test_pages_migrated_per_run_counts_new_ontology_updates(
    db_conn: psycopg.Connection,
) -> None:
    """Pages with new-ontology page_type + active/archived status in-window count."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    # In-window, new ontology — counted (3 matching rows).
    _seed_wiki_page(db_conn, slug="authn", page_type="domain", status="active")
    _seed_wiki_page(db_conn, slug="whatsapp-api", page_type="decision", status="archived")
    _seed_wiki_page(db_conn, slug="glossary-one", page_type="glossary", status="active")
    # In-window, legacy ontology — NOT counted.
    _seed_wiki_page(db_conn, slug="old-topic", page_type="topic", status="current")
    _seed_wiki_page(db_conn, slug="alice", page_type="entity", status="current")
    # Out-of-window, new ontology — NOT counted.
    _seed_wiki_page(
        db_conn,
        slug="ancient-domain",
        page_type="domain",
        status="active",
        updated_at=now - timedelta(days=3),
    )
    # New ontology but status='current' — NOT counted; migration flips status too.
    _seed_wiki_page(db_conn, slug="weird-person", page_type="person", status="current")

    assert _pages_migrated_per_run(cutoff) == 3


def test_pages_migrated_per_run_zero_when_only_legacy(db_conn: psycopg.Connection) -> None:
    """Before any migration ships, everything is legacy → metric stays 0."""
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    _seed_wiki_page(db_conn, slug="topic-a", page_type="topic", status="current")
    _seed_wiki_page(db_conn, slug="alice", page_type="entity", status="current")
    _seed_wiki_page(db_conn, slug="system-b", page_type="system", status="current")
    assert _pages_migrated_per_run(cutoff) == 0


def test_pages_migrated_per_run_db_failure_returns_none() -> None:
    """Hard query error → None, not a crash; caller renders as ``—``."""
    # Simulate connect() raising by pointing it at a broken URL via mock.
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(side_effect=psycopg.OperationalError("boom"))
    mock_ctx.__exit__ = MagicMock(return_value=False)
    with patch.object(trace_scorecard, "connect", return_value=mock_ctx):
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        assert _pages_migrated_per_run(cutoff) is None


def test_migration_inflight_pct_exact_ratio(db_conn: psycopg.Connection) -> None:
    """legacy / total = 2 / 5 → 40% inflight.

    Legacy = status='current' OR page_type='entity'.
    """
    # 2 legacy rows.
    _seed_wiki_page(db_conn, slug="legacy-topic", page_type="topic", status="current")
    _seed_wiki_page(db_conn, slug="alice", page_type="entity", status="active")
    # 3 new-ontology, non-legacy rows.
    _seed_wiki_page(db_conn, slug="authn", page_type="domain", status="active")
    _seed_wiki_page(db_conn, slug="bob-person", page_type="person", status="archived")
    _seed_wiki_page(db_conn, slug="migrations", page_type="decision", status="active")

    # 2 legacy / 5 total = 0.4
    assert _migration_inflight_pct() == pytest.approx(0.4)


def test_migration_inflight_pct_legacy_union_counts_entity_even_when_active(
    db_conn: psycopg.Connection,
) -> None:
    """Entity pages count as legacy even with status='active' — ontology, not status.

    Encodes the "status=current OR page_type=entity" rule so a migration
    that flips an entity row's status but doesn't rename the page_type
    still registers as "inflight".
    """
    _seed_wiki_page(db_conn, slug="alice", page_type="entity", status="active")
    _seed_wiki_page(db_conn, slug="new-domain", page_type="domain", status="active")
    # 1/2 legacy because alice's page_type keeps her in the legacy bucket.
    assert _migration_inflight_pct() == pytest.approx(0.5)


def test_migration_inflight_pct_empty_table_returns_zero(
    db_conn: psycopg.Connection,
) -> None:
    """Empty wiki_pages → 0.0 (no division by zero)."""
    # db_conn fixture wipes wiki_pages before each test, so the table
    # is empty without any seeding here.
    assert _migration_inflight_pct() == 0.0


def test_migration_inflight_pct_db_failure_returns_none() -> None:
    """Hard query error → None, not a crash."""
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(side_effect=psycopg.OperationalError("boom"))
    mock_ctx.__exit__ = MagicMock(return_value=False)
    with patch.object(trace_scorecard, "connect", return_value=mock_ctx):
        assert _migration_inflight_pct() is None


def test_migration_inflight_pct_matches_plan_example(db_conn: psycopg.Connection) -> None:
    """Plan's E3 e2e recipe: legacy=700, total=1000 → 70%.

    Scaled down to (legacy=7, total=10) for speed — same ratio.
    """
    for i in range(7):
        _seed_wiki_page(db_conn, slug=f"legacy-{i}", page_type="topic", status="current")
    for i in range(3):
        _seed_wiki_page(db_conn, slug=f"new-{i}", page_type="domain", status="active")
    assert _migration_inflight_pct() == pytest.approx(0.7)
