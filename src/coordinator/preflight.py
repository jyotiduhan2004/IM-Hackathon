"""Pre-flight + dry-run helpers for the compile coordinator.

These run BEFORE any LLM or DB work so a misconfigured invocation costs
nothing. ``_run_with_timeout`` is the per-batch wall-clock guard used by
the main loop.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from pathlib import Path

import click

from src.coordinator.grouping import _group_by_thread


def _preflight_mount_sanity(raw_dir: Path, wiki_dir: Path) -> int:
    """Verify raw_dir + wiki_dir look like a populated corpus before compiling.

    Returns the raw ``.md`` file count so the caller can log it. Aborts via
    ``click.ClickException`` on:

    - ``raw_dir`` missing on disk
    - ``raw_dir`` has zero ``.md`` files (the 2026-04-16 failure mode where a
      codex worktree pointed at an empty mount with only ``.gitkeep`` +
      attachments)
    - ``wiki_dir`` missing or lacking a ``topics/`` subdir (not a real wiki)

    Runs BEFORE any LLM or DB work so a bad mount costs nothing. The
    ``attachments/`` subtree is automatically excluded because ``glob("*.md")``
    only matches top-level ``raw_dir`` and attachments are always binaries.
    """
    if not raw_dir.exists():
        raise click.ClickException(f"raw_dir={raw_dir} does not exist; check cwd and --raw-dir")
    md_count = sum(1 for _ in raw_dir.glob("*.md"))
    if md_count == 0:
        raise click.ClickException(f"raw_dir={raw_dir} has 0 .md files; check cwd and --raw-dir")
    if not wiki_dir.exists():
        raise click.ClickException(f"wiki_dir={wiki_dir} does not exist; check cwd and --wiki-dir")
    if not (wiki_dir / "topics").exists():
        raise click.ClickException(
            f"wiki_dir={wiki_dir} has no topics/ subdir; is it a real wiki tree?"
        )
    return md_count


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


def _preview_dry_run(uncompiled: list[dict[str, str]], batch_size: int) -> None:
    """Log what the batch loop WOULD do, without invoking the agent."""
    total = len(uncompiled)
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
