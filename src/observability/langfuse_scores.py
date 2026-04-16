"""Push per-trace Scores to Langfuse for the headline north-star metrics.

Each trace gets a small set of scores attached. Langfuse aggregates them
automatically and exposes them in the Scores tab + custom dashboards —
much better than re-running scripts/trace_scorecard.py manually.

Score schema (one per trace):

- ``content_page_cited``           BOOLEAN   1.0 / 0.0
- ``gate_rejected_check_my_work``  NUMERIC   count of D1 gate rejections
- ``auto_corrected``               BOOLEAN   1.0 / 0.0
- ``wrote_todos_early``            BOOLEAN   1.0 / 0.0
- ``reviewer_verdict``             CATEGORICAL  pass | revise | block | none
- ``compile_outcome``              CATEGORICAL  content_page | trivial_skip |
                                                already_captured | filing_cabinet |
                                                ghost (U7 — single bucket per
                                                trace, letting dashboards slice
                                                cleanly by outcome class)

Reuses extraction logic from ``scripts/trace_scorecard.py`` so score
values match what the manual scorecard reports — single source of truth
for "what does this metric mean".

The post-batch coordinator hook in ``scripts/compile_all.py`` calls
``emit_scores_for_run(...)`` which:

  1. enumerates ``(message_id, run_id, thread_id)`` from
     ``compile_attempts`` joined to ``messages`` for this run
  2. resolves each ``(run_id, thread_id)`` to a ``trace_id`` via the
     Langfuse trace search API filtered on ``metadata.compile_run_id``
  3. fetches each trace's full body, computes the 6 metrics, and
     pushes them via ``client.create_score(...)``

Failures (Langfuse 524, missing trace, score push timeout) are logged
and swallowed so a flaky observability backend never breaks compile.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import uuid
from typing import Any
from typing import cast

import psycopg
import structlog

from src.config import settings
from src.observability.trace_signals import AUTO_CORRECT_PAT
from src.observability.trace_signals import CONTENT_PAGE_TYPES
from src.observability.trace_signals import GATE_REJECT_PAT
from src.observability.trace_signals import REVIEWER_VERDICT_PAT
from src.observability.trace_signals import TODOS_EARLY_WINDOW

logger = structlog.get_logger(__name__)

# U7: per-trace categorical outcome labels. Each trace falls into exactly
# one bucket; Langfuse dashboards can group by this score to see the
# content-vs-skip-vs-filing mix at a glance.
COMPILE_OUTCOME_CONTENT_PAGE = "content_page"
COMPILE_OUTCOME_TRIVIAL_SKIP = "trivial_skip"
COMPILE_OUTCOME_ALREADY_CAPTURED = "already_captured"
COMPILE_OUTCOME_FILING_CABINET = "filing_cabinet"
COMPILE_OUTCOME_GHOST = "ghost"

# Same shape as the audit script's category patterns — matches
# `log_insight(category="...")` in the ToolCall's input payload.
_TRIVIAL_SKIP_INSIGHT_PAT = re.compile(r"""["']category["']\s*:\s*["']trivial_skip["']""")
_ALREADY_CAPTURED_INSIGHT_PAT = re.compile(r"""["']category["']\s*:\s*["']already_captured["']""")


def _is_content_page_cited(conn: psycopg.Connection, message_id: str) -> bool:
    """Return True when ``message_id`` is cited in any content-type page.

    "Content-type" = the strict subset in :data:`CONTENT_PAGE_TYPES`
    (entity / person pages don't count — naming an email in a person's
    page is filing-cabinet behaviour, not knowledge extraction).

    Driven by ``message_touched_pages`` joined to ``wiki_pages``; if
    the join finds one row whose ``page_type`` is content-type, the
    check passes.
    """
    row = conn.execute(
        """
        SELECT 1
          FROM message_touched_pages mtp
          JOIN wiki_pages wp ON wp.page_id = mtp.page_id
         WHERE mtp.message_id = %s
           AND wp.page_type = ANY(%s)
         LIMIT 1
        """,
        (message_id, list(CONTENT_PAGE_TYPES)),
    ).fetchone()
    return row is not None


def _content_cited_lookup(message_ids: list[str]) -> dict[str, bool]:
    """Return ``{message_id: cited}`` for many ids in one DB round-trip.

    Avoids the N+1 pattern of opening one connection per trace inside
    :func:`emit_scores_for_trace`. Empty input returns an empty dict.
    Fails open: a DB error logs a warning and returns ``{}``, which the
    caller treats as "every lookup missed" (cited=False).
    """
    if not message_ids:
        return {}
    # Lazy-import: a top-level `from src.db import connect` would bind
    # the original unpatched function, so the test schema's monkeypatch
    # on `src.db.connect` would silently target the production schema.
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            rows = cast(
                "list[dict[str, Any]]",
                conn.execute(
                    """
                    SELECT DISTINCT mtp.message_id
                      FROM message_touched_pages mtp
                      JOIN wiki_pages wp ON wp.page_id = mtp.page_id
                     WHERE mtp.message_id = ANY(%s)
                       AND wp.page_type = ANY(%s)
                    """,
                    (list(message_ids), list(CONTENT_PAGE_TYPES)),
                ).fetchall(),
            )
    except psycopg.Error as exc:
        logger.warning(
            "content_page_citation_batch_lookup_failed",
            error=str(exc)[:200],
        )
        return {}
    cited_ids = {str(r["message_id"]) for r in rows}
    return {mid: mid in cited_ids for mid in message_ids}


def _extract_metric_values(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the 4 trace-derived metrics from raw observations.

    Returns a dict with keys ``gate_rejected_check_my_work`` (int),
    ``auto_corrected`` (bool), ``wrote_todos_early`` (bool),
    ``reviewer_verdict`` (str | None — None means reviewer didn't run).

    Mirrors ``scripts/trace_scorecard.py::_extract_trace_metrics`` for
    the overlapping signals so the Score values match the scorecard
    rates. The fifth metric (``content_page_cited``) needs a DB lookup
    keyed on message_id, so :func:`emit_scores_for_trace` computes it
    separately.
    """
    auto_corrected = False
    reviewer_verdict: str | None = None
    wrote_todos_early = False
    gate_rejected = 0
    tool_calls = 0

    for obs in observations:
        if obs.get("type") != "TOOL":
            continue
        # Match `_extract_trace_metrics`'s "skip null/empty name" guard so
        # we don't shift the wrote_todos_early window on malformed traces.
        name = str(obs.get("name") or "")
        if not name:
            continue
        tool_index = tool_calls
        tool_calls += 1

        raw_output = str(obs.get("output") or "")
        raw_input = str(obs.get("input") or "")

        # Auto-correct annotation appears on the read_file/write_file
        # output (or input) when PathAutoHealMiddleware rewrites a path.
        # One hit is enough — friction rate, not frequency.
        if not auto_corrected and (
            AUTO_CORRECT_PAT.search(raw_output) or AUTO_CORRECT_PAT.search(raw_input)
        ):
            auto_corrected = True

        # First verdict wins. Reviewer may run multiple times; we only
        # want the distribution shape, not an average.
        if reviewer_verdict is None:
            verdict_match = REVIEWER_VERDICT_PAT.search(raw_output)
            if verdict_match:
                reviewer_verdict = verdict_match.group(1).lower()

        if name == "write_todos" and tool_index < TODOS_EARLY_WINDOW:
            wrote_todos_early = True

        # D1 gate rejection — look for the synthetic ToolMessage the
        # middleware emits. Count occurrences so a trace where the
        # agent retried `check_my_work` 3 times pre-write shows
        # value=3, not 1.
        if name == "check_my_work" and GATE_REJECT_PAT.search(raw_output):
            gate_rejected += 1

    return {
        "gate_rejected_check_my_work": gate_rejected,
        "auto_corrected": auto_corrected,
        "wrote_todos_early": wrote_todos_early,
        "reviewer_verdict": reviewer_verdict,
    }


def _classify_compile_outcome(
    observations: list[dict[str, Any]],
    content_page_cited: bool | None,
) -> str:
    """Return one of the ``COMPILE_OUTCOME_*`` labels for this trace (U7).

    Priority (tighter signal wins):

    1. ``content_page`` — the message is cited in a content-type page.
       The work landed; nothing else matters.
    2. ``trivial_skip`` — the agent logged a ``trivial_skip`` insight on
       this trace. Correct no-op.
    3. ``already_captured`` — same, for the ``already_captured`` category.
       Correct no-op.
    4. ``filing_cabinet`` — agent wrote *something* (``write_draft_page``
       or ``create_entity[ies]``) but the message isn't cited in a
       content-type page. Usually an entity-only stub — the failure
       mode the North Star metric is designed to catch.
    5. ``ghost`` — no writes, no trivial-skip insight. Agent did nothing
       useful; worst outcome.

    Splitting trivial_skip from already_captured keeps the carve-out
    visible — a dashboard shows "how often do we correctly skip vs
    correctly dedupe" rather than collapsing them into one "no-op" bin.

    When ``content_page_cited`` is ``None`` (couldn't be computed — no
    message_id), we fall back to the observation-only signals; the
    content-page bin is unreachable in that case.
    """
    if content_page_cited is True:
        return COMPILE_OUTCOME_CONTENT_PAGE

    has_trivial_skip = False
    has_already_captured = False
    has_write = False
    for obs in observations:
        if obs.get("type") != "TOOL":
            continue
        name = str(obs.get("name") or "")
        if not name:
            continue
        raw_input = str(obs.get("input") or "")
        if name == "log_insight":
            if _TRIVIAL_SKIP_INSIGHT_PAT.search(raw_input):
                has_trivial_skip = True
            if _ALREADY_CAPTURED_INSIGHT_PAT.search(raw_input):
                has_already_captured = True
        # create_entity/create_entities and write_draft_page count as
        # "wrote something" for the filing-cabinet fallback. If the
        # agent only writes an entity stub without a content page,
        # content_page_cited is False and we correctly label that
        # trace as filing_cabinet.
        if name == "write_draft_page" or name in {"create_entity", "create_entities"}:
            has_write = True

    # No-op wins over filing-cabinet — a trace with both a trivial_skip
    # insight AND a filing-only write is weird, but the insight is the
    # more specific signal (the agent explicitly declared intent).
    if has_trivial_skip:
        return COMPILE_OUTCOME_TRIVIAL_SKIP
    if has_already_captured:
        return COMPILE_OUTCOME_ALREADY_CAPTURED
    if has_write:
        return COMPILE_OUTCOME_FILING_CABINET
    return COMPILE_OUTCOME_GHOST


def _push_score(
    client: Any,
    *,
    trace_id: str,
    name: str,
    value: float | str,
    data_type: str,
    comment: str | None = None,
) -> None:
    """Wrap ``client.create_score`` with logging + best-effort error handling.

    Langfuse 524s + connection drops are common on the self-hosted
    server; we never want one failed score to break the post-batch hook.
    """
    try:
        client.create_score(
            trace_id=trace_id,
            name=name,
            value=value,
            data_type=data_type,
            comment=comment,
        )
    except Exception as exc:  # noqa: BLE001 — observability must never break compile
        logger.warning(
            "langfuse_score_push_failed",
            trace_id=trace_id,
            name=name,
            error=str(exc)[:200],
        )


def _safe_flush(client: Any) -> None:
    """Flush the Langfuse client, swallowing any backend failure."""
    # Best-effort only — not worth interrupting compile if the server
    # is wedged.
    with contextlib.suppress(Exception):
        client.flush()


def emit_scores_for_trace(
    client: Any,
    trace_id: str,
    observations: list[dict[str, Any]],
    message_id: str | None = None,
    *,
    content_page_cited: bool | None = None,
) -> None:
    """Compute the 6 headline scores for one trace and push them to Langfuse.

    Args:
        client: Langfuse client (``langfuse.Langfuse`` instance).
        trace_id: The Langfuse trace id to attach scores to.
        observations: Trace observations (from the trace fetch
            response). Each observation is a dict with ``type``, ``name``,
            ``input``, ``output`` keys.
        message_id: When provided (and ``content_page_cited`` isn't),
            used to look up the citation flag from the ``messages``
            joined to ``message_touched_pages`` joined to ``wiki_pages``
            tables. When both are omitted, the content-page score is
            skipped (it can't be computed from observations alone).
        content_page_cited: Pre-computed citation flag. Takes precedence
            over the ``message_id`` lookup so a batched caller (e.g.
            :func:`emit_scores_for_run`) can compute every flag in one
            DB round-trip instead of opening one connection per trace.

    All scores are pushed independently — one push failing doesn't
    block the others. Returns ``None``; failures are logged and swallowed.
    """
    metric_values = _extract_metric_values(observations)

    cited: bool | None
    if content_page_cited is not None:
        cited = content_page_cited
    elif message_id is not None:
        from src.db import connect as _connect

        try:
            with _connect() as conn:
                cited = _is_content_page_cited(conn, message_id)
        except psycopg.Error as exc:
            logger.warning(
                "content_page_citation_lookup_failed",
                message_id=message_id,
                error=str(exc)[:200],
            )
            cited = False
    else:
        cited = None

    if cited is not None:
        _push_score(
            client,
            trace_id=trace_id,
            name="content_page_cited",
            value=1.0 if cited else 0.0,
            data_type="BOOLEAN",
        )

    _push_score(
        client,
        trace_id=trace_id,
        name="gate_rejected_check_my_work",
        value=float(metric_values["gate_rejected_check_my_work"]),
        data_type="NUMERIC",
    )
    _push_score(
        client,
        trace_id=trace_id,
        name="auto_corrected",
        value=1.0 if metric_values["auto_corrected"] else 0.0,
        data_type="BOOLEAN",
    )
    _push_score(
        client,
        trace_id=trace_id,
        name="wrote_todos_early",
        value=1.0 if metric_values["wrote_todos_early"] else 0.0,
        data_type="BOOLEAN",
    )
    # "none" stands in for "reviewer didn't run" so the categorical
    # distribution still adds up to 100% in Langfuse dashboards.
    _push_score(
        client,
        trace_id=trace_id,
        name="reviewer_verdict",
        value=metric_values["reviewer_verdict"] or "none",
        data_type="CATEGORICAL",
    )
    # U7: single-bucket outcome categorization so dashboards can slice
    # traces into {content_page, trivial_skip, already_captured,
    # filing_cabinet, ghost} at a glance without reconstructing the
    # decision tree from the five boolean/numeric scores above.
    _push_score(
        client,
        trace_id=trace_id,
        name="compile_outcome",
        value=_classify_compile_outcome(observations, cited),
        data_type="CATEGORICAL",
    )


def _build_client() -> Any | None:
    """Return a configured Langfuse client, or None when unavailable.

    Reads the same settings the trace handler uses
    (LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST). Returns None when keys
    are missing or LANGFUSE_ENABLED=false so the post-batch hook
    becomes a silent no-op rather than failing the compile.
    """
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("langfuse_sdk_missing_for_scores")
        return None
    # Short timeout + immediate flush: this runs at the end of compile so
    # we don't want to block on a stuck server. Score writes are small
    # (5 per trace, N traces); flush_at=1 keeps them moving without a
    # batch deadline.
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        timeout=5,
        flush_at=1,
        flush_interval=1,
    )


def _list_run_batches(run_id: uuid.UUID) -> list[tuple[str, str]]:
    """Return ``[(message_id, thread_id), ...]`` for one run's attempts.

    Joined to ``messages`` so we get the thread_id (the trace lookup
    key) without a second round-trip. Filters out attempts whose
    ``thread_id`` is empty — without one we can't resolve the trace.

    Fails open: a DB error returns ``[]`` so the post-batch hook
    becomes a no-op rather than crashing the compile.
    """
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            rows = cast(
                "list[dict[str, Any]]",
                conn.execute(
                    """
                    SELECT DISTINCT ca.message_id, m.thread_id
                      FROM compile_attempts ca
                      JOIN messages m ON m.message_id = ca.message_id
                     WHERE ca.run_id = %s
                       AND m.thread_id IS NOT NULL
                       AND m.thread_id <> ''
                    """,
                    (run_id,),
                ).fetchall(),
            )
    except psycopg.Error as exc:
        logger.warning(
            "langfuse_scores_list_run_batches_failed",
            run_id=str(run_id),
            error=str(exc)[:200],
        )
        return []
    return [(str(r["message_id"]), str(r["thread_id"])) for r in rows]


# Langfuse OTel ingestion can lag behind by 5-15s after the compile
# batch finishes — we land in this module immediately after the agent
# returns, so the just-emitted trace may not be queryable yet. Retry a
# few times with backoff before giving up.
_TRACE_LIST_MAX_ATTEMPTS = 4
_TRACE_LIST_BACKOFF_S = (2.0, 5.0, 10.0)


def _resolve_traces(client: Any, run_id: uuid.UUID) -> dict[str, str]:
    """Map ``thread_id → trace_id`` for one run.

    The compile coordinator stamps each trace with
    ``metadata.compile_run_id`` and ``metadata.compile_thread_id`` (set
    in ``scripts/compile_all.py``'s ``run_compilation`` call). We
    filter on ``compile_run_id`` so the lookup stays bounded — one
    round-trip per run, not one per batch.

    Retries with backoff on empty results — Langfuse OTel ingestion can
    lag behind the just-finished compile by 5-15s, so the first query
    often returns 0 even when the trace exists.
    """
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
    for attempt in range(_TRACE_LIST_MAX_ATTEMPTS):
        try:
            response = client.api.trace.list(filter=filter_json, limit=100)
        except Exception as exc:  # noqa: BLE001 — Langfuse 524s common
            logger.warning(
                "langfuse_scores_trace_list_failed",
                run_id=str(run_id),
                attempt=attempt + 1,
                error=str(exc)[:200],
            )
            return {}
        # `response.data` is the list of TraceWithDetails pydantic models.
        # Use getattr so a future SDK shape change degrades to "no traces"
        # rather than AttributeError.
        items = getattr(response, "data", None) or []
        if items:
            mapping: dict[str, str] = {}
            for item in items:
                md = getattr(item, "metadata", None) or {}
                thread_id = md.get("compile_thread_id") if isinstance(md, dict) else None
                trace_id = getattr(item, "id", None)
                if thread_id and trace_id:
                    mapping[str(thread_id)] = str(trace_id)
            return mapping
        # Empty result — retry with backoff in case ingestion is lagging.
        if attempt < len(_TRACE_LIST_BACKOFF_S):
            time.sleep(_TRACE_LIST_BACKOFF_S[attempt])
    return {}


def _fetch_trace_observations(client: Any, trace_id: str) -> list[dict[str, Any]] | None:
    """Fetch one trace's observations as plain dicts. None on failure."""
    try:
        trace = client.api.trace.get(trace_id)
    except Exception as exc:  # noqa: BLE001 — Langfuse fetch can timeout
        logger.warning(
            "langfuse_scores_trace_get_failed",
            trace_id=trace_id,
            error=str(exc)[:200],
        )
        return None
    raw_obs = getattr(trace, "observations", None) or []
    out: list[dict[str, Any]] = []
    for obs in raw_obs:
        # Pydantic models in the SDK expose `.dict(by_alias=True)` which
        # gives us the same camelCase shape `_extract_trace_metrics`
        # consumes. Fall back to attribute access if the SDK ever
        # switches to a different serialization story.
        try:
            out.append(obs.dict(by_alias=True))
        except AttributeError:
            out.append({k: getattr(obs, k, None) for k in ("type", "name", "input", "output")})
    return out


def emit_scores_for_run(run_id: uuid.UUID) -> int:
    """Push Langfuse scores for every trace in the run. Returns count emitted.

    Driven from the coordinator post-batch hook. Walks
    ``compile_attempts``, resolves each ``(run_id, thread_id)`` to a
    Langfuse trace_id, fetches the observations, and pushes the 6
    headline scores per trace via :func:`emit_scores_for_trace`.

    Best-effort end-to-end: any of (Langfuse SDK missing, keys
    unconfigured, trace lookup 524, score push 500) results in a
    logged warning and zero or partial emission — the compile run
    finishes either way. Returns the count of traces successfully
    scored so the coordinator can log a single summary line.
    """
    client = _build_client()
    if client is None:
        logger.info("langfuse_scores_skipped", reason="client_unavailable")
        return 0

    batches = _list_run_batches(run_id)
    if not batches:
        logger.info("langfuse_scores_skipped", reason="no_batches", run_id=str(run_id))
        _safe_flush(client)
        return 0

    trace_index = _resolve_traces(client, run_id)
    if not trace_index:
        logger.info(
            "langfuse_scores_skipped",
            reason="no_traces_resolved",
            run_id=str(run_id),
            batch_count=len(batches),
        )
        _safe_flush(client)
        return 0

    # One DB round-trip for every citation flag — beats opening a fresh
    # connection inside emit_scores_for_trace per-message.
    citation_index = _content_cited_lookup([mid for mid, _ in batches])

    emitted = 0
    # Distinct thread_ids — one trace per batch, so we don't re-emit
    # scores for every message in a multi-message batch.
    seen_traces: set[str] = set()
    for message_id, thread_id in batches:
        trace_id = trace_index.get(thread_id)
        if trace_id is None or trace_id in seen_traces:
            continue
        observations = _fetch_trace_observations(client, trace_id)
        if observations is None:
            continue
        emit_scores_for_trace(
            client,
            trace_id,
            observations,
            message_id=message_id,
            content_page_cited=citation_index.get(message_id, False),
        )
        seen_traces.add(trace_id)
        emitted += 1

    # Final flush so scores hit the wire before the coordinator process
    # exits. Without this, in-memory queues can drop the last batch.
    _safe_flush(client)

    logger.info(
        "langfuse_scores_emitted",
        run_id=str(run_id),
        traces=emitted,
        batch_count=len(batches),
    )
    return emitted
