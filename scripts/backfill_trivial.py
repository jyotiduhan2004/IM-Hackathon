"""Scan pending messages, skip the trivial ones.

For every row with ``compile_state='pending'`` the script loads the
raw markdown, runs ``src.ingest.filter_trivial.classify``, and flips
the row to ``skipped`` if the verdict says trivial. Idempotent —
already-skipped rows never show up in the pending scan, so re-running
the script is safe.

Usage:
    uv run python scripts/backfill_trivial.py --limit 50
    uv run python scripts/backfill_trivial.py --limit 5000

One-shot lifecycle:
- Classification: one-shot-done
- Last production run: 2026-04-18
- Safe to delete after: 2026-05-15
- Deletion gate: `migrate_legacy_pages.py` reports zero stragglers for 7 consecutive days.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402
from src.db.messages import mark_skipped  # noqa: E402
from src.ingest.filter_trivial import classify  # noqa: E402
from src.utils import extract_body  # noqa: E402

logger = structlog.get_logger(__name__)


def _load_body(raw_path: Path) -> str | None:
    """Return the body text from a raw markdown file, or None if unreadable."""
    try:
        content = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("raw-read-failed", path=str(raw_path), error=str(exc))
        return None
    return extract_body(content)


@click.command()
@click.option("--limit", default=1000, show_default=True, help="Max pending rows to scan.")
@click.option(
    "--repo-root",
    default=None,
    help="Root to resolve DB `raw_path` values against. Defaults to this script's "
    "repo root; override in a worktree where the main checkout holds raw/.",
)
def main(limit: int, repo_root: str | None) -> None:
    root = Path(repo_root).resolve() if repo_root else REPO_ROOT

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT message_id, raw_path, subject, from_address
              FROM messages
             WHERE compile_state = 'pending'
             ORDER BY date ASC NULLS LAST, message_id ASC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()

    scanned = 0
    skipped = 0
    kept = 0
    for row in rows:
        scanned += 1
        body = _load_body(root / row["raw_path"])
        if body is None:
            # Leave it pending; the compile loop will surface the real
            # error when it picks the message up.
            kept += 1
            continue

        verdict = classify(row["subject"] or "", body, row["from_address"] or "")
        logger.info(
            "trivial-verdict",
            message_id=row["message_id"],
            is_trivial=verdict.is_trivial,
            reason=verdict.reason,
        )
        if verdict.is_trivial:
            mark_skipped(row["message_id"], verdict.reason)
            skipped += 1
        else:
            kept += 1

    click.echo(f"scanned: {scanned}")
    click.echo(f"skipped: {skipped}")
    click.echo(f"kept:    {kept}")


if __name__ == "__main__":
    main()
