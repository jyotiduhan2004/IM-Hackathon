"""Compile all unprocessed raw emails into wiki pages.

Usage:
    uv run python scripts/compile_all.py
    uv run python scripts/compile_all.py --dry-run
    uv run python scripts/compile_all.py --batch-size 10
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Literal

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.budget import fetch_budget  # noqa: E402
from src.compile.compiler import list_uncompiled_emails  # noqa: E402
from src.compile.compiler import run_compilation  # noqa: E402
from src.compile.compiler import update_wiki_index  # noqa: E402
from src.config import settings  # noqa: E402
from src.db.messages import fail_message_compile  # noqa: E402
from src.db.messages import find_by_raw_path  # noqa: E402
from src.db.messages import finish_message_compile  # noqa: E402
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
                md_file.write_text(
                    render_with_frontmatter(fm, body), encoding="utf-8"
                )
                stamped += 1
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(
                    "stamp skip", path=str(md_file), error=str(exc)
                )
                skipped += 1
    return stamped, skipped


_LOG_HEADER = (
    "# Compilation Log\n\n"
    "| Timestamp | Batch | N Emails | Thread ID | Outcome | Notes |\n"
    "|---|---|---|---|---|---|\n"
)


BatchOutcome = Literal["compiled", "failed", "partial"]


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

    row = (
        f"| {timestamp} | {batch_idx} | {n_emails} | {thread_id} | "
        f"{outcome} | {safe_notes} |\n"
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(row)


def _batch_paths(batch: list) -> list[str]:
    """Extract raw_path strings from a batch list (dicts or bare strings)."""
    return [
        item["path"] if isinstance(item, dict) else str(item)
        for item in batch
    ]


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


def _mark_batch_compiled(batch: list, wiki_dir: Path) -> tuple[int, int, int]:
    """Mark only batch emails whose raw_path is actually cited in the wiki.

    Returns (marked, not_cited, missing).
      - marked:   emails cited in >=1 wiki page's sources — flipped to compiled.
      - not_cited: emails not referenced anywhere in wiki — agent likely
        didn't finish them before returning; left pending for next claim.
      - missing:  emails whose raw_path has no `messages` row at all —
        indicates backfill drift; logged as a warning.

    The wiki-citation check is the safety net against the "agent said done
    but actually stopped early" failure mode. A naïve "flip every batch
    email on return" would corrupt state for emails the agent never
    touched.
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
        finish_message_compile(row["message_id"])
        marked += 1
    return marked, not_cited, missing


def _mark_batch_failed(batch: list, error: str) -> int:
    """Mark every email in a crashed batch as failed. Returns marked count."""
    trimmed = error[:500]
    marked = 0
    for path in _batch_paths(batch):
        row = find_by_raw_path(path)
        if row is None:
            continue
        fail_message_compile(row["message_id"], trimmed)
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
    "--recursion-limit",
    type=int,
    default=150,
    help=(
        "Max agent steps per batch. Lower it (e.g. 60) to fail fast on "
        "pathological threads instead of burning budget up to 150 steps."
    ),
)
def main(
    batch_size: int,
    limit: int | None,
    dry_run: bool,
    model: str | None,
    recursion_limit: int,
) -> None:
    """Compile uncompiled raw emails into wiki pages using Deep Agents."""
    import time

    # Capture the run start before any wiki work so we can stamp every page
    # whose mtime advances during the batch loop. See
    # `_stamp_recently_modified_pages` for the why.
    run_start = time.time()
    raw_dir = str(settings.raw_dir)
    wiki_dir = str(settings.wiki_dir)
    resolved_model = model or settings.llm_model

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

    processed = 0
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

        click.echo(
            f"\n=== Batch {batch_idx}/{len(groups)} "
            f"({len(batch)} emails, thread={thread_id[:12]}, earliest={earliest}) ==="
        )
        try:
            run_compilation(
                instruction=instruction,
                model_name=model,
                raw_dir=raw_dir,
                wiki_dir=wiki_dir,
                recursion_limit=recursion_limit,
            )
            marked, not_cited, missing = _mark_batch_compiled(batch, Path(wiki_dir))
            processed += marked
            suffix_parts = []
            if not_cited:
                suffix_parts.append(
                    f"{not_cited} not-yet-cited (kept pending)"
                )
            if missing:
                suffix_parts.append(f"{missing} missing from catalog")
            suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
            click.echo(f"Batch complete. Progress: {processed}/{total}{suffix}")
            log_outcome: BatchOutcome = "partial" if (not_cited or missing) else "compiled"
            log_notes = "; ".join(suffix_parts) if suffix_parts else ""
            _append_batch_log(batch_idx, batch, log_outcome, wiki_dir, notes=log_notes)
        except Exception as e:  # noqa: BLE001
            logger.error("batch compilation failed", batch_index=batch_idx, error=str(e))
            failed_marked = _mark_batch_failed(batch, str(e))
            click.echo(f"ERROR in batch ({failed_marked} marked failed): {e}")
            click.echo("Continuing with next batch...")
            _append_batch_log(batch_idx, batch, "failed", wiki_dir, notes=str(e)[:200])

    # Stamp every wiki page touched during this run before regenerating the
    # index. The agent has a `stamp_page_compiled_at` tool but routinely
    # forgets pages; `update_wiki_index`'s fallback only stamps pages whose
    # `last_compiled` is missing entirely, so re-edits of older pages slip
    # through with stale timestamps. Coordinator owns this now.
    click.echo("\nStamping recently modified wiki pages...")
    stamped, skipped = _stamp_recently_modified_pages(
        wiki_dir, run_start, resolved_model
    )
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

    budget_after = fetch_budget()
    if budget_after:
        click.echo(f"Budget (post-run): {budget_after}")
        if budget_before:
            delta = budget_after.spend - budget_before.spend
            click.echo(f"This run cost: ${delta:.4f}")

    click.echo(f"\nDone. Processed {processed}/{total} emails.")


if __name__ == "__main__":
    main()
