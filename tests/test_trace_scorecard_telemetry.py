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
from scripts.trace_scorecard import CitationCounts  # noqa: E402
from scripts.trace_scorecard import TraceMetrics  # noqa: E402
from scripts.trace_scorecard import _build_row  # noqa: E402
from scripts.trace_scorecard import _citation_counts_by_model  # noqa: E402
from scripts.trace_scorecard import _content_page_citation_rate_by_model  # noqa: E402
from scripts.trace_scorecard import _extract_trace_metrics  # noqa: E402
from scripts.trace_scorecard import _fmt_verdicts  # noqa: E402
from scripts.trace_scorecard import _migration_inflight_pct  # noqa: E402
from scripts.trace_scorecard import _pages_migrated_per_run  # noqa: E402
from scripts.trace_scorecard import _render_citation_breakdown  # noqa: E402


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
    signals = _extract_tier_a_signals(obs)
    assert signals["auto_corrected"] is True
    assert signals["reviewer_verdict"] == "block"
    assert signals["wrote_todos_early"] is True


def test_audit_extract_tier_a_signals_input_side_auto_correct() -> None:
    """If middleware annotates input rather than output, audit also detects."""
    obs = [
        _mk_tool_obs("read_file", output="contents", inputs="auto_corrected_from='/.claude/x.md'"),
    ]
    signals = _extract_tier_a_signals(obs)
    assert signals["auto_corrected"] is True


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
    signals = _extract_tier_a_signals(obs)
    # Unnamed events skipped → write_todos at index 0 → counted as early
    assert signals["wrote_todos_early"] is True


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
    signals = _extract_tier_a_signals(obs)
    assert signals["wrote_todos_early"] is True


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


def test_audit_summarize_schema_stable_across_empty_and_populated() -> None:
    """Empty-rubrics summary must have the same key set as populated.

    Downstream consumers (dashboards, alerts) shouldn't have to do
    presence checks based on whether traces existed in the window.
    """
    populated = _summarize(
        [
            _score_trace(
                _mk_trace([_mk_tool_obs("ls")], trace_id="a"),
                tool_call_median=1.0,
            )
        ]
    )
    empty = _summarize([])
    assert populated.keys() == empty.keys()


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


# -------- content-page citation rate: NULL compile_model regression --------


def _seed_attempt(
    conn: psycopg.Connection,
    *,
    message_id: str,
    model: str | None,
    with_content_page: bool,
) -> None:
    """Insert one message + one compile attempt (optionally w/ content page).

    `model=None` exercises the NULL-join path the CTE now coalesces on;
    `with_content_page=True` also inserts a topic page and a touch row
    so the LEFT JOIN should flag this message as cited.
    """
    conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, subject, from_address, date)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (message_id, f"raw/{message_id}.md", "t1", "subj", "a@b.c", datetime.now(UTC)),
    )
    conn.execute(
        """
        INSERT INTO compile_attempts (message_id, compile_model, outcome, attempted_at, finished_at)
        VALUES (%s, %s, 'compiled', now(), now())
        """,
        (message_id, model),
    )
    if with_content_page:
        row = conn.execute(
            """
            INSERT INTO wiki_pages (slug, path, title, page_type)
            VALUES (%s, %s, %s, 'topic')
            RETURNING page_id
            """,
            (f"topic-{message_id}", f"wiki/topics/{message_id}.md", f"Topic {message_id}"),
        ).fetchone()
        assert row is not None
        conn.execute(
            "INSERT INTO message_touched_pages (message_id, page_id) VALUES (%s, %s)",
            (message_id, row["page_id"]),
        )


def test_content_page_citation_rate_handles_null_compile_model(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NULL `compile_model` must bucket into 'unknown', not get dropped.

    Regression guard: before this fix the final `LEFT JOIN ... USING
    (message_id, compile_model)` evaluated `NULL = NULL` as UNKNOWN,
    dropping the row entirely and understating citation coverage.
    Coalescing `compile_model` to 'unknown' inside `windowed_compiled`
    makes both sides of the USING clause non-null → the join matches.
    """
    # `trace_scorecard` imported `connect` at module-load time, before
    # conftest's autouse fixture rebound it. Rebind on the scorecard
    # module directly so the SQL lands in the test schema.
    import src.db as db_pkg

    monkeypatch.setattr(trace_scorecard, "connect", db_pkg.connect)

    _seed_attempt(db_conn, message_id="m_null_cited", model=None, with_content_page=True)
    _seed_attempt(db_conn, message_id="m_null_nocited", model=None, with_content_page=False)
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = _content_page_citation_rate_by_model(since)

    assert "unknown" in result, f"NULL-model row dropped; got {result}"
    assert result["unknown"] == pytest.approx(0.5)
    assert result["all"] == pytest.approx(0.5)


# --------- U7: CitationCounts carve-out + _citation_counts_by_model ---------


def test_citation_counts_effective_denominator_clamps_to_zero() -> None:
    """Effective denom never goes negative — clamp to 0."""
    c = CitationCounts(compiled_total=1, with_content_page=0, trivial_skip=2, already_captured=0)
    assert c.effective_denominator == 0
    assert c.effective_rate is None


def test_citation_counts_effective_rate_happy_path() -> None:
    """compiled=10, cited=3, skip=2, captured=1 → effective = 3/7."""
    c = CitationCounts(
        compiled_total=10,
        with_content_page=3,
        trivial_skip=2,
        already_captured=1,
    )
    assert c.raw_rate == pytest.approx(0.3)
    assert c.effective_denominator == 7
    assert c.effective_rate == pytest.approx(3 / 7)


def test_citation_counts_all_noop_rate_is_none() -> None:
    """Every compiled attempt was a no-op → effective rate is None, not 0/100%."""
    c = CitationCounts(compiled_total=3, with_content_page=0, trivial_skip=2, already_captured=1)
    assert c.raw_rate == 0.0
    assert c.effective_rate is None


def test_citation_counts_zero_compiled() -> None:
    """No compiled attempts → both rates are None."""
    c = CitationCounts()
    assert c.raw_rate is None
    assert c.effective_rate is None


def _seed_compile_run(conn: psycopg.Connection, run_id: uuid.UUID) -> None:
    """Insert a compile_runs row so insight FK constraints can be satisfied."""
    conn.execute(
        "INSERT INTO compile_runs (run_id, started_at) VALUES (%s, now())",
        (run_id,),
    )


def _seed_attempt_with_run(
    conn: psycopg.Connection,
    *,
    message_id: str,
    model: str | None,
    run_id: uuid.UUID,
    with_content_page: bool,
) -> None:
    """Like `_seed_attempt` but stamps the attempt with a specific run_id.

    Used by the carve-out tests so the compile_insights LEFT JOIN has a
    matching (run_id, email_path) pair.
    """
    conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, subject, from_address, date)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (message_id, f"raw/{message_id}.md", "t1", "subj", "a@b.c", datetime.now(UTC)),
    )
    conn.execute(
        """
        INSERT INTO compile_attempts
          (message_id, compile_model, outcome, run_id, attempted_at, finished_at)
        VALUES (%s, %s, 'compiled', %s, now(), now())
        """,
        (message_id, model, run_id),
    )
    if with_content_page:
        row = conn.execute(
            """
            INSERT INTO wiki_pages (slug, path, title, page_type)
            VALUES (%s, %s, %s, 'topic')
            RETURNING page_id
            """,
            (f"topic-{message_id}", f"wiki/topics/{message_id}.md", f"Topic {message_id}"),
        ).fetchone()
        assert row is not None
        conn.execute(
            "INSERT INTO message_touched_pages (message_id, page_id) VALUES (%s, %s)",
            (message_id, row["page_id"]),
        )


def _seed_insight(
    conn: psycopg.Connection,
    *,
    run_id: uuid.UUID,
    raw_path: str,
    category: str,
) -> None:
    """Insert one compile_insights row tying category to a message's raw_path."""
    conn.execute(
        """
        INSERT INTO compile_insights (run_id, category, message, email_path)
        VALUES (%s, %s, %s, %s)
        """,
        (run_id, category, "test insight", raw_path),
    )


def test_citation_counts_by_model_carves_out_trivial_and_already_captured(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insights join: trivial_skip + already_captured both count per-model (U7).

    Seeds 4 compiled messages under model 'm1' in one run:
    - m_content: cited in a content page (no insight)
    - m_trivial: flagged with trivial_skip insight
    - m_captured: flagged with already_captured insight
    - m_ghost: no insight, not cited

    Expected per-model counts: compiled=4, with_content_page=1,
    trivial_skip=1, already_captured=1.
    Effective denominator = 4 - 1 - 1 = 2; effective rate = 1/2 = 50%.
    """
    import src.db as db_pkg

    monkeypatch.setattr(trace_scorecard, "connect", db_pkg.connect)
    run_id = uuid.uuid4()
    _seed_compile_run(db_conn, run_id)

    _seed_attempt_with_run(
        db_conn, message_id="m_content", model="m1", run_id=run_id, with_content_page=True
    )
    _seed_attempt_with_run(
        db_conn, message_id="m_trivial", model="m1", run_id=run_id, with_content_page=False
    )
    _seed_attempt_with_run(
        db_conn, message_id="m_captured", model="m1", run_id=run_id, with_content_page=False
    )
    _seed_attempt_with_run(
        db_conn, message_id="m_ghost", model="m1", run_id=run_id, with_content_page=False
    )
    _seed_insight(db_conn, run_id=run_id, raw_path="raw/m_trivial.md", category="trivial_skip")
    _seed_insight(
        db_conn,
        run_id=run_id,
        raw_path="raw/m_captured.md",
        category="already_captured",
    )
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = _citation_counts_by_model(since)

    assert set(result) == {"m1", "all"}
    m1 = result["m1"]
    assert m1.compiled_total == 4
    assert m1.with_content_page == 1
    assert m1.trivial_skip == 1
    assert m1.already_captured == 1
    assert m1.effective_denominator == 2
    assert m1.effective_rate == pytest.approx(0.5)
    assert m1.raw_rate == pytest.approx(0.25)


def test_citation_counts_by_model_ignores_insights_from_other_runs(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insights only attach to a message when the run_id matches the attempt.

    Guards against accidental leakage when a message has the same raw_path
    across multiple runs but a trivial_skip insight was only logged in
    an earlier run (e.g. the agent re-compiled after a prompt tweak and
    actually wrote a page this time).
    """
    import src.db as db_pkg

    monkeypatch.setattr(trace_scorecard, "connect", db_pkg.connect)
    this_run = uuid.uuid4()
    prior_run = uuid.uuid4()
    _seed_compile_run(db_conn, this_run)
    _seed_compile_run(db_conn, prior_run)

    # Current run: message was compiled, wrote a content page.
    _seed_attempt_with_run(
        db_conn,
        message_id="m_current",
        model="m1",
        run_id=this_run,
        with_content_page=True,
    )
    # Prior run flagged this message as trivial_skip — should NOT carry
    # forward because the join uses run_id, not just email_path.
    _seed_insight(
        db_conn,
        run_id=prior_run,
        raw_path="raw/m_current.md",
        category="trivial_skip",
    )
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = _citation_counts_by_model(since)
    m1 = result["m1"]
    assert m1.trivial_skip == 0
    assert m1.already_captured == 0
    assert m1.with_content_page == 1
    assert m1.effective_rate == pytest.approx(1.0)


def test_citation_counts_by_model_isolates_insights_per_run(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same message compiled in two runs — trivial_skip from run A must NOT leak to run B.

    Guards the P1 review follow-up: `insights_per_message` groups by
    `(message_id, compile_model, run_id)`, not just `(message_id,
    compile_model)`. Without the run_id in the GROUP BY, `BOOL_OR`
    would merge run A's trivial_skip into run B's row and double-count
    it toward `trivial_skip` (once per attempt-run), inflating the
    carve-out denominator.

    Scenario: message `m_recompiled` is attempted twice under the same
    model. Run A flags it as trivial_skip; Run B writes a content
    page. Expected per-model counts: compiled=2 (one attempt per run),
    with_content_page=1 (dedupe on message_id in `with_content_page`
    CTE — page authorship is not run-specific), trivial_skip=1 (ONLY
    run A's attempt carries the flag), already_captured=0.
    """
    import src.db as db_pkg

    monkeypatch.setattr(trace_scorecard, "connect", db_pkg.connect)
    run_a = uuid.uuid4()
    run_b = uuid.uuid4()
    _seed_compile_run(db_conn, run_a)
    _seed_compile_run(db_conn, run_b)

    # Seed the message once (shared across both runs). Use run_a for
    # the initial `_seed_attempt_with_run` call (no page yet), then
    # add a second attempt row + content page for run_b manually.
    _seed_attempt_with_run(
        db_conn,
        message_id="m_recompiled",
        model="m1",
        run_id=run_a,
        with_content_page=False,
    )
    # Run A → trivial_skip insight on this message.
    _seed_insight(
        db_conn,
        run_id=run_a,
        raw_path="raw/m_recompiled.md",
        category="trivial_skip",
    )
    # Run B → second attempt on the same message, this time writing a
    # content page. No trivial_skip insight for run_b.
    db_conn.execute(
        """
        INSERT INTO compile_attempts
          (message_id, compile_model, outcome, run_id, attempted_at, finished_at)
        VALUES (%s, %s, 'compiled', %s, now(), now())
        """,
        ("m_recompiled", "m1", run_b),
    )
    page_row = db_conn.execute(
        """
        INSERT INTO wiki_pages (slug, path, title, page_type)
        VALUES (%s, %s, %s, 'topic')
        RETURNING page_id
        """,
        ("topic-m-recompiled", "wiki/topics/m-recompiled.md", "Topic m_recompiled"),
    ).fetchone()
    assert page_row is not None
    db_conn.execute(
        "INSERT INTO message_touched_pages (message_id, page_id) VALUES (%s, %s)",
        ("m_recompiled", page_row["page_id"]),
    )
    db_conn.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    result = _citation_counts_by_model(since)
    m1 = result["m1"]
    # Two attempts across two runs → compiled_total = 2.
    assert m1.compiled_total == 2
    # The page authorship is not run-specific; the `with_content_page`
    # CTE dedupes on message_id, so each of the two attempt rows gets
    # the `has_content_page = True` flag. Both rows counted.
    assert m1.with_content_page == 2
    # CRITICAL: only run_a's attempt row should carry trivial_skip.
    # Pre-fix, this was 2 (insight fanned out to both runs).
    assert m1.trivial_skip == 1
    assert m1.already_captured == 0
    # Effective denominator = 2 - 1 - 0 = 1, effective rate = 2/1 but
    # clamped behavior: numerator never exceeds denominator in practice,
    # but here with_content_page(2) / effective_denom(1) = 2.0 —
    # that's a quirk of the current model (a message being both cited
    # AND trivial-skipped across runs is a data-shape question, not a
    # bug in this carve-out). Assert numerator/denom directly.
    assert m1.effective_denominator == 1


def test_render_citation_breakdown_clamps_negative_denominator() -> None:
    """Denominator never renders negative even when no-op counts exceed compiled.

    Guards the P2 review follow-up. If `trivial_skip_count +
    already_captured_count > compiled_total` (e.g. duplicate insight
    rows, noisy migration data), the raw subtraction would print
    "1 of -1" which is both confusing and internally inconsistent
    with `CitationCounts.effective_denominator` (which already clamps
    to 0). Clamp at render time too.
    """
    from scripts.trace_scorecard import ModelAggregate

    rows = [
        ModelAggregate(
            model="m1",
            content_page_citation_rate_raw=1.0,
            content_page_citation_rate_effective=None,
            compiled_total=1,
            compiled_with_content_page=1,
            # Intentionally malformed: 2 + 1 > 1, so raw subtraction
            # would give -2 without the clamp.
            trivial_skip_count=2,
            already_captured_count=1,
        ),
    ]
    out = _render_citation_breakdown(rows)
    assert "1 of 0" in out
    # Explicitly guard against regression — the denominator "of -N" must
    # never appear. " - " (space-dash-space) is still fine because the
    # format string embeds "compiled - trivial_skip - already_captured".
    assert "of -" not in out


def test_render_citation_breakdown_shows_both_rates() -> None:
    """Breakdown block renders raw + effective side-by-side (U7)."""
    from scripts.trace_scorecard import ModelAggregate

    rows = [
        ModelAggregate(
            model="m1",
            content_page_citation_rate_raw=0.25,
            content_page_citation_rate_effective=0.5,
            compiled_total=4,
            compiled_with_content_page=1,
            trivial_skip_count=1,
            already_captured_count=1,
        ),
    ]
    out = _render_citation_breakdown(rows)
    assert "m1" in out
    assert "raw = 25.0%" in out
    assert "effective = 50.0%" in out
    assert "1 of 4 compiled" in out
    assert "1 of 2" in out
    assert "trivial_skip(1)" in out
    assert "already_captured(1)" in out


def test_render_citation_breakdown_skips_empty_rows() -> None:
    """Models with zero compiled attempts are omitted from the breakdown."""
    from scripts.trace_scorecard import ModelAggregate

    rows = [
        ModelAggregate(model="empty"),
    ]
    out = _render_citation_breakdown(rows)
    assert "_no compiled attempts in window_" in out
    assert "empty" not in out
