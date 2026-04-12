"""Second-pass stub-filler.

Walks wiki/entities/ and wiki/systems/ for "stub" pages — pages with empty
sources:[] or `last_compiled: "stub"` — and rebuilds their `sources:` list
by grep-searching raw/ for their slug, name variants, or email.

This runs AFTER a compile batch as a cheap post-processing step. It does NOT
re-compile the page body via LLM — that's the job of the main compiler
when the raw sources get marked uncompiled again.

Two modes:
- --refresh-sources (default): only updates the sources list
- --recompile: marks raw sources as compiled=false so the next compile run
  will rewrite the page body with the full thread context

Usage:
    uv run python scripts/backfill_stubs.py
    uv run python scripts/backfill_stubs.py --recompile
    uv run python scripts/backfill_stubs.py --dry-run
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402


def _grep_raw_for_slug(slug: str, raw_dir: Path) -> list[str]:
    """Find raw files that reference `slug` by name variants."""
    # Generate name candidates from slug. For "lucky-agarwal" → ["lucky agarwal",
    # "lucky.agarwal", "agarwal.lucky", "lucky_agarwal"]
    parts = slug.split("-")
    candidates: set[str] = set()
    if len(parts) >= 2:
        # Multi-part name — likely a person
        candidates.add(" ".join(parts))  # "lucky agarwal"
        candidates.add(".".join(parts))  # "lucky.agarwal"
        if len(parts) == 2:
            candidates.add(f"{parts[1]}.{parts[0]}")  # "agarwal.lucky"
    # Slug itself as a fallback
    candidates.add(slug)
    candidates.add(slug.replace("-", " "))

    hits: set[str] = set()
    for md in raw_dir.glob("*.md"):
        try:
            content = md.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        for cand in candidates:
            if cand.lower() in content:
                hits.add(str(md.relative_to(REPO_ROOT)))
                break
    return sorted(hits)


def _is_stub(fm: dict) -> bool:
    """A page is a stub if sources is empty OR last_compiled is literal 'stub'."""
    if fm.get("last_compiled") == "stub":
        return True
    sources = fm.get("sources") or []
    return len(sources) == 0


@click.command()
@click.option(
    "--dry-run", is_flag=True, help="Show what would be backfilled without writing"
)
@click.option(
    "--recompile",
    is_flag=True,
    help="After rewriting sources, mark those raw files as compiled=false so "
    "the next compile run regenerates the page body with full context",
)
@click.option(
    "--category",
    type=click.Choice(["entities", "systems", "all"]),
    default="all",
    help="Which wiki category to backfill stubs in",
)
def main(dry_run: bool, recompile: bool, category: str) -> None:
    """Find stub wiki pages and backfill their sources list from raw/."""
    wiki_dir = settings.wiki_dir
    raw_dir = settings.raw_dir

    cats = ["entities", "systems"] if category == "all" else [category]

    found = 0
    backfilled = 0
    no_matches = 0
    raw_to_reset: set[Path] = set()

    for cat in cats:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for page in sorted(cat_dir.glob("*.md")):
            content = page.read_text(encoding="utf-8")
            fm = extract_frontmatter(content)
            if not _is_stub(fm):
                continue

            found += 1
            slug = page.stem
            hits = _grep_raw_for_slug(slug, raw_dir)

            if not hits:
                no_matches += 1
                click.echo(f"  no matches for {cat}/{slug}")
                continue

            click.echo(f"  {cat}/{slug}: {len(hits)} raw source(s) found")

            if dry_run:
                continue

            # Rewrite sources; leave body alone
            fm["sources"] = hits
            if fm.get("last_compiled") == "stub":
                fm["last_compiled"] = "stub-backfilled"
            body = extract_body(content)
            page.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
            backfilled += 1

            if recompile:
                # Mark raw files as uncompiled so the next compile run picks them up
                # and can rewrite this page's body with full thread context
                for hit in hits:
                    raw_path = REPO_ROOT / hit
                    if not raw_path.exists():
                        continue
                    rc = raw_path.read_text(encoding="utf-8")
                    rfm = extract_frontmatter(rc)
                    if rfm.get("compiled") is True:
                        rfm["compiled"] = False
                        rfm.pop("compiled_at", None)
                        new = render_with_frontmatter(rfm, extract_body(rc))
                        raw_path.write_text(new, encoding="utf-8")
                        raw_to_reset.add(raw_path)

    click.echo()
    click.echo(f"Stubs found: {found}")
    click.echo(f"Backfilled: {backfilled}")
    click.echo(f"No matches (kept empty): {no_matches}")
    if recompile:
        click.echo(f"Raw files reset for recompile: {len(raw_to_reset)}")


if __name__ == "__main__":
    main()
