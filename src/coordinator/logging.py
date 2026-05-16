"""Batch-log + insights-digest formatting for the compile coordinator.

Owns the structured ``wiki/log.md`` audit trail, the
``wiki/merge_candidates.md`` reviewer queue, the post-batch tool-call
flush, the per-run insights-digest suffix, and the Langfuse score
emission.

These are observation + telemetry helpers — none of them mutate
``messages.compile_state`` or the wiki page corpus. They only append to
markdown files or push records to Postgres / Langfuse for downstream
consumption.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Literal
from uuid import UUID

import psycopg
import structlog

from src.db.insights import list_for_run as list_insights_for_run
from src.db.insights import max_id_for_run as _insights_max_id
from src.db.tool_call_log import fallback_to_jsonl as tool_log_fallback_to_jsonl
from src.db.tool_call_log import insert_many as tool_log_insert_many
from src.db.tool_call_log import summarize as tool_log_summarize
from src.observability.tool_call_log import ToolCallLogHandler

logger = structlog.get_logger(__name__)


BatchOutcome = Literal["compiled", "failed", "partial"]


_LOG_HEADER = (
    "# Compilation Log\n\n"
    "| Timestamp | Batch | N Emails | Thread ID | Outcome | Notes |\n"
    "|---|---|---|---|---|---|\n"
)


_MERGE_CANDIDATES_HEADER = (
    "---\n"
    'title: "Merge candidates"\n'
    "page_type: coordinator_notes\n"
    "status: active\n"
    "---\n\n"
    "# Merge candidates\n\n"
    "Append-only queue populated by the reviewer subagent when it flags two\n"
    "pages as duplicates. Each block is one batch. Apply with:\n\n"
    "    uv run python scripts/apply_merge_candidate.py \\\n"
    "        --pair slug-a,slug-b --keep slug-a --dry-run\n\n"
    "Then re-run with ``--commit`` when the diff looks right.\n\n"
)


def _format_top_tools(pairs: list[tuple[str, int]]) -> str:
    """Render a `top_tools=name:count,…` suffix for the batch log, or ''."""
    if not pairs:
        return ""
    return "top_tools=" + ",".join(f"{n}:{c}" for n, c in pairs)


def _flush_tool_calls(run_id: UUID, tool_cb: ToolCallLogHandler) -> str:
    """Persist buffered tool-call records for this batch and return a log suffix.

    Writes to Postgres via `src.db.tool_call_log.insert_many`; on DB failure
    falls back to `docs/audits/tool_calls-<run_id>.jsonl` so telemetry isn't
    dropped silently. Returns a `top_tools=name:count,…` string for the
    batch-log `Notes` column, or empty string if no calls were captured.

    Uses `flush_all()` so in-flight tool calls (agent crashed mid-call) are
    captured with `status='abandoned'` instead of silently dropped — those
    are the most diagnostic records.
    """
    records: list[dict[str, Any]] = [dict(r) for r in tool_cb.flush_all()]
    if not records:
        return ""

    try:
        tool_log_insert_many(run_id, records)
    except psycopg.Error as exc:
        logger.warning(
            "tool-call DB insert failed; falling back to JSONL",
            run_id=run_id,
            error=str(exc),
        )
        try:
            tool_log_fallback_to_jsonl(run_id, records)
        except OSError as fs_exc:
            logger.warning("tool-call JSONL fallback failed", run_id=run_id, error=str(fs_exc))
        # Without DB, we can't summarize across this run's prior batches —
        # compute a local top-5 from the just-flushed records instead.
        counts: dict[str, int] = {}
        for r in records:
            counts[r["tool_name"]] = counts.get(r["tool_name"], 0) + 1
        return _format_top_tools(sorted(counts.items(), key=lambda kv: -kv[1])[:5])

    try:
        summary = tool_log_summarize(run_id)
    except psycopg.Error as exc:
        logger.warning("tool-call summarize failed", run_id=run_id, error=str(exc))
        return ""
    return _format_top_tools(summary.get("top_by_count") or [])


def _append_batch_log(
    batch_idx: int,
    batch: list[Any],
    outcome: BatchOutcome,
    wiki_dir: str,
    notes: str = "",
) -> None:
    """Append one structured row to wiki/log.md for an end-of-batch event.

    The coordinator owns the audit trail. Previously the LLM agent was
    instructed to call `append_to_log` at the end of each batch, but it
    forgot often enough to leave gaps in the log. Writing here guarantees
    every batch — success, failure, or partial — gets a row.

    Args:
        batch_idx: 1-based batch index in this run.
        batch: List of batch members (dicts with `path`/`thread_id` keys, or
            bare path strings).
        outcome: One of `compiled`, `failed`, `partial`.
        wiki_dir: Root wiki directory.
        notes: Optional human-readable detail (e.g., error message tail).
    """
    wiki_path = Path(wiki_dir)
    wiki_path.mkdir(parents=True, exist_ok=True)
    log_path = wiki_path / "log.md"

    timestamp = datetime.now(UTC).isoformat()
    n_emails = len(batch)
    thread_id = ""
    if batch:
        first = batch[0]
        if isinstance(first, dict):
            thread_id = str(first.get("thread_id", ""))

    # Pipes in notes would break markdown table parsing — escape them.
    safe_notes = notes.replace("|", r"\|").replace("\n", " ").strip()

    if not log_path.exists():
        log_path.write_text(_LOG_HEADER, encoding="utf-8")

    row = f"| {timestamp} | {batch_idx} | {n_emails} | {thread_id} | {outcome} | {safe_notes} |\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(row)


def _ensure_merge_candidates_frontmatter(queue_path: Path) -> None:
    """Prepend the canonical header (with YAML frontmatter) to an existing
    ``merge_candidates.md`` that lacks frontmatter.

    Critique's ``find_touched_pages`` treats any recently-modified page
    with no parseable frontmatter as a "broken" touched page and pulls
    it into unrelated batch reviews — the Codex-flagged "poisoned input
    set" that caused #179. Prepending the header is idempotent: once
    the file has `page_type: coordinator_notes`, subsequent appends
    re-check and no-op.
    """
    try:
        existing = queue_path.read_text(encoding="utf-8")
    except OSError:
        return  # caller will re-create with the full header
    if existing.lstrip().startswith("---"):
        return  # already has frontmatter; leave alone
    # Pre-existing content without frontmatter: prepend the header so
    # the file becomes critique-safe without losing the backlog.
    queue_path.write_text(_MERGE_CANDIDATES_HEADER + existing, encoding="utf-8")


def _append_merge_candidates(
    pairs: list[dict[str, Any]],
    wiki_dir: str,
    *,
    trace_id: str,
) -> int:
    """Append reviewer-flagged merge candidates to ``wiki/merge_candidates.md``.

    The queue is append-only: humans (or a future Claude session) scan the
    file, pick pairs worth merging, and run
    ``scripts/apply_merge_candidate.py``. Returns the number of entries
    written (0 when ``pairs`` is empty). Filesystem failures are logged
    and swallowed — the queue is observational, never load-bearing.

    Args:
        pairs: parsed ``[{"slug_a", "slug_b", "note"}, ...]`` from
            :func:`src.agent.reviewer_result._extract_merge_candidates`.
        wiki_dir: root wiki directory.
        trace_id: ``run_id:batch_index`` so the reader can grep back to
            the originating compile batch.
    """
    if not pairs:
        return 0

    wiki_path = Path(wiki_dir)
    try:
        wiki_path.mkdir(parents=True, exist_ok=True)
        queue_path = wiki_path / "merge_candidates.md"

        if not queue_path.exists():
            queue_path.write_text(_MERGE_CANDIDATES_HEADER, encoding="utf-8")
        else:
            # Legacy files without frontmatter are "broken" to the
            # critique touched-pages scan — backfill the header so the
            # file isn't pulled into unrelated batch reviews.
            _ensure_merge_candidates_frontmatter(queue_path)

        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        lines = [f"## {timestamp} — trace `{trace_id}`", ""]
        for pair in pairs:
            slug_a = str(pair.get("slug_a") or "?")
            slug_b = str(pair.get("slug_b") or "?")
            note = str(pair.get("note") or "")[:200].replace("\n", " ").strip()
            lines.append(f"- [{slug_a}] vs [{slug_b}]: {note}")
        lines.append("")
        with queue_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return len(pairs)
    except OSError as exc:
        logger.warning(
            "merge_candidates_append_failed",
            trace_id=trace_id,
            error=str(exc)[:200],
        )
        return 0


def _max_insight_id_safe(run_id: UUID) -> int:
    """Best-effort fetch of the current max insight id. Returns 0 on DB error
    so a DB blip doesn't crash the compile loop — we just lose the
    "since-last-batch" filter for this one batch's digest."""
    try:
        return _insights_max_id(run_id)
    except Exception as exc:  # noqa: BLE001 — insights are best-effort
        logger.warning("insights cursor fetch failed", run_id=run_id, error=str(exc))
        return 0


def _insights_suffix(run_id: UUID, since_id: int, limit: int = 3) -> str:
    """Return a short ``insights=N: <preview>`` fragment for the log notes.

    Pulls rows newer than `since_id` for this `run_id` so the digest reflects
    only the insights logged during the just-completed batch — not every
    insight accumulated in earlier batches of the same run. Fails open with
    an empty string if the DB is unreachable.
    """
    try:
        rows = list_insights_for_run(run_id, limit=limit, since_id=since_id)
    except Exception as exc:  # noqa: BLE001 — insights are best-effort
        logger.warning("insights fetch failed", run_id=run_id, error=str(exc))
        return ""
    if not rows:
        return ""
    preview = (rows[0].get("message") or "").replace("\n", " ")[:40]
    return f"insights={len(rows)}: {preview}"


def _emit_langfuse_scores_for_run(run_id: UUID) -> None:
    """Push per-trace Langfuse Scores for the headline north-star metrics.

    Best-effort end-to-end: any failure (Langfuse 524, missing keys,
    SDK import error, DB blip) logs a warning and returns. Observability
    must never break the compile coordinator.
    """
    try:
        from src.observability.langfuse_scores import emit_scores_for_run

        emit_scores_for_run(run_id)
    except Exception as exc:  # noqa: BLE001 — observability is best-effort
        logger.warning(
            "langfuse_scores_emit_failed",
            run_id=str(run_id),
            error=str(exc)[:200],
        )
