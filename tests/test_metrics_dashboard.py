"""Tests for the longitudinal metrics dashboard layer.

Covers:
- ``append_to_history`` + ``read_history`` round-trip
- ``push_langfuse_scores`` payload shape (mocked langfuse client)
- Dashboard rendering: sparklines, trend arrows, cohort table, outliers
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.post_run_metrics import MetricResult  # noqa: E402
from scripts.post_run_metrics import Report  # noqa: E402
from scripts.post_run_metrics import append_to_history  # noqa: E402
from scripts.post_run_metrics import push_langfuse_scores  # noqa: E402
from scripts.post_run_metrics import read_history  # noqa: E402
from scripts.render_metrics_dashboard import _outliers  # noqa: E402
from scripts.render_metrics_dashboard import _sparkline  # noqa: E402
from scripts.render_metrics_dashboard import _trend_arrow  # noqa: E402
from scripts.render_metrics_dashboard import render_dashboard  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(values: dict[str, float | None]) -> Report:
    metrics = []
    units = {
        "M1": "pct",
        "M2": "pct",
        "M3": "raw",
        "M4": "pct",
        "M5": "pct",
        "M6": "pct",
        "M7": "raw",
        "M8": "pct",
        "M9": "raw",
    }
    for name, value in values.items():
        metrics.append(
            MetricResult(
                name=name,
                label=f"label-{name}",
                value=value,
                target="target",
                sample_size=10,
                unit=units.get(name, "raw"),
            )
        )
    return Report(
        run_id="abc",
        generated_at="2026-04-29T00:00:00+00:00",
        since=None,
        new_pages_total=5,
        metrics=metrics,
        archetype_dist={"launch": 2, "bug": 1},
    )


# ---------------------------------------------------------------------------
# Layer 1 — JSONL append + read round-trip
# ---------------------------------------------------------------------------


def test_append_and_read_history_round_trip(tmp_path: Path) -> None:
    history = tmp_path / "metrics-history.jsonl"
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "topics").mkdir()
    (wiki_dir / "topics" / "alpha.md").write_text("---\n---\n", encoding="utf-8")
    (wiki_dir / "topics" / "beta.md").write_text("---\n---\n", encoding="utf-8")

    report1 = _make_report({"M1": 0.85, "M3": 2.5, "M9": 1234.5})
    report2 = _make_report({"M1": 0.90, "M3": 3.0, "M9": 1100.0})

    # Mock _resolve_run_meta — DB isn't available in unit tests.
    with patch("scripts.post_run_metrics._resolve_run_meta") as m:
        m.return_value = {"model": "test-model", "pages_compiled_this_run": 4}
        append_to_history(
            report1,
            run_id=None,
            prompt_commit_sha="abc1234",
            wiki_dir=wiki_dir,
            history_path=history,
        )
        append_to_history(
            report2,
            run_id=None,
            prompt_commit_sha="def5678",
            wiki_dir=wiki_dir,
            history_path=history,
        )

    rows = read_history(history)
    assert len(rows) == 2
    assert rows[0]["prompt_commit_sha"] == "abc1234"
    assert rows[1]["prompt_commit_sha"] == "def5678"
    assert rows[0]["metrics"]["M1"] == 0.85
    assert rows[1]["metrics"]["M9"] == 1100.0
    assert rows[0]["pages_total"] == 2  # two .md files in topics/
    assert rows[0]["model"] == "test-model"
    assert rows[0]["archetype_dist"] == {"launch": 2, "bug": 1}


def test_read_history_skips_malformed_lines(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    history.write_text(
        '{"run_id": "ok", "metrics": {}}\nthis is not json\n\n{"run_id": "ok2", "metrics": {}}\n',
        encoding="utf-8",
    )
    rows = read_history(history)
    assert len(rows) == 2
    assert rows[0]["run_id"] == "ok"
    assert rows[1]["run_id"] == "ok2"


def test_read_history_empty_file_returns_empty_list(tmp_path: Path) -> None:
    history = tmp_path / "absent.jsonl"
    assert read_history(history) == []


# ---------------------------------------------------------------------------
# Layer 3 — Langfuse score push (mocked client)
# ---------------------------------------------------------------------------


def test_push_langfuse_scores_payload_shape() -> None:
    report = _make_report(
        {
            "M3": 2.5,  # active-teaching insights/email
            "M4": 0.05,  # cmw pre-write rate
            "M8": 0.75,  # reviewer pass rate
            "M9": 1500.0,  # prompt tokens avg
            "M1": 0.90,  # NOT a trace metric — should be skipped
            "M5": 0.10,  # NOT a trace metric — should be skipped
        }
    )
    fake_client = MagicMock()
    fake_client.create_score = MagicMock()
    fake_client.flush = MagicMock()

    # Patch `_build_client` directly: that's the contract
    # `push_langfuse_scores` depends on after the refactor. Patching
    # `scripts.post_run_metrics.settings` is INERT — `_build_client`
    # reads from `src.observability.langfuse_scores.settings`, a
    # different module. The previous version of this test only passed
    # because real env credentials happened to populate the gate;
    # CI without Langfuse keys would fail.
    with patch("src.observability.langfuse_scores._build_client", return_value=fake_client):
        n = push_langfuse_scores(report, run_id=None, prompt_commit_sha="abc1234")

    assert n == 4  # M3, M4, M8, M9 — wiki-side metrics excluded
    score_calls = fake_client.create_score.call_args_list
    score_names = {c.kwargs["name"] for c in score_calls}
    assert score_names == {
        "log_insight_active_teaching_per_email",
        "check_my_work_pre_write_rate",
        "reviewer_pass_first_cycle_rate",
        "prompt_tokens_avg_per_trace",
    }
    # Every score must have session_id + comment — that's the contract.
    for call in score_calls:
        assert call.kwargs["session_id"].startswith("compile-metrics-")
        assert "abc1234" in call.kwargs["comment"]
        assert call.kwargs["data_type"] == "NUMERIC"
        assert isinstance(call.kwargs["value"], float)


def test_push_langfuse_scores_skips_when_disabled() -> None:
    # `_build_client()` returns None when Langfuse is disabled / unconfigured.
    # Patch the helper directly — that's the contract `push_langfuse_scores`
    # depends on after the refactor to share with `langfuse_scores.py`.
    report = _make_report({"M3": 2.5})
    with patch("src.observability.langfuse_scores._build_client", return_value=None):
        n = push_langfuse_scores(report, run_id=None, prompt_commit_sha="x")
    assert n == 0


def test_push_langfuse_scores_skips_when_no_credentials() -> None:
    report = _make_report({"M3": 2.5})
    with patch("src.observability.langfuse_scores._build_client", return_value=None):
        n = push_langfuse_scores(report, run_id=None, prompt_commit_sha="x")
    assert n == 0


def test_push_langfuse_scores_skips_when_all_metrics_null() -> None:
    """No trace-derived metric has a value → no SDK call attempted."""
    report = _make_report({"M3": None, "M4": None, "M8": None, "M9": None})
    # Even with a working client, when all pushable metrics are None the
    # function short-circuits before _build_client is even called.
    with patch("src.observability.langfuse_scores._build_client") as mock_build:
        n = push_langfuse_scores(report, run_id=None, prompt_commit_sha="x")
    assert n == 0
    mock_build.assert_not_called()


def test_push_langfuse_scores_per_metric_failure_does_not_break_others() -> None:
    report = _make_report({"M3": 2.0, "M4": 0.1, "M8": 0.7, "M9": 100.0})
    fake_client = MagicMock()

    # First call raises; others succeed. Expected result: 3 pushed.
    def flaky(*_args: Any, **kwargs: Any) -> None:
        if kwargs.get("name") == "log_insight_active_teaching_per_email":
            raise RuntimeError("simulated 524")

    fake_client.create_score = MagicMock(side_effect=flaky)
    fake_client.flush = MagicMock()

    # Patch `_build_client` (see test_push_langfuse_scores_payload_shape
    # for why patching `scripts.post_run_metrics.settings` is inert).
    with patch("src.observability.langfuse_scores._build_client", return_value=fake_client):
        n = push_langfuse_scores(report, run_id=None, prompt_commit_sha="abc")
    assert n == 3


# ---------------------------------------------------------------------------
# Layer 2 — Dashboard helpers
# ---------------------------------------------------------------------------


def test_sparkline_renders_8_bucket_string() -> None:
    s = _sparkline([0.0, 0.5, 1.0])
    assert len(s) == 3
    # Lowest value = first bucket; highest = last bucket.
    assert s[0] == "▁"
    assert s[-1] == "█"


def test_sparkline_handles_empty_and_flat() -> None:
    assert _sparkline([]) == ""
    flat = _sparkline([0.5, 0.5, 0.5])
    assert len(flat) == 3
    # All same character — flat midline.
    assert len(set(flat)) == 1


def test_trend_arrow_up_metric_better_when_higher() -> None:
    # M1: higher is better. Latest 0.95 vs baseline 0.80 → ↑.
    assert _trend_arrow("M1", latest=0.95, baseline=0.80) == "↑"
    assert _trend_arrow("M1", latest=0.50, baseline=0.80) == "↓"


def test_trend_arrow_down_metric_better_when_lower() -> None:
    # M5: lower is better. Latest 0.0 vs baseline 0.10 → ↑ (improvement).
    assert _trend_arrow("M5", latest=0.0, baseline=0.10) == "↑"
    assert _trend_arrow("M5", latest=0.20, baseline=0.10) == "↓"


def test_trend_arrow_steady_within_tolerance() -> None:
    # Within ±5% of baseline → →
    assert _trend_arrow("M1", latest=0.81, baseline=0.80) == "→"


def test_trend_arrow_handles_none() -> None:
    assert _trend_arrow("M1", latest=None, baseline=0.5) == "-"
    assert _trend_arrow("M1", latest=0.5, baseline=None) == "-"


def test_outliers_flag_extreme_latest_values() -> None:
    # 7 history rows clustered around 0.50 + 1 extreme latest at 0.99.
    rows = []
    for i in range(7):
        rows.append(
            {
                "timestamp": f"2026-04-{20 + i}T00:00:00+00:00",
                "prompt_commit_sha": "old",
                "metrics": {"M1": 0.50 + (i * 0.01)},  # 0.50..0.56
            }
        )
    rows.append(
        {
            "timestamp": "2026-04-29T00:00:00+00:00",
            "prompt_commit_sha": "new",
            "metrics": {"M1": 0.99},
        }
    )
    warnings = _outliers(rows)
    assert any("M1" in w for w in warnings)


def test_outliers_silent_when_history_too_small() -> None:
    rows = [
        {"timestamp": "t1", "metrics": {"M1": 0.5}},
        {"timestamp": "t2", "metrics": {"M1": 0.99}},
    ]
    assert _outliers(rows) == []


# ---------------------------------------------------------------------------
# End-to-end: render_dashboard against synthetic JSONL
# ---------------------------------------------------------------------------


def _write_history(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_render_dashboard_with_empty_history(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    out = tmp_path / "dashboard.md"
    written = render_dashboard(history, out)
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "No runs in history" in text


def test_render_dashboard_full_smoke(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    out = tmp_path / "dashboard.md"
    rows = [
        {
            "run_id": f"r{i}",
            "timestamp": f"2026-04-{20 + i}T00:00:00+00:00",
            "prompt_commit_sha": "abc1234" if i < 3 else "def5678",
            "model": "claude-sonnet-4",
            "pages_total": 100 + i,
            "pages_compiled_this_run": 5 + i,
            "new_pages_window": 3,
            "metrics": {
                "M1": 0.5 + i * 0.05,
                "M3": 1.0 + i * 0.1,
                "M5": 0.20 - i * 0.02,
                "M9": 1500.0 - i * 50,
            },
            "archetype_dist": {"launch": i, "bug": 1},
        }
        for i in range(6)
    ]
    _write_history(history, rows)

    written = render_dashboard(history, out)
    text = written.read_text(encoding="utf-8")

    # Top summary
    assert "## Latest run" in text
    assert "abc1234" in text or "def5678" in text  # prompt SHA shown
    # Trend table with sparklines
    assert "## Trends" in text
    assert "Sparkline" in text
    assert "M1" in text
    # Cohort table
    assert "Per-prompt-version cohorts" in text
    assert "abc1234" in text
    assert "def5678" in text
    # Trend arrow exists
    assert ("↑" in text) or ("↓" in text) or ("→" in text)


def test_render_dashboard_outputs_sparkline_chars(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    out = tmp_path / "dashboard.md"
    rows = [
        {
            "run_id": f"r{i}",
            "timestamp": f"2026-04-{20 + i}T00:00:00+00:00",
            "prompt_commit_sha": "abc",
            "metrics": {"M1": i / 10.0},
        }
        for i in range(5)
    ]
    _write_history(history, rows)
    render_dashboard(history, out)
    text = out.read_text(encoding="utf-8")
    # At least one bucket char from the 8-bucket scheme appears.
    assert any(ch in text for ch in "▁▂▃▄▅▆▇█")


def test_render_dashboard_outliers_block_appears_when_triggered(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    out = tmp_path / "dashboard.md"
    rows = []
    for i in range(7):
        rows.append(
            {
                "run_id": f"r{i}",
                "timestamp": f"2026-04-{20 + i}T00:00:00+00:00",
                "prompt_commit_sha": "old",
                "metrics": {"M1": 0.50 + i * 0.005},
            }
        )
    rows.append(
        {
            "run_id": "latest",
            "timestamp": "2026-04-29T00:00:00+00:00",
            "prompt_commit_sha": "new",
            "metrics": {"M1": 0.99},
        }
    )
    _write_history(history, rows)
    render_dashboard(history, out)
    text = out.read_text(encoding="utf-8")
    assert "Outliers in latest run" in text


# ---------------------------------------------------------------------------
# Prompt SHA helper
# ---------------------------------------------------------------------------


def test_prompt_commit_sha_returns_short_string() -> None:
    """Smoke test against the real repo — must return non-empty short SHA."""
    from scripts.post_run_metrics import _prompt_commit_sha

    sha = _prompt_commit_sha()
    assert isinstance(sha, str)
    assert sha != ""
    # Either exactly 7 chars (the `--abbrev=7` flag) or "unknown".
    assert sha == "unknown" or (len(sha) == 7 and sha.isalnum())


def test_prompt_commit_sha_returns_unknown_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "nope.py"
    monkeypatch.setattr("scripts.post_run_metrics.PROMPTS_FILE", fake)
    from scripts.post_run_metrics import _prompt_commit_sha

    assert _prompt_commit_sha() == "unknown"
