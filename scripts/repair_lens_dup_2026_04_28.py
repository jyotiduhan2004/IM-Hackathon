"""One-shot consolidation of the Lens.IndiaMART system page dup
surfaced by the audit-status tracker (`docs/audits/STATUS.md`,
finding F-024).

Current state on disk:

- ``wiki/systems/Lens.IndiaMART.md`` — UPPERCASE filename, real
  content (~57 lines, 11 sources, 3 source_threads)
- ``wiki/systems/lens-indiamart-com.md`` — kebab-case stub, 23 lines,
  1 source_thread, has a ``## TL;DR`` the other lacks

Target state:

- ``wiki/systems/indiamart-lens.md`` — canonical kebab-case slug
  matching ``tests/fixtures/new_joiner/domains.json`` (``expected_slug:
  indiamart-lens``). Carries the merged body (TL;DR from kebab page,
  Overview + Browser Extension + Related from Uppercase page) and the
  union of sources / source_threads / related.
- Both legacy files: ``status: superseded`` + ``superseded_by:
  indiamart-lens`` in markdown frontmatter and DB
  (``wiki_pages.status``).
- All 24 inbound wikilinks across 10 wiki/ files rewritten to point
  at ``indiamart-lens``: ``[[Lens.IndiaMART]]``, ``[[Lens.IndiaMART|alias]]``,
  ``[[lens-indiamart-com]]``, ``[[lens-indiamart-com|alias]]``.

Out of scope:

- Auto-generated landing pages (``wiki/compile-status.md``,
  ``wiki/log.md``, ``wiki/domains/*.md``) self-correct on next compile —
  but for cleanliness this script also rewrites them so the next
  reader sees consistent slugs.
- ``raw/*.md`` is immutable evidence; subjects mention "Lens.IndiaMART"
  as the product name (correct usage).

Idempotent: re-running after the merge is a no-op (target file exists,
losers already superseded, wikilinks already point at canonical).

One-shot lifecycle:

- Last production run: 2026-04-28
- Safe to delete after: 2026-05-28
- Deletion gate: ``scripts/audit.py`` reports zero pages with
  ``[[Lens.IndiaMART]]`` or ``[[lens-indiamart-com]]`` wikilinks for
  7 consecutive days, and ``wiki/systems/indiamart-lens.md`` remains
  on disk with ``page_type: system`` + ``status: active``.

Usage::

    uv run python scripts/repair_lens_dup_2026_04_28.py --dry-run
    uv run python scripts/repair_lens_dup_2026_04_28.py --commit
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

WIKI = REPO_ROOT / "wiki"
TARGET_SLUG = "indiamart-lens"
TARGET_PATH = WIKI / "systems" / f"{TARGET_SLUG}.md"
LEGACY_UPPERCASE = WIKI / "systems" / "Lens.IndiaMART.md"
LEGACY_KEBAB = WIKI / "systems" / "lens-indiamart-com.md"

# Wikilink patterns. We must preserve aliases (``[[OldSlug|alias]]`` →
# ``[[NewSlug|alias]]``) and not match across wikilinks (no greedy regex).
# Pattern handles both ``[[X]]`` and ``[[X|alias]]``.
_OLD_SLUG_RES = [
    re.compile(r"\[\[Lens\.IndiaMART(?P<rest>(?:\|[^\]]*)?)\]\]"),
    re.compile(r"\[\[lens-indiamart-com(?P<rest>(?:\|[^\]]*)?)\]\]"),
]


def _build_merged_page() -> str:
    """Compose the merged ``indiamart-lens.md`` content as YAML+markdown."""
    fm = {
        "title": "Lens.IndiaMART",
        "page_type": "system",
        "status": "active",
        "domain": "buyer-experience",
        "sources": [
            "raw/2026-01-12_mplaunchim-indiamart-lens-extension-faster-access-_721df76b.md",
            "raw/2026-01-16_mplaunchim-lensindiamart-fully-logged-in-user-flow_c5a02e55.md",
            "raw/2026-01-19_mplaunchim-lensindiamart-fully-logged-in-user-flow_1aa11125.md",
            "raw/2026-01-19_mplaunchim-lensindiamart-fully-logged-in-user-flow_a5dd1ff2.md",
            "raw/2026-01-19_mplaunchim-lensindiamart-fully-logged-in-user-flow_dc6b7590.md",
            "raw/2026-01-19_mplaunchim-lensindiamart-fully-logged-in-user-flow_18a248b9.md",
            "raw/2026-01-21_mplaunchim-lensindiamart-fully-logged-in-user-flow_97a3d36f.md",
            "raw/2026-01-21_mplaunchim-lensindiamart-fully-logged-in-user-flow_8d5c0edb.md",
            "raw/2026-01-21_mplaunchim-lensindiamart-fully-logged-in-user-flow_2069b9c0.md",
            "raw/2026-01-23_mplaunchim-lensindiamart-fully-logged-in-user-flow_fc31eddc.md",
            "raw/2026-01-27_mplaunchim-lensindiamart-fully-logged-in-user-flow_852b1c52.md",
        ],
        "source_threads": [
            "19bb0edbfe86e385",
            "19bb66cef7aae875",
            "1994c8d390469f2b",
        ],
        "related": [
            "[[photosearch]]",
            "[[photo-search-team]]",
            "[[city-based-filters-on-lens-results-page]]",
            "[[lens-extension-right-click-menu-access]]",
            "[[lens-2-0-hybrid-photosearch]]",
            "[[im-live-link-removal-lens-indiamart]]",
            "[[lens-indiamart-fully-logged-in-user-flow]]",
        ],
    }
    body = """## TL;DR

Lens.IndiaMART (lens.indiamart.com) is IndiaMART's visual search and image-based product discovery platform. Users search by uploading images instead of typing text queries; the surface ships as a browser extension and as the photo-search experience inside the IndiaMART buyer flows.

## Overview

Lens.IndiaMART is the visual-search product within the IndiaMART ecosystem. Buyers can search for products either by uploading an image directly or by right-clicking any image on the web. The platform has rolled forward through a series of launches: hybrid visual + text search ([[lens-2-0-hybrid-photosearch]]), full logged-in flow on mSite and desktop ([[lens-indiamart-fully-logged-in-user-flow]]), city-based result filters ([[city-based-filters-on-lens-results-page]]), and right-click menu access via the browser extension ([[lens-extension-right-click-menu-access]]).

## Browser Extension

The Lens Extension enables users to search for products by right-clicking on any image. Key features:

- **Right-click menu access** (v7.0): users can access Lens directly from the browser's context menu on any image.
- **Dynamic content-script injection**: content scripts are injected on-demand using the `chrome.scripting` API.
- **Manifest V3 compatible**: uses the `chrome.contextMenus` API for context-menu integration.

## Related

- [[photosearch]]
- [[photo-search-team]]
- [[lens-2-0-hybrid-photosearch]]
- [[lens-indiamart-fully-logged-in-user-flow]]
- [[lens-mobile-ui-upgrade-ab-test]]
- [[city-based-filters-on-lens-results-page]]
- [[lens-extension-right-click-menu-access]]
- [[im-live-link-removal-lens-indiamart]]
"""
    return render_with_frontmatter(fm, body)


def _supersede_in_place(path: Path, target_slug: str) -> bool:
    """Mark a legacy file as superseded. Returns True if changes made."""
    if not path.exists():
        return False
    text = path.read_text()
    fm, body = extract_frontmatter(text), extract_body(text)
    if fm.get("status") == "superseded" and fm.get("superseded_by") == target_slug:
        return False
    fm["status"] = "superseded"
    fm["superseded_by"] = target_slug
    new_text = render_with_frontmatter(fm, body)
    path.write_text(new_text)
    return True


def _rewrite_wikilinks(commit: bool) -> tuple[int, int]:
    """Rewrite [[Lens.IndiaMART]] and [[lens-indiamart-com]] (with aliases)
    to [[indiamart-lens]] across all .md files in wiki/ EXCEPT the two
    legacy files themselves (their status: superseded tombstones still
    reference the canonical via superseded_by). Returns (files_touched,
    wikilinks_rewritten)."""
    files_touched = 0
    rewrites = 0
    for path in WIKI.rglob("*.md"):
        if path.resolve() in (LEGACY_UPPERCASE.resolve(), LEGACY_KEBAB.resolve()):
            continue
        if path.resolve() == TARGET_PATH.resolve():
            continue
        text = path.read_text()
        new_text = text
        local_rewrites = 0
        for pat in _OLD_SLUG_RES:
            new_text, n = pat.subn(lambda m: f"[[{TARGET_SLUG}{m.group('rest')}]]", new_text)
            local_rewrites += n
        if new_text != text:
            files_touched += 1
            rewrites += local_rewrites
            if commit:
                path.write_text(new_text)
    return files_touched, rewrites


def _update_db(commit: bool) -> None:
    """Apply DB transitions for the consolidation.

    - INSERT row for indiamart-lens (or UPDATE if it already exists).
    - Mark Lens.IndiaMART and lens-indiamart-com as status='superseded'.
    """
    if not commit:
        click.echo(
            "  [dry-run] would: insert indiamart-lens row; supersede Lens.IndiaMART + lens-indiamart-com"
        )
        return
    with connect() as conn:
        cur = conn.cursor()
        # Upsert canonical row.
        cur.execute(
            """
            INSERT INTO wiki_pages (slug, path, title, page_type, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE
            SET path = EXCLUDED.path,
                title = EXCLUDED.title,
                status = 'active'
            """,
            (
                TARGET_SLUG,
                f"wiki/systems/{TARGET_SLUG}.md",
                "Lens.IndiaMART",
                "system",
                "active",
            ),
        )
        # Supersede the two legacy slugs.
        cur.execute("UPDATE wiki_pages SET status = 'superseded' WHERE slug = 'Lens.IndiaMART'")
        cur.execute("UPDATE wiki_pages SET status = 'superseded' WHERE slug = 'lens-indiamart-com'")
        conn.commit()


@click.command()
@click.option("--commit", is_flag=True, help="Apply changes for real.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would change.")
def main(commit: bool, dry_run: bool) -> None:
    if commit == dry_run:
        click.echo("Pass exactly one of --commit / --dry-run.", err=True)
        sys.exit(2)

    click.echo("=== 1. write merged target page ===")
    if TARGET_PATH.exists():
        click.echo(f"  {TARGET_PATH.relative_to(REPO_ROOT)} already exists — skip merge")
    else:
        click.echo(
            f"  would create {TARGET_PATH.relative_to(REPO_ROOT)} ({len(_build_merged_page())} bytes)"
        )
        if commit:
            TARGET_PATH.write_text(_build_merged_page())
            click.echo("  written")

    click.echo("")
    click.echo("=== 2. supersede legacy pages in markdown ===")
    for path in (LEGACY_UPPERCASE, LEGACY_KEBAB):
        rel = path.relative_to(REPO_ROOT)
        if not path.exists():
            click.echo(f"  {rel}: missing — skip")
            continue
        if commit:
            changed = _supersede_in_place(path, TARGET_SLUG)
            click.echo(f"  {rel}: {'updated' if changed else 'already superseded'}")
        else:
            click.echo(f"  {rel}: would set status=superseded + superseded_by={TARGET_SLUG}")

    click.echo("")
    click.echo("=== 3. rewrite inbound wikilinks ===")
    files_touched, rewrites = _rewrite_wikilinks(commit=commit)
    click.echo(f"  {files_touched} files touched, {rewrites} wikilinks rewritten")

    click.echo("")
    click.echo("=== 4. DB transitions ===")
    _update_db(commit=commit)

    click.echo("")
    click.echo("done." if commit else "(dry-run; pass --commit to apply)")


if __name__ == "__main__":
    main()
