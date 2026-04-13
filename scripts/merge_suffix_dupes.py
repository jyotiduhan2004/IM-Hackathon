"""Merge `foo-new.md` / `foo-v2.md` variants back into canonical `foo.md`.

Heuristic: the richer body (more sources + longer content) wins. Frontmatter
fields are union-merged. Wikilinks in other pages get rewritten from
`[[foo-new]]` to `[[foo]]`.

Usage:
    uv run python scripts/merge_suffix_dupes.py --dry-run
    uv run python scripts/merge_suffix_dupes.py       # commits changes
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

SUFFIX_RE = re.compile(r"^(.+?)-(new|v\d+|copy|latest|updated|temp|draft|rev\d*|clean)$")
# Bare-numeric suffix: "alok-kumar2", "deepak-jain1", "sahil-sharma2".
# Matched only when the non-digit base also exists as a page.
NUMERIC_SUFFIX_RE = re.compile(r"^(.+?)(\d+)$")
# US/UK spelling pairs (common ones the compiler has tripped over).
SPELLING_PAIRS = (
    ("labelling", "labeling"),
    ("optimise", "optimize"),
    ("behaviour", "behavior"),
    ("favourite", "favorite"),
    ("colour", "color"),
    ("centre", "center"),
    ("organisation", "organization"),
)
CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")


def _merge_fm(a: dict, b: dict) -> dict:
    """Union-merge frontmatter: newer timestamp wins scalars, lists merge unique."""
    out = dict(a)
    for k, v in b.items():
        if k not in out:
            out[k] = v
            continue
        if isinstance(v, list) and isinstance(out[k], list):
            seen: list = []
            for item in out[k] + v:
                if item not in seen:
                    seen.append(item)
            out[k] = seen
            continue
        # Scalar: prefer whichever has a later last_compiled
        if k == "last_compiled":
            out[k] = max(out[k], v)
        # else keep existing
    return out


def _richer(a_content: str, b_content: str) -> bool:
    """Return True if a is richer than b (more sources or more body)."""
    fa, ba = extract_frontmatter(a_content), extract_body(a_content)
    fb, bb = extract_frontmatter(b_content), extract_body(b_content)
    a_sources = len(fa.get("sources") or [])
    b_sources = len(fb.get("sources") or [])
    if a_sources != b_sources:
        return a_sources > b_sources
    return len(ba) >= len(bb)


def _rewrite_wikilinks(wiki_dir: Path, old: str, new: str, dry_run: bool) -> int:
    """Rewrite [[old]] → [[new]] across the whole wiki. Returns count of changes."""
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


def _find_variant_base(stem: str, stems: set[str]) -> str | None:
    """Given a page stem, return the base-canonical stem if this is a variant.

    Tries three patterns in order:
    1. Named suffix (`-clean`, `-new`, `-v2`, etc.) — base is stripped prefix.
    2. Bare numeric suffix (`alok-kumar2`) — base is the non-digit prefix IF
       it exists. Avoids false-positive on legit slugs like `himanshu-jain01`
       when `himanshu-jain` doesn't exist.
    3. US/UK spelling variant — swap and see if the sibling exists.

    Returns the base stem if a variant is detected AND the base exists,
    else None.
    """
    match = SUFFIX_RE.match(stem)
    if match and match.group(1) in stems:
        return match.group(1)

    match = NUMERIC_SUFFIX_RE.match(stem)
    if match and match.group(1) in stems:
        return match.group(1)

    for uk, us in SPELLING_PAIRS:
        for a, b in ((uk, us), (us, uk)):
            if a in stem:
                candidate = stem.replace(a, b, 1)
                if candidate != stem and candidate in stems:
                    # canonical = alphabetically-first so merges are stable
                    return min(stem, candidate)
    return None


@click.command()
@click.option("--dry-run", is_flag=True, help="Report without merging")
def main(dry_run: bool) -> None:
    wiki_dir = settings.wiki_dir
    merged = 0
    skipped = 0

    for cat in CATEGORIES:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        stems = {p.stem for p in cat_dir.glob("*.md")}
        for variant_path in sorted(cat_dir.glob("*.md")):
            base_stem = _find_variant_base(variant_path.stem, stems)
            if base_stem is None or base_stem == variant_path.stem:
                continue
            base_path = cat_dir / f"{base_stem}.md"
            if not base_path.exists() or base_stem not in stems:
                skipped += 1
                click.echo(f"  skip {variant_path.name}: base {base_stem}.md doesn't exist")
                continue

            v_content = variant_path.read_text(encoding="utf-8")
            b_content = base_path.read_text(encoding="utf-8")

            # Pick the richer body, merge frontmatter, rewrite links
            keep_content = v_content if _richer(v_content, b_content) else b_content
            keep_fm = extract_frontmatter(keep_content)
            other_fm = extract_frontmatter(b_content if keep_content == v_content else v_content)
            merged_fm = _merge_fm(keep_fm, other_fm)
            merged_body = extract_body(keep_content)

            click.echo(f"  merge {variant_path.name} → {base_path.name}")
            if not dry_run:
                base_path.write_text(
                    render_with_frontmatter(merged_fm, merged_body), encoding="utf-8"
                )
                variant_path.unlink()

            rewrites = _rewrite_wikilinks(
                wiki_dir, variant_path.stem, base_stem, dry_run=dry_run
            )
            click.echo(f"    rewrote {rewrites} incoming wikilinks")
            merged += 1

    click.echo()
    click.echo(f"Merged: {merged}")
    click.echo(f"Skipped (no base): {skipped}")


if __name__ == "__main__":
    main()
