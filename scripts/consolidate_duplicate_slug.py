"""Consolidate a duplicate-slug pair in the wiki.

The deep 10-page audit (docs/audits/deep-audit-10-pages-2026-04-17.md)
found `systems/Lens.IndiaMART.md` (uppercase, 11 sources) and
`systems/lens-indiamart-com.md` (kebab, skeletal stub). Both represent
the same system. The agent's `resolve_page` fuzzy-match missed the
`.`-for-`-` variant, so a second stub was created on a later compile.

This script is the general-purpose fix: given two slugs that name the
same concept, keep one (the "winner") and retire the other ("loser"):

1. Rewrite the winner's file body — pull any rich prose from the
   loser and drop it on top of the winner's TL;DR. We don't try to
   merge prose blocks intelligently; the agent will re-compile on
   the next cycle if anything's missing.
2. Repoint `message_touched_pages` rows from loser.page_id →
   winner.page_id (`INSERT … ON CONFLICT DO NOTHING` so we don't
   double-count).
3. DELETE the loser's row from `wiki_pages`.
4. `git rm` the loser's .md file.
5. Rewrite `[[loser_slug]]` wikilinks across wiki/ to `[[winner_slug]]`.

`--dry-run` (default) prints a plan. `--commit` applies.

Usage:

    uv run python scripts/consolidate_duplicate_slug.py \
        --winner lens-indiamart-com \
        --loser Lens.IndiaMART \
        [--commit]

Reusable for other duplicates (topic dedup, etc.) — pass whatever
slug pair you want consolidated.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import psycopg

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402


@dataclass(frozen=True)
class PageRow:
    page_id: int
    slug: str
    path: Path


def _fetch_page(conn: psycopg.Connection, slug: str) -> PageRow:
    row = conn.execute(
        "SELECT page_id, slug, path FROM wiki_pages WHERE slug = %s",
        (slug,),
    ).fetchone()
    if row is None:
        raise click.ClickException(f"no wiki_pages row for slug={slug!r}")
    return PageRow(page_id=int(row["page_id"]), slug=row["slug"], path=Path(row["path"]))


def _merge_bodies(winner_text: str, loser_text: str) -> str:
    """Minimal merge: keep the winner's frontmatter; stitch loser's
    body under the winner's body. Doesn't try to dedupe H2s — leaves
    that for the next compile's reviewer pass."""

    def _split(text: str) -> tuple[str, str]:
        if not text.startswith("---\n"):
            return "", text
        end = text.find("\n---\n", 4)
        if end == -1:
            return "", text
        return text[: end + 5], text[end + 5 :]

    winner_fm, winner_body = _split(winner_text)
    _, loser_body = _split(loser_text)
    combined = winner_body.rstrip() + "\n\n" + loser_body.lstrip()
    return winner_fm + combined


def _count_touches(conn: psycopg.Connection, page_id: int) -> int:
    row = conn.execute(
        "SELECT count(*) c FROM message_touched_pages WHERE page_id = %s",
        (page_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def _find_wikilink_refs(wiki_dir: Path, slug: str) -> list[Path]:
    """Files containing `[[<slug>]]`. Case-exact — the loser's slug
    might have casing variants (Lens.IndiaMART) that stay distinct
    from the winner's lowercase slug."""
    needle = re.compile(r"\[\[" + re.escape(slug) + r"(?:\||\]\])")
    matched: list[Path] = []
    for md in wiki_dir.rglob("*.md"):
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if needle.search(content):
            matched.append(md)
    return matched


def _rewrite_wikilinks(path: Path, loser_slug: str, winner_slug: str) -> int:
    """Rewrite `[[loser]]` and `[[loser|alias]]` to the winner.
    Returns number of substitutions."""
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(r"\[\[" + re.escape(loser_slug) + r"(\||\]\])")
    new_content, n = pattern.subn(r"[[" + winner_slug + r"\1", content)
    if n:
        path.write_text(new_content, encoding="utf-8")
    return n


@click.command()
@click.option("--winner", required=True, help="Slug to keep (canonical, usually kebab-case).")
@click.option("--loser", required=True, help="Slug to retire.")
@click.option("--commit", is_flag=True, help="Apply changes. Default is --dry-run.")
@click.option("--wiki-dir", default=None, help="Override wiki/ path (tests). Default: <repo>/wiki.")
def main(winner: str, loser: str, commit: bool, wiki_dir: str | None) -> None:
    wiki_root = Path(wiki_dir) if wiki_dir else REPO_ROOT / "wiki"
    if winner == loser:
        raise click.ClickException("winner and loser must differ")

    with connect() as conn:
        win = _fetch_page(conn, winner)
        lose = _fetch_page(conn, loser)
        win_touches = _count_touches(conn, win.page_id)
        lose_touches = _count_touches(conn, lose.page_id)

        refs = _find_wikilink_refs(wiki_root, loser)

        click.echo(f"Winner : {win.slug} (page_id={win.page_id}, touches={win_touches})")
        click.echo(f"        path={win.path}")
        click.echo(f"Loser  : {lose.slug} (page_id={lose.page_id}, touches={lose_touches})")
        click.echo(f"        path={lose.path}")
        click.echo(f"Wikilink refs to rewrite: {len(refs)}")
        for r in refs:
            try:
                rel = r.relative_to(wiki_root)
            except ValueError:
                rel = r
            click.echo(f"  - {rel}")

        if not commit:
            click.echo("\n-- DRY RUN; re-run with --commit to apply --")
            return

        # 1. Merge bodies + write winner file
        winner_path = Path(win.path)
        loser_path = Path(lose.path)
        if not winner_path.is_file() or not loser_path.is_file():
            raise click.ClickException(
                f"file missing: winner_exists={winner_path.is_file()}, "
                f"loser_exists={loser_path.is_file()}"
            )
        merged = _merge_bodies(
            winner_path.read_text(encoding="utf-8"),
            loser_path.read_text(encoding="utf-8"),
        )
        winner_path.write_text(merged, encoding="utf-8")
        click.echo(f"✓ merged body → {winner_path}")

        # 2. Repoint message_touched_pages. Use INSERT … SELECT ON
        # CONFLICT DO NOTHING to avoid duplicate-key errors when both
        # pages already share a touch.
        conn.execute(
            """
            INSERT INTO message_touched_pages (message_id, page_id, compiled_at)
            SELECT message_id, %s, compiled_at
              FROM message_touched_pages
             WHERE page_id = %s
            ON CONFLICT (message_id, page_id) DO NOTHING
            """,
            (win.page_id, lose.page_id),
        )
        moved = conn.execute(
            "DELETE FROM message_touched_pages WHERE page_id = %s",
            (lose.page_id,),
        ).rowcount
        click.echo(f"✓ repointed {moved} touches {lose.page_id} → {win.page_id}")

        # 3. Delete loser's wiki_pages row
        conn.execute("DELETE FROM wiki_pages WHERE page_id = %s", (lose.page_id,))
        click.echo(f"✓ deleted wiki_pages row for {lose.slug}")

        # 4. Remove loser file
        loser_path.unlink()
        click.echo(f"✓ unlinked {loser_path}")

        # 5. Rewrite wikilinks
        total_subs = 0
        for ref in refs:
            n = _rewrite_wikilinks(ref, loser, winner)
            total_subs += n
        click.echo(f"✓ rewrote {total_subs} wikilinks across {len(refs)} files")

        conn.commit()
        click.echo("\nDone.")


if __name__ == "__main__":
    main()
