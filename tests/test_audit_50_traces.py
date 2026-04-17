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
    batch_index: int | None = None,
) -> dict[str, Any]:
    md: dict[str, Any] = {"compile_model": model}
    if run_id:
        md["compile_run_id"] = run_id
    if thread_id:
        md["compile_thread_id"] = thread_id
    if batch_index is not None:
        md["compile_batch_index"] = batch_index
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
    """Host-rootfs paths (``/Users/...``) count as contract violations."""
    trace = _mk_trace(
        [
            _mk_tool_obs("read_file", inputs='{"file_path": "/Users/foo/bar.md"}'),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["abs_path_violation"] is True


def test_scan_trace_allows_virtual_raw_prefix() -> None:
    """``/raw/...`` is a sanctioned virtual mount; NOT an abs-path violation.

    The agent prompt actively teaches virtual-mode reads from
    ``/raw/<email>.md`` — flagging them as path violations made healthy
    runs report false positives (U1).
    """
    trace = _mk_trace(
        [
            _mk_tool_obs("read_file", inputs='{"file_path": "/raw/foo.md"}'),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["abs_path_violation"] is False


def test_scan_trace_allows_virtual_wiki_prefix() -> None:
    """``/wiki/...`` is a sanctioned virtual mount; NOT an abs-path violation."""
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "write_file",
                inputs='{"file_path": "/wiki/topics/cool-topic.md"}',
            ),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["abs_path_violation"] is False


def test_scan_trace_flags_mnt_prefix() -> None:
    """Deep Agents' built-in ``/mnt/...`` sandbox root still counts."""
    trace = _mk_trace(
        [
            _mk_tool_obs("read_file", inputs='{"file_path": "/mnt/data.md"}'),
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


def test_scan_trace_detects_trivial_skip_insight() -> None:
    """log_insight(category='trivial_skip') must register in signals."""
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "log_insight",
                inputs=('{"category": "trivial_skip", "message": "OOO reply, nothing to extract"}'),
            ),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["trivial_skip_calls"] == 1
    # A trivial_skip call is still a log_insight call.
    assert signals["log_insight_calls"] == 1


def test_scan_trace_detects_already_captured_insight() -> None:
    """log_insight(category='already_captured') must register in signals (U7)."""
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "log_insight",
                inputs=(
                    '{"category": "already_captured", '
                    '"message": "Already on [[topic-x]] from prior thread-mate"}'
                ),
            ),
        ]
    )
    signals, _ = _scan_trace(trace)
    assert signals["already_captured_calls"] == 1
    assert signals["log_insight_calls"] == 1
    assert signals["trivial_skip_calls"] == 0


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
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
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


def test_build_audit_patch_page_counts_as_content_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """patch_page edits an existing content page → attempted_content_page=True (U1).

    Before this fix the audit only recognised the legacy
    ``write_draft_page`` proxy and ignored ``patch_page`` / ``edit_file``
    / ``write_file``, so modern-tool healthy runs reported "0 content
    page attempts".
    """
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [_mk_tool_obs("patch_page", inputs='{"slug": "foo", "section": "Current state"}')],
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="t1",
    )
    a = _build_audit(trace)
    assert a.attempted_content_page is True
    assert a.content_write_calls == 1
    assert "no_content_page_attempt" not in a.flags


def test_build_audit_write_file_to_wiki_topic_counts_as_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """write_file to ``/wiki/topics/...`` counts toward attempted_content_page (U1)."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "write_file",
                inputs='{"file_path": "/wiki/topics/authn.md", "content": "# Authn\\n"}',
            ),
        ],
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="t1",
    )
    a = _build_audit(trace)
    assert a.attempted_content_page is True
    assert a.content_write_calls == 1


def test_build_audit_edit_file_on_entity_not_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edit_file on ``/wiki/entities/...`` does NOT count as content write.

    Entity pages are filing-cabinet territory by design — only content-
    type paths (topic/system/policy/decision/...) should bump the
    denominator.
    """
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "edit_file",
                inputs='{"file_path": "/wiki/entities/alice.md"}',
            ),
        ],
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="t1",
    )
    a = _build_audit(trace)
    assert a.attempted_content_page is False
    assert a.content_write_calls == 0
    assert "no_content_page_attempt" in a.flags


def test_build_audit_distinct_rows_for_same_thread_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different batch_indexes on same (run_id, thread_id) yield two audits (U1).

    Previously both would share the same DB citation lookup — still
    true until the schema persists batch_index — but each trace body
    still produces its own ``TraceAudit`` row with the batch_index
    stamp intact in downstream logs. This guards the citation-rate
    helper against confusing them at the trace-ID layer.
    """
    # Record each (run_id, thread_id, batch_index) the citation helper
    # was called with so we can assert distinct lookups per batch.
    calls: list[tuple[str, str, int]] = []

    def fake_rate(
        run_id: str, thread_id: str, batch_index: int = -1
    ) -> tuple[int, int, float | None]:
        calls.append((run_id, thread_id, batch_index))
        return (1, 1, 1.0)

    monkeypatch.setattr(audit_50_traces, "_compute_citation_rate_for_run", fake_rate)

    trace_a = _mk_trace(
        [_mk_tool_obs("write_draft_page")],
        trace_id="trace-a",
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="thread-abc",
        batch_index=1,
    )
    trace_b = _mk_trace(
        [_mk_tool_obs("write_draft_page")],
        trace_id="trace-b",
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="thread-abc",
        batch_index=2,
    )
    audit_a = _build_audit(trace_a)
    audit_b = _build_audit(trace_b)
    assert audit_a.trace_id != audit_b.trace_id
    # Both batches called the citation helper with their own batch_index,
    # so the audit layer can distinguish them.
    assert (
        "11111111-1111-1111-1111-111111111111",
        "thread-abc",
        1,
    ) in calls
    assert (
        "11111111-1111-1111-1111-111111111111",
        "thread-abc",
        2,
    ) in calls


def test_build_audit_touched_only_counts_as_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the DB shows touched messages, the compile produced content even
    when the scanned trace missed the tool names (e.g. via an indirect
    write). The U1 denominator treats that as attempted.
    """
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (2, 2, 1.0),
    )
    trace = _mk_trace(
        [_mk_tool_obs("read_file", inputs='{"file_path": "/raw/m.md"}')],
        run_id="11111111-1111-1111-1111-111111111111",
        thread_id="t1",
    )
    a = _build_audit(trace)
    assert a.attempted_content_page is True
    assert a.touched_messages == 2
    assert a.content_cited_messages == 2


def test_build_audit_filing_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Touched 3 messages, content-cited only 1 → filing_cabinet."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (3, 1, 1 / 3),
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
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
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
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
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


def test_build_audit_trivial_skip_excluded_from_no_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace that trivially skips must not be flagged as no_content_page_attempt.

    Agent called ``log_insight(category="trivial_skip")`` and no
    write_draft_page / create_entity — that's a correct skip of a
    non-substantive email (OOO, auto-reply). It must get the
    ``trivial_skip`` flag but NOT the ``no_content_page_attempt`` flag,
    so the synthesis-failure denominator isn't polluted.
    """
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "log_insight",
                inputs='{"category": "trivial_skip", "message": "OOO"}',
            ),
        ]
    )
    a = _build_audit(trace)
    assert a.trivial_skip_trace is True
    assert a.attempted_content_page is False
    assert "trivial_skip" in a.flags
    assert "no_content_page_attempt" not in a.flags


def test_build_audit_trivial_skip_with_writes_is_not_trivial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the trace ALSO wrote content, it's not a pure trivial skip.

    The agent can call log_insight('trivial_skip') for ONE email in a
    batch while still writing pages for others. In that case the trace
    is NOT overall trivial — we only strip the failure flag when the
    entire trace looks like a pure skip (no content / entity writes).
    """
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "log_insight",
                inputs='{"category": "trivial_skip", "message": "one OOO"}',
            ),
            _mk_tool_obs("write_draft_page"),
        ]
    )
    a = _build_audit(trace)
    # trivial_skip_calls > 0 but writes happened → not a pure-skip trace.
    assert a.trivial_skip_calls == 1
    assert a.trivial_skip_trace is False
    assert a.attempted_content_page is True


def test_build_audit_trivial_skip_with_patch_page_is_not_trivial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """patch_page counts as a content write — trivial_skip classification
    must guard against it too, not just write_draft_page. Without the
    content_write_calls guard, a trace calling patch_page + trivial_skip
    would be counted as BOTH trivial and attempted, inflating the
    trivial_skip_count denominator carve-out."""
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "log_insight",
                inputs='{"category": "trivial_skip", "message": "one OOO"}',
            ),
            _mk_tool_obs(
                "patch_page",
                inputs='{"slug": "some-topic", "section": "Updates"}',
            ),
        ]
    )
    a = _build_audit(trace)
    assert a.trivial_skip_calls == 1
    assert a.trivial_skip_trace is False
    assert a.attempted_content_page is True


def test_build_audit_already_captured_excluded_from_no_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """already_captured trace must NOT be flagged as no_content_page_attempt (U7).

    Correct no-op — the content was already on an existing topic page
    (prior thread-mate was already compiled). Like trivial_skip, this
    is carved out of the synthesis-failure denominator.
    """
    monkeypatch.setattr(
        audit_50_traces,
        "_compute_citation_rate_for_run",
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    trace = _mk_trace(
        [
            _mk_tool_obs(
                "log_insight",
                inputs='{"category": "already_captured", "message": "on [[topic-x]]"}',
            ),
        ]
    )
    a = _build_audit(trace)
    assert a.already_captured_trace is True
    assert a.attempted_content_page is False
    assert "already_captured" in a.flags
    assert "no_content_page_attempt" not in a.flags


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
    assert agg["trivial_skip_count"] == 0
    assert agg["already_captured_count"] == 0
    assert agg["non_trivial_total"] == 0


def test_aggregate_trivial_skip_count_exposed() -> None:
    """Trivial-skip traces must be counted separately in the aggregate."""
    audits = [
        TraceAudit(
            trace_id="a",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            attempted_content_page=True,
        ),
        TraceAudit(
            trace_id="b",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            trivial_skip_trace=True,
            trivial_skip_calls=1,
        ),
        TraceAudit(
            trace_id="c",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            trivial_skip_trace=True,
            trivial_skip_calls=2,
        ),
    ]
    for a in audits:
        a.flags = audit_50_traces._flag_labels(a)
    agg = _aggregate(audits)
    assert agg["trivial_skip_count"] == 2
    assert agg["non_trivial_total"] == 1
    # Trivial-skip traces don't carry no_content_page_attempt — they're
    # excluded from the synthesis-failure denominator by design.
    assert agg["flag_counts"].get("no_content_page_attempt", 0) == 0


def test_aggregate_already_captured_count_exposed() -> None:
    """already_captured traces must be counted separately and carved out (U7)."""
    audits = [
        TraceAudit(
            trace_id="a",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            attempted_content_page=True,
        ),
        TraceAudit(
            trace_id="b",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            already_captured_trace=True,
            already_captured_calls=1,
        ),
        TraceAudit(
            trace_id="c",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            trivial_skip_trace=True,
            trivial_skip_calls=1,
        ),
    ]
    for a in audits:
        a.flags = audit_50_traces._flag_labels(a)
    agg = _aggregate(audits)
    assert agg["trivial_skip_count"] == 1
    assert agg["already_captured_count"] == 1
    # Effective (non-trivial) total = 3 - 1 - 1 = 1
    assert agg["non_trivial_total"] == 1
    # Neither no-op trace fires no_content_page_attempt.
    assert agg["flag_counts"].get("no_content_page_attempt", 0) == 0
    # Both no-op labels fired once.
    assert agg["flag_counts"]["trivial_skip"] == 1
    assert agg["flag_counts"]["already_captured"] == 1


def test_verdict_excludes_trivial_skips_from_synthesis_denominator() -> None:
    """Verdict must base 'attempted share' on non-trivial total, not full sample.

    Two trivial-skips and one content attempt = 1/1 (100%) attempted,
    not 1/3 (33%). Catches the regression the F3 audit refinement
    exists to prevent.
    """
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
            trivial_skip_trace=True,
            trivial_skip_calls=1,
        ),
        TraceAudit(
            trace_id="c",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            trivial_skip_trace=True,
            trivial_skip_calls=1,
        ),
    ]
    for a in audits:
        a.flags = audit_50_traces._flag_labels(a)
    agg = _aggregate(audits)
    verdict = audit_50_traces._verdict_paragraph(audits, agg)
    # The non-trivial denominator is 1; the one attempt is 100% of that.
    assert "1 non-trivial compile attempts" in verdict
    assert "(100%)" in verdict
    assert "2 trivial-skip traces" in verdict
    assert "excluded from the synthesis denominator" in verdict


def test_verdict_excludes_already_captured_from_synthesis_denominator() -> None:
    """Verdict must ALSO carve out already_captured — not just trivial_skip (U7).

    Two already_captured + one trivial_skip + one content attempt = 1/1
    (100%) attempted, not 1/4 (25%). Before U7 the verdict would have
    under-reported synthesis performance by counting correct no-ops as
    missed synthesis attempts.
    """
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
            trivial_skip_trace=True,
            trivial_skip_calls=1,
        ),
        TraceAudit(
            trace_id="c",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            already_captured_trace=True,
            already_captured_calls=1,
        ),
        TraceAudit(
            trace_id="d",
            model="m1",
            name=None,
            created_at=None,
            thread_id=None,
            already_captured_trace=True,
            already_captured_calls=1,
        ),
    ]
    for a in audits:
        a.flags = audit_50_traces._flag_labels(a)
    agg = _aggregate(audits)
    verdict = audit_50_traces._verdict_paragraph(audits, agg)
    # Effective denominator = 4 - 1 - 2 = 1. The one attempt is 100%.
    assert "1 non-trivial compile attempts" in verdict
    assert "(100%)" in verdict
    assert "1 trivial-skip traces" in verdict
    assert "2 already-captured traces" in verdict


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
    # Healthy sample → no invalid banner.
    assert "SAMPLE INVALID" not in md
    assert "## Aggregate flag counts" in md
    assert "## Per-model breakdown" in md
    assert "## Verdict" in md
    assert "## Per-trace notes" in md
    assert "compile:m1:a" in md


def test_render_markdown_prepends_invalid_sample_banner() -> None:
    """When invalid_sample=True, a banner MUST appear at the top of the md (U1)."""
    audits = [
        TraceAudit(
            trace_id="a",
            model="m1",
            name="compile:m1:a",
            created_at="2026-04-16T12:00:00Z",
            thread_id="t1",
            attempted_content_page=True,
            content_citation_rate=1.0,
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
        limit=10,
        fetch_failures=4,
        generated_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        invalid_sample=True,
    )
    # Banner is at the top, before the main H1 title.
    banner_idx = md.find("SAMPLE INVALID")
    title_idx = md.find("# 50-trace audit")
    assert banner_idx >= 0
    assert title_idx >= 0
    assert banner_idx < title_idx
    assert "4/10 fetches failed" in md


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
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
    )
    monkeypatch.setattr(audit_50_traces, "REPO_ROOT", tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_50_traces.main, ["--limit", "10"])
    assert result.exit_code == 3
    # Audit file should still be written — and carry the invalid-sample
    # banner so the written doc self-describes the bad state (U1).
    audit_dir = tmp_path / "docs" / "audits"
    audit_files = list(audit_dir.glob("audit-*.md"))
    assert audit_files, "audit file not written"
    content = audit_files[0].read_text(encoding="utf-8")
    assert "SAMPLE INVALID" in content
    assert "3/10 fetches failed" in content


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
        lambda run_id, thread_id, batch_index=-1: (0, 0, None),
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
