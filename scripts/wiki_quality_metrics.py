"""Emit structured wiki quality metrics for CI gating.

Companion to `scripts/audit.py` — that script emits a prose report, this
one emits a CI-friendly single-line summary plus structured JSON that
release gates can parse. See Phase 1 plan Workstream 6 (structural
quality metrics) in `docs/issues/10-phase1-implementation-plan.md`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import CATEGORIES  # noqa: E402
from scripts.audit import STUB_BYTES  # noqa: E402
from scripts.audit import WIKILINK_RE  # noqa: E402
from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402


def collect_metrics(wiki_dir: Path) -> dict[str, object]:
    """Compute structural wiki quality metrics at ``wiki_dir``.

    Returns a JSON-serialisable dict. Mirrors the "Structural quality
    metrics" bullet list in
    ``docs/issues/10-phase1-implementation-plan.md`` Workstream 6.
    """
    pages_by_type: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
    stubs_by_type: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
    sizes_by_type: dict[str, list[int]] = {cat: [] for cat in CATEGORIES}
    # Outbound wikilinks per page — captured in the same pass that reads
    # each page's body so we don't re-read every file for orphan detection.
    outgoing: dict[str, list[str]] = {}

    all_pages: list[Path] = []
    for cat in CATEGORIES:
        d = wiki_dir / cat
        if not d.exists():
            continue
        pages = sorted(d.glob("*.md"))
        all_pages.extend(pages)
        pages_by_type[cat] = len(pages)
        for p in pages:
            try:
                content = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            body = extract_body(content)
            n = len(body.encode("utf-8"))
            sizes_by_type[cat].append(n)
            if n < STUB_BYTES:
                stubs_by_type[cat] += 1
            outgoing[p.stem] = [t.split("|")[0].strip() for t in WIKILINK_RE.findall(content)]

    # Orphans = pages with no incoming wikilinks from other category
    # pages (index.md is intentionally excluded so we can surface pages
    # that are only reachable from the home page separately).
    incoming: dict[str, int] = {p.stem: 0 for p in all_pages}
    for src_stem, targets in outgoing.items():
        for target_stem in targets:
            if target_stem in incoming and target_stem != src_stem:
                incoming[target_stem] += 1
    orphan_count = sum(1 for n in incoming.values() if n == 0)

    # Pages reachable only from index.md — i.e. their single incoming
    # wikilink (n == 1 in the ``incoming`` map) lives in ``index.md``.
    # Useful to spot pages that wouldn't be findable without the home
    # page; candidates for better cross-linking.
    index_links: set[str] = set()
    index_path = wiki_dir / "index.md"
    if index_path.exists():
        try:
            idx_content = index_path.read_text(encoding="utf-8")
            index_links = {m.split("|")[0].strip() for m in WIKILINK_RE.findall(idx_content)}
        except (OSError, UnicodeDecodeError):
            pass
    only_index_count = sum(1 for stem, n in incoming.items() if n == 1 and stem in index_links)

    total_pages = sum(pages_by_type.values())
    total_stubs = sum(stubs_by_type.values())
    topic_to_entity_ratio: float | None = (
        round(pages_by_type["topics"] / pages_by_type["entities"], 3)
        if pages_by_type["entities"] > 0
        else None
    )

    avg_by_type = {
        cat: (sum(sizes) // len(sizes)) if sizes else 0 for cat, sizes in sizes_by_type.items()
    }

    return {
        "pages_by_type": pages_by_type,
        "stubs_by_type": stubs_by_type,
        "avg_body_bytes_by_type": avg_by_type,
        "total_pages": total_pages,
        "total_stubs": total_stubs,
        "stub_rate_pct": round(100 * total_stubs / total_pages, 1) if total_pages else 0.0,
        "orphan_count": orphan_count,
        "topic_to_entity_ratio": topic_to_entity_ratio,
        "pages_only_reachable_from_index": only_index_count,
    }


@click.command()
@click.option(
    "--wiki-dir",
    type=click.Path(),
    default=None,
    help="Wiki root (default: settings.wiki_dir)",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON only")
@click.option(
    "--min-topic-ratio",
    type=float,
    default=0.3,
    help="Exit non-zero if topic_to_entity_ratio below this",
)
def main(wiki_dir: str | None, json_output: bool, min_topic_ratio: float) -> None:
    """Emit wiki quality metrics and optionally gate on topic/entity ratio."""
    wd = Path(wiki_dir) if wiki_dir else settings.wiki_dir
    if not wd.exists():
        click.echo(f"ERROR: wiki dir not found: {wd}", err=True)
        sys.exit(2)

    metrics = collect_metrics(wd)
    if json_output:
        click.echo(json.dumps(metrics, indent=2))
    else:
        click.echo(
            f"pages={metrics['total_pages']} "
            f"stubs={metrics['total_stubs']} ({metrics['stub_rate_pct']}%) "
            f"orphans={metrics['orphan_count']} "
            f"topic_ratio={metrics['topic_to_entity_ratio']}"
        )
        click.echo(json.dumps(metrics, indent=2))

    ratio = metrics["topic_to_entity_ratio"]
    if isinstance(ratio, int | float) and ratio < min_topic_ratio:
        click.echo(
            f"FAIL: topic_to_entity_ratio={ratio} < --min-topic-ratio={min_topic_ratio}",
            err=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
