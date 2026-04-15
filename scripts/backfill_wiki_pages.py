"""Populate wiki_pages from wiki/{topics,entities,systems,policies,timelines,conflicts}/*.md.

Walks each category folder, parses frontmatter, and upserts a wiki_pages
row per markdown file. Idempotent — re-running is safe and only updates
metadata (title / path / status / canonical_user_email) for existing rows.

Entity pages pull `canonical_user_email` from frontmatter `email:`. Other
page types leave it NULL (a topic page may reference a person but doesn't
own their identity).

Usage:
    uv run python scripts/backfill_wiki_pages.py
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
from src.db.users import upsert_user  # noqa: E402
from src.db.wiki_pages import count_wiki_pages_by_type  # noqa: E402
from src.db.wiki_pages import upsert_wiki_page  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

# Folder name → page_type value. Matches the CHECK constraint in schema.sql.
_CATEGORIES: dict[str, str] = {
    "topics": "topic",
    "entities": "entity",
    "systems": "system",
    "policies": "policy",
    "timelines": "timeline",
    "conflicts": "conflict",
}

_VALID_STATUS = {"current", "superseded", "contested"}


def _str_or_none(val: object) -> str | None:
    return val if isinstance(val, str) and val.strip() else None


@click.command()
@click.option("--wiki-dir", default=None, help="wiki/ root (default settings.wiki_dir)")
def main(wiki_dir: str | None) -> None:
    wiki_root = Path(wiki_dir) if wiki_dir else settings.wiki_dir
    if not wiki_root.is_absolute():
        wiki_root = (REPO_ROOT / wiki_root).resolve()
    if not wiki_root.exists():
        click.echo(f"ERROR: {wiki_root} not found", err=True)
        sys.exit(2)

    upserted = 0
    skipped_no_title = 0
    per_type: dict[str, int] = dict.fromkeys(_CATEGORIES.values(), 0)
    seen_slugs: dict[str, str] = {}  # slug → first-seen page_type
    cross_type_collisions: list[tuple[str, str, str]] = []

    with connect() as conn:
        for folder, page_type in _CATEGORIES.items():
            folder_path = wiki_root / folder
            if not folder_path.exists():
                continue

            for md in sorted(folder_path.glob("*.md")):
                try:
                    content = md.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                fm = extract_frontmatter(content)
                title = _str_or_none(fm.get("title"))
                if not title:
                    # Fall back to filename stem — better than skipping
                    # a page the compiler wrote but forgot to title.
                    title = md.stem.replace("-", " ").title()
                    if not title:
                        skipped_no_title += 1
                        continue

                status_raw = _str_or_none(fm.get("status")) or "current"
                status = status_raw if status_raw in _VALID_STATUS else "current"

                canonical_email: str | None = None
                if page_type == "entity":
                    canonical_email = _str_or_none(fm.get("email"))
                    # The entity page's existence is itself evidence this user is
                    # real. Pre-insert into `users` so the FK on `wiki_pages` can
                    # resolve — otherwise a person whose only appearance is the
                    # entity page stub (no participant record yet) would fail the
                    # upsert with a FK violation.
                    if canonical_email:
                        display_name = _str_or_none(fm.get("title"))
                        upsert_user(conn, email=canonical_email, display_name=display_name)

                # slug is globally UNIQUE — record cross-folder clashes
                # so we don't silently lose pages on the second insert.
                prior = seen_slugs.get(md.stem)
                if prior is not None and prior != page_type:
                    cross_type_collisions.append((md.stem, prior, page_type))
                    continue
                seen_slugs[md.stem] = page_type

                # Prefer repo-relative path; fall back to absolute when the
                # wiki dir lives outside the repo (manual --wiki-dir override).
                resolved = md.resolve()
                try:
                    rel_path = str(resolved.relative_to(REPO_ROOT))
                except ValueError:
                    rel_path = str(resolved)
                upsert_wiki_page(
                    conn,
                    slug=md.stem,
                    path=rel_path,
                    title=title,
                    page_type=page_type,
                    status=status,
                    canonical_user_email=canonical_email,
                )
                upserted += 1
                per_type[page_type] += 1

        conn.commit()

    click.echo(f"upserted: {upserted}")
    if skipped_no_title:
        click.echo(f"skipped (no title): {skipped_no_title}")
    if cross_type_collisions:
        click.echo(
            f"\nWARNING: {len(cross_type_collisions)} slug(s) appear in multiple "
            "category folders (kept the first seen):"
        )
        for slug, kept, dropped in cross_type_collisions[:5]:
            click.echo(f"  {slug}: kept={kept}, dropped={dropped}")
    click.echo("\nper-type counts (from this run):")
    for pt in sorted(per_type):
        click.echo(f"  {pt}: {per_type[pt]}")
    click.echo("\ntotals in DB:")
    for pt, n in sorted(count_wiki_pages_by_type().items()):
        click.echo(f"  {pt}: {n}")


if __name__ == "__main__":
    main()
