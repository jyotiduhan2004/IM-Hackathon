"""Per-model north-star scorecard joining Langfuse traces + Postgres.

Goal (Week-1 Workstream A, Unit 2 of the North-Star Recovery Program):
every prompt/tool PR gets a same-day before/after readout. The script
answers "are we moving toward North Star?" objectively.

It pulls three data sources for a time window:

1. ``compile_attempts`` (Postgres, append-only log) — per-model
   attempts / successes / failures, joined to ``messages`` for
   ``thread_id``.
2. ``messages`` (Postgres, compile queue) — the trivial-skip share
   (added by Unit 9; degrades gracefully if absent).
3. Langfuse traces per ``(run_id, thread_id)`` batch — tool-call
   counts, tool inputs, recursion/timeout errors, resolve_page
   usefulness, absolute-path rate, check_my_work ordering.

One trace corresponds to one batch (one thread, one or more
messages), so trace-derived metrics are attributed per-trace rather
than per-message. Attempts stay per-message — that's the DB's grain.

Output: a markdown table to stdout with one row per model + an "all"
aggregate. If ``--out`` is supplied, the raw JSON scorecard is also
written there.

Usage::

    uv run python scripts/trace_scorecard.py --since 24h
    uv run python scripts/trace_scorecard.py --since 7d --out /tmp/s.json

Notes:
- Langfuse is queried via the ``npx langfuse-cli`` shell (same pattern
  used in ``/tmp/trace_audit/{extract,retry}.py`` — no Python SDK pin
  drift to worry about on the self-hosted v3 server).
- Each trace fetch has a 90s timeout and up to 3 retries with linear
  backoff; failures are logged and skipped.
- DB columns that don't yet exist (e.g. ``messages.compile_state =
  'skipped'`` landing in Unit 9) are treated as zero — the scorecard
  stays runnable during the multi-PR rollout.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
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

from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

# Tool-name buckets used by several metrics. Kept as module-level
# constants so they're easy to audit against the compiler source.
FS_TOOLS = frozenset({"read_file", "write_file", "edit_file", "ls", "glob", "grep"})
ENTITY_TOOLS = frozenset({"create_entity", "create_entities"})

# A "useful" resolve_page result is either a direct hit (exists=True)
# or a miss that still returned candidates the agent can inspect.
# Langfuse stores observations as Python-repr dicts, so match both
# single- and double-quoted forms.
_RESOLVE_HIT_PAT = re.compile(r"['\"]exists['\"]\s*:\s*(True|true)")
_RESOLVE_CANDIDATES_PAT = re.compile(r"['\"]candidates['\"]\s*:\s*\[\s*\{")
# Recursion-limit and timeout errors both count as "the agent didn't
# converge" — accept either phrasing.
_RECURSION_PAT = re.compile(
    r"GraphRecursionError|recursion_limit|Timed out|TimeoutError", re.IGNORECASE
)
# Absolute-path heuristic over fs tool inputs. Safe paths are
# mount-relative (``wiki/...`` / ``raw/...``); anything starting with
# ``/`` is either the host rootfs or Deep Agents' built-in ``/mnt``
# default — both count as "abs".
_FILE_PATH_PAT = re.compile(r"""["']file_path["']\s*:\s*["']([^"']+)["']""")

# Tier A + D1 telemetry patterns. Shared with `nightly_trace_audit.py`
# and `src/observability/langfuse_scores.py` via the `trace_signals`
# module so all three pipelines agree on what counts as a hit. Re-
# exported here so existing imports (tests, audit scripts) keep
# working with no migration churn.
from src.observability.trace_signals import AUTO_CORRECT_PAT  # noqa: E402
from src.observability.trace_signals import GATE_REJECT_PAT  # noqa: E402,F401
from src.observability.trace_signals import REVIEWER_VERDICT_PAT  # noqa: E402
from src.observability.trace_signals import REVIEWER_VERDICTS  # noqa: E402
from src.observability.trace_signals import TODOS_EARLY_WINDOW  # noqa: E402

# Retry policy for the trace-fetch subprocess. Linear backoff is fine
# — the self-hosted instance just needs a bit of breathing room, and
# the script's overall runtime is dominated by the sequential fetch.
_FETCH_TIMEOUT_S = 90
_FETCH_MAX_ATTEMPTS = 3


@dataclass
class TraceMetrics:
    """Per-trace facts the scorecard aggregates over."""

    trace_id: str
    model: str | None
    tool_calls: int = 0
    first_tool: str | None = None
    fs_calls: int = 0
    fs_absolute_calls: int = 0
    resolve_page_calls: int = 0
    resolve_page_useful: int = 0
    entity_tool_calls: int = 0
    write_draft_page_calls: int = 0
    log_insight_calls: int = 0
    recursion_or_timeout: bool = False
    # Tier A signals. Default to "feature off" values pre-Tier-A.
    auto_corrected: bool = False
    reviewer_verdict: str | None = None
    wrote_todos_early: bool = False


@dataclass
class Attempt:
    """Subset of a compile_attempts row we actually use."""

    message_id: str
    run_id: uuid.UUID | None
    thread_id: str | None
    compile_model: str | None
    outcome: str | None


@dataclass
class ModelAggregate:
    """One scorecard row — rendered as JSON + the markdown table."""

    model: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    success_rate: float = 0.0
    median_tool_calls: float | None = None
    recursion_timeout_rate: float = 0.0
    resolve_page_called: int = 0
    resolve_page_useful_rate: float = 0.0
    create_entity_rate: float = 0.0
    write_draft_page_calls: int = 0
    log_insight_calls: int = 0
    absolute_path_rate: float = 0.0
    check_my_work_first_call_rate: float = 0.0
    traces_included: int = 0
    # auto_correction_rate should trend DOWN as the LLM internalizes
    # the chroot — it's a friction metric, not a health metric.
    auto_correction_rate: float = 0.0
    reviewer_verdicts_dist: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys((*REVIEWER_VERDICTS, "none"), 0)
    )
    todo_adoption_rate: float = 0.0


def _parse_since(since: str) -> datetime:
    """Map ``24h|7d|YYYY-MM-DD`` to a UTC cutoff datetime."""
    now = datetime.now(UTC)
    match = re.fullmatch(r"(\d+)([hd])", since.strip())
    if match:
        qty = int(match.group(1))
        unit = match.group(2)
        delta = timedelta(hours=qty) if unit == "h" else timedelta(days=qty)
        return now - delta
    try:
        # strptime can't parse a TZ from '%Y-%m-%d' alone — we stamp UTC
        # after the parse to match the "%Y-%m-%dT00:00:00Z" intent.
        parsed = datetime.strptime(since.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise click.BadParameter(
            f"--since must match '24h', '7d', or 'YYYY-MM-DD' (got {since!r})"
        ) from exc
    return parsed


def _langfuse_env() -> dict[str, str]:
    """Inject Langfuse keys into the env npx inherits.

    We raise loudly if unset so the operator can't accidentally
    produce an empty scorecard that looks like "nothing happened".
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise click.ClickException("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
    env = os.environ.copy()
    env["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    env["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    env["LANGFUSE_HOST"] = settings.langfuse_host
    return env


def _run_langfuse(args: list[str], env: dict[str, str]) -> Any | None:
    """Run ``npx langfuse-cli ... --json`` with timeout + retry.

    Returns the parsed JSON body (dict for ``traces get``, list for
    ``traces list``) or None on persistent failure; callers decide how to
    handle the miss. Logs one warning per retry so we can spot Langfuse
    flakes without flooding structlog.
    """
    last_err: str | None = None
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                ["npx", "langfuse-cli", *args, "--json"],
                capture_output=True,
                text=True,
                timeout=_FETCH_TIMEOUT_S,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_err = f"timeout: {exc}"
            logger.warning("langfuse_timeout", args=args, attempt=attempt)
        else:
            if result.returncode != 0:
                last_err = (result.stderr or result.stdout or "").strip()[:400]
                logger.warning("langfuse_nonzero", args=args, attempt=attempt, rc=result.returncode)
            else:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError as exc:
                    last_err = f"parse: {exc}"
                    logger.warning(
                        "langfuse_parse_error",
                        args=args,
                        attempt=attempt,
                        error=str(exc)[:200],
                    )
        if attempt < _FETCH_MAX_ATTEMPTS:
            time.sleep(2 * attempt)
    logger.error("langfuse_failed", args=args, error=last_err)
    return None


def _extract_trace_metrics(trace: dict[str, Any]) -> TraceMetrics:
    """Compute per-trace facts from a raw Langfuse trace body.

    The self-hosted v3 API returns observations inline under
    ``body.observations``; for each TOOL observation we count the
    call, look for resolve_page usefulness, and detect abs-path fs
    writes. Input/output are stringified Python dicts (matching what
    ``/tmp/trace_audit/extract.py`` consumes), so we scan the
    stringified blob rather than re-parsing pyrepr.
    """
    body = trace.get("body") or trace
    md = body.get("metadata") or {}
    model = md.get("compile_model") or md.get("model")
    trace_id = body.get("id") or body.get("traceId") or ""

    metrics = TraceMetrics(trace_id=str(trace_id), model=model)
    observations = body.get("observations") or []

    for obs in observations:
        level = (obs.get("level") or "").upper()
        if level == "ERROR":
            blob = f"{obs.get('statusMessage', '')} {obs.get('output', '')}"
            if _RECURSION_PAT.search(blob):
                metrics.recursion_or_timeout = True

        if obs.get("type") != "TOOL":
            continue

        # `or ""` collapses both missing key and explicit JSON null to ""
        # — without it, `str(None)` becomes the truthy string "None" and
        # bypasses the unnamed-skip guard below.
        name = str(obs.get("name") or "")
        if not name:
            continue
        tool_index = metrics.tool_calls  # 0-based ordinal within this trace
        metrics.tool_calls += 1
        if metrics.first_tool is None:
            metrics.first_tool = name

        raw_output = str(obs.get("output") or "")
        raw_input = str(obs.get("input") or "")

        # One hit is enough: the metric is friction rate, not frequency.
        # Scan input AND output — PathAutoHealMiddleware appends the
        # annotation to the ToolMessage output, but if the design ever
        # flips to mutating the input args dict, the metric still works.
        if not metrics.auto_corrected and (
            AUTO_CORRECT_PAT.search(raw_output) or AUTO_CORRECT_PAT.search(raw_input)
        ):
            metrics.auto_corrected = True

        # First verdict wins. Reviewer may run multiple times; we only
        # want the distribution shape, not an average.
        if metrics.reviewer_verdict is None:
            verdict_match = REVIEWER_VERDICT_PAT.search(raw_output)
            if verdict_match:
                metrics.reviewer_verdict = verdict_match.group(1).lower()

        if name == "write_todos" and tool_index < TODOS_EARLY_WINDOW:
            metrics.wrote_todos_early = True

        if name in FS_TOOLS:
            metrics.fs_calls += 1
            for fp in _FILE_PATH_PAT.findall(raw_input):
                if fp.startswith("/"):
                    metrics.fs_absolute_calls += 1
                    break

        if name == "resolve_page":
            metrics.resolve_page_calls += 1
            if _RESOLVE_HIT_PAT.search(raw_output) or _RESOLVE_CANDIDATES_PAT.search(raw_output):
                metrics.resolve_page_useful += 1

        if name in ENTITY_TOOLS:
            metrics.entity_tool_calls += 1
        elif name == "write_draft_page":
            metrics.write_draft_page_calls += 1
        elif name == "log_insight":
            metrics.log_insight_calls += 1

    # Traces sometimes carry a top-level error string outside the
    # observations (LiteLLM proxy failures before any tool call
    # landed). Fold that in so we don't underreport timeouts.
    if not metrics.recursion_or_timeout:
        top_level = str(body.get("output") or "")
        if _RECURSION_PAT.search(top_level):
            metrics.recursion_or_timeout = True

    return metrics


def _load_attempts(since: datetime) -> list[Attempt]:
    """Return compile_attempts rows in-window joined to messages.thread_id.

    One row per attempt — an attempt can be in-flight (``outcome``
    NULL), succeeded, failed, or timed out. Orphaned in-flight
    attempts are included; they count toward ``attempts`` but not
    ``successes`` or ``failures``.
    """
    sql = """
        SELECT
          ca.message_id,
          ca.run_id,
          ca.compile_model,
          ca.outcome,
          m.thread_id
        FROM compile_attempts ca
        LEFT JOIN messages m ON m.message_id = ca.message_id
        WHERE ca.attempted_at >= %s
        ORDER BY ca.attempted_at DESC
    """
    try:
        with connect() as conn:
            # connect() pins row_factory=dict_row, so every row is a
            # dict[str, Any] at runtime — mypy can't infer that, hence
            # the cast.
            rows = cast("list[dict[str, Any]]", conn.execute(sql, (since,)).fetchall())
    except psycopg.Error as exc:
        logger.error("compile_attempts_query_failed", error=str(exc))
        raise click.ClickException(f"Postgres query failed: {exc}") from exc
    return [
        Attempt(
            message_id=str(row["message_id"]),
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            compile_model=row["compile_model"],
            outcome=row["outcome"],
        )
        for row in rows
    ]


def _trivial_skip_rate(since: datetime) -> float | None:
    """Return the share of ingested messages in ``compile_state='skipped'``.

    Unit 9 adds the ``skipped`` state; before it lands, no rows match
    and the numerator is always 0 — still a valid answer (0.0%), so
    we only return None on a hard query error.
    """
    sql = """
        SELECT
          COUNT(*) FILTER (WHERE compile_state = 'skipped') AS skipped,
          COUNT(*)                                            AS total
        FROM messages
        WHERE created_at >= %s
    """
    try:
        with connect() as conn:
            raw_row = conn.execute(sql, (since,)).fetchone()
    except psycopg.Error as exc:
        logger.warning("trivial_skip_query_failed", error=str(exc))
        return None
    row = cast("dict[str, Any] | None", raw_row)
    if not row or not row["total"]:
        return 0.0
    return float(row["skipped"]) / float(row["total"])


# The "new ontology" side of the 4+2 taxonomy (docs/NORTH-STAR.md).
# Legacy = status='current' OR page_type='entity'. Anything page-typed
# into this set AND status IN ('active', 'archived') is considered
# migrated. Kept module-level so the test can pin the definition.
NEW_ONTOLOGY_PAGE_TYPES: frozenset[str] = frozenset(
    {"domain", "glossary", "decision", "person", "home", "changes"}
)
NEW_ONTOLOGY_STATUSES: frozenset[str] = frozenset({"active", "archived"})


def _pages_migrated_per_run(since: datetime) -> int | None:
    """Count wiki_pages updated in-window that now sit on the new ontology.

    D4 / C1 / C2 will start flipping legacy pages (``page_type=entity``
    or ``status=current``) to the 4+2 taxonomy (domain/glossary/
    decision/person/home/changes with status active/archived). Until
    the migration scripts ship this returns 0 — a valid "no migrations
    ran" answer. Returns None only on a hard query error so the caller
    can render ``—``.
    """
    sql = """
        SELECT COUNT(*) AS n
          FROM wiki_pages
         WHERE updated_at >= %s
           AND page_type = ANY(%s)
           AND status = ANY(%s)
    """
    try:
        with connect() as conn:
            raw_row = conn.execute(
                sql,
                (since, list(NEW_ONTOLOGY_PAGE_TYPES), list(NEW_ONTOLOGY_STATUSES)),
            ).fetchone()
    except psycopg.Error as exc:
        logger.warning("pages_migrated_query_failed", error=str(exc))
        return None
    row = cast("dict[str, Any] | None", raw_row)
    if not row:
        return 0
    return int(row["n"])


def _migration_inflight_pct() -> float | None:
    """Snapshot: fraction of wiki pages still on the legacy ontology.

    Legacy = ``status='current'`` OR ``page_type='entity'``. Independent
    of the scorecard time window — it's a "where's the migration at"
    gauge, not a per-run count. Returns None on DB error (rendered as
    ``—``); returns 0.0 when the table is empty.
    """
    sql = """
        SELECT
          COUNT(*) FILTER (
            WHERE status = 'current' OR page_type = 'entity'
          ) AS legacy,
          COUNT(*) AS total
        FROM wiki_pages
    """
    try:
        with connect() as conn:
            raw_row = conn.execute(sql).fetchone()
    except psycopg.Error as exc:
        logger.warning("migration_inflight_query_failed", error=str(exc))
        return None
    row = cast("dict[str, Any] | None", raw_row)
    if not row or not row["total"]:
        return 0.0
    return float(row["legacy"]) / float(row["total"])


def _list_traces_by_run(run_ids: set[uuid.UUID], env: dict[str, str]) -> dict[tuple[str, str], str]:
    """Map ``(run_id, thread_id) → trace_id`` for every run in scope.

    The compile coordinator stamps each trace with
    ``metadata.compile_run_id`` and ``metadata.compile_thread_id``
    but does NOT set ``sessionId``, so we filter on the metadata
    key. Langfuse's v3 ``--filter`` API is JSON: one ``stringObject``
    condition per run keeps the URL bounded and the returned dict
    drives the trace fetch loop.
    """
    mapping: dict[tuple[str, str], str] = {}
    for run_id in run_ids:
        filter_json = json.dumps(
            [
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": "compile_run_id",
                    "operator": "=",
                    "value": str(run_id),
                }
            ]
        )
        payload = _run_langfuse(
            ["api", "traces", "list", "--filter", filter_json, "--limit", "100"],
            env,
        )
        if payload is None:
            continue
        # Langfuse returns either a list at top level OR a dict wrapping
        # it; handle both. `traces list` is the list-shaped case in v0.0.8+.
        if isinstance(payload, list):
            items = payload
        else:
            items = payload.get("body", {}).get("data") or payload.get("data") or []
        for item in items:
            md = item.get("metadata") or {}
            thread_id = md.get("compile_thread_id") or ""
            trace_id = str(item.get("id") or item.get("traceId") or "")
            if thread_id and trace_id:
                mapping[(str(run_id), str(thread_id))] = trace_id
    return mapping


def _fetch_trace_metrics(
    attempts: list[Attempt], env: dict[str, str]
) -> dict[tuple[str, str], TraceMetrics]:
    """Fetch one trace per distinct batch and return its metrics.

    Returns ``{(run_id, thread_id): metrics}`` — caller looks the
    trace up by (run_id, thread_id) when aggregating per model.
    """
    run_ids: set[uuid.UUID] = {a.run_id for a in attempts if a.run_id is not None}
    if not run_ids:
        return {}
    trace_index = _list_traces_by_run(run_ids, env)
    logger.info("trace_index_built", batches=len(trace_index))

    out: dict[tuple[str, str], TraceMetrics] = {}
    for key, trace_id in trace_index.items():
        trace = _run_langfuse(["api", "traces", "get", trace_id], env)
        if trace is None:
            continue
        out[key] = _extract_trace_metrics(trace)
    logger.info("trace_metrics_fetched", count=len(out))
    return out


def _aggregate(
    attempts: list[Attempt],
    batch_metrics: dict[tuple[str, str], TraceMetrics],
) -> list[ModelAggregate]:
    """Build one scorecard row per model + an "all" aggregate.

    Attempts are per-message; trace metrics are per-batch. A batch
    covers N messages from the same thread, so we attribute its
    trace metrics ONCE per batch to avoid inflating tool-call totals
    when one thread happens to have several messages.
    """
    # Group attempts by model so per-model counters are cheap.
    attempts_by_model: dict[str, list[Attempt]] = defaultdict(list)
    for a in attempts:
        attempts_by_model[a.compile_model or "unknown"].append(a)

    # Deduplicate the (run_id, thread_id) batches each model owns so
    # the same trace isn't counted twice when multiple attempts share
    # a batch.
    batches_by_model: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for a in attempts:
        if a.run_id is None or not a.thread_id:
            continue
        model = a.compile_model or "unknown"
        batches_by_model[model].add((str(a.run_id), a.thread_id))

    models = sorted(attempts_by_model.keys())
    all_batches: set[tuple[str, str]] = set()
    for bs in batches_by_model.values():
        all_batches.update(bs)

    rows: list[ModelAggregate] = []
    for name in [*models, "all"]:
        if name == "all":
            model_attempts = attempts
            traces = [batch_metrics[k] for k in all_batches if k in batch_metrics]
        else:
            model_attempts = attempts_by_model[name]
            traces = [batch_metrics[k] for k in batches_by_model[name] if k in batch_metrics]

        rows.append(_build_row(name, model_attempts, traces))
    return rows


def _build_row(model: str, attempts: list[Attempt], traces: list[TraceMetrics]) -> ModelAggregate:
    """Compute one scorecard row from a slice of attempts + traces."""
    total = len(attempts)
    succeeded = sum(1 for a in attempts if a.outcome == "compiled")
    failed = sum(1 for a in attempts if a.outcome in {"failed", "timeout"})
    success_rate = succeeded / total if total else 0.0

    # Median tool calls over traces that actually used tools. Zero
    # traces produces None (rendered as "—") so we don't pretend a
    # single compile sets the median.
    tool_counts = [t.tool_calls for t in traces if t.tool_calls > 0]
    median_tool_calls: float | None = statistics.median(tool_counts) if tool_counts else None

    recursion_hits = sum(1 for t in traces if t.recursion_or_timeout)
    recursion_rate = recursion_hits / len(traces) if traces else 0.0

    resolve_calls = sum(t.resolve_page_calls for t in traces)
    resolve_useful = sum(t.resolve_page_useful for t in traces)
    resolve_useful_rate = resolve_useful / resolve_calls if resolve_calls else 0.0

    entity_calls = sum(t.entity_tool_calls for t in traces)
    # Definition: total create_entity + create_entities calls / attempts.
    # This stays per-attempt (not per-trace) because the North-Star
    # target is "<10% of successful compiles" — i.e. per compile/email.
    create_entity_rate = entity_calls / total if total else 0.0

    draft_calls = sum(t.write_draft_page_calls for t in traces)
    insight_calls = sum(t.log_insight_calls for t in traces)

    fs_total = sum(t.fs_calls for t in traces)
    fs_abs = sum(t.fs_absolute_calls for t in traces)
    abs_path_rate = fs_abs / fs_total if fs_total else 0.0

    cmw_first = sum(1 for t in traces if t.first_tool == "check_my_work")
    cmw_first_rate = cmw_first / len(traces) if traces else 0.0

    # Rates are over traces (not attempts) because the signals live in
    # observations, one per batch — symmetric with recursion_timeout_rate.
    auto_corrected_count = sum(1 for t in traces if t.auto_corrected)
    auto_correction_rate = auto_corrected_count / len(traces) if traces else 0.0

    todo_early_count = sum(1 for t in traces if t.wrote_todos_early)
    todo_adoption_rate = todo_early_count / len(traces) if traces else 0.0

    verdicts_dist: dict[str, int] = dict.fromkeys((*REVIEWER_VERDICTS, "none"), 0)
    for t in traces:
        key = t.reviewer_verdict if t.reviewer_verdict in REVIEWER_VERDICTS else "none"
        verdicts_dist[key] += 1

    return ModelAggregate(
        model=model,
        attempts=total,
        successes=succeeded,
        failures=failed,
        success_rate=success_rate,
        median_tool_calls=median_tool_calls,
        recursion_timeout_rate=recursion_rate,
        resolve_page_called=resolve_calls,
        resolve_page_useful_rate=resolve_useful_rate,
        create_entity_rate=create_entity_rate,
        write_draft_page_calls=draft_calls,
        log_insight_calls=insight_calls,
        absolute_path_rate=abs_path_rate,
        check_my_work_first_call_rate=cmw_first_rate,
        traces_included=len(traces),
        auto_correction_rate=auto_correction_rate,
        reviewer_verdicts_dist=verdicts_dist,
        todo_adoption_rate=todo_adoption_rate,
    )


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return str(int(value))


_VERDICT_SHORT_KEYS = {"pass": "p", "revise": "r", "block": "b", "none": "n"}


def _fmt_verdicts(value: dict[str, int] | None) -> str:
    """Render verdict distribution compactly (e.g. ``p=4 r=1 b=0 n=2``).

    Short keys keep the column narrow; JSON output retains full keys.
    Renders ``—`` when no traces have any verdict — distinguishes
    "reviewer ran and produced 0 of each" from "reviewer hasn't landed yet".
    """
    if not value or sum(value.values()) == 0:
        return "—"
    parts = [
        f"{_VERDICT_SHORT_KEYS.get(k, k)}={value.get(k, 0)}" for k in (*REVIEWER_VERDICTS, "none")
    ]
    return " ".join(parts)


_TABLE_COLUMNS: list[tuple[str, str]] = [
    ("model", "str"),
    ("attempts", "num"),
    ("successes", "num"),
    ("failures", "num"),
    ("success_rate", "pct"),
    ("median_tool_calls", "num"),
    ("recursion_timeout_rate", "pct"),
    ("resolve_page_called", "num"),
    ("resolve_page_useful_rate", "pct"),
    ("create_entity_rate", "num"),
    ("write_draft_page_calls", "num"),
    ("log_insight_calls", "num"),
    ("absolute_path_rate", "pct"),
    ("check_my_work_first_call_rate", "pct"),
    ("auto_correction_rate", "pct"),
    ("reviewer_verdicts_dist", "dist"),
    ("todo_adoption_rate", "pct"),
]


def _render_markdown(
    rows: list[ModelAggregate],
    trivial_skip_rate: float | None,
    pages_migrated_per_run: int | None,
    migration_inflight_pct: float | None,
) -> str:
    """Pretty-print the scorecard as a markdown table + summary line."""
    headers = [c[0] for c in _TABLE_COLUMNS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        cells: list[str] = []
        row_dict = asdict(row)
        for key, kind in _TABLE_COLUMNS:
            value = row_dict[key]
            if kind == "str":
                cells.append(str(value))
            elif kind == "pct":
                cells.append(_fmt_pct(value))
            elif kind == "dist":
                cells.append(_fmt_verdicts(value))
            else:
                cells.append(_fmt_num(value))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"**trivial_skip_rate**: {_fmt_pct(trivial_skip_rate)}")
    lines.append(f"**pages_migrated_per_run**: {_fmt_num(pages_migrated_per_run)}")
    lines.append(f"**migration_inflight_pct**: {_fmt_pct(migration_inflight_pct)}")
    return "\n".join(lines)


@click.command()
@click.option(
    "--since",
    default="24h",
    show_default=True,
    help="Window start: '24h', '7d', or an ISO date 'YYYY-MM-DD'.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional path — when set, raw JSON scorecard is written here.",
)
def main(since: str, out_path: Path | None) -> None:
    """Emit a north-star scorecard for a time window."""
    cutoff = _parse_since(since)
    env = _langfuse_env()

    logger.info("scorecard_start", since=since, cutoff=cutoff.isoformat())
    attempts = _load_attempts(cutoff)
    logger.info("attempts_loaded", count=len(attempts))

    batch_metrics = _fetch_trace_metrics(attempts, env)
    rows = _aggregate(attempts, batch_metrics)
    trivial_rate = _trivial_skip_rate(cutoff)
    migrated = _pages_migrated_per_run(cutoff)
    inflight_pct = _migration_inflight_pct()

    click.echo(_render_markdown(rows, trivial_rate, migrated, inflight_pct))

    if out_path is not None:
        payload = {
            "window": {
                "since": since,
                "cutoff_utc": cutoff.isoformat(),
                "generated_at_utc": datetime.now(UTC).isoformat(),
            },
            "rows": [asdict(row) for row in rows],
            "trivial_skip_rate": trivial_rate,
            "pages_migrated_per_run": migrated,
            "migration_inflight_pct": inflight_pct,
            "attempts_total": len(attempts),
            "traces_fetched": len(batch_metrics),
        }
        out_path.write_text(json.dumps(payload, indent=2, default=_json_default))
        logger.info("scorecard_written", path=str(out_path))


def _json_default(value: Any) -> Any:
    """Serialize psycopg-returned datetime/UUID cleanly for JSON."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"unserializable: {type(value).__name__}")


if __name__ == "__main__":
    main()
