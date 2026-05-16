"""Batch-state DB writes for the compile coordinator.

Owns the catalog-truth state flips (``messages.compile_state``,
``compile_attempts`` outcomes, terminal-decision-guard skips) plus the
catalog queries that drive them. Every helper here is best-effort with
respect to DB blips: failures log a warning and leave the message
``pending`` rather than rolling forward incorrect state.

The functions here are coordinator-side, not agent-side: the agent
returns prose and tool calls, then THIS module decides which messages
graduate from ``pending`` based on what landed in
``message_touched_pages`` (catalog-truth) and ``compile_attempts``
(secondary signal).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import cast
from uuid import UUID

import psycopg
import structlog

from src.db import compile_attempts as compile_attempts_repo
from src.db import connect
from src.db.messages import fail_message_compile
from src.db.messages import find_by_raw_path
from src.db.messages import finish_message_compile
from src.db.messages import mark_skipped

logger = structlog.get_logger(__name__)


# Page types that count as "the agent did real compile work". Entity /
# person stubs name-drop a message without extracting content, so citing
# an email only in a stub is NOT evidence of compile success. The
# catalog-truth compile-state check filters `message_touched_pages`
# joins against this set; anything else (entity, person, home, changes,
# domain) keeps the message in `pending` so the next claim cycle retries.
CONTENT_PAGE_TYPES = frozenset(
    {"topic", "system", "policy", "decision", "glossary", "timeline", "conflict"}
)


# Insight categories that mean "don't compile this email, but don't
# leave it pending either". `trivial_skip` is currently accepted by the
# ``compile_insights`` CHECK (migration 202604160000); `already_captured`
# lands with U3 once the CHECK is widened. Keeping both names here is
# cheap and future-proofs the lookup: the DB never produces
# ``already_captured`` yet, so querying for it is a harmless no-op.
_SKIP_INSIGHT_CATEGORIES = frozenset({"trivial_skip", "already_captured"})


# Reason string stamped on ``last_error`` when the terminal-decision
# guard ran out of nudges. Matched verbatim by operator greps; change
# only in tandem with ``docs/audits/v12-50-compile-deep-audit-...``.
TERMINAL_GUARD_EXHAUSTED_REASON = "agent_exited_without_terminal_decision"


# Canonical sentinel substring for nudge detection. Must appear
# verbatim in ``TERMINAL_NUDGE_MESSAGE`` — enforced by
# ``test_terminal_guard_exhausted_detects_injected_nudge``. Kept as a
# short phrase (not the full message) so typo fixes or indentation
# tweaks to the nudge body don't silently break detection.
_TERMINAL_NUDGE_SENTINEL = "batch is about to exit without a terminal"


def _batch_paths(batch: list[Any]) -> list[str]:
    """Extract raw_path strings from a batch list (dicts or bare strings)."""
    return [item["path"] if isinstance(item, dict) else str(item) for item in batch]


def _collect_content_cited_message_ids(message_ids: list[str]) -> set[str]:
    """Return the subset of `message_ids` with >=1 touch in a content-type page.

    Replaces the older frontmatter scan with a catalog query against
    `message_touched_pages` joined to `wiki_pages` filtered by
    ``CONTENT_PAGE_TYPES``. Entity/person stubs don't count — name-dropping
    a message in a stub is not evidence the agent did compile work.

    Empty input → empty set (no DB round-trip).
    """
    if not message_ids:
        return set()
    # Lazy import so the conftest monkeypatch on ``src.db.connect`` (which
    # pins search_path to the per-test schema) is picked up. A top-level
    # ``from src.db import connect`` would bind the unpatched function and
    # tests would read from the production schema.
    from src.db import connect as _connect

    with _connect() as conn:
        # ``src.db.connect`` uses ``dict_row`` so fetchall() yields dicts,
        # but mypy can't see that through the generic ``psycopg.Connection``
        # return type — cast the result explicitly.
        rows = cast(
            list[dict[str, Any]],
            conn.execute(
                """
                SELECT DISTINCT mtp.message_id
                  FROM message_touched_pages mtp
                  JOIN wiki_pages wp ON wp.page_id = mtp.page_id
                 WHERE mtp.message_id = ANY(%s)
                   AND wp.page_type IN (
                     'topic','system','policy','decision','glossary',
                     'timeline','conflict'
                   )
                """,
                (list(message_ids),),
            ).fetchall(),
        )
    return {r["message_id"] for r in rows}


def _collect_attempts_compiled_message_ids(run_id: UUID, message_ids: list[str]) -> set[str]:
    """Secondary compile evidence (#174): return message_ids that already
    have ``compile_attempts.outcome='compiled'`` stamped in the current run.

    WHY — the primary signal is a content-page touch
    (``_collect_content_cited_message_ids``). That misses the legitimate
    edge case where the agent did real work (``write_draft_page``,
    ``edit_file``) but the only citation landed on a people/entity stub
    — the page_type filter excludes those by design, so the message
    wrongly stays ``pending`` and the next run's kimi batch re-processes
    it and often lands on ``skipped``. That was the ~5% waste #174
    identified.

    Scoping the lookup to ``run_id`` keeps the signal tight: older
    compile-attempts (from prior runs) don't reach across and flip
    messages the current run never processed.

    Fails open — a DB blip returns an empty set so the coordinator
    leaves the message pending rather than falsely promoting it.
    """
    if not message_ids:
        return set()
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            rows = cast(
                list[dict[str, Any]],
                conn.execute(
                    """
                    SELECT DISTINCT message_id
                      FROM compile_attempts
                     WHERE run_id = %s
                       AND outcome = 'compiled'
                       AND message_id = ANY(%s)
                    """,
                    (run_id, list(message_ids)),
                ).fetchall(),
            )
    except Exception as exc:  # noqa: BLE001 — attempts lookup is best-effort
        logger.warning(
            "attempts_compiled_fetch_failed",
            run_id=str(run_id),
            error=str(exc),
        )
        return set()
    return {r["message_id"] for r in rows}


def _insights_skip_paths(run_id: UUID) -> set[str]:
    """Return `email_path`s the agent flagged as skip-worthy this run.

    Joined against batch raw paths to flip matching messages to
    ``skipped`` instead of leaving them pending. Failures are logged and
    return an empty set — absence of a skip signal is the safe default
    (leave ``pending``; the claim loop will retry).
    """
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            rows = cast(
                list[dict[str, Any]],
                conn.execute(
                    """
                    SELECT DISTINCT email_path
                      FROM compile_insights
                     WHERE run_id = %s
                       AND category = ANY(%s)
                       AND email_path IS NOT NULL
                    """,
                    (run_id, list(_SKIP_INSIGHT_CATEGORIES)),
                ).fetchall(),
            )
    except Exception as exc:  # noqa: BLE001 — insights are best-effort
        logger.warning("skip_insights_fetch_failed", run_id=run_id, error=str(exc))
        return set()
    return {r["email_path"] for r in rows if r.get("email_path")}


def _mark_batch_compiled(
    batch: list[Any],
    wiki_dir: Path,
    compile_model: str | None = None,
    *,
    run_id: UUID | None = None,
) -> tuple[list[str], list[str], list[str], int]:
    """Flip batch emails to `compiled` / `skipped` / keep-pending using
    the catalog as source of truth.

    Returns ``(compiled_ids, skipped_ids, not_cited_paths, missing)``.
      - ``compiled_ids``: message_ids with >=1 touch in a content-type
        page (``CONTENT_PAGE_TYPES``) → flipped to ``compiled``.
      - ``skipped_ids``: message_ids the agent declared trivial /
        already-captured via ``log_insight`` this run → flipped to
        ``skipped`` (terminal; never re-claimed).
      - ``not_cited_paths``: raw_paths of emails without a content
        touch AND without a skip insight — agent likely didn't finish
        them; kept ``pending`` for the next claim cycle. Returned as
        paths (not counts) so the caller can selectively flip the
        terminal-decision-guard-exhausted subset to ``skipped``.
      - ``missing``: count of emails whose raw_path has no ``messages``
        row at all — indicates backfill drift; logged as a warning.

    `compile_model` records which A/B-pool model produced this batch so
    we can later join model → outcome. `run_id` scopes the skip-insight
    lookup to the current run — insights from earlier runs don't reach
    back and flip emails this run didn't touch. When ``run_id`` is None
    the skip path is a no-op (callers can still get the compiled/pending
    split).

    Catalog-truth successor to the frontmatter scan: entity/person stubs
    name-dropping the email no longer count as "compiled" because the
    ``page_type`` join filters them out. ``wiki_dir`` is retained for
    call-site compatibility; no longer read.
    """
    _ = wiki_dir  # retained for back-compat; content-truth now comes from the catalog
    # Resolve (path → messages row) once so we can batch the catalog
    # query by message_id instead of running one query per path.
    row_by_path: dict[str, Any] = {}
    missing = 0
    for path in _batch_paths(batch):
        row = find_by_raw_path(path)
        if row is None:
            logger.warning("no messages row for batch path", path=path)
            missing += 1
            continue
        row_by_path[path] = row

    message_ids = [str(row["message_id"]) for row in row_by_path.values()]
    content_cited = _collect_content_cited_message_ids(message_ids)
    skip_paths = _insights_skip_paths(run_id) if run_id is not None else set()
    # Secondary compile signal (#174): message_ids that already have an
    # attempts-row outcome='compiled' in this run. Catches the edge case
    # where the agent's only citation was to a people/entity stub —
    # content-page filter drops those but the attempts row says the
    # batch did real work.
    attempts_compiled: set[str] = (
        _collect_attempts_compiled_message_ids(run_id, message_ids) if run_id is not None else set()
    )

    compiled_ids: list[str] = []
    skipped_ids: list[str] = []
    not_cited_paths: list[str] = []
    for path, row in row_by_path.items():
        mid = str(row["message_id"])
        cited_in_content = mid in content_cited
        outcome_in_attempts = mid in attempts_compiled
        if cited_in_content or outcome_in_attempts:
            finish_message_compile(mid, compile_model=compile_model)
            compiled_ids.append(mid)
            decision = "compiled"
        elif path in skip_paths:
            # ``mark_skipped`` is a no-op on already-compiled/claimed
            # rows (state guard inside the repo function), so ordering
            # vs. the ``content_cited`` branch is safe either way.
            mark_skipped(mid, "insight:trivial_or_already_captured")
            skipped_ids.append(mid)
            decision = "skipped"
        else:
            logger.warning(
                "batch email not cited in content page; leaving pending",
                path=path,
                message_id=mid,
            )
            not_cited_paths.append(path)
            decision = "kept_pending"
        logger.info(
            "state_flip_decision",
            message_id=mid,
            outcome_in_attempts=outcome_in_attempts,
            cited_in_content_page=cited_in_content,
            decision=decision,
        )
    return compiled_ids, skipped_ids, not_cited_paths, missing


def _mark_batch_failed(batch: list[Any], error: str, compile_model: str | None = None) -> int:
    """Mark every email in a crashed batch as failed. Returns marked count.

    Records the model that failed so per-model failure rates are
    recoverable from the catalog later.
    """
    trimmed = error[:500]
    marked = 0
    for path in _batch_paths(batch):
        row = find_by_raw_path(path)
        if row is None:
            continue
        fail_message_compile(row["message_id"], trimmed, compile_model=compile_model)
        marked += 1
    return marked


def _terminal_guard_exhausted(batch_result: dict[str, Any] | None) -> bool:
    """True when the terminal-decision guard ran but didn't secure a commit.

    The middleware (``TerminalDecisionGuardMiddleware``) injects the
    ``TERMINAL_NUDGE_MESSAGE`` into agent state each time the agent
    tries to exit without a content write or terminal ``log_insight``.
    Presence of that message in the final batch result means the
    guard fired at least once; combined with a downstream ``not_cited``
    classification for the same email, it's the load-bearing signal
    that the agent genuinely refused to decide.
    """
    if not isinstance(batch_result, dict):
        return False
    messages = batch_result.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and _TERMINAL_NUDGE_SENTINEL in content:
            return True
    return False


def _mark_terminal_guard_exhausted_paths(
    not_cited_paths: list[str],
) -> list[str]:
    """Flip ``not_cited`` paths to ``skipped`` with the guard-exhausted reason.

    Returns the list of ``message_id`` strings that were actually
    flipped (``mark_skipped`` rowcount 1). The rest — already
    compiled/claimed, or without a ``messages`` row — are silently
    dropped; those cases are handled by the caller's existing
    branches.

    Marking ``skipped`` (rather than ``failed``) is deliberate: the
    claim loop filters to ``pending`` + ``failed`` and re-queues
    anything matching. ``skipped`` is terminal, which matches the
    spec's "investigate but do NOT auto-requeue" requirement — the
    agent made a decision-shaped non-decision that won't resolve by
    retry. ``last_error`` carries the distinct reason so humans can
    grep for guard-exhausted messages separately from the
    trivial/already-captured skips.
    """
    flipped: list[str] = []
    for path in not_cited_paths:
        row = find_by_raw_path(path)
        if row is None:
            continue
        mid = str(row["message_id"])
        rowcount = mark_skipped(mid, TERMINAL_GUARD_EXHAUSTED_REASON)
        if rowcount:
            flipped.append(mid)
    return flipped


def _record_attempts_start(
    batch: list[Any],
    *,
    run_id: UUID,
    compile_model: str,
) -> dict[str, int]:
    """Insert one in-flight ``compile_attempts`` row per batch message.

    Returns a map of ``message_id → attempt_id`` so the caller can stamp
    the outcome on batch completion. Uses a single connection and commits
    immediately — if the batch crashes mid-compile, the in-flight rows
    are still visible for the next run's ``_healthy_pool`` pass (they just
    lack an outcome; ``model_health_stats`` filters them via
    ``finished_at IS NOT NULL``).

    Fails open on DB errors: logs a warning and returns an empty map so
    the caller proceeds without attempt tracking rather than aborting the
    batch. Rows that can't map a path → message_id (backfill drift) are
    skipped with a warning — same behavior as ``_mark_batch_compiled``.
    """
    attempts: dict[str, int] = {}
    try:
        with connect() as conn:
            for path in _batch_paths(batch):
                row = find_by_raw_path(path)
                if row is None:
                    logger.warning(
                        "attempt_start: no messages row for batch path",
                        path=path,
                    )
                    continue
                message_id = str(row["message_id"])
                attempt_id = compile_attempts_repo.record_start(
                    conn,
                    message_id=message_id,
                    run_id=run_id,
                    compile_model=compile_model,
                )
                attempts[message_id] = attempt_id
            conn.commit()
    except psycopg.Error as exc:
        logger.warning("attempt_start_db_error", error=str(exc))
        return {}
    return attempts


def _record_attempts_outcome(
    attempts: dict[str, int],
    message_ids: list[str],
    *,
    outcome: str,
    error: str | None = None,
) -> None:
    """Stamp ``outcome`` + ``finished_at`` on the given attempt rows.

    ``message_ids`` scopes which attempts to update (so success path only
    stamps the actually-marked messages). Fails open on DB errors — the
    next run's guard just sees stale in-flight rows, which are filtered
    out of ``model_health_stats``.
    """
    if not attempts or not message_ids:
        return
    try:
        with connect() as conn:
            for mid in message_ids:
                attempt_id = attempts.get(mid)
                if attempt_id is None:
                    continue
                compile_attempts_repo.record_outcome(
                    conn,
                    attempt_id=attempt_id,
                    outcome=outcome,
                    error=error,
                )
            conn.commit()
    except psycopg.Error as exc:
        logger.warning("attempt_outcome_db_error", error=str(exc))
