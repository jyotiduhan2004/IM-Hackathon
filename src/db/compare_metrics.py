"""Shared metric helpers for model A/B comparisons.

Both ``scripts/compare_models.py`` and ``scripts/trace_scorecard.py`` read
from ``compile_attempts`` + friends to answer "how does model X compare
to Y". This module holds the joins and aggregations that both scripts
need — keeping them here prevents definition drift when a metric's SQL
gets refined in one place but not the other.

Every function takes a ``psycopg.Connection`` so callers can ride the
same transaction as a surrounding write (though today all callers read
only). Time windows are passed as ``since`` / ``until`` ``datetime``
objects; None on ``until`` means "now", handled in SQL via ``COALESCE``.

Per ``CLAUDE.md``: functions are deterministic, return per-model dicts
keyed on ``compile_model`` (NULL collapses to ``'unknown'``), and return
empty dicts (not raise) when the underlying table / column is missing
so callers can render ``-`` instead of crashing during schema rollouts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import cast

import psycopg
import structlog

logger = structlog.get_logger(__name__)


def outcomes_by_model(
    conn: psycopg.Connection,
    *,
    since: datetime,
    until: datetime | None,
    models: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Return ``{model: {'attempts', 'compiled', 'skipped', 'failed', 'timeout'}}``.

    One row per model in the window. ``models`` is an optional substring
    filter — each entry is matched with ``ILIKE '%s%'``. Unknown models
    (NULL ``compile_model``) collapse to the ``'unknown'`` bucket.

    Counts are for ALL attempts in window (including in-flight ones with
    ``outcome IS NULL``). Callers that want a validity percentage should
    compute it from ``compiled + skipped`` / ``attempts`` directly.
    """
    sql = """
        SELECT
          COALESCE(compile_model, 'unknown') AS model,
          COUNT(*) AS attempts,
          COUNT(*) FILTER (WHERE outcome = 'compiled') AS compiled,
          COUNT(*) FILTER (WHERE outcome = 'skipped')  AS skipped,
          COUNT(*) FILTER (WHERE outcome = 'failed')   AS failed,
          COUNT(*) FILTER (WHERE outcome = 'timeout')  AS timeout
        FROM compile_attempts
        WHERE attempted_at >= %(since)s
          AND attempted_at < COALESCE(%(until)s, now())
        GROUP BY COALESCE(compile_model, 'unknown')
    """
    try:
        rows = cast(
            "list[dict[str, Any]]",
            conn.execute(sql, {"since": since, "until": until}).fetchall(),
        )
    except psycopg.Error as exc:
        logger.warning("compare_metrics.outcomes_by_model failed", error=str(exc))
        return {}
    return {
        row["model"]: {
            "attempts": int(row["attempts"]),
            "compiled": int(row["compiled"]),
            "skipped": int(row["skipped"]),
            "failed": int(row["failed"]),
            "timeout": int(row["timeout"]),
        }
        for row in rows
        if _model_matches(str(row["model"]), models)
    }


def error_class_counts(
    conn: psycopg.Connection,
    *,
    since: datetime,
    until: datetime | None,
    models: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Return ``{model: {class_name: count}}`` for known failure classes.

    Substring matches on ``compile_attempts.error``. Classes:

    - ``recursion_fail`` — graph recursion limit / ``GraphRecursionError``
    - ``not_cited`` — the compile finished but no content page cited the email
    - ``auth_401`` — LiteLLM auth / proxy permission failures
    - ``litellm_invalid_model`` — LiteLLM 400 ``Invalid model name``

    Counts are 0 (not missing) for classes that didn't trigger in the
    window so callers can render a stable row.
    """
    sql = """
        SELECT
          COALESCE(compile_model, 'unknown') AS model,
          COUNT(*) FILTER (
            WHERE error ILIKE '%%recursion limit%%'
               OR error ILIKE '%%GraphRecursionError%%'
          ) AS recursion_fail,
          COUNT(*) FILTER (WHERE error ILIKE '%%not cited%%')      AS not_cited,
          COUNT(*) FILTER (
            WHERE error ILIKE '%%401%%' OR error ILIKE '%%invalid api key%%'
          ) AS auth_401,
          COUNT(*) FILTER (
            WHERE error ILIKE '%%invalid model name%%'
          ) AS litellm_invalid_model
        FROM compile_attempts
        WHERE attempted_at >= %(since)s
          AND attempted_at < COALESCE(%(until)s, now())
        GROUP BY COALESCE(compile_model, 'unknown')
    """
    try:
        rows = cast(
            "list[dict[str, Any]]",
            conn.execute(sql, {"since": since, "until": until}).fetchall(),
        )
    except psycopg.Error as exc:
        logger.warning("compare_metrics.error_class_counts failed", error=str(exc))
        return {}
    return {
        row["model"]: {
            "recursion_fail": int(row["recursion_fail"]),
            "not_cited": int(row["not_cited"]),
            "auth_401": int(row["auth_401"]),
            "litellm_invalid_model": int(row["litellm_invalid_model"]),
        }
        for row in rows
        if _model_matches(str(row["model"]), models)
    }


def tool_stats_by_model(
    conn: psycopg.Connection,
    *,
    since: datetime,
    until: datetime | None,
    models: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Return ``{model: {'avg_tools_per_batch', 'check_my_work_blocks'}}``.

    Joins ``compile_tool_calls`` → ``compile_attempts`` via ``run_id``.
    ``avg_tools_per_batch`` = total tool calls / distinct (run_id,
    message_id) pairs per model. ``check_my_work_blocks`` = count of
    ``check_my_work`` calls whose output preview signals the critique
    blocked (``%block%`` match).

    Returns empty dict on hard DB error so the caller can render ``-``.
    """
    sql = """
        WITH attempts_in_window AS (
          SELECT DISTINCT run_id,
                          message_id,
                          COALESCE(compile_model, 'unknown') AS model
          FROM compile_attempts
          WHERE attempted_at >= %(since)s
            AND attempted_at < COALESCE(%(until)s, now())
            AND run_id IS NOT NULL
        )
        SELECT
          aiw.model AS model,
          COUNT(ctc.id)::float
            / NULLIF(COUNT(DISTINCT (aiw.run_id, aiw.message_id))::float, 0)
            AS avg_tools_per_batch,
          COUNT(*) FILTER (
            WHERE ctc.tool_name = 'check_my_work'
              AND ctc.output_preview ILIKE '%%block%%'
          ) AS check_my_work_blocks
        FROM attempts_in_window aiw
        LEFT JOIN compile_tool_calls ctc ON ctc.run_id = aiw.run_id
        GROUP BY aiw.model
    """
    try:
        rows = cast(
            "list[dict[str, Any]]",
            conn.execute(sql, {"since": since, "until": until}).fetchall(),
        )
    except psycopg.Error as exc:
        logger.warning("compare_metrics.tool_stats_by_model failed", error=str(exc))
        return {}
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        model = str(row["model"])
        if not _model_matches(model, models):
            continue
        avg = row["avg_tools_per_batch"]
        out[model] = {
            "avg_tools_per_batch": float(avg) if avg is not None else float("nan"),
            "check_my_work_blocks": float(int(row["check_my_work_blocks"])),
        }
    return out


def _model_matches(model: str, filters: list[str] | None) -> bool:
    """Substring-match ``model`` against any of ``filters``. None → always True."""
    if not filters:
        return True
    return any(f.lower() in model.lower() for f in filters)
