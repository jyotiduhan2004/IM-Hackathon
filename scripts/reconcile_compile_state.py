"""Reconcile messages.compile_state against real wiki provenance.

Problem it solves: earlier compile runs trusted the LLM to call
`mark_as_compiled` at the end of processing each raw email. The LLM
silently forgot on ~68% of emails (28/30 reported processed, only 9
actually transitioned to `compile_state='compiled'` in Postgres on
run 20260413T092936Z). Those emails had their wiki content written
but the queue never flipped.

This script forensically reconciles state by checking wiki evidence.

**The citation-type trap (learned the hard way).** A first attempt
flipped every pending message whose `raw_path` appeared in ANY wiki
page's `sources:` list. That would have falsely marked 715 of 748
matches as compiled. Why? The LLM frequently name-drops a raw email
in an entity's `sources:` (because the email mentioned someone) but
never writes a topic page extracting the email's actual content.
Entity-only citation is "the person was tagged," NOT "the content was
extracted."

**Strict rule (default)**: flip only messages cited in a content-type
page — topic, system, policy, timeline, or conflict. Entity pages
don't count. That's the only signal that the agent actually wrote
something about what the email said.

Messages cited only in entity pages are LEFT PENDING on purpose — so
the next compile batch re-claims them and the LLM gets another shot
at writing a proper topic page. That's a self-healing feedback loop:
coordinator catches the partial-processing case, LLM gets to finish
the job.

Safe to re-run. Idempotent. Prints what it would change under
`--dry-run`.

Usage:
    uv run python scripts/reconcile_compile_state.py --dry-run
    uv run python scripts/reconcile_compile_state.py
    uv run python scripts/reconcile_compile_state.py --include-entity-only  # diagnostics
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

CONTENT_CATEGORIES = ("topics", "systems", "policies", "timelines", "conflicts")
ALL_CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")


def _collect_cited_raw_paths(wiki_dir: Path, categories: tuple[str, ...]) -> set[str]:
    """Scan the given category directories' `sources:` lists.

    Only pages in `categories` are scanned. Pass `CONTENT_CATEGORIES` for
    strict mode (ignores entity pages), `ALL_CATEGORIES` for diagnostic
    mode.
    """
    cited: set[str] = set()
    for cat in categories:
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
@click.option(
    "--include-entity-only",
    is_flag=True,
    help=(
        "Diagnostic: also flip messages cited only in entity pages. "
        "Default (strict) excludes these because entity-only citation is "
        "name-drop, not content extraction. Leaving them pending lets the "
        "next compile batch re-claim them — self-healing loop."
    ),
)
def main(dry_run: bool, include_entity_only: bool) -> None:
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: {wiki_dir} not found", err=True)
        sys.exit(2)

    categories = ALL_CATEGORIES if include_entity_only else CONTENT_CATEGORIES
    mode = "loose (entity pages count)" if include_entity_only else "strict (content pages only)"
    cited = _collect_cited_raw_paths(wiki_dir, categories)
    click.echo(f"Mode: {mode}")
    click.echo(f"Wiki cites {len(cited)} distinct raw paths.")

    pending = _pending_messages()
    click.echo(f"DB has {len(pending)} pending/failed messages.")

    to_flip = [m for m in pending if m["raw_path"] in cited]
    click.echo(f"Reconcile candidates: {len(to_flip)}")

    # Always report the entity-only leak count even in strict mode, so the
    # operator sees the self-healing backlog forming.
    if not include_entity_only:
        entity_cited = _collect_cited_raw_paths(wiki_dir, ("entities",))
        entity_only = (entity_cited - cited) & {m["raw_path"] for m in pending}
        click.echo(f"Entity-only-cited (left pending for re-compile): {len(entity_only)}")

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
