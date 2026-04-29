"""Page size + source-list diagnostics.

Surfaces wiki health signals early — BEFORE compile stalls from context
overflow. Reports:
- Size distribution per category (p50 / p90 / p99 / max)
- Top N biggest pages with source counts
- Pages with duplicated subjects in sources (same thread listed 20+ times)
- Pages approaching the 10KB "danger zone"

Usage:
    uv run python scripts/size_stats.py
    uv run python scripts/size_stats.py --top 30
    uv run python scripts/size_stats.py --danger-kb 10 --dupe-threshold 5
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from statistics import quantiles

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")


def _normalize_subject(raw_path: str) -> str:
    """Extract subject slug from a raw filename.

    raw/2026-01-02_mplaunchim-indiamart-premium-buyer-subscription-tr_abc123.md
    → "mplaunchim-indiamart-premium-buyer-subscription-tr"
    """
    name = Path(raw_path).name
    # Strip YYYY-MM-DD_ prefix and _<hex>.md suffix
    m = re.match(r"^\d{4}-\d{2}-\d{2}_(.+)_[a-f0-9]{8}\.md$", name)
    return m.group(1) if m else name


def _percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {}
    if len(values) == 1:
        v = values[0]
        return {"p50": v, "p90": v, "p99": v, "max": v}
    qs = quantiles(values, n=100)
    return {
        "p50": int(qs[49]),
        "p90": int(qs[89]),
        "p99": int(qs[98]),
        "max": int(max(values)),
    }


@click.command()
@click.option("--top", default=15, help="How many bloated pages to show per category")
@click.option("--danger-kb", default=10, help="Flag pages >= this size (KB)")
@click.option(
    "--dupe-threshold",
    default=5,
    help="Flag sources with same subject appearing N+ times",
)
def main(top: int, danger_kb: int, dupe_threshold: int) -> None:
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: {wiki_dir} not found", err=True)
        sys.exit(1)

    # Collect sizes per category
    by_cat: dict[str, list[tuple[int, Path, dict]]] = {c: [] for c in CATEGORIES}
    for cat in CATEGORIES:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for p in cat_dir.glob("*.md"):
            try:
                size = p.stat().st_size
                fm = extract_frontmatter(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            by_cat[cat].append((size, p, fm))

    # Summary per category
    danger_bytes = danger_kb * 1024
    click.echo("## Size distribution (bytes)\n")
    click.echo(
        f"{'Category':<12} {'N':>5} {'p50':>8} {'p90':>8} {'p99':>8} {'max':>8} {'>' + str(danger_kb) + 'KB':>6}"
    )
    click.echo("-" * 65)
    grand_over_danger: list[tuple[int, Path, dict]] = []
    for cat in CATEGORIES:
        rows = by_cat[cat]
        if not rows:
            continue
        sizes = [s for s, _, _ in rows]
        p = _percentiles(sizes)
        over = sum(1 for s in sizes if s >= danger_bytes)
        click.echo(
            f"{cat:<12} {len(rows):>5} "
            f"{p.get('p50', 0):>8} {p.get('p90', 0):>8} "
            f"{p.get('p99', 0):>8} {p.get('max', 0):>8} "
            f"{over:>6}"
        )
        grand_over_danger.extend([r for r in rows if r[0] >= danger_bytes])

    # Top bloated pages
    if grand_over_danger:
        click.echo(f"\n## Pages >= {danger_kb}KB (top {top} by size)\n")
        grand_over_danger.sort(reverse=True)
        for size, path, fm in grand_over_danger[:top]:
            sources = fm.get("sources") or []
            rel = path.relative_to(wiki_dir)
            click.echo(f"  {size / 1024:>6.1f}KB  {len(sources):>4} sources  {rel}")

    # Subject-dup analysis
    click.echo(
        f"\n## Subject duplication (same thread repeated {dupe_threshold}+ times in sources)\n"
    )
    dupe_findings: list[tuple[int, Path, list[tuple[str, int]]]] = []
    for cat in CATEGORIES:
        for _size, path, fm in by_cat[cat]:
            sources = fm.get("sources") or []
            if len(sources) < dupe_threshold:
                continue
            subjects = [_normalize_subject(s) for s in sources if isinstance(s, str)]
            counter = Counter(subjects)
            dupes = [(subj, n) for subj, n in counter.most_common() if n >= dupe_threshold]
            if dupes:
                total_dupe = sum(n for _, n in dupes)
                dupe_findings.append((total_dupe, path, dupes))

    dupe_findings.sort(reverse=True)
    if not dupe_findings:
        click.echo("  (none — healthy)")
    for total, path, dupes in dupe_findings[:top]:
        rel = path.relative_to(wiki_dir)
        click.echo(f"  {rel} ({total} duped refs)")
        for subj, n in dupes[:5]:
            click.echo(f"    - {n}x {subj}")

    # Body-light-but-frontmatter-heavy pages
    click.echo("\n## Pages with sparse body but many sources (likely auto-bloat candidates)\n")
    sparse: list[tuple[int, int, Path]] = []
    for cat in CATEGORIES:
        for size, path, fm in by_cat[cat]:
            sources = fm.get("sources") or []
            if len(sources) < 20:
                continue
            body_chars = max(0, size - 500 * len(sources))  # frontmatter dominates
            if body_chars < 500:
                sparse.append((len(sources), body_chars, path))
    sparse.sort(reverse=True)
    if not sparse:
        click.echo("  (none)")
    for src_count, body_chars, path in sparse[:top]:
        rel = path.relative_to(wiki_dir)
        click.echo(f"  {src_count} sources, ~{body_chars}B body  {rel}")


if __name__ == "__main__":
    main()
