"""Unit tests for scripts/audit_50_traces.py.

Pure-function + CLI tests:
- `_scan_trace` pulls rubric bits from synthetic Langfuse payloads.
- `_build_audit` composes a `TraceAudit` (citation rate is stubbed out).
- `_aggregate` + `_render_markdown` produce a stable markdown report.
- The CLI aborts with exit code 3 when >20% of trace fetches fail.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import audit_50_traces  # noqa: E402
from scripts.audit_50_traces import TraceAudit  # noqa: E402
from scripts.audit_50_traces import _aggregate  # noqa: E402
from scripts.audit_50_traces import _build_audit  # noqa: E402
from scripts.audit_50_traces import _parse_since  # noqa: E402
from scripts.audit_50_traces import _render_markdown  # noqa: E402
from scripts.audit_50_traces import _scan_trace  # noqa: E402


def _mk_tool_obs(
    name: str, output: str = "", inputs: str = "", level: str = "DEFAULT"
) -> dict[str, Any]:
    return {"type": "TOOL", "name": name, "output": output, "input": inputs, "level": level}


def _mk_trace(
    observations: list[dict[str, Any]],
    trace_id: str = "t1",
    model: str = "test-model",
    run_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    md: dict[str, Any] = {"compile_model": model}
    if run_id:
        md["compile_run_id"] = run_id
    if thread_id:
        md["compile_thread_id"] = thread_id
    return {
        "body": {
            "id": trace_id,
            "metadata": md,
            "observations": observations,
            "name": f"compile:{model}:{trace_id}",
            "createdAt": "2026-04-16T12:00:00Z",
        }
    }


# ------------------------------ _parse_since ------------------------------


def test_parse_since_accepts_durations_and_dates() -> None:
    assert _parse_since("24h") == "24h"
    assert _parse_since("7d") == "7d"
    assert _parse_since("2026-04-16") == "2026-04-16"


def test_parse_since_rejects_bad_syntax() -> None:
    import click

    with pytest.raises(click.BadParameter):
        _parse_since("forever")


# ------------------------------- _scan_trace ------------------------------


def test_scan_trace_detects_abs_path_violation() -> None:
    trace = _mk_trace(
        [
            _mk_tool_obs("read_file", inputs='{"file_path": "/raw/foo.md"}'),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["abs_path_violation"] is True


def test_scan_trace_detects_resolve_page_abs_output() -> None:
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "resolve_page",
                output='{"exists": true, "path": "/wiki/topics/foo.md"}',
            ),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["resolve_page_abs"] is True


def test_scan_trace_detects_create_entities_empty() -> None:
    trace = _mk_trace(
        [
            _mk_tool_obs("create_entities", inputs='{"entities": [], "raw_paths": ["raw/a.md"]}'),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["create_entities_empty"] is True


def test_scan_trace_tool_friction_from_error_level() -> None:
    trace = _mk_trace(
        [
            _mk_tool_obs("read_file", output="oops", level="ERROR"),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["tool_friction"] is True


def test_scan_trace_counts_write_draft_and_entity_tools() -> None:
    trace = _mk_trace(
        [
            _mk_tool_obs("write_draft_page"),
            _mk_tool_obs("create_entity"),
            _mk_tool_obs("create_entities", inputs='{"entities": [{"email": "x@y"}]}'),
            _mk_tool_obs("log_insight"),
        ]
    )
    signals, counters = _scan_trace(trace)
    assert signals["write_draft_page_calls"] == 1
    assert signals["create_entity_calls"] == 2
    assert signals["log_insight_calls"] == 1
    assert counters["write_draft_page"] == 1


def test_scan_trace_skips_unnamed_and_non_tool_obs() -> None:
    trace = _mk_trace(
        [
            {"type": "TOOL", "name": "", "output": "", "input": ""},  # unnamed
            {"type": "SPAN", "name": "foo"},  # non-tool
            _mk_tool_obs("resolve_page"),
        ]
    )
    signals, counters = _scan_trace(trace)
    assert signals["tool_calls"] == 1
    assert counters == {"resolve_page": 1}


# ------------------------------ _build_audit ------------------------------


def test_build_audit_clean_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trace with write_draft_page + no DB touches → attempted, no flags."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id: (0, 0, None),
    )
    trace = _mk_trace(
        [_mk_tool_obs("write_draft_page")],
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="t1",
    )
    a = _build_audit(trace)
    assert a.attempted_content_page is True
    assert a.filing_cabinet_signal is False
    assert a.flags == []


def test_build_audit_filing_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Touched 3 messages, content-cited only 1 → filing_cabinet."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id: (3, 1, 1 / 3),
    )
    trace = _mk_trace(
        [_mk_tool_obs("create_entity")],
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="t1",
    )
    a = _build_audit(trace)
    assert a.filing_cabinet_signal is True
    assert "filing_cabinet" in a.flags
    assert a.content_citation_rate == pytest.approx(1 / 3)


def test_build_audit_no_content_attempt_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """No write_draft_page call → no_content_page_attempt flag fires."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id: (0, 0, None),
    )
    trace = _mk_trace([_mk_tool_obs("read_file")])
    a = _build_audit(trace)
    assert a.attempted_content_page is False
    assert "no_content_page_attempt" in a.flags


def test_build_audit_log_insight_missed_despite_friction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool ERROR observed but no log_insight call → log_insight_missed."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs("write_draft_page"),
            _mk_tool_obs("read_file", output="oops", level="ERROR"),
        ]
    )
    a = _build_audit(trace)
    assert a.log_insight_absent_despite_friction is True
    assert "log_insight_missed" in a.flags


# ------------------------------ _aggregate --------------------------------


def test_aggregate_counts_and_rates() -> None:
    audits = [
        TraceAudit(
            trace_id="a",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            attempted_content_page=True,
            content_citation_rate=1.0,
        ),
        TraceAudit(
            trace_id="b",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            attempted_content_page=False,
            filing_cabinet_signal=True,
            content_citation_rate=0.0,
            touched_messages=2,
            content_cited_messages=0,
        ),
        TraceAudit(
            trace_id="c",
            model="m2",
            name=None,
            created_at=None,
            thread_id=None,
            abs_path_violation=True,
        ),
    ]
    for a in audits:
        a.flags = audit_50_traces._flag_labels(a)
    agg = _aggregate(audits)
    assert agg["total"] == 3
    assert agg["attempted_content_page"] == 1
    assert agg["flag_counts"]["filing_cabinet"] == 1
    assert agg["flag_counts"]["abs_path"] == 1
    assert agg["flag_counts"]["no_content_page_attempt"] == 2
    assert agg["per_model_totals"] == {"m1": 2, "m2": 1}
    # Mean citation = mean of [1.0, 0.0] = 0.5 (trace "c" has no data).
    assert agg["mean_content_citation_rate"] == pytest.approx(0.5)
    assert agg["traces_with_citation_data"] == 2


def test_aggregate_empty_sample() -> None:
    agg = _aggregate([])
    assert agg["total"] == 0
    assert agg["flag_counts"] == {}
    assert agg["mean_content_citation_rate"] is None


# ------------------------- _render_markdown -------------------------------


def test_render_markdown_has_expected_sections() -> None:
    audits = [
        TraceAudit(
            trace_id="a",
            model="m1",
            name="compile:m1:a",
            created_at="2026-04-16T12:00:00Z",
            thread_id="t1",
            attempted_content_page=True,
            content_citation_rate=1.0,
            touched_messages=1,
            content_cited_messages=1,
        ),
    ]
    audits[0].flags = audit_50_traces._flag_labels(audits[0])
    audits[0].note = audit_50_traces._note_for(audits[0])
    agg = _aggregate(audits)
    from datetime import UTC
    from datetime import datetime

    md = _render_markdown(
        audits,
        agg,
        since_tag="24h",
        limit=50,
        fetch_failures=0,
        generated_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
    )
    assert "# 50-trace audit" in md
    assert "## Aggregate flag counts" in md
    assert "## Per-model breakdown" in md
    assert "## Verdict" in md
    assert "## Per-trace notes" in md
    assert "compile:m1:a" in md


# ----------------------------- CLI abort ---------------------------------


def test_cli_aborts_on_fetch_failure_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When >20% of trace fetches fail, the CLI should exit 3.

    We stub the Langfuse shell-outs and DB citation query so the test is
    hermetic: list returns 10 trace IDs, fetch returns None for 3 of them
    (30% failure > 20% threshold).
    """
    monkeypatch.setattr(audit_50_traces, "_langfuse_env", dict)
    monkeypatch.setattr(
        audit_50_traces,
        "_list_recent_trace_ids",
        lambda limit, env: [{"id": f"t{i}"} for i in range(10)],
    )

    def fake_fetch(tid: str, env: dict[str, str]) -> dict[str, Any] | None:
        # Fail the first three; succeed for the rest.
        if tid in {"t0", "t1", "t2"}:
            return None
        return _mk_trace([_mk_tool_obs("write_draft_page")], trace_id=tid)

    monkeypatch.setattr(audit_50_traces, "_fetch_trace", fake_fetch)
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id: (0, 0, None),
    )
    monkeypatch.setattr(audit_50_traces, "REPO_ROOT", tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_50_traces.main, ["--limit", "10"])
    assert result.exit_code == 3
    # Audit file should still be written.
    audit_dir = tmp_path / "docs" / "audits"
    assert any(p.name.startswith("audit-") for p in audit_dir.iterdir())


def test_cli_ok_under_threshold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With only 1/10 failures (10%), the CLI should exit 0."""
    monkeypatch.setattr(audit_50_traces, "_langfuse_env", dict)
    monkeypatch.setattr(
        audit_50_traces,
        "_list_recent_trace_ids",
        lambda limit, env: [{"id": f"t{i}"} for i in range(10)],
    )

    def fake_fetch(tid: str, env: dict[str, str]) -> dict[str, Any] | None:
        if tid == "t0":
            return None
        return _mk_trace([_mk_tool_obs("write_draft_page")], trace_id=tid)

    monkeypatch.setattr(audit_50_traces, "_fetch_trace", fake_fetch)
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id: (0, 0, None),
    )
    monkeypatch.setattr(audit_50_traces, "REPO_ROOT", tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_50_traces.main, ["--limit", "10"])
    assert result.exit_code == 0, result.output
    audit_dir = tmp_path / "docs" / "audits"
    audit_files = list(audit_dir.glob("audit-*.md"))
    assert len(audit_files) == 1


def test_cli_empty_trace_list_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Langfuse unreachable / empty list → exit 2 with error message."""
    monkeypatch.setattr(audit_50_traces, "_langfuse_env", dict)
    monkeypatch.setattr(audit_50_traces, "_list_recent_trace_ids", lambda limit, env: [])
    monkeypatch.setattr(audit_50_traces, "REPO_ROOT", tmp_path)

    # Click 8.2+ merges stderr into output unconditionally; grep the merged stream.
    runner = CliRunner()
    result = runner.invoke(audit_50_traces.main, ["--limit", "10"])
    assert result.exit_code == 2
    assert "unreachable" in result.output
