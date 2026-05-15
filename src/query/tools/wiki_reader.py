"""Read-only tools for navigating the wiki."""

from __future__ import annotations

from pathlib import Path

from src.query.config import DOMAIN_HUBS, WIKI_CATEGORIES, WIKI_DIR
from src.query.indexer import parse_frontmatter, split_by_h2


def read_page(slug: str, wiki_dir: Path | None = None) -> dict | None:
    """Read a wiki page by slug. Returns frontmatter + full body, or None."""
    wiki_dir = wiki_dir or WIKI_DIR

    for category in WIKI_CATEGORIES:
        page_path = wiki_dir / category / f"{slug}.md"
        if page_path.exists():
            text = page_path.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(text)
            return {
                "slug": slug,
                "title": fm.get("title", slug),
                "page_type": fm.get("page_type", ""),
                "status": fm.get("status", "active"),
                "domain": fm.get("domain", ""),
                "tags": fm.get("tags", []),
                "body": body,
                "path": str(page_path),
                "related": fm.get("related", []),
            }
    return None


def get_page_summary(slug: str, wiki_dir: Path | None = None) -> dict | None:
    """Get a concise summary of a page: title, lead paragraph, H2 headings, source count."""
    page = read_page(slug, wiki_dir)
    if not page:
        return None

    sections = split_by_h2(page["body"])
    headings = [name for name, _ in sections if name != "lead"]

    lead = ""
    for name, body in sections:
        if name == "lead":
            lead = body[:300]
            break

    source_count = page["body"].count("[^msg-")

    return {
        "slug": slug,
        "title": page["title"],
        "page_type": page["page_type"],
        "status": page["status"],
        "domain": page["domain"],
        "lead": lead,
        "headings": headings,
        "source_count": source_count,
        "related": page["related"],
    }


def list_pages(
    domain: str | None = None,
    category: str | None = None,
    wiki_dir: Path | None = None,
) -> list[dict]:
    """List wiki pages, optionally filtered by domain or category.

    Returns concise metadata for each page (no body content).
    """
    wiki_dir = wiki_dir or WIKI_DIR
    results: list[dict] = []
    categories = [category] if category else WIKI_CATEGORIES

    for cat in categories:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for page_path in sorted(cat_dir.glob("*.md")):
            if page_path.name == "index.md" or page_path.name.startswith(".") or page_path.name.startswith("_"):
                continue
            text = page_path.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(text)

            if fm.get("status") in ("superseded", "archived"):
                continue

            page_domain = fm.get("domain", "")
            if domain and page_domain != domain:
                continue

            lead = ""
            sections = split_by_h2(body)
            for name, sec_body in sections:
                if name == "lead":
                    lead = sec_body[:200]
                    break

            results.append({
                "slug": fm.get("slug", page_path.stem),
                "title": fm.get("title", page_path.stem),
                "page_type": fm.get("page_type", cat.rstrip("s")),
                "domain": page_domain,
                "status": fm.get("status", "active"),
                "lead": lead,
            })

    return results


def list_domains() -> list[dict]:
    """List all 8 domain hubs with page counts."""
    all_pages = list_pages()
    domain_counts: dict[str, int] = {}
    for p in all_pages:
        d = p.get("domain", "uncategorized")
        domain_counts[d] = domain_counts.get(d, 0) + 1

    return [
        {"domain": d, "page_count": domain_counts.get(d, 0)}
        for d in DOMAIN_HUBS
    ]


def get_recent_changes(n: int = 10, wiki_dir: Path | None = None) -> list[dict]:
    """Get last N changes from wiki/changes.md, or fallback to page mtimes."""
    wiki_dir = wiki_dir or WIKI_DIR
    changes_path = wiki_dir / "changes.md"

    if changes_path.exists():
        text = changes_path.read_text(encoding="utf-8", errors="replace")
        lines = [l.strip() for l in text.splitlines() if l.strip().startswith("-") or l.strip().startswith("*")]
        return [{"entry": line.lstrip("-* ").strip()} for line in lines[:n]]

    pages = list_pages(wiki_dir=wiki_dir)
    return [{"slug": p["slug"], "title": p["title"]} for p in pages[:n]]
