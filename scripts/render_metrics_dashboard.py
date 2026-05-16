"""Render ``docs/audits/dashboard.md`` from ``metrics-history.jsonl``.

The dashboard is the longitudinal view on top of the per-run dipstick:

- **Latest run summary** — value + Δ vs the prior run, by metric.
- **Trend table** — latest, prior, 7-day median, 30-day median, ASCII
  sparkline of the last 20 runs, trend arrow (↑ better / ↓ worse / →
  steady) per metric.
- **Per-prompt-version cohort table** — for the latest 5 distinct
  ``prompt_commit_sha`` values, mean + median of each metric across
  every run on that prompt version. The killer view: pre-revamp vs
  post-revamp side-by-side.
- **Outlier callouts** — any latest-run metric >2 stdev from the historical
  median surfaces a warning at the top.

Usage::

    uv run python scripts/render_metrics_dashboard.py
    uv run python scripts/render_metrics_dashboard.py --history /tmp/h.jsonl
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from statistics import median
from statistics import pstdev
from typing import Any

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.post_run_metrics import DASHBOARD_PATH  # noqa: E402
from scripts.post_run_metrics import HISTORY_PATH  # noqa: E402
from scripts.post_run_metrics import read_history  # noqa: E402

logger = structlog.get_logger(__name__)

SPARK_BUCKETS = "▁▂▃▄▅▆▇█"
SPARKLINE_WINDOW = 20
COHORT_LIMIT = 5
OUTLIER_SIGMA = 2.0
TREND_TOLERANCE = 0.05  # ±5% of 7-day-median is "→" (steady)

# Direction per metric. ``"up"`` means higher is better; ``"down"`` means
# lower is better. Targets come from the dipstick docstring (M1-M10).
METRIC_DIRECTION: dict[str, str] = {
    "M1": "up",  # owner_rate target ≥80%
    "M2": "up",  # lead_with_number_rate target ≥70%
    "M3": "up",  # active-teaching insights target ≥1
    "M4": "down",  # cmw pre-write rate target 0%
    "M5": "down",  # TL;DR rate target 0%
    "M6": "down",  # strikethrough rate target 0%
    "M7": "down",  # people-link median target ≤3 (lower = tidier)
    "M8": "up",  # reviewer pass rate target ≥60%
    "M9": "down",  # prompt tokens — fewer is cheaper, lower better
    # M10 is a distribution, not a scalar; excluded from trends.
}

METRIC_LABELS: dict[str, str] = {
    "M1": "owner: frontmatter %",
    "M2": "lead-with-number %",
    "M3": "active-teaching insights/email",
    "M4": "check_my_work pre-write %",
    "M5": "TL;DR H2 %",
    "M6": "strikethrough %",
    "M7": "people-wikilink median",
    "M8": "reviewer pass %",
    "M9": "prompt tokens avg",
}

METRIC_UNIT: dict[str, str] = {
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


def _fmt(metric: str, value: float | None) -> str:
    if value is None:
        return "-"
    if METRIC_UNIT.get(metric) == "pct":
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def _delta_str(metric: str, current: float | None, prior: float | None) -> str:
    if current is None or prior is None:
        return "-"
    delta = current - prior
    sign = "+" if delta >= 0 else ""
    if METRIC_UNIT.get(metric) == "pct":
        return f"{sign}{delta * 100:.1f}pp"
    return f"{sign}{delta:.2f}"


def _sparkline(values: list[float]) -> str:
    """Render a list of floats as an 8-bucket ASCII sparkline.

    Empty list → empty string. All-equal values → flat midline. None
    values are dropped before bucketing (the sparkline shows the
    *available* points, not gaps).
    """
    nums = [v for v in values if v is not None and not math.isnan(v)]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    if lo == hi:
        # Flat line — pick a mid-bucket so it's visible.
        return SPARK_BUCKETS[len(SPARK_BUCKETS) // 2] * len(nums)
    span = hi - lo
    out = []
    for v in nums:
        idx = int((v - lo) / span * (len(SPARK_BUCKETS) - 1))
        out.append(SPARK_BUCKETS[idx])
    return "".join(out)


def _trend_arrow(metric: str, latest: float | None, baseline: float | None) -> str:
    """↑ better, ↓ worse, → steady. Steady = within ±5% of baseline."""
    if latest is None or baseline is None:
        return "-"
    direction = METRIC_DIRECTION.get(metric, "up")
    if baseline == 0:
        # Avoid div-by-zero. Treat any non-zero latest as a clear
        # improvement (down→better) or regression (up→better).
        if latest == 0:
            return "→"
        return "↑" if direction == "down" else "↓"
    pct_change = (latest - baseline) / abs(baseline)
    if abs(pct_change) <= TREND_TOLERANCE:
        return "→"
    if direction == "up":
        return "↑" if latest > baseline else "↓"
    # direction == "down"
    return "↑" if latest < baseline else "↓"


def _values_for(rows: list[dict[str, Any]], metric: str) -> list[float]:
    """Pull the metric value out of each row, skipping nulls."""
    out = []
    for r in rows:
        v = (r.get("metrics") or {}).get(metric)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _sorted_by_ts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("timestamp") or "")


def _last_n(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return _sorted_by_ts(rows)[-n:]


def _outliers(rows: list[dict[str, Any]]) -> list[str]:
    """Flag latest-run metrics that drift >OUTLIER_SIGMA stdev off the median."""
    if len(rows) < 5:  # need a minimum sample to compute stdev meaningfully
        return []
    sorted_rows = _sorted_by_ts(rows)
    latest = sorted_rows[-1]
    history = sorted_rows[:-1]
    warnings: list[str] = []
    latest_metrics = latest.get("metrics") or {}
    for metric in METRIC_DIRECTION:
        values = _values_for(history, metric)
        if len(values) < 3:
            continue
        latest_val = latest_metrics.get(metric)
        if latest_val is None:
            continue
        med = median(values)
        sd = pstdev(values)
        if sd == 0:
            continue
        deviation = abs(latest_val - med) / sd
        if deviation >= OUTLIER_SIGMA:
            direction = "above" if latest_val > med else "below"
            warnings.append(
                f"**{metric}** ({METRIC_LABELS.get(metric, metric)}): "
                f"latest {_fmt(metric, latest_val)} is {deviation:.1f} stdev "
                f"{direction} median {_fmt(metric, med)}"
            )
    return warnings


def _render_top_summary(rows: list[dict[str, Any]]) -> list[str]:
    sorted_rows = _sorted_by_ts(rows)
    latest = sorted_rows[-1]
    prior = sorted_rows[-2] if len(sorted_rows) > 1 else None
    lines: list[str] = []
    lines.append("## Latest run")
    lines.append("")
    sha = latest.get("prompt_commit_sha", "unknown")
    lines.append(f"- **Timestamp**: `{latest.get('timestamp', '?')}`")
    lines.append(f"- **Prompt commit**: `{sha}`")
    lines.append(f"- **Model**: `{latest.get('model', 'unknown')}`")
    lines.append(f"- **Pages compiled this run**: {latest.get('pages_compiled_this_run', '?')}")
    lines.append(f"- **Wiki size (content pages)**: {latest.get('pages_total', '?')}")
    if prior:
        lines.append(
            f"- **Compared to prior run**: "
            f"`{prior.get('timestamp', '?')}` "
            f"(prompt `{prior.get('prompt_commit_sha', '?')}`)"
        )
    lines.append("")
    lines.append("| Metric | Latest | Prior | Δ |")
    lines.append("|---|---:|---:|---:|")
    latest_metrics = latest.get("metrics") or {}
    prior_metrics = (prior or {}).get("metrics") or {}
    for metric, label in METRIC_LABELS.items():
        cur = latest_metrics.get(metric)
        prev = prior_metrics.get(metric)
        lines.append(
            f"| {metric} {label} | {_fmt(metric, cur)} | "
            f"{_fmt(metric, prev)} | {_delta_str(metric, cur, prev)} |"
        )
    lines.append("")
    return lines


def _render_trend_table(rows: list[dict[str, Any]]) -> list[str]:
    sorted_rows = _sorted_by_ts(rows)
    latest = sorted_rows[-1]
    prior = sorted_rows[-2] if len(sorted_rows) > 1 else None
    last7 = _last_n(rows, 7)
    last30 = _last_n(rows, 30)
    last20 = _last_n(rows, SPARKLINE_WINDOW)
    lines: list[str] = []
    lines.append("## Trends")
    lines.append("")
    lines.append(
        "| Metric | Latest | Prior | 7d-median | 30d-median | Trend | "
        f"Sparkline (last {SPARKLINE_WINDOW}) |"
    )
    lines.append("|---|---:|---:|---:|---:|:---:|---|")
    latest_metrics = latest.get("metrics") or {}
    prior_metrics = (prior or {}).get("metrics") or {}
    for metric, label in METRIC_LABELS.items():
        cur = latest_metrics.get(metric)
        prev = prior_metrics.get(metric)
        med7_vals = _values_for(last7, metric)
        med30_vals = _values_for(last30, metric)
        med7 = median(med7_vals) if med7_vals else None
        med30 = median(med30_vals) if med30_vals else None
        spark_vals = _values_for(last20, metric)
        spark = _sparkline(spark_vals) or "-"
        arrow = _trend_arrow(metric, cur, med7)
        lines.append(
            f"| {metric} {label} | {_fmt(metric, cur)} | "
            f"{_fmt(metric, prev)} | {_fmt(metric, med7)} | "
            f"{_fmt(metric, med30)} | {arrow} | `{spark}` |"
        )
    lines.append("")
    return lines


def _render_cohort_table(rows: list[dict[str, Any]]) -> list[str]:
    """For each prompt_commit_sha cohort, mean + median per metric.

    The killer view: lined up cohorts let you see whether a prompt
    revamp moved each metric. We list latest 5 SHAs by their latest
    timestamp, newest first.
    """
    if not rows:
        return []
    cohorts: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        sha = r.get("prompt_commit_sha") or "unknown"
        cohorts.setdefault(sha, []).append(r)
    # Sort cohorts by their newest run's timestamp, descending.
    sha_order = sorted(
        cohorts.items(),
        key=lambda kv: max((r.get("timestamp") or "") for r in kv[1]),
        reverse=True,
    )[:COHORT_LIMIT]
    lines: list[str] = []
    lines.append(f"## Per-prompt-version cohorts (latest {COHORT_LIMIT})")
    lines.append("")
    lines.append(
        "Each row averages every run that landed on that prompt commit. "
        "Metric format: `median (mean, n)`."
    )
    lines.append("")
    header = "| Prompt SHA | Runs | " + " | ".join(METRIC_LABELS) + " |"
    sep = "|---|---:|" + "|".join(["---:"] * len(METRIC_LABELS)) + "|"
    lines.append(header)
    lines.append(sep)
    for sha, cohort_rows in sha_order:
        cells = [f"`{sha}`", str(len(cohort_rows))]
        for metric in METRIC_LABELS:
            vals = _values_for(cohort_rows, metric)
            if not vals:
                cells.append("-")
                continue
            med = median(vals)
            mean = sum(vals) / len(vals)
            cells.append(f"{_fmt(metric, med)} ({_fmt(metric, mean)}, {len(vals)})")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def render_dashboard(
    history_path: Path = HISTORY_PATH,
    out_path: Path = DASHBOARD_PATH,
) -> Path:
    """Read the JSONL history and write ``dashboard.md``. Returns out_path.

    Empty history → renders a stub explaining how to populate it.
    """
    rows = read_history(history_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out_path.write_text(
            "# Metrics dashboard\n\n"
            "_No runs in history yet. Run `make compile` (which auto-emits the "
            "dipstick) or `uv run python scripts/post_run_metrics.py` to "
            "populate `docs/audits/metrics-history.jsonl`._\n",
            encoding="utf-8",
        )
        return out_path
    lines: list[str] = []
    lines.append("# Metrics dashboard")
    lines.append("")
    lines.append(f"_Generated from `{history_path.name}` ({len(rows)} runs)._")
    lines.append("")
    outliers = _outliers(rows)
    if outliers:
        lines.append("## Outliers in latest run")
        lines.append("")
        for w in outliers:
            lines.append(f"- {w}")
        lines.append("")
    lines.extend(_render_top_summary(rows))
    lines.extend(_render_trend_table(rows))
    lines.extend(_render_cohort_table(rows))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


@click.command()
@click.option(
    "--history",
    "history_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=HISTORY_PATH,
    help="JSONL input. Default: docs/audits/metrics-history.jsonl",
)
@click.option(
    "--output",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DASHBOARD_PATH,
    help="Markdown output. Default: docs/audits/dashboard.md",
)
def main(history_path: Path, out_path: Path) -> None:
    """Render the longitudinal metrics dashboard from JSONL history."""
    written = render_dashboard(history_path, out_path)
    click.echo(f"Wrote: {written}")


__all__ = [
    "METRIC_DIRECTION",
    "METRIC_LABELS",
    "METRIC_UNIT",
    "_outliers",
    "_sparkline",
    "_trend_arrow",
    "render_dashboard",
]


if __name__ == "__main__":
    main()
