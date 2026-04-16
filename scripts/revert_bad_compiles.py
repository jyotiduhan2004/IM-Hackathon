"""Revert ``compile_state='compiled'`` → ``'pending'`` for messages without
real content-page evidence.

Problem: earlier compile runs flipped ``compile_state='compiled'`` for emails
that either

- **ghost**: no raw file on disk (deleted/moved) or no wiki page cites the
  ``raw/...md`` path anywhere, or
- **filing-cabinet**: the raw_path is cited only in entity pages — the LLM
  name-dropped the email in someone's entity ``sources:`` list but never
  wrote topic/system/policy content about what the email said.

Both classes violate the "coordinators verify, LLMs propose" rule. They are
reverted to ``pending`` so the compile queue re-claims them and the LLM gets
another shot under the post-Tier-A prompt + F3 preflight guards.

The mirror of ``reconcile_compile_state.py`` (which flips pending→compiled
when evidence exists). Share the same content-category source scan.

Usage::

    uv run python scripts/revert_bad_compiles.py --dry-run
    uv run python scripts/revert_bad_compiles.py --commit
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

CONTENT_CATEGORIES = ("topics", "systems", "policies", "timelines", "conflicts")


def _collect_content_cited(wiki_dir: Path) -> set[str]:
    cited: set[str] = set()
    for cat in CONTENT_CATEGORIES:
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


def _compiled_messages() -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT message_id, raw_path, compiled_at
              FROM messages
             WHERE compile_state = 'compiled'
            """
        ).fetchall()


def _revert(message_ids: list[str]) -> int:
    if not message_ids:
        return 0
    with connect() as conn:
        conn.execute(
            """
            UPDATE messages
               SET compile_state = 'pending',
                   compiled_at = NULL
             WHERE message_id = ANY(%s)
            """,
            (message_ids,),
        )
        conn.commit()
    return len(message_ids)


@click.command()
@click.option("--dry-run/--commit", "dry_run", default=True, show_default=True)
def main(dry_run: bool) -> None:
    wiki_dir = Path(settings.wiki_dir).resolve()

    content_cited = _collect_content_cited(wiki_dir)
    rows = _compiled_messages()

    ghost: list[tuple[str, str]] = []
    filing_cabinet: list[tuple[str, str]] = []

    for row in rows:
        raw_path = row["raw_path"]
        full_path = (REPO_ROOT / raw_path) if raw_path else None
        file_missing = not (full_path and full_path.exists())
        cited_in_content = raw_path in content_cited

        if file_missing or not cited_in_content:
            if file_missing:
                ghost.append((row["message_id"], raw_path))
            else:
                filing_cabinet.append((row["message_id"], raw_path))

    total = len(ghost) + len(filing_cabinet)

    click.echo(
        f"Scanned {len(rows)} compiled messages against {len(content_cited)} content-page citations"
    )
    click.echo(f"  ghost (file missing or uncited anywhere): {len(ghost)}")
    click.echo(f"  filing-cabinet (cited only in entities): {len(filing_cabinet)}")
    click.echo(f"  total revert candidates: {total}")

    if total == 0:
        click.echo("nothing to revert")
        return

    click.echo("\nGhost candidates (first 10):")
    for mid, rp in ghost[:10]:
        click.echo(f"  {mid}  {rp}")
    click.echo("\nFiling-cabinet candidates (first 10):")
    for mid, rp in filing_cabinet[:10]:
        click.echo(f"  {mid}  {rp}")

    if dry_run:
        click.echo("\n--dry-run — no DB changes. Re-run with --commit to apply.")
        return

    message_ids = [mid for mid, _ in ghost + filing_cabinet]
    flipped = _revert(message_ids)
    click.echo(f"\ncommitted: {flipped} messages reverted to pending")


if __name__ == "__main__":
    main()
