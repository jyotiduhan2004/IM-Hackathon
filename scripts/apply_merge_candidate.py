"""Manually apply a reviewer-flagged page merge.

Not a one-shot — this script is long-lived (human-in-loop). No expiry marker.

Reviewer subagent flags pages as merge candidates; the coordinator appends
them to ``wiki/merge_candidates.md``. This script applies a single pair:

    uv run python scripts/apply_merge_candidate.py \\
        --pair slug-a,slug-b --keep slug-a --dry-run

    uv run python scripts/apply_merge_candidate.py \\
        --pair slug-a,slug-b --keep slug-a --commit

What it does:
- Concatenates unique H2 body sections from the loser page into the keeper.
  When the same ``## title`` appears in both, the keeper's content wins.
- Merges ``source_threads:`` (and ``sources:``) frontmatter lists, deduped.
- Flips the loser's frontmatter: ``status: superseded`` + ``superseded_by: keep-slug``.
- Updates DB ``wiki_pages.status`` on both rows (keeper → ``active``,
  loser → ``superseded``). Skipped when ``--dry-run``.

This is intentionally conservative — we do NOT touch incoming wikilinks
(they keep pointing at the loser, which is now a tombstone with a link
forward). Wikilink migration is a separate pass.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

CATEGORIES = ("topics", "systems", "policies", "decisions", "people")


def _find_page(wiki_dir: Path, slug: str) -> Path | None:
    """Return the markdown file for a slug, or None if not found."""
    for cat in CATEGORIES:
        candidate = wiki_dir / cat / f"{slug}.md"
        if candidate.exists():
            return candidate
    return None


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Return ``[(header, section_body), ...]`` for each H2 section.

    The preamble (text before the first H2) is returned as the first entry
    with an empty ``header`` string. Higher-level H1s are treated as body
    text — the wiki convention is to put H1 in frontmatter ``title:`` and
    start the body with H2.
    """
    sections: list[tuple[str, str]] = []
    current_header = ""
    current_lines: list[str] = []
    for line in body.splitlines(keepends=True):
        if line.startswith("## ") and not line.startswith("### "):
            # Flush the previous section.
            sections.append((current_header, "".join(current_lines)))
            current_header = line.rstrip("\n").strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    sections.append((current_header, "".join(current_lines)))
    return sections


def _merge_bodies(keeper_body: str, loser_body: str) -> str:
    """Concatenate unique loser sections onto the keeper body.

    Uniqueness is keyed on the normalised H2 title. Title clashes keep the
    keeper's content — the reviewer flagged these as duplicates, so the
    keeper is assumed to be the canonical voice. Loser-only sections are
    appended in their original order at the end of the keeper body.
    """
    keeper_sections = _split_sections(keeper_body)
    loser_sections = _split_sections(loser_body)

    keeper_titles = {_normalize_title(h) for h, _ in keeper_sections if h}
    extras: list[str] = []
    for header, section_text in loser_sections:
        if not header:
            # Loser preamble is dropped — the keeper preamble is canonical.
            continue
        if _normalize_title(header) in keeper_titles:
            continue
        extras.append(section_text.rstrip("\n"))

    if not extras:
        return keeper_body

    # Keep the keeper's trailing newline shape and append extras separated by one blank line.
    return keeper_body.rstrip("\n") + "\n\n" + "\n\n".join(extras) + "\n"


def _normalize_title(header: str) -> str:
    """Return a comparable form of an H2 header (lower, stripped, no date)."""
    title = header.removeprefix("##").strip().lower()
    # Drop trailing "(Jan 12, 2026)"-style date parentheticals so titles that
    # only differ by date still cluster. Matches the tolerance the dated-H2
    # cleanup script assumes.
    title = re.sub(r"\s*\([^)]+\)\s*$", "", title)
    return title.strip()


def _merge_list_field(a: list[Any] | None, b: list[Any] | None) -> list[Any]:
    """Union-merge two list-valued frontmatter fields, deduped, order-preserving."""
    seen: list[Any] = []
    for item in (a or []) + (b or []):
        if item not in seen:
            seen.append(item)
    return seen


def _diff_preview(before: str, after: str, path: Path) -> str:
    """Return a unified diff string for ``before`` → ``after`` on ``path``."""
    import difflib

    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    return "".join(lines) if lines else "(no changes)\n"


def _db_set_status(slug: str, status: str, superseded_by: str | None = None) -> None:
    """Update ``wiki_pages.status`` for ``slug``. Best-effort.

    We don't store ``superseded_by`` in the DB today — it lives in the
    markdown frontmatter. The DB columns tracked are ``status`` only.
    ``superseded_by`` is carried through the file write above; this
    function is intentionally narrow so the coordinator stays the
    source of truth.
    """
    del superseded_by  # reserved for when the catalog schema grows the column

    from src.db import connect

    with connect() as conn:
        conn.execute(
            "UPDATE wiki_pages SET status = %s WHERE slug = %s",
            (status, slug),
        )
        conn.commit()


@click.command()
@click.option(
    "--pair",
    required=True,
    help="Comma-separated slug pair, e.g. --pair bl-notif,bl-sms",
)
@click.option(
    "--keep",
    required=True,
    help="Which slug to keep (must be one of the --pair values). The other becomes superseded.",
)
@click.option(
    "--wiki-dir",
    default=None,
    help="Override wiki root (default: settings.wiki_dir). Pointed at fixtures in tests.",
)
@click.option(
    "--dry-run/--commit", default=True, help="Dry-run by default; pass --commit to apply."
)
def main(pair: str, keep: str, wiki_dir: str | None, dry_run: bool) -> None:
    """Apply one reviewer-flagged merge. See module docstring for semantics."""
    parts = [p.strip() for p in pair.split(",") if p.strip()]
    if len(parts) != 2:
        raise click.BadParameter("--pair must be two comma-separated slugs")
    if keep not in parts:
        raise click.BadParameter("--keep must match one of the --pair slugs")
    loser_slug = parts[0] if parts[1] == keep else parts[1]

    wiki_root = Path(wiki_dir) if wiki_dir else settings.wiki_dir
    keeper_path = _find_page(wiki_root, keep)
    loser_path = _find_page(wiki_root, loser_slug)
    if keeper_path is None:
        raise click.ClickException(f"keeper slug {keep!r} not found under {wiki_root}")
    if loser_path is None:
        raise click.ClickException(f"loser slug {loser_slug!r} not found under {wiki_root}")

    keeper_raw = keeper_path.read_text(encoding="utf-8")
    loser_raw = loser_path.read_text(encoding="utf-8")

    keeper_fm = extract_frontmatter(keeper_raw)
    loser_fm = extract_frontmatter(loser_raw)
    keeper_body = extract_body(keeper_raw)
    loser_body = extract_body(loser_raw)

    # Keeper gets merged body + union source_threads/sources + status=active.
    merged_body = _merge_bodies(keeper_body, loser_body)
    merged_fm = dict(keeper_fm)
    merged_fm["source_threads"] = _merge_list_field(
        keeper_fm.get("source_threads"), loser_fm.get("source_threads")
    )
    merged_fm["sources"] = _merge_list_field(keeper_fm.get("sources"), loser_fm.get("sources"))
    merged_fm["tags"] = _merge_list_field(keeper_fm.get("tags"), loser_fm.get("tags"))
    merged_fm["related"] = _merge_list_field(keeper_fm.get("related"), loser_fm.get("related"))
    # Drop empty lists so we don't add noise to the frontmatter.
    if not merged_fm["source_threads"]:
        merged_fm.pop("source_threads")
    if not merged_fm["sources"]:
        merged_fm.pop("sources")
    if not merged_fm["tags"]:
        merged_fm.pop("tags")
    if not merged_fm["related"]:
        merged_fm.pop("related")
    if merged_fm.get("status") in (None, "", "superseded"):
        merged_fm["status"] = "active"

    keeper_new = render_with_frontmatter(merged_fm, merged_body)

    # Loser becomes a tombstone: original body preserved, status flipped.
    loser_fm_new = dict(loser_fm)
    loser_fm_new["status"] = "superseded"
    loser_fm_new["superseded_by"] = keep
    loser_new = render_with_frontmatter(loser_fm_new, loser_body)

    click.echo(f"=== merge: {loser_slug} -> {keep} ===")
    click.echo()
    click.echo(f"--- {keeper_path} ---")
    click.echo(_diff_preview(keeper_raw, keeper_new, keeper_path))
    click.echo(f"--- {loser_path} ---")
    click.echo(_diff_preview(loser_raw, loser_new, loser_path))

    if dry_run:
        click.echo("(dry-run; pass --commit to apply)")
        return

    keeper_path.write_text(keeper_new, encoding="utf-8")
    loser_path.write_text(loser_new, encoding="utf-8")
    try:
        _db_set_status(keep, "active")
        _db_set_status(loser_slug, "superseded", superseded_by=keep)
    except Exception as exc:  # noqa: BLE001 — DB update is best-effort
        click.echo(f"  warning: DB status update failed: {exc}")
        click.echo("  (files written; run scripts/backfill_wiki_pages.py to resync if needed)")
        return

    click.echo(f"applied merge. keeper={keep}, superseded={loser_slug}")


if __name__ == "__main__":
    main()
