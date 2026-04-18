"""Tests for U13 per-observation Langfuse scores.

Exercises the path in ``emit_scores_for_trace`` that iterates trace
observations and pushes a score attached to each observation's `id`.
Synthetic observations mimic the shape the Langfuse SDK returns via
``obs.dict(by_alias=True)`` — camelCase keys (``startTime`` / ``endTime``)
and `type` values matching the Observation enum (``TOOL``, ``AGENT``).

No network, no DB — the citation lookup is shimmed with a precomputed
flag on each emit call.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.observability.langfuse_scores import emit_scores_for_trace  # noqa: E402


def _mk_obs(
    *,
    obs_type: str,
    name: str,
    obs_id: str | None,
    output: str = "",
    input_str: str = "",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Build an observation dict matching the SDK's by_alias serializer shape."""
    d: dict[str, Any] = {
        "type": obs_type,
        "name": name,
        "output": output,
        "input": input_str,
    }
    if obs_id is not None:
        d["id"] = obs_id
    if start_time is not None:
        d["startTime"] = start_time
    if end_time is not None:
        d["endTime"] = end_time
    return d


def _scores_by_name_and_obs(client: MagicMock) -> list[dict[str, Any]]:
    """Return a flat list of kwargs for each `create_score` call."""
    return [call.kwargs for call in client.create_score.call_args_list]


def _find_scores(
    calls: list[dict[str, Any]], *, name: str, observation_id: str | None = None
) -> list[dict[str, Any]]:
    """Filter score calls by name and (optional) observation_id."""
    return [c for c in calls if c["name"] == name and c.get("observation_id") == observation_id]


# =============================== resolve_page ==================================


def test_resolve_page_alphabetical_score_emitted_per_observation() -> None:
    """Each `resolve_page` TOOL obs gets its own BOOLEAN score attached."""
    client = MagicMock()
    alphabetical = {
        "exists": False,
        "candidates": [
            {"slug": "apple"},
            {"slug": "banana"},
            {"slug": "cherry"},
        ],
    }
    not_sorted = {
        "exists": False,
        "candidates": [
            {"slug": "zebra"},
            {"slug": "apple"},
            {"slug": "mango"},
        ],
    }
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="resolve_page",
            obs_id="obs-alpha",
            output=json.dumps(alphabetical),
        ),
        _mk_obs(
            obs_type="TOOL",
            name="resolve_page",
            obs_id="obs-mixed",
            output=json.dumps(not_sorted),
        ),
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)
    alpha_scores = _find_scores(
        calls, name="resolve_page_candidates_alphabetical", observation_id="obs-alpha"
    )
    mixed_scores = _find_scores(
        calls, name="resolve_page_candidates_alphabetical", observation_id="obs-mixed"
    )
    assert len(alpha_scores) == 1
    assert alpha_scores[0]["value"] == 1.0
    assert alpha_scores[0]["data_type"] == "BOOLEAN"
    assert alpha_scores[0]["trace_id"] == "trace-1"
    assert len(mixed_scores) == 1
    assert mixed_scores[0]["value"] == 0.0


def test_resolve_page_score_skipped_when_observation_has_no_id() -> None:
    """Observations without `id` can't be attached — we skip rather than raise."""
    client = MagicMock()
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="resolve_page",
            obs_id=None,
            output=json.dumps({"exists": False, "candidates": []}),
        ),
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)
    assert _find_scores(calls, name="resolve_page_candidates_alphabetical") == []


# =================================== glob ======================================


def test_glob_timeout_and_latency_emitted_per_observation() -> None:
    """`glob` TOOL obs gets BOOLEAN `glob_timed_out` AND NUMERIC `glob_latency_ms`."""
    client = MagicMock()
    start = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=3, milliseconds=500)
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="glob",
            obs_id="obs-glob-fast",
            output="/wiki/topics/alpha.md\n/wiki/topics/beta.md",
            start_time=start,
            end_time=end,
        ),
        _mk_obs(
            obs_type="TOOL",
            name="glob",
            obs_id="obs-glob-timeout",
            output=(
                "Error: glob timed out after 30s. Try a more specific pattern or a narrower path."
            ),
            start_time=start,
            end_time=start + timedelta(seconds=30),
        ),
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)

    fast_timeout = _find_scores(calls, name="glob_timed_out", observation_id="obs-glob-fast")
    assert len(fast_timeout) == 1
    assert fast_timeout[0]["value"] == 0.0
    assert fast_timeout[0]["data_type"] == "BOOLEAN"

    fast_latency = _find_scores(calls, name="glob_latency_ms", observation_id="obs-glob-fast")
    assert len(fast_latency) == 1
    assert fast_latency[0]["value"] == 3500.0
    assert fast_latency[0]["data_type"] == "NUMERIC"

    timeout_timeout = _find_scores(calls, name="glob_timed_out", observation_id="obs-glob-timeout")
    assert len(timeout_timeout) == 1
    assert timeout_timeout[0]["value"] == 1.0

    timeout_latency = _find_scores(calls, name="glob_latency_ms", observation_id="obs-glob-timeout")
    assert len(timeout_latency) == 1
    assert timeout_latency[0]["value"] == 30_000.0


def test_glob_latency_skipped_when_timestamps_missing() -> None:
    """In-flight obs (no endTime) => no latency score, but timeout still pushes."""
    client = MagicMock()
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="glob",
            obs_id="obs-glob-inflight",
            output="/wiki/topics/x.md",
        ),  # no start/end
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)
    assert len(_find_scores(calls, name="glob_latency_ms")) == 0
    # timeout score still gets emitted with value=0
    glob_timeout = _find_scores(calls, name="glob_timed_out", observation_id="obs-glob-inflight")
    assert len(glob_timeout) == 1
    assert glob_timeout[0]["value"] == 0.0


def test_glob_latency_skipped_when_timestamps_are_strings() -> None:
    """Legacy serializer may emit ISO-8601 strings => skip rather than guess.

    Exercises the ``except (TypeError, AttributeError)`` fallback in
    ``_observation_latency_ms``: string subtraction raises ``TypeError``
    and we prefer a missing score over a fabricated duration.
    """
    client = MagicMock()
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="glob",
            obs_id="obs-glob-strtime",
            output="/wiki/topics/x.md",
        ),
    ]
    # Bypass the _mk_obs datetime-typed helper to inject raw ISO strings,
    # matching the legacy serializer shape.
    observations[0]["startTime"] = "2026-04-18T12:00:00Z"
    observations[0]["endTime"] = "2026-04-18T12:00:01Z"
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)
    assert len(_find_scores(calls, name="glob_latency_ms")) == 0
    # timeout score still pushes (string output doesn't affect timeout detection)
    glob_timeout = _find_scores(calls, name="glob_timed_out", observation_id="obs-glob-strtime")
    assert len(glob_timeout) == 1
    assert glob_timeout[0]["value"] == 0.0


# ============================== reviewer AGENT =================================


def test_reviewer_merge_candidates_count_emitted_per_observation() -> None:
    """Each reviewer AGENT obs gets its own NUMERIC merge-count score."""
    client = MagicMock()
    report_a = {
        "verdict": "revise",
        "merge_candidates": ["slug-a", "slug-b", "slug-c"],
        "summary": "Three overlaps.",
    }
    report_b = {
        "verdict": "pass",
        "merge_candidates": [],
        "summary": "Clean.",
    }
    observations = [
        _mk_obs(
            obs_type="AGENT",
            name="reviewer",
            obs_id="obs-reviewer-a",
            output=json.dumps(report_a),
        ),
        _mk_obs(
            obs_type="AGENT",
            name="reviewer",
            obs_id="obs-reviewer-b",
            output=json.dumps(report_b),
        ),
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)

    a = _find_scores(calls, name="reviewer_merge_candidates_count", observation_id="obs-reviewer-a")
    assert len(a) == 1
    assert a[0]["value"] == 3.0
    assert a[0]["data_type"] == "NUMERIC"

    b = _find_scores(calls, name="reviewer_merge_candidates_count", observation_id="obs-reviewer-b")
    assert len(b) == 1
    assert b[0]["value"] == 0.0


# =========================== trace-level rollup ================================


def test_reviewer_unacted_merge_requests_is_sum_across_reviewer_obs() -> None:
    """Trace-level rollup sums every reviewer AGENT obs's merge count."""
    client = MagicMock()
    observations = [
        _mk_obs(
            obs_type="AGENT",
            name="reviewer",
            obs_id="obs-reviewer-a",
            output=json.dumps(
                {"verdict": "revise", "merge_candidates": ["a", "b"], "summary": "two"}
            ),
        ),
        _mk_obs(
            obs_type="AGENT",
            name="reviewer",
            obs_id="obs-reviewer-b",
            output=json.dumps({"verdict": "revise", "merge_candidates": ["c"], "summary": "one"}),
        ),
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)
    rollup = _find_scores(calls, name="reviewer_unacted_merge_requests", observation_id=None)
    assert len(rollup) == 1
    assert rollup[0]["value"] == 3.0
    assert rollup[0]["data_type"] == "NUMERIC"
    assert rollup[0]["trace_id"] == "trace-1"


def test_reviewer_rollup_zero_when_no_reviewer_obs() -> None:
    """No reviewer observations => rollup score = 0, still emitted."""
    client = MagicMock()
    emit_scores_for_trace(
        client,
        "trace-1",
        [_mk_obs(obs_type="TOOL", name="ls", obs_id=None)],
        message_id=None,
        content_page_cited=False,
    )
    calls = _scores_by_name_and_obs(client)
    rollup = _find_scores(calls, name="reviewer_unacted_merge_requests", observation_id=None)
    assert len(rollup) == 1
    assert rollup[0]["value"] == 0.0


# ============================ integration: all 7 trace + per-obs ===============


def test_per_obs_and_trace_scores_coexist_without_conflict() -> None:
    """A mixed trace emits the right count of trace-level + per-obs scores."""
    client = MagicMock()
    start = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(milliseconds=250)
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="resolve_page",
            obs_id="obs-rp",
            output=json.dumps(
                {
                    "exists": False,
                    "candidates": [
                        {"slug": "a"},
                        {"slug": "b"},
                        {"slug": "c"},
                    ],
                }
            ),
        ),
        _mk_obs(
            obs_type="TOOL",
            name="glob",
            obs_id="obs-g",
            output="/wiki/x.md",
            start_time=start,
            end_time=end,
        ),
        _mk_obs(
            obs_type="AGENT",
            name="reviewer",
            obs_id="obs-rv",
            output=json.dumps(
                {"verdict": "revise", "merge_candidates": ["dup-a"], "summary": "one"}
            ),
        ),
    ]
    emit_scores_for_trace(client, "trace-1", observations, message_id="m1", content_page_cited=True)
    calls = _scores_by_name_and_obs(client)
    names = [c["name"] for c in calls]

    # Trace-level (7): content_page_cited + original 5 + reviewer_unacted_merge_requests.
    trace_level_expected = {
        "content_page_cited",
        "gate_rejected_check_my_work",
        "auto_corrected",
        "wrote_todos_early",
        "reviewer_verdict",
        "compile_outcome",
        "reviewer_unacted_merge_requests",
    }
    assert trace_level_expected.issubset(set(names))
    for name in trace_level_expected:
        trace_scores = [c for c in calls if c["name"] == name and c.get("observation_id") is None]
        assert len(trace_scores) == 1, f"expected exactly 1 trace-level push for {name}"

    # Per-obs (4): resolve_page + glob_timed_out + glob_latency_ms + reviewer_merge_count.
    assert (
        len(
            _find_scores(
                calls, name="resolve_page_candidates_alphabetical", observation_id="obs-rp"
            )
        )
        == 1
    )
    assert len(_find_scores(calls, name="glob_timed_out", observation_id="obs-g")) == 1
    assert len(_find_scores(calls, name="glob_latency_ms", observation_id="obs-g")) == 1
    assert (
        len(_find_scores(calls, name="reviewer_merge_candidates_count", observation_id="obs-rv"))
        == 1
    )

    # Total pushes: 7 trace + 4 per-obs = 11
    assert client.create_score.call_count == 11


def test_per_obs_skipped_for_non_target_tools() -> None:
    """Unrelated TOOL obs (e.g. `read_file`) don't trigger per-obs scores."""
    client = MagicMock()
    observations = [
        _mk_obs(obs_type="TOOL", name="read_file", obs_id="obs-rf", output="file body"),
    ]
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    calls = _scores_by_name_and_obs(client)
    per_obs_names = {
        "resolve_page_candidates_alphabetical",
        "glob_timed_out",
        "glob_latency_ms",
        "reviewer_merge_candidates_count",
    }
    for c in calls:
        assert c["name"] not in per_obs_names or c.get("observation_id") is None


def test_per_obs_score_failure_does_not_block_others() -> None:
    """Simulate create_score raising — other pushes still happen."""
    client = MagicMock()
    # Every call raises; loop must press on.
    client.create_score.side_effect = RuntimeError("Langfuse 524")
    observations = [
        _mk_obs(
            obs_type="TOOL",
            name="resolve_page",
            obs_id="obs-rp",
            output=json.dumps({"exists": False, "candidates": []}),
        ),
        _mk_obs(
            obs_type="AGENT",
            name="reviewer",
            obs_id="obs-rv",
            output=json.dumps({"verdict": "pass", "merge_candidates": [], "summary": "ok"}),
        ),
    ]
    # Must not raise.
    emit_scores_for_trace(
        client, "trace-1", observations, message_id=None, content_page_cited=False
    )
    # 7 trace-level (content_page_cited emits because the flag was
    # precomputed) + 1 resolve_page + 1 reviewer per-obs = 9 attempted
    # pushes — all raise, none block the next.
    assert client.create_score.call_count == 9
