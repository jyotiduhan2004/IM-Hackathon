"""Parallel/async wiki compilation — faster batch processing for large backlogs.

Strategy:
- Run N batches concurrently via asyncio.gather
- Each batch creates its own Deep Agents instance (they're stateless per invocation
  beyond file I/O)
- Thread-awareness: group emails by thread_id so one agent sees a full thread
  and doesn't mid-thread-race another agent

Race considerations:
- Shared entity/system pages ARE a risk when batches write concurrently
- We accept eventual consistency: the LAST write wins for shared pages
- lint --fix + normalize_wikilinks after all batches normalizes any drift
- Git history preserves each write for debugging

Usage:
    uv run python scripts/compile_parallel.py                 # auto-detect concurrency
    uv run python scripts/compile_parallel.py --concurrency 4 # 4 batches at a time
    uv run python scripts/compile_parallel.py --batch-size 3 --concurrency 4
    uv run python scripts/compile_parallel.py --dry-run

NOTE: Experimental. Start with --dry-run and small --concurrency values to
measure actual speedup and file-conflict behavior on your model/proxy.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.compiler import list_uncompiled_emails  # noqa: E402
from src.compile.compiler import run_compilation  # noqa: E402
from src.compile.compiler import update_wiki_index  # noqa: E402
from src.config import settings  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)


def _group_by_thread(
    emails: list[dict[str, str]], max_per_group: int
) -> list[list[dict[str, str]]]:
    """Group emails by thread_id, keeping groups under max_per_group size.

    Threads larger than max_per_group are split into sub-groups.
    Emails without a thread_id each go in their own group.
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
    for _tid, members in by_thread.items():
        members.sort(key=lambda e: e.get("date", ""))
        for i in range(0, len(members), max_per_group):
            groups.append(members[i : i + max_per_group])
    groups.extend(standalone)

    # Sort groups by earliest email date so we process older threads first
    def earliest_date(group: list[dict[str, str]]) -> str:
        return min((e.get("date", "") for e in group), default="")

    groups.sort(key=earliest_date)
    return groups


async def _run_batch_async(
    batch: list[dict[str, str]],
    batch_num: int,
    total_batches: int,
    model: str | None,
    raw_dir: str,
    wiki_dir: str,
    semaphore: asyncio.Semaphore,
) -> tuple[int, bool, str]:
    """Run one batch through the compiler agent, gated by a semaphore.

    Returns (batch_num, success, short_msg).
    """
    async with semaphore:
        paths = [e["path"] for e in batch]
        batch_files = "\n".join(f"- {p}" for p in paths)
        subject_hint = batch[0].get("subject", "")[:60]
        thread_id = batch[0].get("thread_id", "")
        instruction = (
            f"Compile the following {len(batch)} uncompiled raw emails "
            f"(thread_id={thread_id}, subject preview: {subject_hint!r}) "
            f"into wiki pages. Process chronologically. Mark each as compiled "
            f"when done.\n\nFiles:\n{batch_files}"
        )

        click.echo(f"[batch {batch_num}/{total_batches}] starting ({len(batch)} emails)")

        # Use run_compilation (not create_compiler + agent.invoke) so the
        # _current_raw_paths ContextVar gets set — otherwise create_entities
        # in this parallel path fails with "no raw_paths in batch context".
        # run_compilation does view-root lifecycle + callbacks too.
        try:
            result = await asyncio.to_thread(
                run_compilation,
                instruction,
                model_name=model,
                raw_dir=raw_dir,
                wiki_dir=wiki_dir,
            )
            last = result["messages"][-1]
            summary = str(getattr(last, "content", ""))[:150]
            click.echo(f"[batch {batch_num}/{total_batches}] done")
            return (batch_num, True, summary)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "parallel batch failed",
                batch_num=batch_num,
                thread_id=thread_id,
                error=str(e),
            )
            return (batch_num, False, str(e)[:200])


@click.command()
@click.option(
    "--batch-size",
    default=5,
    help="Max emails per batch (within one thread). Default 5.",
)
@click.option(
    "--concurrency",
    default=3,
    help="Max batches to run concurrently. Default 3. Higher = more parallelism but more file conflicts.",
)
@click.option(
    "--model",
    default=None,
    help="Override LLM model (default from .env LLM_MODEL)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the batching plan without running any LLM calls",
)
def main(batch_size: int, concurrency: int, model: str | None, dry_run: bool) -> None:
    """Compile uncompiled raw emails in parallel, thread-aware batches."""
    raw_dir = str(settings.raw_dir)
    wiki_dir = str(settings.wiki_dir)

    uncompiled = list_uncompiled_emails.invoke({"raw_dir": raw_dir})
    total = len(uncompiled)

    if total == 0:
        click.echo("Nothing to compile.")
        click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
        return

    groups = _group_by_thread(uncompiled, max_per_group=batch_size)
    click.echo(f"Total emails: {total}")
    click.echo(f"Batches: {len(groups)} (grouped by thread, max {batch_size}/batch)")
    click.echo(f"Concurrency: {concurrency}")
    click.echo(f"Model: {model or settings.llm_model}")
    click.echo()

    # Auto-snapshot before compiling (parity with compile_all.py)
    if not dry_run:
        import shutil
        from datetime import UTC
        from datetime import datetime

        label = f"pre-parallel-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        snapshot_path = REPO_ROOT / ".snapshots" / label
        if (REPO_ROOT / wiki_dir).exists():
            snapshot_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(REPO_ROOT / wiki_dir, snapshot_path / "wiki")
            click.echo(f"Pre-parallel snapshot: .snapshots/{label}/wiki")

    # Budget snapshot (parity with compile_all.py)
    try:
        from src.budget import fetch_budget

        budget_before = fetch_budget()
        if budget_before:
            click.echo(f"Budget (pre-run): {budget_before}")
    except ImportError:
        budget_before = None

    if dry_run:
        for i, g in enumerate(groups[:20], 1):
            tid = g[0].get("thread_id", "")[:12]
            subj = g[0].get("subject", "")[:50]
            click.echo(f"  Batch {i}: thread={tid} size={len(g)} subj={subj!r}")
        if len(groups) > 20:
            click.echo(f"  ... and {len(groups) - 20} more batches")
        return

    async def run_all() -> list[tuple[int, bool, str]]:
        sem = asyncio.Semaphore(concurrency)
        tasks = [
            _run_batch_async(
                batch=g,
                batch_num=i + 1,
                total_batches=len(groups),
                model=model,
                raw_dir=raw_dir,
                wiki_dir=wiki_dir,
                semaphore=sem,
            )
            for i, g in enumerate(groups)
        ]
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_all())

    succeeded = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - succeeded

    click.echo("\nRegenerating wiki index...")
    click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
    click.echo(f"\nDone. Batches: {succeeded} succeeded, {failed} failed.")


if __name__ == "__main__":
    main()
