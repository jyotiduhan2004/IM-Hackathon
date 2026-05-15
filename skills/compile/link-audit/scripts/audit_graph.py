"""Wikilink graph auditor — finds orphans, dead-ends, and broken links.

Usage:
    python skills/compile/link-audit/scripts/audit_graph.py
    python skills/compile/link-audit/scripts/audit_graph.py --wiki-dir wiki --json
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

WIKI_CATEGORIES = ["topics", "systems", "policies", "decisions"]
SKIP_CATEGORIES = ["people"]


def extract_wikilinks(text: str) -> list[str]:
    """Extract all [[wikilink]] targets from text."""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


def normalize_slug(link: str) -> str:
    """Normalize a wikilink target to a slug."""
    link = link.strip("/")
    for prefix in ["topics/", "systems/", "policies/", "decisions/", "domains/", "people/"]:
        if link.startswith(prefix):
            link = link[len(prefix):]
    return link


def audit_wiki(wiki_dir: str = "wiki") -> dict:
    """Audit the wikilink graph and return a report."""
    wiki_path = Path(wiki_dir)

    all_slugs: set[str] = set()
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = defaultdict(list)

    for category in WIKI_CATEGORIES:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for page_path in sorted(cat_dir.glob("*.md")):
            if page_path.name.startswith(".") or page_path.name.startswith("_") or page_path.name == "index.md":
                continue
            slug = page_path.stem
            all_slugs.add(slug)

            try:
                text = page_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            links = [normalize_slug(lnk) for lnk in extract_wikilinks(text)]
            links = [lnk for lnk in links if lnk and lnk != slug]
            outgoing[slug] = links

            for target in links:
                incoming[target].append(slug)

    orphans = [s for s in all_slugs if s not in incoming or len(incoming[s]) == 0]
    dead_ends = [s for s in all_slugs if s not in outgoing or len(outgoing[s]) == 0]
    broken = set()
    for slug, links in outgoing.items():
        for target in links:
            norm = normalize_slug(target)
            if norm not in all_slugs and not norm.startswith("glossary"):
                broken.add((slug, target))

    return {
        "total_pages": len(all_slugs),
        "total_links": sum(len(v) for v in outgoing.values()),
        "orphan_pages": sorted(orphans),
        "orphan_count": len(orphans),
        "dead_end_pages": sorted(dead_ends),
        "dead_end_count": len(dead_ends),
        "broken_links": sorted([{"source": s, "target": t} for s, t in broken], key=lambda x: x["source"]),
        "broken_count": len(broken),
    }


def print_report(report: dict) -> None:
    """Print a human-readable audit report."""
    print(f"Wiki Link Audit Report")
    print(f"=" * 50)
    print(f"Total pages: {report['total_pages']}")
    print(f"Total links: {report['total_links']}")
    print()

    print(f"Orphan pages ({report['orphan_count']} — no incoming links):")
    for slug in report["orphan_pages"][:20]:
        print(f"  - {slug}")
    if report["orphan_count"] > 20:
        print(f"  ... and {report['orphan_count'] - 20} more")
    print()

    print(f"Dead-end pages ({report['dead_end_count']} — no outgoing links):")
    for slug in report["dead_end_pages"][:20]:
        print(f"  - {slug}")
    if report["dead_end_count"] > 20:
        print(f"  ... and {report['dead_end_count'] - 20} more")
    print()

    print(f"Broken links ({report['broken_count']}):")
    for bl in report["broken_links"][:20]:
        print(f"  - {bl['source']} -> [[{bl['target']}]]")
    if report["broken_count"] > 20:
        print(f"  ... and {report['broken_count'] - 20} more")


if __name__ == "__main__":
    wiki_dir = "wiki"
    output_json = False

    for arg in sys.argv[1:]:
        if arg == "--json":
            output_json = True
        elif arg.startswith("--wiki-dir"):
            wiki_dir = arg.split("=")[1] if "=" in arg else sys.argv[sys.argv.index(arg) + 1]

    report = audit_wiki(wiki_dir)

    if output_json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
