"""Iteratively migrate legacy display-name entity slugs to email-canonical slugs.

Why: the old compile loop invented slugs from display names (`amit-agarwal`),
which caused duplicates (`arjun-gaur`, `arjun-gaur-clean`, `arjun-gaur-v2`)
and garbage slugs when names didn't slugify (`vishakha-indiamart`). The new
`create_entity` tool uses email as the deterministic key. This script brings
existing pages into line — one chunk at a time, so it's safe to run
partially.

Strategy:
1. Walk `wiki/entities/*.md`. For each page, discover its email:
     a. `email:` in frontmatter (new format), or
     b. `Email: foo@bar.com` in body (legacy format).
2. Compute the canonical slug via `email_to_slug`.
3. If slug already matches, only normalize frontmatter (`email:`,
   `is_external:`) if missing.
4. If slug differs:
     a. If the target path already exists → collision; skip and log.
        Humans pick the canonical page and merge bodies manually.
     b. Else: rename the file, rewrite all incoming `[[old]]` wikilinks to
        `[[new]]` across every category, and normalize frontmatter.

Flags:
    --dry-run           Preview only. No filesystem writes.
    --limit N           Process at most N pages this run (default 20).
    --only-missing-fm   Only touch pages whose frontmatter is missing
                        `email:` or `is_external:`. Skip the renames.

Usage:
    uv run python scripts/migrate_entity_slugs.py --dry-run
    uv run python scripts/migrate_entity_slugs.py --limit 20
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.entities import email_to_slug  # noqa: E402
from src.compile.entities import is_external_email  # noqa: E402
from src.compile.entities import is_valid_email  # noqa: E402
from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")
BODY_EMAIL_RE = re.compile(
    r"(?mi)^\s*(?:\*\*)?email(?:\*\*)?[:\s]+"
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]+)"
)


def _email_for(page: Path) -> str | None:
    """Extract the canonical email for an entity page.

    Frontmatter `email:` wins. Body `Email: foo@bar.com` is the legacy
    fallback. Returns lowercase, trimmed.
    """
    try:
        content = page.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = extract_frontmatter(content)
    fm_email = fm.get("email")
    if isinstance(fm_email, str) and is_valid_email(fm_email):
        return fm_email.strip().lower()
    body = extract_body(content)
    m = BODY_EMAIL_RE.search(body)
    if m and is_valid_email(m.group(1)):
        return m.group(1).strip().lower()
    return None


def _rewrite_wikilinks(wiki_dir: Path, old: str, new: str, *, dry_run: bool) -> int:
    """Rewrite [[old]] → [[new]] across every category. Returns rewrite count.

    Pattern matches both `[[old]]` and `[[old|alias]]` forms.
    """
    if old == new:
        return 0
    count = 0
    pattern = re.compile(rf"\[\[{re.escape(old)}(\||\]\])")
    for cat in CATEGORIES:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for md in cat_dir.glob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new_content, n = pattern.subn(f"[[{new}\\1", content)
            if n:
                count += n
                if not dry_run:
                    md.write_text(new_content, encoding="utf-8")
    return count


def _normalize_frontmatter(page: Path, email: str, *, dry_run: bool) -> bool:
    """Ensure `email:` and `is_external:` are present + correct. Returns True
    if the page was changed (or would have been under --dry-run)."""
    try:
        content = page.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm = extract_frontmatter(content)
    body = extract_body(content)

    expected_external = is_external_email(email)
    needs_update = (
        fm.get("email") != email or fm.get("is_external") != expected_external
    )
    if not needs_update:
        return False
    fm["email"] = email
    fm["is_external"] = expected_external
    if not dry_run:
        page.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
    return True


@click.command()
@click.option("--dry-run", is_flag=True, help="Preview only. No writes.")
@click.option(
    "--limit", type=int, default=20, help="Max migrations per run (default 20)"
)
@click.option(
    "--only-missing-fm",
    is_flag=True,
    help="Only normalize frontmatter on pages whose slug already matches.",
)
def main(dry_run: bool, limit: int, only_missing_fm: bool) -> None:
    entities_dir = settings.wiki_dir / "entities"
    if not entities_dir.exists():
        click.echo(f"ERROR: {entities_dir} not found", err=True)
        sys.exit(2)

    to_rename: list[tuple[Path, Path, str]] = []
    collisions: list[tuple[Path, Path, str]] = []
    no_email: list[Path] = []
    fm_only: list[tuple[Path, str]] = []

    for page in sorted(entities_dir.glob("*.md")):
        email = _email_for(page)
        if not email:
            no_email.append(page)
            continue
        canonical = email_to_slug(email)
        if page.stem == canonical:
            fm_only.append((page, email))
            continue
        target = entities_dir / f"{canonical}.md"
        if target.exists():
            collisions.append((page, target, email))
            continue
        to_rename.append((page, target, email))

    click.echo(f"Total entity pages: {len(list(entities_dir.glob('*.md')))}")
    click.echo(f"  already canonical: {len(fm_only)}")
    click.echo(f"  needing migration: {len(to_rename)}")
    click.echo(f"  collisions (skip):   {len(collisions)}")
    click.echo(f"  no-email (skip):     {len(no_email)}")
    click.echo()

    fm_updates = 0

    if only_missing_fm:
        batch = fm_only[:limit]
        click.echo(f"-- Frontmatter normalization ({len(batch)} pages) --")
        for page, email in batch:
            changed = _normalize_frontmatter(page, email, dry_run=dry_run)
            if changed:
                fm_updates += 1
                click.echo(f"  update {page.name} (email={email})")
        click.echo()
        click.echo(f"Frontmatter updates: {fm_updates}")
        return

    batch = to_rename[:limit]
    if not batch:
        click.echo("Nothing to rename. Pass --only-missing-fm to normalize existing pages.")
        return

    click.echo(f"-- Renaming {len(batch)} pages --")
    renamed = 0
    total_rewrites = 0
    for page, target, email in batch:
        click.echo(f"  {page.name} → {target.name} (email={email})")
        rewrites = _rewrite_wikilinks(
            settings.wiki_dir, page.stem, target.stem, dry_run=dry_run
        )
        click.echo(f"    rewrites pending: {rewrites}")
        if not dry_run:
            page.rename(target)
            _normalize_frontmatter(target, email, dry_run=False)
        renamed += 1
        total_rewrites += rewrites

    click.echo()
    click.echo(f"Renamed: {renamed}")
    click.echo(f"Incoming wikilink rewrites: {total_rewrites}")
    click.echo(f"Remaining to rename: {max(0, len(to_rename) - renamed)}")
    if collisions:
        click.echo()
        click.echo(
            f"Skipped {len(collisions)} collisions — target page already exists. "
            "Manual merge required. First few:"
        )
        for page, target, email in collisions[:5]:
            click.echo(f"  {page.name} ↔ {target.name}  (email={email})")


if __name__ == "__main__":
    main()
