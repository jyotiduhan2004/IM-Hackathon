"""Compile all unprocessed raw emails into wiki pages.

Usage:
    uv run python scripts/compile_all.py
    uv run python scripts/compile_all.py --dry-run
    uv run python scripts/compile_all.py --batch-size 10
"""

from __future__ import annotations

import concurrent.futures
import os
import sys
from datetime import UTC
from datetime import datetime
from functools import partial
from pathlib import Path

import click
import psycopg
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent.compiler_agent import run_compilation  # noqa: E402
from src.agent.reviewer_result import _extract_merge_candidates  # noqa: E402
from src.agent.tools.sources import list_uncompiled_emails  # noqa: E402
from src.budget import fetch_budget  # noqa: E402
from src.config import settings  # noqa: E402
from src.coordinator.batch_state import TERMINAL_GUARD_EXHAUSTED_REASON  # noqa: E402
from src.coordinator.batch_state import _mark_batch_compiled  # noqa: E402
from src.coordinator.batch_state import _mark_batch_failed  # noqa: E402
from src.coordinator.batch_state import _mark_terminal_guard_exhausted_paths  # noqa: E402
from src.coordinator.batch_state import _record_attempts_outcome  # noqa: E402
from src.coordinator.batch_state import _record_attempts_start  # noqa: E402
from src.coordinator.batch_state import _terminal_guard_exhausted  # noqa: E402
from src.coordinator.grouping import _group_by_thread  # noqa: E402
from src.coordinator.logging import BatchOutcome  # noqa: E402
from src.coordinator.logging import _append_batch_log  # noqa: E402
from src.coordinator.logging import _append_merge_candidates  # noqa: E402
from src.coordinator.logging import _emit_langfuse_scores_for_run  # noqa: E402
from src.coordinator.logging import _flush_tool_calls  # noqa: E402
from src.coordinator.logging import _insights_suffix  # noqa: E402
from src.coordinator.logging import _max_insight_id_safe  # noqa: E402
from src.coordinator.model_pool import _fetch_available_models  # noqa: E402
from src.coordinator.model_pool import _is_model_unavailable_error  # noqa: E402
from src.coordinator.model_pool import _prepare_model_pool  # noqa: E402
from src.coordinator.model_pool import _refresh_pool_for_batch  # noqa: E402
from src.coordinator.model_pool import _setup_model_pool  # noqa: E402
from src.coordinator.post_batch import _backfill_references_on_touched_pages  # noqa: E402
from src.coordinator.post_batch import _iter_touched_content_pages  # noqa: E402
from src.coordinator.post_batch import _iter_touched_pages  # noqa: E402
from src.coordinator.post_batch import _mdlint_autofix_touched_pages  # noqa: E402
from src.coordinator.post_batch import _normalize_touched_pages  # noqa: E402
from src.coordinator.post_batch import _refresh_qmd_index  # noqa: E402
from src.coordinator.post_batch import _regenerate_landing_surfaces  # noqa: E402
from src.coordinator.post_batch import _stamp_recently_modified_pages  # noqa: E402
from src.coordinator.post_batch import _sync_and_stamp_landing_surfaces  # noqa: E402
from src.coordinator.post_batch import _sync_wiki_catalog  # noqa: E402
from src.coordinator.post_batch import _validate_touched_pages  # noqa: E402
from src.coordinator.post_batch import _write_touch_catalog  # noqa: E402
from src.coordinator.preflight import _preflight_mount_sanity  # noqa: E402
from src.coordinator.preflight import _preview_dry_run  # noqa: E402
from src.coordinator.preflight import _run_with_timeout  # noqa: E402
from src.db import compile_attempts as compile_attempts_repo  # noqa: E402
from src.db.compile_runs import finish_run  # noqa: E402
from src.db.compile_runs import start_run  # noqa: E402
from src.db.messages import recover_stale_claims_at_startup  # noqa: E402
from src.observability.cache_stats import BatchStatsCallback  # noqa: E402
from src.observability.tool_call_log import ToolCallLogHandler  # noqa: E402
from src.wiki.landing import update_wiki_index  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)


@click.command()
@click.option(
    "--batch-size",
    default=20,
    help="Max emails to compile per agent invocation (default 20)",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help=(
        "Max number of THREADS to process this run (oldest-first by earliest "
        "message date). All pending emails in those threads are pulled. "
        "Default: all pending. Standalone (no-thread) emails each count as "
        "one thread."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List uncompiled emails without compiling",
)
@click.option(
    "--model",
    default=None,
    help="Override LLM model (default from .env LLM_MODEL)",
)
@click.option(
    "--model-pool",
    default=None,
    help=(
        "Comma-separated model IDs. If set, picks one at random per batch "
        "(sticky for that batch) so per-batch cache stats compare models. "
        "Overrides --model. Example: "
        "'z-ai/glm-4.6,minimax/minimax-m2.7'"
    ),
)
@click.option(
    "--recursion-limit",
    type=int,
    default=250,
    help=(
        "Max LangGraph super-steps per batch (each parent turn costs ~5 "
        "super-steps: model + ToolNode + 3 after_model middlewares). "
        "Default 250 (was 150) per smoke-99a267f4 audit — covers legitimate "
        "5-email batches with multi-page writes + reviewer subagents that "
        "share the parent budget. Lower it (e.g. 60) to fail fast on "
        "pathological threads."
    ),
)
@click.option(
    "--batch-timeout",
    type=click.IntRange(min=0),
    # 1800s gives one 900s ultimate-failure + one fallback-retry budget;
    # see _make_chat_model docstring for the full invariant.
    default=1800,
    help=(
        "Per-batch wall-clock timeout in seconds (default 1800 = 30 min). "
        "Pass 0 to disable. Guards against a single hung batch "
        "(slow OTel export, stuck LLM provider, rare deadlock) freezing "
        "the whole compile loop."
    ),
)
@click.option(
    "--deploy",
    is_flag=True,
    default=False,
    help=(
        "After a successful compile, run the publish-gate + rsync wiki to GCS "
        "+ redeploy Cloud Run viewer (equiv of `make publish`). No-op if the "
        "run did not complete successfully (killed / failed)."
    ),
)
@click.option(
    "--deploy-force",
    is_flag=True,
    default=False,
    help=(
        "After a successful compile, deploy EVEN IF validate_wiki has errors "
        "(equiv of `make publish-force`). Use only when you know the errors "
        "are pre-existing and not introduced by this run."
    ),
)
def main(
    batch_size: int,
    limit: int | None,
    dry_run: bool,
    model: str | None,
    model_pool: str | None,
    recursion_limit: int,
    batch_timeout: int,
    deploy: bool,
    deploy_force: bool,
) -> None:
    """Compile uncompiled raw emails into wiki pages using Deep Agents.

    Pass ``--deploy`` (or ``--deploy-force``) to run ``make publish`` /
    ``make publish-force`` after the compile loop. The deploy step only
    fires when ``run_status == "completed"`` — a killed/failed run will
    skip it so operators don't publish a partially-compiled wiki.
    """
    import random
    import time

    # Capture the run start before any wiki work so we can stamp every page
    # whose mtime advances during the batch loop. See
    # `_stamp_recently_modified_pages` for the why.
    run_start = time.time()
    raw_dir = str(settings.raw_dir)
    wiki_dir = str(settings.wiki_dir)
    resolved_model = model or settings.llm_model

    # Preflight: fail fast if the raw/wiki mounts look wrong. Catches the
    # 2026-04-16 failure mode where a codex worktree had an empty /raw
    # mount (just .gitkeep + attachments), read_file silently failed,
    # and traces looked like synthesis failures. See F3 in the plan.
    raw_dir_path = Path(raw_dir).resolve()
    wiki_dir_path = Path(wiki_dir).resolve()
    raw_md_count = _preflight_mount_sanity(raw_dir_path, wiki_dir_path)
    # topics/ is guaranteed to exist post-preflight.
    topics_count = sum(1 for _ in (wiki_dir_path / "topics").glob("*.md"))
    click.echo(
        f"Preflight OK: cwd={Path.cwd()} raw_dir={raw_dir_path} "
        f"wiki_dir={wiki_dir_path} raw_md={raw_md_count} topics={topics_count}"
    )
    logger.info(
        "preflight_mount_ok",
        cwd=str(Path.cwd()),
        raw_dir=str(raw_dir_path),
        wiki_dir=str(wiki_dir_path),
        raw_dir_md_count=raw_md_count,
        wiki_dir_topics_count=topics_count,
    )

    pool = _setup_model_pool(model_pool, resolved_model)

    if not dry_run:
        try:
            compile_attempts_repo.ensure_schema()
        except psycopg.Error as exc:
            logger.warning("compile_attempts_schema_ensure_failed", error=str(exc))

    pool = _prepare_model_pool(
        pool, _fetch_available_models(), settings.litellm_base_url, resolved_model
    )
    # Snapshot for per-batch _healthy_pool re-call (#194). 401/403 prunes
    # carry cross-batch in `unauthorized`; quarantine log dedupes per (model,
    # reason).
    initial_pool: list[str] = list(pool)
    unauthorized: set[str] = set()
    announced_quarantines: set[tuple[str, str]] = set()

    # Recover orphan claims from prior crashed runs (dispatcher's list helpers
    # only see pending/failed; claims invisible without this flip).
    if not dry_run:
        recover_stale_claims_at_startup(echo_fn=click.echo)

    # Use the tool directly for listing, not through the agent.
    # When --limit is set, `list_uncompiled_by_thread` pulls all pending
    # emails from the oldest N THREADS (not N emails) so batch_size can
    # actually matter; without --limit we fall back to the full pool.
    if limit:
        from src.db.messages import list_uncompiled_by_thread

        rows = list_uncompiled_by_thread(limit_threads=limit)
        uncompiled = [
            {
                "path": str(row["raw_path"]),
                "date": row["date"].isoformat() if row["date"] else "",
                "subject": str(row["subject"] or ""),
                "from": str(row["from_address"] or ""),
                "thread_id": str(row["thread_id"] or ""),
            }
            for row in rows
        ]
        total = len(uncompiled)
        click.echo(f"Processing oldest {limit} thread(s): {total} emails total.")
    else:
        all_uncompiled = list_uncompiled_emails.invoke({"raw_dir": raw_dir})
        uncompiled = all_uncompiled
        total = len(uncompiled)
        click.echo(f"Found {total} uncompiled emails total (no --limit; processing all).")
    if total == 0:
        click.echo("Nothing to compile.")
        # Still regenerate index in case wiki changed
        click.echo("Regenerating wiki index...")
        click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
        _regenerate_landing_surfaces(wiki_dir)
        landing_stamped, landing_synced = _sync_and_stamp_landing_surfaces(wiki_dir, resolved_model)
        if landing_stamped or landing_synced:
            click.echo(
                f"Landing surfaces: stamped {landing_stamped}, catalog synced {landing_synced}"
            )
        return

    # Auto-snapshot before compiling so we can roll back if the run corrupts
    # wiki pages. Snapshots are cheap (local copy) and have saved us pain.
    if not dry_run:
        label = f"pre-compile-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        snapshot_path = REPO_ROOT / ".snapshots" / label
        if (REPO_ROOT / wiki_dir).exists():
            import shutil

            snapshot_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(REPO_ROOT / wiki_dir, snapshot_path / "wiki")
            click.echo(f"Pre-compile snapshot: .snapshots/{label}/wiki")

    if dry_run:
        _preview_dry_run(uncompiled, batch_size)
        return

    click.echo(f"Compiling in batches of {batch_size}...")
    click.echo(f"Model: {resolved_model}")
    click.echo(f"Wiki dir: {wiki_dir}")
    budget_before = fetch_budget()
    if budget_before:
        click.echo(f"Budget (pre-run): {budget_before}")
    click.echo()

    # Group uncompiled emails into thread batches. One compile invocation per
    # thread so the agent sees full conversation context at once (3-5x cheaper
    # than recompiling the same page for each reply).
    groups = _group_by_thread(uncompiled, max_per_group=batch_size)
    click.echo(
        f"Thread-grouped into {len(groups)} batches "
        f"(avg {total / max(len(groups), 1):.1f} emails/batch)"
    )

    # Start a compile_runs row so we get per-invocation observability even if
    # the loop crashes. finish_run() below runs in `finally:` → always written.
    run_id = start_run(
        model=resolved_model,
        notes=f"limit={limit} batch_size={batch_size} recursion_limit={recursion_limit}",
    )
    click.echo(f"Run id: {run_id}")

    # Expose the run id to in-process tools (see
    # `src/agent/tools/insights.py::log_insight`) so every insight the agent
    # records can be joined back to this run.
    os.environ["COMPILE_RUN_ID"] = str(run_id)

    processed = 0
    failed = 0
    # Pessimistic default — overwritten to 'completed' on clean loop exit or
    # 'killed' on KeyboardInterrupt. Any other exception leaves it 'failed'.
    run_status = "failed"
    budget_after = None
    try:
        for batch_idx, batch in enumerate(groups, start=1):
            batch_paths = [b["path"] if isinstance(b, dict) else b for b in batch]
            batch_files = "\n".join(f"- {p}" for p in batch_paths)
            thread_id = batch[0].get("thread_id", "") if batch else ""
            earliest = batch[0].get("date", "")[:10] if batch else ""

            instruction = (
                f"Your job is to update or create the CONCEPT page that best "
                f"describes the subject of these {len(batch)} emails from one "
                f"thread (thread_id={thread_id}, earliest={earliest}). The emails "
                f"are EVIDENCE — what was announced, what was built, what was "
                f"tested, what was decided, what went wrong, who asked what. "
                f"The wiki page is a CONCEPT — a durable description of the "
                f"feature / initiative / decision / system itself, independent "
                f"of the thread that surfaced it. Ask yourself before writing: "
                f'"If the emails went away tomorrow, would this page still '
                f'stand as a useful description of the thing?" If the Summary '
                f"reads like a thread intro (`This thread discusses...`, `We "
                f"announced...`, `The team decided...`), rewrite it as a "
                f"concept definition (`<Thing> is <description>. It <does X> "
                f"for <who>`). When the concept page already exists, UPDATE it: "
                f"absorb new evidence into the current-truth Summary, append a "
                f"`Recent changes` bullet, and add `Open questions` if the "
                f"emails raise unresolved decisions. Never dump the email "
                f"thread verbatim. Never add `## Launch Announcement` / "
                f"`## Bug Report` / `## Testing Results` / `## Final Decision` "
                f"as H2 headings — those describe one email, not a concept. "
                f"Process the emails chronologically as a conversation; when "
                f"multiple replies build on the same concept, merge them into "
                f"a single coherent page rather than one page per message.\n\n"
                f"Files to compile:\n{batch_files}"
            )

            # Re-filter pool per batch so mid-run fail-rate crossings quarantine
            # on the next batch (#194); run-start alone missed kimi-k2.6 burning
            # 60/106 attempts in run 02c9d536.
            if initial_pool:
                pool = _refresh_pool_for_batch(
                    initial_pool, unauthorized, announced_quarantines, batch_idx
                )
            batch_model = random.choice(pool) if pool else resolved_model
            # Per-batch visibility — pairs with `pool_refresh` in
            # `_refresh_pool_for_batch`. Log the actual pool truthfully
            # (empty list when fallback to `resolved_model` fired) so
            # telemetry doesn't claim `resolved_model` was a pool member.
            logger.info(
                "pool_pick",
                batch_idx=batch_idx,
                pool_size=len(pool) if pool else 0,
                pool=pool or [],
                picked=batch_model,
                fallback=not pool,
            )
            click.echo(
                f"\n=== Batch {batch_idx}/{len(groups)} "
                f"({len(batch)} emails, thread={thread_id[:12]}, "
                f"earliest={earliest}, model={batch_model}) ==="
            )
            cache_cb = BatchStatsCallback(model=batch_model)
            tool_cb = ToolCallLogHandler()
            # Snapshot the insight-id cursor BEFORE the batch so we can show
            # only the insights logged during this batch in the digest.
            insights_cursor = _max_insight_id_safe(run_id)
            # Snapshot wall-clock BEFORE run_compilation so the post-batch
            # formatter hook can find every page the agent touched during
            # the batch (mtime >= batch_start).
            batch_start = time.time()
            # Record in-flight compile_attempts rows BEFORE dispatch so a
            # mid-batch crash still leaves the attempt visible (with a
            # NULL outcome) for post-mortem and for the next run's
            # `_healthy_pool` pass to filter out.
            attempts = _record_attempts_start(batch, run_id=run_id, compile_model=batch_model)
            try:
                # ``concurrent.futures.TimeoutError`` is a subclass of
                # ``Exception``, so the outer ``except`` below handles it
                # via ``_mark_batch_failed`` + ``_append_batch_log``.
                #
                # Inner retry loop: when LiteLLM rejects the picked model
                # (401 ``team not allowed`` / 400 ``Invalid model name``),
                # drop the model from ``pool`` for this run and retry the
                # same batch with another. Avoids burning every batch on
                # an unprovisioned model while waiting for ``_healthy_pool``
                # to accumulate enough failures cross-run.
                while True:
                    # Cumulative deadline across model retries — the
                    # documented per-batch wall-clock cap is enforced
                    # against batch_start, not per-attempt. A pathological
                    # 401-after-N-seconds attempt won't get a fresh full
                    # budget for its retry.
                    remaining_budget = batch_timeout - (time.time() - batch_start)
                    if remaining_budget <= 0:
                        raise concurrent.futures.TimeoutError(
                            f"batch budget ({batch_timeout}s) exhausted across model "
                            f"retries (thread={thread_id[:12]})"
                        )
                    try:
                        batch_result = _run_with_timeout(
                            partial(
                                run_compilation,
                                instruction=instruction,
                                model_name=batch_model,
                                raw_dir=raw_dir,
                                wiki_dir=wiki_dir,
                                recursion_limit=recursion_limit,
                                cache_stats=cache_cb,
                                tool_log=tool_cb,
                                run_name=f"compile:{batch_model}:{thread_id[:12] or 'no-thread'}",
                                trace_metadata={
                                    "compile_run_id": str(run_id),
                                    "compile_batch_index": batch_idx,
                                    "compile_email_count": len(batch),
                                    "compile_model": batch_model,
                                    "compile_thread_id": thread_id,
                                },
                                trace_tags=[
                                    "email-kb",
                                    "compile",
                                    f"model:{batch_model}",
                                    f"batch:{batch_idx}",
                                ],
                            ),
                            timeout_s=remaining_budget,
                        )
                        break
                    except Exception as exc:
                        if not _is_model_unavailable_error(exc):
                            raise
                        eligible = [m for m in pool if m != batch_model]
                        if not eligible:
                            raise
                        click.echo(
                            f"  model {batch_model} unavailable (LiteLLM 401/403/400/5xx) — "
                            f"dropping for this run; retrying batch with another"
                        )
                        _record_attempts_outcome(
                            attempts,
                            list(attempts.keys()),
                            outcome="failed",
                            error=str(exc)[:500],
                        )
                        _flush_tool_calls(run_id, tool_cb)
                        # 401/403 won't recover this run; subtract from the
                        # per-batch _healthy_pool re-filter too (initial_pool).
                        unauthorized.add(batch_model)
                        pool[:] = eligible
                        batch_model = random.choice(eligible)
                        cache_cb = BatchStatsCallback(model=batch_model)
                        tool_cb = ToolCallLogHandler()
                        attempts = _record_attempts_start(
                            batch, run_id=run_id, compile_model=batch_model
                        )
                        click.echo(f"  retry: model={batch_model}")
                # Post-batch normalization + validation. Catches agent-
                # introduced format drift (duplicate ## Related headings,
                # broken wikilinks, malformed frontmatter) right after the
                # batch commits, so the next batch sees a clean wiki. Both
                # helpers are best-effort: they log warnings but never roll
                # back a successful compile.
                #
                # Validator input is the full touched-page set (not just
                # formatter-normalized pages): pages the formatter skips as
                # malformed or leaves alone as already-clean must still be
                # validated, otherwise newly-introduced corruption slips
                # through silently.
                #
                # Order is load-bearing: the touch-catalog write + mark
                # step below depend on ``_sync_wiki_catalog`` having
                # upserted the ``wiki_pages`` rows they join against.
                # Running either earlier leaves ``message_touched_pages``
                # empty and every email stays ``pending``.
                touched_pages = _iter_touched_pages(batch_start, Path(wiki_dir))
                # `touched_content_pages` covers topics + systems +
                # policies + decisions + timelines + conflicts (wider
                # scope than `_iter_touched_pages`). Computed once and
                # reused for the References backfill below + the
                # touch-catalog write below `_mark_batch_compiled`.
                touched_content_pages = _iter_touched_content_pages(batch_start, Path(wiki_dir))
                normalized = _normalize_touched_pages(touched_pages, Path(wiki_dir))
                _mdlint_autofix_touched_pages(touched_pages)
                # Backfill ## References on every touched content page —
                # decisions in particular cite raw emails and aren't
                # covered by the narrower `touched_pages`.
                _backfill_references_on_touched_pages(touched_content_pages, Path(wiki_dir))
                catalog_synced = _sync_wiki_catalog(touched_pages, Path(wiki_dir))
                errors_by_page = _validate_touched_pages(touched_pages, Path(wiki_dir))
                # Refresh qmd's index so the next batch's resolve_page
                # sees pages this batch just wrote. Skip on empty
                # batches (trivial-skip / no-op runs) — nothing changed.
                if touched_pages:
                    _refresh_qmd_index()
                if errors_by_page:
                    logger.warning(
                        "batch touched pages have validator errors",
                        batch_index=batch_idx,
                        errors=[
                            {"page": str(p), "reasons": [e.reason for e in errs]}
                            for p, errs in errors_by_page.items()
                        ],
                    )
                # Catalog-truth write: one ``(message_id, page_id)`` row
                # per (batch message, touched content-type page). Must
                # run AFTER ``_sync_wiki_catalog`` (so the join target
                # has rows) and BEFORE ``_mark_batch_compiled`` (which
                # reads the catalog). ``attempts`` is keyed by
                # ``message_id`` so we reuse its keys instead of a
                # second ``find_by_raw_path`` pass over the batch. Reuses
                # ``touched_content_pages`` from above so we don't scan
                # the wiki twice per batch.
                touches_inserted = _write_touch_catalog(touched_content_pages, list(attempts))
                compiled_ids, skipped_ids, not_cited_paths, missing = _mark_batch_compiled(
                    batch,
                    Path(wiki_dir),
                    compile_model=batch_model,
                    run_id=run_id,
                )
                processed += len(compiled_ids)
                _record_attempts_outcome(attempts, compiled_ids, outcome="compiled")
                if skipped_ids:
                    _record_attempts_outcome(
                        attempts,
                        skipped_ids,
                        outcome="skipped",
                        error="insight:trivial_or_already_captured",
                    )
                # Terminal-decision guard fallback: when the guard fired
                # (nudge present in the batch result) AND the email is
                # still uncited, the agent refused to decide even after
                # the bounded retry budget. Flip those to ``skipped``
                # with a distinguished reason — preserves investigation
                # (``last_error`` grep-able) and prevents the claim loop
                # from re-queueing a model that already said "no". See
                # ``docs/audits/v12-50-compile-deep-audit-2026-04-23.md``
                # §7 Tier 2 #5 + §3 batch-45 finding.
                guard_skipped_ids: list[str] = []
                if not_cited_paths and _terminal_guard_exhausted(batch_result):
                    guard_skipped_ids = _mark_terminal_guard_exhausted_paths(not_cited_paths)
                    if guard_skipped_ids:
                        _record_attempts_outcome(
                            attempts,
                            guard_skipped_ids,
                            outcome="skipped",
                            error=TERMINAL_GUARD_EXHAUSTED_REASON,
                        )
                not_cited = len(not_cited_paths) - len(guard_skipped_ids)
                # Stamp the un-accounted-for attempts as failures so they
                # don't sit in-flight forever. ``not_cited`` (agent didn't
                # cite the email) and ``missing`` (backfill drift) both
                # count as model-level failures for that batch —
                # ``model_health_stats`` will see them.
                accounted_for = set(compiled_ids) | set(skipped_ids) | set(guard_skipped_ids)
                unfinished_ids = [mid for mid in attempts if mid not in accounted_for]
                if unfinished_ids:
                    _record_attempts_outcome(
                        attempts,
                        unfinished_ids,
                        outcome="failed",
                        error="not cited in wiki",
                    )
                # Drain reviewer-flagged merge candidates into the queue
                # (``wiki/merge_candidates.md``) so a human can apply them
                # via ``scripts/apply_merge_candidate.py``. Best-effort:
                # parse errors are swallowed, and an empty reviewer output
                # writes zero lines. Queue growth shows up in log_notes
                # so operators see the signal without opening the file.
                merge_pairs = _extract_merge_candidates(batch_result)
                merge_written = _append_merge_candidates(
                    merge_pairs,
                    wiki_dir,
                    trace_id=f"{run_id}:{batch_idx}",
                )
                suffix_parts = []
                if merge_written:
                    suffix_parts.append(f"merge_candidates+={merge_written}")
                if not_cited:
                    suffix_parts.append(f"{not_cited} not-yet-cited (kept pending)")
                if skipped_ids:
                    suffix_parts.append(f"{len(skipped_ids)} skipped (trivial/already-captured)")
                if guard_skipped_ids:
                    suffix_parts.append(
                        f"{len(guard_skipped_ids)} skipped (terminal-guard exhausted)"
                    )
                if missing:
                    suffix_parts.append(f"{missing} missing from catalog")
                suffix_parts.append(
                    f"normalized {len(normalized)} pages, "
                    f"{len(errors_by_page)} with validator errors, "
                    f"catalog_synced {catalog_synced}, "
                    f"touches_inserted {touches_inserted}"
                )
                cache = cache_cb.snapshot()
                served = ",".join(cache["served_models"]) or cache["requested_model"]
                suffix_parts.append(
                    f"model={served} cache={cache['cached_tokens']}/"
                    f"{cache['prompt_tokens']} ({cache['cache_pct']}%) "
                    f"writes={cache['cache_creation_tokens']} "
                    f"turns={cache['turns']} tools={cache['tool_calls']} "
                    f"tools/turn={cache['tools_per_turn']} "
                    f"total_tok={cache['total_tokens']}"
                )
                tool_suffix = _flush_tool_calls(run_id, tool_cb)
                if tool_suffix:
                    suffix_parts.append(tool_suffix)
                suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
                click.echo(f"Batch complete. Progress: {processed}/{total}{suffix}")
                log_outcome: BatchOutcome = "partial" if (not_cited or missing) else "compiled"
                log_notes_parts = list(suffix_parts)
                insights_tail = _insights_suffix(run_id, since_id=insights_cursor)
                if insights_tail:
                    log_notes_parts.append(insights_tail)
                log_notes = "; ".join(log_notes_parts) if log_notes_parts else ""
                _append_batch_log(batch_idx, batch, log_outcome, wiki_dir, notes=log_notes)
            except KeyboardInterrupt:
                # Ctrl+C: flush any in-flight tool records before letting the
                # outer handler mark the run 'killed'. Without this, the
                # Postgres compile_tool_calls table loses every tool call
                # since the last batch boundary. Same for the in-flight
                # compile_attempts rows — best-effort stamp so we don't
                # carry a bunch of orphaned NULL-outcome rows forward.
                _flush_tool_calls(run_id, tool_cb)
                _record_attempts_outcome(
                    attempts,
                    list(attempts.keys()),
                    outcome="failed",
                    error="KeyboardInterrupt",
                )
                raise
            except Exception as e:  # noqa: BLE001
                # concurrent.futures.TimeoutError has an empty str(),
                # so fall back to a synthesized message that names the
                # timeout budget — otherwise wiki/log.md gets a blank
                # notes column and the run looks silently broken.
                if isinstance(e, concurrent.futures.TimeoutError):
                    err_msg = (
                        f"TimeoutError: batch exceeded {batch_timeout}s (thread={thread_id[:12]})"
                    )
                    attempt_outcome = "timeout"
                else:
                    # str(e) is empty for some zero-message exception types;
                    # repr(e) keeps the type name so the log row stays useful.
                    err_msg = str(e) or repr(e)
                    attempt_outcome = "failed"
                logger.error("batch compilation failed", batch_index=batch_idx, error=err_msg)
                # Flush tool-call records BEFORE _mark_batch_failed so a secondary
                # DB failure on the mark step doesn't swallow the primary telemetry.
                # In-flight records get `status='abandoned'` via flush_all.
                fail_notes = err_msg[:200]
                tool_suffix = _flush_tool_calls(run_id, tool_cb)
                if tool_suffix:
                    fail_notes = f"{fail_notes}; {tool_suffix}"
                failed_marked = _mark_batch_failed(batch, err_msg, compile_model=batch_model)
                failed += failed_marked
                _record_attempts_outcome(
                    attempts,
                    list(attempts.keys()),
                    outcome=attempt_outcome,
                    error=err_msg[:500],
                )
                click.echo(f"ERROR in batch ({failed_marked} marked failed): {err_msg}")
                click.echo("Continuing with next batch...")
                _append_batch_log(batch_idx, batch, "failed", wiki_dir, notes=fail_notes)
        run_status = "completed"
    except KeyboardInterrupt:
        run_status = "killed"
        click.echo("\nInterrupted — marking run as killed.")
        raise
    finally:
        # Cost delta in int cents. Skip if either budget fetch failed (e.g.
        # LiteLLM proxy down) — cost_cents stays NULL in that case.
        budget_after = fetch_budget()
        cost_cents: int | None = None
        if budget_before is not None and budget_after is not None:
            cost_cents = round((budget_after.spend - budget_before.spend) * 100)
        finish_run(
            run_id,
            status=run_status,
            emails_processed=processed,
            emails_failed=failed,
            cost_cents=cost_cents,
        )
        click.echo(
            f"Recorded compile run {run_id}: status={run_status} "
            f"processed={processed} failed={failed} cost_cents={cost_cents}"
        )

    # Stamp every wiki page touched during this run before regenerating the
    # index. The agent has a `stamp_page_compiled_at` tool but routinely
    # forgets pages; `update_wiki_index`'s fallback only stamps pages whose
    # `last_compiled` is missing entirely, so re-edits of older pages slip
    # through with stale timestamps. Coordinator owns this now.
    click.echo("\nStamping recently modified wiki pages...")
    stamped, skipped = _stamp_recently_modified_pages(wiki_dir, run_start, resolved_model)
    click.echo(
        f"Stamped {stamped} pages with last_compiled"
        + (f" ({skipped} skipped — corrupt frontmatter)" if skipped else "")
    )

    # Regenerate index once after all batches complete — authoritative, not stale
    click.echo("\nRegenerating wiki index (post-compile)...")
    click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
    _regenerate_landing_surfaces(wiki_dir)

    # Stamp + catalog the landing surfaces we just regenerated. Must run
    # AFTER `_regenerate_landing_surfaces` because the generators rewrite
    # those files without `last_compiled` — stamping earlier would be wiped.
    landing_stamped, landing_synced = _sync_and_stamp_landing_surfaces(wiki_dir, resolved_model)
    if landing_stamped or landing_synced:
        click.echo(
            f"Landing surfaces: stamped {landing_stamped}, "
            f"catalog synced {landing_synced} (home/glossary/changes + domains + decisions)"
        )

    # Run-end reindex: per-batch hook misses landing surfaces written here.
    _refresh_qmd_index()

    # Push per-trace Langfuse Scores for the headline north-star metrics
    # so they show up in dashboards without re-running trace_scorecard.py.
    # Best-effort: any Langfuse failure logs a warning and the compile
    # finishes normally — observability never blocks the writer.
    _emit_langfuse_scores_for_run(run_id)

    # Run validator and warn (but don't fail) if integrity is broken. Pre-compile
    # snapshot is already captured above for rollback.
    click.echo("\nValidating wiki integrity...")
    import subprocess

    result = subprocess.run(
        ["uv", "run", "python", "scripts/validate_wiki.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    click.echo(result.stdout)
    if result.returncode != 0:
        click.echo(result.stderr)
        click.echo(
            "\n⚠ Validation failed. Pre-compile snapshot is saved. "
            "Restore with: uv run python scripts/snapshot_wiki.py restore <label>"
        )

    # Reuse the post-run budget snapshot captured in `finally:` above.
    if budget_after:
        click.echo(f"Budget (post-run): {budget_after}")
        if budget_before:
            delta = budget_after.spend - budget_before.spend
            click.echo(f"This run cost: ${delta:.4f}")

    click.echo(f"\nDone. Processed {processed}/{total} emails.")

    # Post-run metrics dipstick — does the prompt revamp actually land?
    # Only emit on a clean run (failed runs would muddy the comparison
    # window). Best-effort: any langfuse / DB hiccup is logged and the
    # report ships with whatever metrics succeeded.
    if run_status == "completed":
        try:
            from scripts.post_run_metrics import emit_for_run

            metrics_path = emit_for_run(run_id)
            if metrics_path is not None:
                # `is_relative_to` survives test setups that monkeypatch
                # REPO_ROOT to a tmp dir while the dipstick still writes
                # under the real repo audits dir.
                pretty = (
                    metrics_path.relative_to(REPO_ROOT)
                    if metrics_path.is_relative_to(REPO_ROOT)
                    else metrics_path
                )
                click.echo(f"\nPost-run metrics: {pretty}")
        except ImportError as exc:
            logger.warning("post_run_metrics_import_failed", error=str(exc))

    # Optional: publish the freshly-compiled wiki to Cloud Run. Only fires
    # on a clean 'completed' run. KeyboardInterrupt re-raises through the
    # `finally:` above so deploy never runs on Ctrl+C. Uncaught exceptions
    # that escape the loop likewise bubble past this block. The elif below
    # is defensive — reached only if a future refactor surfaces a non-
    # 'completed' run_status into this code path.
    if (deploy or deploy_force) and run_status == "completed":
        import subprocess

        target = "publish-force" if deploy_force else "publish"
        click.echo(f"\n=== Deploy: running `make {target}` ===")
        try:
            result = subprocess.run(
                ["make", target],
                cwd=REPO_ROOT,
                check=False,  # we want to handle failure, not raise
            )
            if result.returncode == 0:
                click.echo("Deploy succeeded.")
            else:
                click.echo(
                    f"Deploy failed (make {target} exited {result.returncode}). "
                    "Wiki is NOT updated on Cloud Run."
                )
                sys.exit(result.returncode)
        except FileNotFoundError:
            # Operator explicitly asked to deploy but the toolchain is missing.
            # Exit non-zero so CI/operators notice instead of silently leaving
            # Cloud Run stale.
            logger.error("deploy_toolchain_missing", target=target)
            click.echo(
                f"Deploy FAILED: `make` not found on PATH (cannot run `make {target}`). "
                "Install `make` or run the deploy step manually. "
                "Wiki is NOT updated on Cloud Run.",
                err=True,
            )
            sys.exit(1)
    elif (deploy or deploy_force) and run_status != "completed":
        click.echo(
            f"\nDeploy skipped — run_status={run_status!r} (deploy runs only "
            "after a clean 'completed' run)."
        )


if __name__ == "__main__":
    main()
