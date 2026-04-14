"""Compile all unprocessed raw emails into wiki pages.

Usage:
    uv run python scripts/compile_all.py
    uv run python scripts/compile_all.py --dry-run
    uv run python scripts/compile_all.py --batch-size 10
"""

from __future__ import annotations

import concurrent.futures
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any
from typing import Literal
from uuid import UUID

import click
import psycopg
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.budget import fetch_budget  # noqa: E402
from src.compile.cache_stats import BatchStatsCallback  # noqa: E402
from src.compile.compiler import list_uncompiled_emails  # noqa: E402
from src.compile.compiler import run_compilation  # noqa: E402
from src.compile.compiler import update_wiki_index  # noqa: E402
from src.compile.tool_call_log import ToolCallLogHandler  # noqa: E402
from src.config import settings  # noqa: E402
from src.db.compile_runs import finish_run  # noqa: E402
from src.db.compile_runs import start_run  # noqa: E402
from src.db.insights import list_for_run as list_insights_for_run  # noqa: E402
from src.db.insights import max_id_for_run as _insights_max_id  # noqa: E402
from src.db.messages import fail_message_compile  # noqa: E402
from src.db.messages import find_by_raw_path  # noqa: E402
from src.db.messages import finish_message_compile  # noqa: E402
from src.db.tool_call_log import fallback_to_jsonl as tool_log_fallback_to_jsonl  # noqa: E402
from src.db.tool_call_log import insert_many as tool_log_insert_many  # noqa: E402
from src.db.tool_call_log import summarize as tool_log_summarize  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

_WIKI_CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)


def _stamp_recently_modified_pages(
    wiki_dir: str, since_timestamp: float, model_name: str
) -> tuple[int, int]:
    """Stamp `last_compiled`/`updated_by`/`update_count` on pages touched
    after `since_timestamp` (POSIX seconds).

    The LLM agent has a `stamp_page_compiled_at` tool but routinely forgets
    to call it on every page it touched. This coordinator-side pass walks
    the wiki after the batch loop and stamps every page whose mtime is
    newer than the run start time. `update_wiki_index` has a similar
    fallback for missing-stamp pages, but it only fires when `last_compiled`
    is absent — pages updated by the agent in this run already have a
    stale stamp from a previous compile, so the index pass skips them.

    Returns (stamped_count, skipped_count). `skipped` covers pages whose
    frontmatter looks corrupt (missing `title`/`page_type` after extraction)
    — same guard `update_wiki_index` uses to avoid clobbering mangled pages.
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return 0, 0

    now_iso = datetime.now(UTC).isoformat()
    stamped, skipped = 0, 0

    for category in _WIKI_CATEGORIES:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                if md_file.stat().st_mtime <= since_timestamp:
                    continue
                content = md_file.read_text(encoding="utf-8")
                fm = extract_frontmatter(content)
                # Mirror update_wiki_index's "looks like a real page" guard
                # — a page with neither title nor page_type is either an
                # orphan or got mangled by the agent's edit_file. Don't
                # overwrite it from the coordinator either.
                if not ("title" in fm or "page_type" in fm):
                    skipped += 1
                    continue
                fm["last_compiled"] = now_iso
                fm["updated_by"] = model_name
                fm["update_count"] = int(fm.get("update_count") or 0) + 1
                body = extract_body(content)
                md_file.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
                stamped += 1
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("stamp skip", path=str(md_file), error=str(exc))
                skipped += 1
    return stamped, skipped


def _run_with_timeout[T](fn: Callable[[], T], timeout_s: float | None) -> T:
    """Run ``fn()`` in a worker thread, raising
    ``concurrent.futures.TimeoutError`` after ``timeout_s`` seconds.
    ``timeout_s`` of ``0`` or ``None`` runs ``fn`` inline.

    Caveat — Python threads are cooperative. On timeout the worker is
    orphaned (``shutdown(wait=False)``) and may linger until process
    exit if it's wedged in C code or a blocking socket. Acceptable
    trade-off: the outer batch loop progresses instead of freezing the
    whole run. For the same reason we avoid ``with ThreadPoolExecutor``
    — its ``__exit__`` would block on ``shutdown(wait=True)``.
    """
    if timeout_s is None or timeout_s == 0:
        return fn()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    finally:
        # ``wait=False`` only; no ``cancel_futures=True``. The orphaned
        # worker keeps running regardless — Python can't forcibly stop
        # a thread, so a hung HTTP socket continues until the process
        # exits. This just avoids blocking ``shutdown()`` on it.
        pool.shutdown(wait=False)


_LOG_HEADER = (
    "# Compilation Log\n\n"
    "| Timestamp | Batch | N Emails | Thread ID | Outcome | Notes |\n"
    "|---|---|---|---|---|---|\n"
)


BatchOutcome = Literal["compiled", "failed", "partial"]


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


def _batch_paths(batch: list) -> list[str]:
    """Extract raw_path strings from a batch list (dicts or bare strings)."""
    return [item["path"] if isinstance(item, dict) else str(item) for item in batch]


def _collect_cited_raw_paths(wiki_dir: Path) -> set[str]:
    """Scan every wiki page's `sources:` list; return the set of cited raw paths.

    This is the durable evidence that the agent actually processed an
    email — the email's raw path will appear in at least one wiki page's
    frontmatter `sources:` list.
    """
    cited: set[str] = set()
    for cat in _WIKI_CATEGORIES:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for md in cat_dir.glob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = extract_frontmatter(content)
            sources = fm.get("sources")
            if not isinstance(sources, list):
                continue
            for src in sources:
                if isinstance(src, str) and src.strip():
                    cited.add(src.strip())
    return cited


def _mark_batch_compiled(
    batch: list, wiki_dir: Path, compile_model: str | None = None
) -> tuple[int, int, int]:
    """Mark only batch emails whose raw_path is actually cited in the wiki.

    Returns (marked, not_cited, missing).
      - marked:   emails cited in >=1 wiki page's sources — flipped to compiled.
      - not_cited: emails not referenced anywhere in wiki — agent likely
        didn't finish them before returning; left pending for next claim.
      - missing:  emails whose raw_path has no `messages` row at all —
        indicates backfill drift; logged as a warning.

    `compile_model` records which A/B-pool model produced this batch so
    we can later join model → outcome.

    The wiki-citation check is the safety net against the "agent said done
    but actually stopped early" failure mode.
    """
    cited = _collect_cited_raw_paths(wiki_dir)
    marked, not_cited, missing = 0, 0, 0
    for path in _batch_paths(batch):
        row = find_by_raw_path(path)
        if row is None:
            logger.warning("no messages row for batch path", path=path)
            missing += 1
            continue
        if path not in cited:
            logger.warning(
                "batch email not cited in wiki; leaving pending",
                path=path,
                message_id=row["message_id"],
            )
            not_cited += 1
            continue
        finish_message_compile(row["message_id"], compile_model=compile_model)
        marked += 1
    return marked, not_cited, missing


def _mark_batch_failed(batch: list, error: str, compile_model: str | None = None) -> int:
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


def _group_by_thread(
    emails: list[dict[str, str]], max_per_group: int
) -> list[list[dict[str, str]]]:
    """Group emails by thread_id, chronological within thread, threads ordered
    by earliest message date.

    Threads longer than `max_per_group` are split into sub-groups (still in
    order). Emails without a thread_id become singleton groups. The whole
    return list is sorted by each group's earliest date, so callers can
    process batches in chronological order across threads.
    """
    by_thread: dict[str, list[dict[str, str]]] = defaultdict(list)
    standalone: list[list[dict[str, str]]] = []

    for email in emails:
        tid = email.get("thread_id") or ""
        if tid:
            by_thread[tid].append(email)
        else:
            standalone.append([email])

    groups: list[list[dict[str, str]]] = []
    for members in by_thread.values():
        members.sort(key=lambda e: e.get("date", ""))
        # Split huge threads for safety; most will be one group
        for i in range(0, len(members), max_per_group):
            groups.append(members[i : i + max_per_group])
    groups.extend(standalone)

    # Threads processed in order of their earliest message — strict
    # chronological for supersession detection across topics.
    groups.sort(key=lambda g: min(e.get("date", "") for e in g) if g else "")
    return groups


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
    help="Max TOTAL emails to process this run (oldest-first). Default: all.",
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
    default=150,
    help=(
        "Max agent steps per batch. Lower it (e.g. 60) to fail fast on "
        "pathological threads instead of burning budget up to 150 steps."
    ),
)
@click.option(
    "--batch-timeout",
    type=click.IntRange(min=0),
    default=900,
    help=(
        "Per-batch wall-clock timeout in seconds (default 900 = 15 min, "
        "matching scripts/compile_overnight.sh). Pass 0 to disable. "
        "Guards interactive runs against a single hung batch "
        "(slow OTel export, stuck LLM provider, rare deadlock) freezing "
        "the whole compile loop."
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
) -> None:
    """Compile uncompiled raw emails into wiki pages using Deep Agents."""
    import random
    import time

    # Capture the run start before any wiki work so we can stamp every page
    # whose mtime advances during the batch loop. See
    # `_stamp_recently_modified_pages` for the why.
    run_start = time.time()
    raw_dir = str(settings.raw_dir)
    wiki_dir = str(settings.wiki_dir)
    resolved_model = model or settings.llm_model

    # Parse --model-pool. CLI flag overrides settings.model_pool. Empty
    # final list = no pool (use `resolved_model` for every batch); a list
    # = sample one at random per batch (sticky for the batch).
    pool: list[str]
    if model_pool is not None:
        pool = [m.strip() for m in model_pool.split(",") if m.strip()]
    else:
        pool = settings.model_pool if len(settings.model_pool) > 1 else []
    if pool:
        click.echo(f"Model pool: {pool} (random pick per batch)")

    # Use the tool directly for listing, not through the agent
    all_uncompiled = list_uncompiled_emails.invoke({"raw_dir": raw_dir})
    # list_uncompiled_emails already sorts by filename (= YYYY-MM-DD prefix),
    # so slicing gives us the oldest N emails — strict chronological order.
    uncompiled = all_uncompiled[:limit] if limit else all_uncompiled
    total = len(uncompiled)

    click.echo(f"Found {len(all_uncompiled)} uncompiled emails total.")
    if limit and limit < len(all_uncompiled):
        click.echo(f"Processing oldest {limit} this run (chronological).")
    if total == 0:
        click.echo("Nothing to compile.")
        # Still regenerate index in case wiki changed
        click.echo("Regenerating wiki index...")
        click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
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
        preview_groups = _group_by_thread(uncompiled, max_per_group=batch_size)
        click.echo(
            f"Thread-grouped into {len(preview_groups)} batches "
            f"(avg {total / max(len(preview_groups), 1):.1f} emails/batch)"
        )
        sizes = [len(g) for g in preview_groups]
        if sizes:
            click.echo(
                f"Group size distribution: min={min(sizes)} median={sorted(sizes)[len(sizes) // 2]} "
                f"max={max(sizes)} singletons={sizes.count(1)}"
            )
        click.echo("\nFirst 10 batches:")
        for i, g in enumerate(preview_groups[:10], 1):
            tid = g[0].get("thread_id", "")[:12]
            earliest = g[0].get("date", "")[:10]
            subj = g[0].get("subject", "")[:50]
            click.echo(f"  batch {i}: thread={tid} earliest={earliest} n={len(g)} subj={subj!r}")
        if len(preview_groups) > 10:
            click.echo(f"  ... and {len(preview_groups) - 10} more batches")
        click.echo("\nDry run complete.")
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
    # `src/compile/compiler.py::log_insight`) so every insight the agent
    # records can be joined back to this run.
    import os

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
                f"Compile the following {len(batch)} uncompiled raw emails from one "
                f"thread (thread_id={thread_id}, earliest={earliest}) into wiki pages. "
                f"Process them chronologically as a conversation. When multiple replies "
                f"build on the same decision/policy/feature, merge them into a single "
                f"coherent wiki page rather than one page per message. "
                f"Do NOT call any tool to mark these emails compiled — the coordinator "
                f"handles that deterministically after you return. Focus on writing the "
                f"right wiki content.\n\n"
                f"Files to compile:\n{batch_files}"
            )

            batch_model = random.choice(pool) if pool else resolved_model
            click.echo(
                f"\n=== Batch {batch_idx}/{len(groups)} "
                f"({len(batch)} emails, thread={thread_id[:12]}, "
                f"earliest={earliest}, model={batch_model}) ==="
            )
            cache_cb = BatchStatsCallback(model=batch_model)
<<<<<<< HEAD
            tool_cb = ToolCallLogHandler()
=======
            # Snapshot the insight-id cursor BEFORE the batch so we can show
            # only the insights logged during this batch in the digest.
            insights_cursor = _max_insight_id_safe(run_id)
>>>>>>> 7154b89 (fix(compile): address log_insight review — batch-scoped digest + uuid FK + real test)
            try:
                # ``concurrent.futures.TimeoutError`` is a subclass of
                # ``Exception``, so the outer ``except`` below handles it
                # via ``_mark_batch_failed`` + ``_append_batch_log``.
                _run_with_timeout(
                    partial(
                        run_compilation,
                        instruction=instruction,
                        model_name=batch_model,
                        raw_dir=raw_dir,
                        wiki_dir=wiki_dir,
                        recursion_limit=recursion_limit,
                        cache_stats=cache_cb,
                        tool_log=tool_cb,
                    ),
                    timeout_s=batch_timeout,
                )
                marked, not_cited, missing = _mark_batch_compiled(
                    batch, Path(wiki_dir), compile_model=batch_model
                )
                processed += marked
                suffix_parts = []
                if not_cited:
                    suffix_parts.append(f"{not_cited} not-yet-cited (kept pending)")
                if missing:
                    suffix_parts.append(f"{missing} missing from catalog")
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
                # since the last batch boundary.
                _flush_tool_calls(run_id, tool_cb)
                raise
            except Exception as e:  # noqa: BLE001
                # concurrent.futures.TimeoutError has an empty str(),
                # so fall back to a synthesized message that names the
                # timeout budget — otherwise wiki/log.md gets a blank
                # notes column and the run looks silently broken.
                if isinstance(e, concurrent.futures.TimeoutError):
                    err_msg = (
                        f"TimeoutError: batch exceeded {batch_timeout}s "
                        f"(thread={thread_id[:12]})"
                    )
                else:
                    # str(e) is empty for some zero-message exception types;
                    # repr(e) keeps the type name so the log row stays useful.
                    err_msg = str(e) or repr(e)
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


if __name__ == "__main__":
    main()
