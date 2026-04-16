"""Unit tests for Tier A telemetry signals in the scorecard + audit scripts.

Pure-function tests — they construct synthetic trace payloads matching
the shape Langfuse returns and exercise the extractors / summarizers
directly. No DB, no Langfuse CLI, no subprocess.

The three signals under test are passive: before Tier A's
PathAutoHealMiddleware / reviewer subagent / todo nudging land, every
trace should score all-off (False/None/False) and the aggregate rates
should be zero — the scorecard must not break on pre-Tier-A data.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.nightly_trace_audit import _extract_tier_a_signals  # noqa: E402
from scripts.nightly_trace_audit import _score_trace  # noqa: E402
from scripts.nightly_trace_audit import _summarize  # noqa: E402
from scripts.trace_scorecard import Attempt  # noqa: E402
from scripts.trace_scorecard import TraceMetrics  # noqa: E402
from scripts.trace_scorecard import _build_row  # noqa: E402
from scripts.trace_scorecard import _extract_trace_metrics  # noqa: E402
from scripts.trace_scorecard import _fmt_verdicts  # noqa: E402


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
