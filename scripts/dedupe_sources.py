"""Collapse same-thread sources in wiki page frontmatter.

Problem: entity pages accumulate 300+ sources because every reply in a
thread gets its own entry. himanshu-jain01 has 312 sources but only ~50
unique threads — 262 are reply-level dupes.

Strategy:
- Extract normalized subject slug from each raw filename
  (strip YYYY-MM-DD prefix and _{hash}.md suffix)
- Group sources by that slug
- Keep ONE canonical entry per group — the oldest-dated one
- Record a parallel `source_threads:` field with (subject, count, dates)
  summaries so we don't lose the reply count information

Before:
  sources:
    - raw/2026-01-02_foo-subject_abc12345.md
    - raw/2026-01-05_foo-subject_def67890.md
    - raw/2026-01-08_foo-subject_f0e1d2c3.md   (all same thread, different replies)
    - raw/2026-02-01_bar-subject_a1b2c3d4.md

After:
  sources:
    - raw/2026-01-02_foo-subject_abc12345.md   (canonical, oldest)
    - raw/2026-02-01_bar-subject_a1b2c3d4.md
  source_threads:
    - subject: foo-subject
      message_count: 3
      date_range: 2026-01-02..2026-01-08
    - subject: bar-subject
      message_count: 1
      date_range: 2026-02-01

Usage:
    uv run python scripts/dedupe_sources.py --dry-run
    uv run python scripts/dedupe_sources.py --min-sources 20  # only dedupe big pages
    uv run python scripts/dedupe_sources.py                   # apply to all

One-shot lifecycle:
- Classification: one-shot-done
- Last production run: 2026-04-18
- Safe to delete after: 2026-05-15
- Deletion gate: no duplicate source_threads entries exist in wiki/ frontmatter (verify with --dry-run returning 0 pages).
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")
RAW_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)_[a-f0-9]{8}\.md$")


@dataclass
class ThreadGroup:
    subject: str
    canonical: str  # chosen source path (oldest)
    count: int
    earliest_date: str
    latest_date: str


def _parse_raw_path(source: str) -> tuple[str, str] | None:
    """Return (date, subject_slug) or None if unparseable."""
    name = Path(source).name
    m = RAW_NAME_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def _dedupe_sources(sources: list[str]) -> tuple[list[str], list[ThreadGroup]]:
    """Group by normalized subject, keep oldest per group. Return (new_sources, thread_groups)."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    unparseable: list[str] = []

    for src in sources:
        if not isinstance(src, str):
            continue
        parsed = _parse_raw_path(src)
        if parsed is None:
            unparseable.append(src)
            continue
        date, subject = parsed
        groups[subject].append((date, src))

    new_sources: list[str] = []
    thread_groups: list[ThreadGroup] = []

    for subject, entries in groups.items():
        entries.sort(key=lambda t: t[0])  # chronological
        earliest_date, canonical = entries[0]
        latest_date = entries[-1][0]
        new_sources.append(canonical)
        if len(entries) > 1:
            thread_groups.append(
                ThreadGroup(
                    subject=subject,
                    canonical=canonical,
                    count=len(entries),
                    earliest_date=earliest_date,
                    latest_date=latest_date,
                )
            )

    # Sort canonical sources by date (earliest first, same as before)
    new_sources.sort()
    new_sources.extend(unparseable)  # preserve any sources we couldn't parse
    return new_sources, thread_groups


@click.command()
@click.option("--dry-run", is_flag=True, help="Report without writing")
@click.option(
    "--min-sources",
    default=5,
    help="Only process pages with >= N sources (default 5)",
)
@click.option(
    "--category",
    type=click.Choice([*list(CATEGORIES), "all"]),
    default="all",
)
def main(dry_run: bool, min_sources: int, category: str) -> None:
    wiki_dir = settings.wiki_dir
    cats = list(CATEGORIES) if category == "all" else [category]

    total_before = 0
    total_after = 0
    pages_changed = 0

    for cat in cats:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for page in sorted(cat_dir.glob("*.md")):
            content = page.read_text(encoding="utf-8")
            fm = extract_frontmatter(content)
            sources = fm.get("sources") or []
            if len(sources) < min_sources:
                continue

            new_sources, thread_groups = _dedupe_sources(sources)
            if len(new_sources) == len(sources):
                continue  # no dupes

            saved = len(sources) - len(new_sources)
            total_before += len(sources)
            total_after += len(new_sources)
            pages_changed += 1

            click.echo(
                f"  {cat}/{page.stem}: {len(sources)} → {len(new_sources)} sources "
                f"({saved} collapsed into {len(thread_groups)} thread groups)"
            )

            if dry_run:
                continue

            fm["sources"] = new_sources
            if thread_groups:
                fm["source_threads"] = [
                    {
                        "subject": g.subject,
                        "message_count": g.count,
                        "date_range": (
                            f"{g.earliest_date}..{g.latest_date}"
                            if g.earliest_date != g.latest_date
                            else g.earliest_date
                        ),
                    }
                    for g in sorted(thread_groups, key=lambda g: -g.count)
                ]
            body = extract_body(content)
            page.write_text(render_with_frontmatter(fm, body), encoding="utf-8")

    click.echo()
    click.echo(f"Pages changed: {pages_changed}")
    click.echo(
        f"Total sources: {total_before} → {total_after} "
        f"(-{total_before - total_after}, "
        f"{(total_before - total_after) / max(total_before, 1) * 100:.0f}% reduction)"
    )


if __name__ == "__main__":
    main()
