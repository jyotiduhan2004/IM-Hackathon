"""Compile all unprocessed raw emails into wiki pages.

Usage:
    uv run python scripts/compile_all.py
    uv run python scripts/compile_all.py --dry-run
    uv run python scripts/compile_all.py --batch-size 10
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.budget import fetch_budget  # noqa: E402
from src.compile.compiler import (  # noqa: E402
    list_uncompiled_emails,
    run_compilation,
    update_wiki_index,
)
from src.config import settings  # noqa: E402

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
def main(batch_size: int, limit: int | None, dry_run: bool, model: str | None) -> None:
    """Compile uncompiled raw emails into wiki pages using Deep Agents."""
    raw_dir = str(settings.raw_dir)
    wiki_dir = str(settings.wiki_dir)

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
        from datetime import UTC, datetime
        label = f"pre-compile-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        snapshot_path = REPO_ROOT / ".snapshots" / label
        if (REPO_ROOT / wiki_dir).exists():
            import shutil
            snapshot_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(REPO_ROOT / wiki_dir, snapshot_path / "wiki")
            click.echo(f"Pre-compile snapshot: .snapshots/{label}/wiki")

    if dry_run:
        for email in uncompiled[:30]:
            path = email["path"] if isinstance(email, dict) else email
            click.echo(f"  {path}")
        if total > 30:
            click.echo(f"  ... and {total - 30} more")
        click.echo("\nDry run complete.")
        return

    click.echo(f"Compiling in batches of {batch_size}...")
    click.echo(f"Model: {model or settings.llm_model}")
    click.echo(f"Wiki dir: {wiki_dir}")
    budget_before = fetch_budget()
    if budget_before:
        click.echo(f"Budget (pre-run): {budget_before}")
    click.echo()

    processed = 0
    for i in range(0, total, batch_size):
        batch = uncompiled[i : i + batch_size]
        # batch items may be dicts (new schema) or strings (legacy); handle both
        batch_paths = [b["path"] if isinstance(b, dict) else b for b in batch]
        batch_files = "\n".join(f"- {p}" for p in batch_paths)
        instruction = (
            f"Compile the following {len(batch)} uncompiled raw emails into wiki pages. "
            f"Process them chronologically and create/update wiki pages as needed. "
            f"Mark each email as compiled with `mark_as_compiled` when done.\n\n"
            f"Files to compile:\n{batch_files}"
        )

        click.echo(f"\n=== Batch {i // batch_size + 1} ({len(batch)} emails) ===")
        try:
            run_compilation(
                instruction=instruction,
                model_name=model,
                raw_dir=raw_dir,
                wiki_dir=wiki_dir,
            )
            processed += len(batch)
            click.echo(f"Batch complete. Progress: {processed}/{total}")
        except Exception as e:  # noqa: BLE001
            logger.error("batch compilation failed", batch_index=i, error=str(e))
            click.echo(f"ERROR in batch: {e}")
            click.echo("Continuing with next batch...")

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
            f"\n⚠ Validation failed. Pre-compile snapshot is saved. "
            f"Restore with: uv run python scripts/snapshot_wiki.py restore <label>"
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
