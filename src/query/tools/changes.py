"""Tools for searching decisions and recent changes."""

from __future__ import annotations

from pathlib import Path

from src.query.config import WIKI_DIR
from src.query.indexer import parse_frontmatter
from src.query.tools.wiki_reader import list_pages


def search_decisions(topic: str, wiki_dir: Path | None = None) -> list[dict]:
    """Find decision pages related to a topic by scanning decision slugs and bodies."""
    wiki_dir = wiki_dir or WIKI_DIR
    decisions_dir = wiki_dir / "decisions"

    if not decisions_dir.exists():
        return []

    results: list[dict] = []
    topic_lower = topic.lower()

    for page_path in sorted(decisions_dir.glob("*.md")):
        text = page_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)

        if topic_lower in text.lower():
            results.append({
                "slug": fm.get("slug", page_path.stem),
                "title": fm.get("title", page_path.stem),
                "status": fm.get("status", "active"),
                "body_preview": body[:300],
            })

    return results


def find_pages_missing_section(section_name: str, wiki_dir: Path | None = None) -> list[dict]:
    """Find pages that do NOT have a specific H2 section (for absence queries)."""
    wiki_dir = wiki_dir or WIKI_DIR
    all_pages = list_pages(wiki_dir=wiki_dir)
    missing: list[dict] = []

    for page_meta in all_pages:
        slug = page_meta["slug"]
        for category in ["topics", "systems", "policies"]:
            page_path = wiki_dir / category / f"{slug}.md"
            if page_path.exists():
                text = page_path.read_text(encoding="utf-8", errors="replace")
                if f"## {section_name}" not in text:
                    missing.append(page_meta)
                break

    return missing
