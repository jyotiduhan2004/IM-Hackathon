"""Reconcile messages.compile_state against real wiki provenance.

Problem it solves: earlier compile runs trusted the LLM to call
`mark_as_compiled` at the end of processing each raw email. The LLM
silently forgot on ~68% of emails (28/30 reported processed, only 9
actually transitioned to `compile_state='compiled'` in Postgres on
run 20260413T092936Z). Those emails had their wiki content written
but the queue never flipped.

This script forensically reconciles state. For every pending message,
if ANY wiki page's frontmatter `sources:` list contains the message's
`raw_path`, we treat the message as actually compiled and flip it.
The wiki is the durable evidence that the work was done; the `sources`
list is the canonical provenance surface.

Safe to re-run. Idempotent. Prints what it would change under
`--dry-run`.

Usage:
    uv run python scripts/reconcile_compile_state.py --dry-run
    uv run python scripts/reconcile_compile_state.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")


def _collect_cited_raw_paths(wiki_dir: Path) -> set[str]:
    """Scan every wiki page's `sources:` list and collect raw paths cited.

    Returns a set of `raw_path` strings — whatever the frontmatter lists,
    normalized only by strip().
    """
    cited: set[str] = set()
    for cat in CATEGORIES:
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


def _pending_messages() -> list[dict[str, str]]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT message_id, raw_path
              FROM messages
             WHERE compile_state IN ('pending', 'failed')
            """
        ).fetchall()


def _mark_compiled(message_ids: list[str]) -> int:
    if not message_ids:
        return 0
    with connect() as conn, conn.transaction():
        cur = conn.execute(
            """
            UPDATE messages
               SET compile_state = 'compiled',
                   compiled_at = now(),
                   last_error = NULL
             WHERE message_id = ANY(%s)
               AND compile_state IN ('pending', 'failed')
            """,
            (message_ids,),
        )
        return cur.rowcount or 0


@click.command()
@click.option("--dry-run", is_flag=True, help="Preview only; no DB writes.")
def main(dry_run: bool) -> None:
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: {wiki_dir} not found", err=True)
        sys.exit(2)

    cited = _collect_cited_raw_paths(wiki_dir)
    click.echo(f"Wiki cites {len(cited)} distinct raw paths.")

    pending = _pending_messages()
    click.echo(f"DB has {len(pending)} pending/failed messages.")

    to_flip = [m for m in pending if m["raw_path"] in cited]
    click.echo(f"Reconcile candidates (pending BUT cited in wiki): {len(to_flip)}")

    if not to_flip:
        click.echo("Nothing to reconcile.")
        return

    if dry_run:
        click.echo("\nFirst 10 candidates (dry-run — DB unchanged):")
        for m in to_flip[:10]:
            click.echo(f"  {m['raw_path']}  ({m['message_id'][:30]}...)")
        click.echo(f"\nWould flip {len(to_flip)} messages. Re-run without --dry-run.")
        return

    flipped = _mark_compiled([m["message_id"] for m in to_flip])
    click.echo(f"Flipped {flipped} messages to compiled.")


if __name__ == "__main__":
    main()
