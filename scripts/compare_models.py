"""Canonical CLI for model A/B comparisons — one place for every metric.

Every "did this PR move the needle?" readout in the past two weeks was a
one-off Postgres query. This script standardizes the 15 metrics we
actually look at — volume, outcomes, failure classes, tool efficiency,
content citation, cost — so comparisons are reproducible by anyone and
so adding a new metric is a 3-LOC registry entry, not a new SQL file.

Usage::

    # "Did PR X move the needle?" — the most-run shape.
    uv run python scripts/compare_models.py \\
        --compare-window pre-PR-225,post-PR-225 --format markdown

    # Weekly scorecard across all models seen in window.
    uv run python scripts/compare_models.py --since 7d

    # Specific models + CSV snapshot to docs/feedback.
    uv run python scripts/compare_models.py \\
        --since 2026-04-17 --until 2026-04-24 \\
        --models grok,minimax --format csv

    # Exact runs for a cost audit.
    uv run python scripts/compare_models.py \\
        --run-ids 8bfb66f4-...,a1452a9c-... --format table

Graceful failure: individual metric queries that fail (missing column,
DB down during rollout, etc.) return ``None`` for the affected models
and render as ``-`` / ``null``. The run never aborts mid-scorecard.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import uuid
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from typing import cast

import click
import psycopg
import structlog

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.trace_scorecard import _parse_since  # noqa: E402
from src.db import connect  # noqa: E402
from src.db.compare_metrics import error_class_counts  # noqa: E402
from src.db.compare_metrics import outcomes_by_model  # noqa: E402
from src.db.compare_metrics import tool_stats_by_model  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

# Each entry is ``(since, until)``. ``_parse_since`` handles both
# ``YYYY-MM-DD`` and ``24h``/``7d`` grammars; ``'now'`` resolves to None.
COMPARE_WINDOWS: dict[str, tuple[str, str]] = {
    "pre-PR-225": ("2026-04-17", "2026-04-18"),
    "post-PR-225": ("2026-04-23", "2026-04-24"),
    "cycle-9": ("2026-04-17", "2026-04-18"),
    "cycle-10": ("2026-04-18", "2026-04-19"),
    "last-week": ("7d", "now"),
    "today": ("24h", "now"),
}


# ---------------------------------------------------------------------
# Metric registry
# ---------------------------------------------------------------------

MetricFn = Callable[
    [psycopg.Connection, datetime, datetime | None, list[str] | None],
    dict[str, float | None],
]


def _project(
    source_fn: Callable[
        [psycopg.Connection, datetime, datetime | None, list[str] | None],
        dict[str, dict[str, float | int]],
    ],
    key: str | tuple[str, ...],
) -> MetricFn:
    """Project one field (or sum of fields) out of a per-model dict source.

    Most metrics are trivial extractions from ``outcomes_by_model`` or
    ``error_class_counts``. Wrapping each in its own function was
    boilerplate; this factory collapses the repetition while keeping
    the registry's shape (one MetricFn per name).
    """
    keys = (key,) if isinstance(key, str) else key

    def _fn(
        conn: psycopg.Connection,
        since: datetime,
        until: datetime | None,
        models: list[str] | None,
    ) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for model, d in source_fn(conn, since, until, models).items():
            total = sum(d[k] for k in keys)
            out[model] = float(total)
        return out

    return _fn


def _outcomes(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, dict[str, float | int]]:
    """Adapter: ``outcomes_by_model`` typed as ``dict[str, float | int]``."""
    return {
        m: dict(d)
        for m, d in outcomes_by_model(conn, since=since, until=until, models=models).items()
    }


def _errors(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, dict[str, float | int]]:
    return {
        m: dict(d)
        for m, d in error_class_counts(conn, since=since, until=until, models=models).items()
    }


def _metric_valid_pct(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """``(compiled + skipped) / attempts * 100`` — clean-termination rate."""
    out: dict[str, float | None] = {}
    for model, d in outcomes_by_model(conn, since=since, until=until, models=models).items():
        total = d["attempts"]
        valid = d["compiled"] + d["skipped"]
        out[model] = (valid / total * 100.0) if total else None
    return out


def _metric_skip_discrimination_pct(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """``skipped / (compiled + skipped) * 100`` — Kimi 75% over-skip signal."""
    out: dict[str, float | None] = {}
    for model, d in outcomes_by_model(conn, since=since, until=until, models=models).items():
        valid = d["compiled"] + d["skipped"]
        out[model] = (d["skipped"] / valid * 100.0) if valid else None
    return out


def _metric_avg_tools_per_batch(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """Mean tool-call count per batch (NaN → None so the render shows ``-``)."""
    out: dict[str, float | None] = {}
    for model, d in tool_stats_by_model(conn, since=since, until=until, models=models).items():
        avg = d["avg_tools_per_batch"]
        out[model] = None if math.isnan(avg) else avg
    return out


def _metric_avg_turns_per_batch(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """Placeholder — ``turns`` not yet persisted.

    Returns empty dict; render shows ``-`` for every model. Registry
    entry exists so the metric is addressable for filtering and
    dashboards don't break when the data lands.
    """
    return {}


def _metric_check_my_work_blocks_total(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """Total ``check_my_work`` calls whose output preview said 'block'."""
    return {
        m: d["check_my_work_blocks"]
        for m, d in tool_stats_by_model(conn, since=since, until=until, models=models).items()
    }


def _metric_pages_normalized(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """Count of ``message_touched_pages`` rows joined via attempts in window."""
    sql = """
        SELECT COALESCE(ca.compile_model, 'unknown') AS model,
               COUNT(*) AS touches
          FROM compile_attempts ca
          JOIN message_touched_pages mtp ON mtp.message_id = ca.message_id
         WHERE ca.attempted_at >= %(since)s
           AND ca.attempted_at < COALESCE(%(until)s, now())
         GROUP BY COALESCE(ca.compile_model, 'unknown')
    """
    return _query_per_model(conn, sql, since, until, "touches", models)


def _metric_cost_usd(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """Sum of ``compile_runs.cost_cents / 100`` per model.

    Returns None for models whose runs have cost_cents IS NULL (typical
    today — cost tracking not wired for every provider yet).
    """
    sql = """
        SELECT COALESCE(ca.compile_model, 'unknown') AS model,
               SUM(cr.cost_cents)::float / 100.0 AS cost_usd
          FROM compile_attempts ca
          JOIN compile_runs cr ON cr.run_id = ca.run_id
         WHERE ca.attempted_at >= %(since)s
           AND ca.attempted_at < COALESCE(%(until)s, now())
         GROUP BY COALESCE(ca.compile_model, 'unknown')
    """
    return _query_per_model(conn, sql, since, until, "cost_usd", models, allow_null=True)


def _metric_unique_threads_processed(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
) -> dict[str, float | None]:
    """``COUNT(DISTINCT thread_id)`` per model — cross-thread reach."""
    sql = """
        SELECT COALESCE(ca.compile_model, 'unknown') AS model,
               COUNT(DISTINCT m.thread_id) AS unique_threads
          FROM compile_attempts ca
          JOIN messages m ON m.message_id = ca.message_id
         WHERE ca.attempted_at >= %(since)s
           AND ca.attempted_at < COALESCE(%(until)s, now())
         GROUP BY COALESCE(ca.compile_model, 'unknown')
    """
    return _query_per_model(conn, sql, since, until, "unique_threads", models)


def _query_per_model(
    conn: psycopg.Connection,
    sql: str,
    since: datetime,
    until: datetime | None,
    value_col: str,
    models: list[str] | None,
    *,
    allow_null: bool = False,
) -> dict[str, float | None]:
    """Run a GROUP-BY-model query; return ``{model: float | None}``.

    Shared shell for the three metrics that can't use
    ``outcomes_by_model`` / ``error_class_counts`` because they join
    extra tables (touched_pages, compile_runs, messages). Returns {}
    on DB error so a missing-column rollout doesn't crash the script.
    """
    try:
        rows = cast(
            "list[dict[str, Any]]",
            conn.execute(sql, {"since": since, "until": until}).fetchall(),
        )
    except psycopg.Error as exc:
        logger.warning("per_model_query_failed", error=str(exc), col=value_col)
        return {}
    out: dict[str, float | None] = {}
    for r in rows:
        model = str(r["model"])
        if not _model_matches(model, models):
            continue
        val = r[value_col]
        if val is None:
            out[model] = None if allow_null else 0.0
        else:
            out[model] = float(val)
    return out


# Registry order drives row order in the rendered output. Adding a new
# metric is: (1) write the ``_metric_*`` function (or pass a key pair to
# ``_project``), (2) add an entry here, (3) add one test row.
METRIC_REGISTRY: dict[str, MetricFn] = {
    "volume_attempts": _project(_outcomes, "attempts"),
    "outcome_compiled": _project(_outcomes, "compiled"),
    "outcome_skipped": _project(_outcomes, "skipped"),
    "outcome_failed": _project(_outcomes, ("failed", "timeout")),
    "valid_pct": _metric_valid_pct,
    "skip_discrimination_pct": _metric_skip_discrimination_pct,
    "error_recursion_fail": _project(_errors, "recursion_fail"),
    "error_not_cited": _project(_errors, "not_cited"),
    "error_timeout": _project(_outcomes, "timeout"),
    "avg_tools_per_batch": _metric_avg_tools_per_batch,
    "avg_turns_per_batch": _metric_avg_turns_per_batch,
    "pages_normalized": _metric_pages_normalized,
    "check_my_work_blocks_total": _metric_check_my_work_blocks_total,
    "cost_usd": _metric_cost_usd,
    "unique_threads_processed": _metric_unique_threads_processed,
}


# ---------------------------------------------------------------------
# Window + model resolution
# ---------------------------------------------------------------------


def _parse_window(since: str, until: str) -> tuple[datetime, datetime | None]:
    """Return ``(since_dt, until_dt_or_None)``.

    ``until='now'`` → None so the SQL ``COALESCE(%(until)s, now())``
    picks up the server clock.
    """
    since_dt = _parse_since(since)
    if until.strip().lower() == "now":
        return since_dt, None
    return since_dt, _parse_since(until)


def _resolve_compare_window(
    name: str,
) -> tuple[tuple[datetime, datetime | None], ...]:
    """Turn a ``--compare-window`` arg into ``((since, until), ...)``.

    Accepts single (``pre-PR-225``) or comma-paired names
    (``pre-PR-225,post-PR-225``) for side-by-side diffs.
    """
    names = [n.strip() for n in name.split(",") if n.strip()]
    if not names:
        raise click.BadParameter("empty --compare-window")
    windows: list[tuple[datetime, datetime | None]] = []
    for n in names:
        if n not in COMPARE_WINDOWS:
            raise click.BadParameter(f"unknown window {n!r}; pick from {sorted(COMPARE_WINDOWS)}")
        since_s, until_s = COMPARE_WINDOWS[n]
        windows.append(_parse_window(since_s, until_s))
    return tuple(windows)


def _resolve_run_ids(
    conn: psycopg.Connection, run_ids: list[uuid.UUID]
) -> tuple[datetime, datetime | None]:
    """Compute the min/max attempted_at bracket covering the given runs."""
    sql = """
        SELECT MIN(attempted_at) AS lo, MAX(attempted_at) AS hi
          FROM compile_attempts
         WHERE run_id = ANY(%s)
    """
    row = cast(
        "dict[str, Any] | None",
        conn.execute(sql, (run_ids,)).fetchone(),
    )
    if row is None or row["lo"] is None:
        raise click.ClickException(f"no compile_attempts rows for run_ids {run_ids!r}")
    # +1s so the SQL ``<`` comparison includes the last attempt.
    return row["lo"], row["hi"] + timedelta(seconds=1)


def _all_models_in_window(
    conn: psycopg.Connection,
    since: datetime,
    until: datetime | None,
) -> list[str]:
    """Return distinct ``compile_model`` values seen in the window, sorted."""
    sql = """
        SELECT DISTINCT COALESCE(compile_model, 'unknown') AS model
          FROM compile_attempts
         WHERE attempted_at >= %(since)s
           AND attempted_at < COALESCE(%(until)s, now())
         ORDER BY model
    """
    rows = cast(
        "list[dict[str, Any]]",
        conn.execute(sql, {"since": since, "until": until}).fetchall(),
    )
    return [str(r["model"]) for r in rows]


def _model_matches(model: str, filters: list[str] | None) -> bool:
    """Substring-match ``model`` against any of ``filters``. None → always True."""
    if not filters:
        return True
    return any(f.lower() in model.lower() for f in filters)


# ---------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------


def _collect_metrics(
    conn: psycopg.Connection,
    *,
    since: datetime,
    until: datetime | None,
    models: list[str] | None,
    metric_names: list[str] | None = None,
) -> tuple[dict[str, dict[str, float | None]], list[str]]:
    """Run every registered metric; return ``(metrics, sorted_model_list)``.

    The seen-model list is the union of models returned by any metric
    (or the caller-provided filter, projected onto what's actually in
    the DB, so a pinned model that ran zero attempts still gets a
    blank column instead of silently disappearing).
    """
    names = metric_names or list(METRIC_REGISTRY)
    metrics: dict[str, dict[str, float | None]] = {}
    seen_models: set[str] = set()
    for name in names:
        result = METRIC_REGISTRY[name](conn, since, until, models)
        metrics[name] = result
        seen_models.update(result)
    if models:
        for m in _all_models_in_window(conn, since, until):
            if _model_matches(m, models):
                seen_models.add(m)
    return metrics, sorted(seen_models)


# ---------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------


def _fmt_value(value: float | None) -> str:
    """``None`` → ``-``; integer floats drop the decimal; others use 2dp."""
    if value is None:
        return "-"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _render_table(
    metrics: dict[str, dict[str, float | None]],
    models: list[str],
    window_label: str,
) -> str:
    """ASCII table with metrics down the left, models across the top."""
    headers = ["metric", *models]
    rows = [[name, *[_fmt_value(metrics[name].get(m)) for m in models]] for name in metrics]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def _fmt_row(cells: list[str]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths, strict=True))

    lines = [
        f"# compare_models — {window_label}",
        "",
        _fmt_row(headers),
        _fmt_row(["-" * w for w in widths]),
        *[_fmt_row(r) for r in rows],
    ]
    return "\n".join(lines)


def _render_markdown(
    metrics: dict[str, dict[str, float | None]],
    models: list[str],
    window_label: str,
) -> str:
    """Pipe-delimited markdown table — same shape as ``_render_table``."""
    headers = ["metric", *models]
    lines = [
        f"# compare_models — {window_label}",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for name, per_model in metrics.items():
        cells = [name, *[_fmt_value(per_model.get(m)) for m in models]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_delta(
    before: dict[str, dict[str, float | None]],
    after: dict[str, dict[str, float | None]],
    models: list[str],
) -> str:
    """Per-model ``after - before`` delta table; ``-`` where either side is None."""
    lines = [
        "",
        "## Delta (post - pre)",
        "",
        "| metric | " + " | ".join(f"Δ {m}" for m in models) + " |",
        "|" + "|".join(["---"] * (1 + len(models))) + "|",
    ]
    for name in before:
        cells = [name]
        for m in models:
            b = before[name].get(m)
            a = after.get(name, {}).get(m)
            cells.append(_fmt_value(a - b) if a is not None and b is not None else "-")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_csv(
    metrics: dict[str, dict[str, float | None]],
    models: list[str],
) -> str:
    """One row per metric; first column = metric name, rest = model values."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", *models])
    for name, per_model in metrics.items():
        writer.writerow([name, *[_fmt_value(per_model.get(m)) for m in models]])
    return buf.getvalue()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"unserializable: {type(value).__name__}")


def _window_label(since: datetime, until: datetime | None) -> str:
    end = until.date().isoformat() if until else "now"
    return f"{since.date().isoformat()} -> {end}"


def _write_or_print(
    content: str,
    *,
    output_dir: Path,
    fmt: str,
    window_label: str,
) -> None:
    """Echo table/markdown to stdout; write csv/json to ``output_dir``."""
    if fmt in {"table", "markdown"}:
        click.echo(content)
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = "csv" if fmt == "csv" else "json"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = window_label.replace(" ", "").replace("->", "to")
    out_path = output_dir / f"compare-models-{safe_label}-{stamp}.{ext}"
    out_path.write_text(content)
    click.echo(f"wrote {out_path}")


def _split_csv(value: str | None) -> list[str] | None:
    """``'a,b, c'`` → ``['a', 'b', 'c']``. None / empty → None."""
    if not value:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return parts or None


def _parse_run_ids(value: str | None) -> list[uuid.UUID] | None:
    parts = _split_csv(value)
    if not parts:
        return None
    try:
        return [uuid.UUID(p) for p in parts]
    except ValueError as exc:
        raise click.BadParameter(f"invalid UUID in --run-ids: {exc}") from exc


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


@click.command()
@click.option(
    "--since",
    default="7d",
    show_default=True,
    help="Window start: '24h', '7d', or 'YYYY-MM-DD'. Matches trace_scorecard.",
)
@click.option(
    "--until",
    default="now",
    show_default=True,
    help="Window end: 'now', '24h' (ago), '7d' (ago), or 'YYYY-MM-DD'.",
)
@click.option(
    "--run-ids",
    "run_ids_csv",
    default=None,
    help="Comma-separated run UUIDs. Overrides --since/--until.",
)
@click.option(
    "--models",
    "models_csv",
    default=None,
    help="Substring filter, e.g. 'grok,minimax'. Default: all seen in window.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "csv", "json", "markdown"]),
    default="table",
    show_default=True,
    help="Output format. Table/markdown → stdout; csv/json → --output-dir.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("docs/feedback"),
    show_default=True,
    help="Directory for csv/json snapshots.",
)
@click.option(
    "--compare-window",
    "compare_window_name",
    default=None,
    help=(
        f"Named preset(s); comma-separated for before/after diff. Known: {sorted(COMPARE_WINDOWS)}."
    ),
)
def main(
    since: str,
    until: str,
    run_ids_csv: str | None,
    models_csv: str | None,
    fmt: str,
    output_dir: Path,
    compare_window_name: str | None,
) -> None:
    """Emit a per-model scorecard for a time window (or pair of windows)."""
    models = _split_csv(models_csv)
    run_ids = _parse_run_ids(run_ids_csv)

    # Precedence: --compare-window > --run-ids > --since/--until. If you
    # asked for specific runs, don't second-guess with date arithmetic.
    if compare_window_name:
        windows = _resolve_compare_window(compare_window_name)
    elif run_ids:
        with connect() as conn:
            windows = (_resolve_run_ids(conn, run_ids),)
    else:
        windows = (_parse_window(since, until),)

    with connect() as conn:
        results: list[tuple[str, dict[str, dict[str, float | None]], list[str]]] = []
        for window_since, window_until in windows:
            metrics, seen_models = _collect_metrics(
                conn,
                since=window_since,
                until=window_until,
                models=models,
            )
            if not seen_models:
                raise click.ClickException(
                    f"no compile_attempts in window "
                    f"[{window_since.isoformat()}, "
                    f"{window_until.isoformat() if window_until else 'now'})"
                )
            results.append((_window_label(window_since, window_until), metrics, seen_models))

    logger.info(
        "compare_models_done",
        windows=len(results),
        format=fmt,
        model_filter=models,
    )

    if fmt == "json":
        # JSON collapses multi-window runs into one payload so downstream
        # diffing tools (jq / pandas) stay simple.
        payload = {
            "windows": [
                {
                    "label": label,
                    "models": seen_models,
                    "metrics": {name: dict(per_model) for name, per_model in metrics.items()},
                }
                for label, metrics, seen_models in results
            ],
        }
        _write_or_print(
            json.dumps(payload, indent=2, default=_json_default),
            output_dir=output_dir,
            fmt=fmt,
            window_label="multi",
        )
        return

    chunks: list[str] = []
    for label, metrics, seen_models in results:
        if fmt == "csv":
            chunks.append(_render_csv(metrics, seen_models))
        elif fmt == "markdown":
            chunks.append(_render_markdown(metrics, seen_models, label))
        else:
            chunks.append(_render_table(metrics, seen_models, label))

    # Two windows in markdown → attach a delta table so operators don't
    # eyeball the diff manually. That's the whole point of --compare-window.
    if len(results) == 2 and fmt == "markdown":
        _, before_metrics, before_models = results[0]
        _, after_metrics, after_models = results[1]
        delta_models = sorted(set(before_models) & set(after_models))
        if delta_models:
            chunks.append(_render_delta(before_metrics, after_metrics, delta_models))

    combined = "\n\n".join(chunks)
    window_label = "_vs_".join(label for label, _, _ in results)
    _write_or_print(combined, output_dir=output_dir, fmt=fmt, window_label=window_label)


if __name__ == "__main__":
    main()
