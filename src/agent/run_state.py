"""Run-state globals shared across the compile agent's tools and middleware.

ContextVars + the `_check_my_work_cache` / `_write_epoch` pair are
extracted from the legacy `src/compile/compiler.py` (Phase 1C of the
src/wiki/-vs-src/agent/ refactor). `src/compile/` was deleted in the
2026-04-29 refactor — middleware, agent-tools, and the orchestrator
read/write these directly from `src.agent.run_state`.

Module-globals smell is acknowledged — see the plan's "What this plan
does NOT do" section. Wave 2 work, separate PR.
"""

from __future__ import annotations

import contextvars
import re
from datetime import date
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ContextVar carrying the current batch's raw paths. The coordinator sets
# this in `run_compilation` before invoking the agent, and `create_entities`
# reads it without needing the LLM to thread `raw_paths` through. Shrinking
# the LLM-visible signature cuts one frequent error mode (agent forgets or
# malforms the raw_paths list).
_current_raw_paths: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "current_raw_paths", default=None
)

# ContextVar carrying the chronological cutoff for this batch — the latest
# `messages.date` among the batch's raw_paths. `get_thread_context` reads
# it to clip future replies the writer shouldn't see (Bug H fix). The
# prompt tells the agent it's processing email N of a thread "as a writer
# at that point in time"; this enforces it structurally.
#
# Stored as ISO8601 string (not datetime) so the ContextVar stays picklable
# and avoids tz-comparison surprises at query time — Postgres casts the
# literal to timestamptz.
_current_batch_cutoff_date: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_batch_cutoff_date", default=None
)

# ContextVar carrying the batch's thread_id — populated only when every
# raw_path in the batch belongs to the same Gmail thread. Read by the
# `same_thread_topic_guard` middleware to detect a second /wiki/topics/
# write within the same concept stream (Codex 2026-04-17 fragmentation
# bug: Seller BL thread producing two topic pages for one stream).
#
# Stays None when the batch straddles multiple threads — the guard
# isn't meaningful across threads and shouldn't fire.
_current_batch_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_batch_thread_id", default=None
)

# Topic slugs the agent has successfully written during the current
# run. Populated by `SameThreadTopicGuardMiddleware` on each
# successful `write_file` to /wiki/topics/ so that an in-run duplicate
# (second topic write in the same batch, before the coordinator's
# post-run catalog sync has a chance to land the first) is still
# caught. Catalog-only checks miss this case because
# `message_touched_pages` is populated *after* `run_compilation`
# returns. Codex P1 on PR #171.
_current_batch_topic_slugs_written: contextvars.ContextVar[set[str] | None] = (
    contextvars.ContextVar("current_batch_topic_slugs_written", default=None)
)

# Sibling-eligible page slugs (topic + system) the agent has written in
# this batch. Populated by `SiblingDraftCheckMiddleware` to detect
# near-duplicate page creations within a single batch — e.g. the Cycle
# 10 case where one batch produced both `seller-bl-api-optimization`
# and `seller-bl-api-hit-optimisation` from the same thread. The post-
# hoc reviewer (v9-U14) catches these too, but only AFTER both pages
# ship; this set lets the middleware reject the second write before it
# lands. v11-U9.
_current_batch_sibling_slugs_written: contextvars.ContextVar[set[str] | None] = (
    contextvars.ContextVar("current_batch_sibling_slugs_written", default=None)
)


# Cache of prior `check_my_work` payloads keyed by
# (raw_email_path, write_epoch, sha256(acknowledge)). Repeat calls with
# no intervening write hit cache and return the prior payload so the
# agent stops spinning (PR #225 regression: 4-7 blocked calls in a row
# with zero edits between). `_write_epoch` bumps invalidate the entry.
_check_my_work_cache: dict[tuple[str, int, str], dict[str, Any]] = {}

# Bumped by `CheckMyWorkGateMiddleware._record_success` on every
# successful content-page write — exposes one choke point to log or
# instrument if the count diverges from the agent's self-report.
_write_epoch: int = 0


def _bump_write_epoch() -> int:
    """Advance the write epoch and return the new value."""
    global _write_epoch
    _write_epoch += 1
    return _write_epoch


def _extract_raw_paths_from_instruction(instruction: str) -> list[str]:
    """Pull raw/*.md paths out of the coordinator's instruction string.

    The coordinator inlines the batch's raw paths in the user message (see
    `scripts/compile_all.py::_build_batch_instruction`). We grep them out
    so `create_entities` can inject `raw_paths` without the LLM having to
    thread them through.

    Returns the unique list in source order. An empty list is a benign
    signal — create_entities will error out with a clear message.
    """
    matches = re.findall(r"raw/[^\s`'\"]+?\.md", instruction)
    seen: dict[str, None] = {}
    for m in matches:
        seen[m] = None
    return list(seen)


def _preflight_raw_paths_exist(raw_paths: list[str]) -> None:
    """Assert every batch raw_path exists on disk before agent invocation.

    Catches the "agent didn't write anything" failure mode from 2026-04-16
    where ``find_new_sources`` returned valid raw_paths from the DB but the
    filesystem mount was empty (worktree with only ``.gitkeep`` under raw/).
    In that case every `read_file` silently failed and traces looked like
    synthesis failures — this guard turns it into an unambiguous
    environment/config error BEFORE any LLM cost is incurred.

    Empty ``raw_paths`` is not an error here — run_compilation is sometimes
    invoked without a batch list (e.g. free-form queries). The guard fires
    only when the caller explicitly passes paths that should exist.
    """
    missing = [p for p in raw_paths if not Path(p).exists()]
    if missing:
        raise FileNotFoundError(
            "environment/config error — raw files missing from disk: "
            + ", ".join(missing[:5])
            + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
        )


def _autoheal_email_path(email_path: str) -> str:
    """Normalize + verify `email_path` against DB + filesystem.

    Agent sees the raw dir as ``/raw/`` via its chrooted virtual-mode
    filesystem, so it often passes the virtual path (leading slash).
    The coordinator's skip-path matcher compares to ``messages.raw_path``
    which stores the unrooted form — so ``/raw/...`` silently misses
    (Bug L). Autoheal steps:

    1. Normalize: strip any leading slashes so ``/raw/...`` becomes
       ``raw/...``.
    2. DB check: if the normalized path exists in ``messages.raw_path``,
       accept it — this is the happy path.
    3. Filesystem fallback: if DB doesn't know the path (test fixtures,
       pre-ingest fossils) but the file exists on disk, accept it with
       a warning.
    4. Warn: otherwise, log ``log_insight_path_unknown`` and return the
       normalized path anyway. The coordinator's batch-end skip-path
       materialization is the authoritative gate — rejecting here would
       only couple ``log_insight`` to infrastructure state.

    Always returns the normalized path. Point of autoheal is the common
    leading-slash case (caught by strip) + observability when something
    less obvious drifts.
    """
    normalized = email_path.lstrip("/")

    try:
        from src.db.messages import find_by_raw_path

        row = find_by_raw_path(normalized)
    except Exception as exc:  # noqa: BLE001 — DB outage is non-fatal
        logger.warning("autoheal_db_lookup_failed", path=normalized, error=str(exc))
        return normalized

    if row is None and not Path(normalized).is_file():
        logger.warning(
            "log_insight_path_unknown",
            path=normalized,
            reason="not in messages and not on disk — skip-materialization will fail",
        )

    return normalized


def _compute_batch_cutoff_date(raw_paths: list[str]) -> str | None:
    """Return the latest raw-filename date for the batch as YYYY-MM-DD.

    The cutoff is derived from filename prefixes (``YYYY-MM-DD_...``),
    NOT from the Postgres ``messages.date`` timestamp. Rationale: the
    ingest pipeline writes filenames in its local timezone (IST for
    IndiaMART), but ``messages.date`` lands as UTC timestamptz. A
    near-midnight email can therefore have a filename dated Jan 10 and
    a DB timestamp on Jan 9 UTC — the middleware, which compares against
    the filename prefix, would false-reject the batch's own raw if we
    went through the DB. Using the filename on BOTH sides keeps the
    enforcement layer consistent.

    Returns None when no raw path has a parseable date prefix (test
    fixtures, pre-ingest fossils).
    """
    if not raw_paths:
        return None

    from src.agent.middleware.chronological_scope import _raw_file_date

    dates: list[date] = [d for p in raw_paths if (d := _raw_file_date(p)) is not None]
    if not dates:
        return None
    return max(dates).isoformat()
