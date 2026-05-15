"""Filesystem-style tools for the Duckie query agent.

v3 Architecture (G-Brain inspired):
- wiki_tree.json + wiki_graph.json loaded at session start
- cat() reads pages with in-memory cache
- grep() uses Chroma, qmd_search() uses QMD hybrid BM25+vector
- load_skill() injects IndiaMART-specific knowledge into agent context
- related_pages() traverses wikilink graph
- No sub-agents — single LLM session with skill context injection
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from src.query.config import WIKI_DIR, WIKI_CATEGORIES

REPO_ROOT = Path(__file__).resolve().parents[3]

# ─── Wiki Tree (loaded once at import time) ───

_wiki_tree: dict[str, dict] = {}
_tree_loaded = False


def _load_wiki_tree() -> dict[str, dict]:
    global _wiki_tree, _tree_loaded
    if _tree_loaded:
        return _wiki_tree
    tree_path = WIKI_DIR / "wiki_tree.json"
    if _tree_loaded:
        return _wiki_tree
    if tree_path.exists():
        _wiki_tree = json.loads(tree_path.read_text(encoding="utf-8", errors="replace"))
    else:
        _wiki_tree = _build_tree_from_disk()
    _tree_loaded = True
    return _wiki_tree


def _build_tree_from_disk() -> dict:
    from src.query.indexer import iter_wiki_pages
    tree: dict[str, dict] = {}
    for cat in WIKI_CATEGORIES:
        tree[cat] = {}
    tree["domains"] = {}
    for page in iter_wiki_pages(WIKI_DIR):
        category = page["category"]
        slug = page["slug"]
        if category not in tree:
            tree[category] = {}
        tree[category][slug] = {
            "title": page["title"],
            "domain": page["domain"],
            "type": page["page_type"],
        }
    return tree


# ─── Wiki Graph (loaded once at import time) ───

_wiki_graph: dict[str, dict] = {}
_graph_loaded = False


def _load_wiki_graph() -> dict[str, dict]:
    global _wiki_graph, _graph_loaded
    if _graph_loaded:
        return _wiki_graph
    graph_path = WIKI_DIR / "wiki_graph.json"
    if graph_path.exists():
        _wiki_graph = json.loads(graph_path.read_text(encoding="utf-8", errors="replace"))
    _graph_loaded = True
    return _wiki_graph


# ─── Page Cache (per-session) ───

_page_cache: dict[str, str] = {}


def clear_page_cache():
    _page_cache.clear()


def _read_page_from_disk(slug: str) -> str | None:
    for category in WIKI_CATEGORIES:
        page_path = WIKI_DIR / category / f"{slug}.md"
        if page_path.exists():
            return page_path.read_text(encoding="utf-8", errors="replace")
    # Also check domains/ and root-level files
    for extra_dir in ["domains"]:
        page_path = WIKI_DIR / extra_dir / f"{slug}.md"
        if page_path.exists():
            return page_path.read_text(encoding="utf-8", errors="replace")
    return None


# ─── Tool Definitions ───

@tool
def ls(path: str = "") -> str:
    """Browse wiki directories. Examples: ls('topics'), ls('systems'), ls('domains'), ls('ai-automation'). Returns page titles and slugs."""
    tree = _load_wiki_tree()

    if path == "domains" or path == "":
        domain_counts: dict[str, int] = {}
        for cat_pages in tree.values():
            if isinstance(cat_pages, dict):
                for info in cat_pages.values():
                    if isinstance(info, dict):
                        d = info.get("domain", "")
                        if d:
                            domain_counts[d] = domain_counts.get(d, 0) + 1
        lines = ["Wiki domains:"]
        for d, count in sorted(domain_counts.items()):
            lines.append(f"  {d}: {count} pages")
        total = sum(domain_counts.values())
        lines.append(f"\nTotal: {total} pages across {len(domain_counts)} domains")
        return "\n".join(lines)

    if path in tree:
        pages = tree[path]
        lines = [f"{path}/ ({len(pages)} pages):"]
        for slug, info in sorted(pages.items()):
            title = info.get("title", slug) if isinstance(info, dict) else slug
            domain = info.get("domain", "") if isinstance(info, dict) else ""
            lines.append(f"  {slug} — {title} [{domain}]")
        return "\n".join(lines)

    results = []
    for cat, pages in tree.items():
        if isinstance(pages, dict):
            for slug, info in pages.items():
                if isinstance(info, dict) and info.get("domain") == path:
                    results.append((slug, info.get("title", slug), cat))
    if results:
        lines = [f"Pages in domain '{path}' ({len(results)} pages):"]
        for slug, title, cat in sorted(results):
            lines.append(f"  [{cat}] {slug} — {title}")
        return "\n".join(lines)

    return f"No directory or domain found for '{path}'. Try: ls('topics'), ls('systems'), ls('domains'), or a domain name like ls('ai-automation')."


@tool
def cat(slug: str) -> str:
    """Read a full wiki page by slug. Cached — repeated reads are free. Example: cat('weberp'), cat('seller-isq')."""
    if slug in _page_cache:
        return _page_cache[slug]
    content = _read_page_from_disk(slug)
    if content is None:
        return f"Page '{slug}' not found. Use grep() or keyword_search() to find the right slug."
    _page_cache[slug] = content
    return content


@tool
def grep(query: str) -> str:
    """Semantic search across all wiki pages using Chroma embeddings. Returns top matches. Example: grep('AI initiatives'), grep('ISQ rollout')."""
    from src.query.tools.search import search_wiki
    results = search_wiki(query, top_k=10)
    if not results:
        return f"No results found for '{query}'. Try different keywords or qmd_search('{query}')."
    lines = [f"Search results for '{query}' ({len(results)} matches):"]
    for r in results:
        lines.append(f"  {r['slug']} — {r['title']} (section: {r['section']}, domain: {r['domain']}, score: {r['score']})")
    return "\n".join(lines)


@tool
def keyword_search(query: str) -> str:
    """Search page titles by keyword match. Instant, no embeddings. Example: keyword_search('AI'), keyword_search('WebERP')."""
    tree = _load_wiki_tree()
    q_lower = query.lower()
    results = []
    for cat_name, pages in tree.items():
        if not isinstance(pages, dict):
            continue
        for slug, info in pages.items():
            if not isinstance(info, dict):
                continue
            title = info.get("title", "")
            if q_lower in slug.lower() or q_lower in title.lower():
                results.append((slug, title, info.get("domain", ""), cat_name))
    if not results:
        return f"No pages match '{query}' in titles. Try grep('{query}') or qmd_search('{query}')."
    lines = [f"Keyword matches for '{query}' ({len(results)} pages):"]
    for slug, title, domain, cat in sorted(results):
        lines.append(f"  [{cat}] {slug} — {title} [{domain}]")
    return "\n".join(lines)


@tool
def find(domain: str = "", page_type: str = "") -> str:
    """Find pages by domain and/or type. Examples: find(domain='ai-automation'), find(page_type='system'), find(domain='seller-experience', page_type='topic')."""
    tree = _load_wiki_tree()
    results = []
    for cat, pages in tree.items():
        if not isinstance(pages, dict):
            continue
        if page_type and cat != page_type + "s" and cat != page_type:
            continue
        for slug, info in pages.items():
            if not isinstance(info, dict):
                continue
            if domain and info.get("domain") != domain:
                continue
            results.append((slug, info.get("title", slug), info.get("domain", ""), cat))
    if not results:
        return f"No pages found for domain='{domain}' type='{page_type}'. Try ls('domains') to see available domains."
    lines = [f"Found {len(results)} pages:"]
    for slug, title, dom, cat in sorted(results):
        lines.append(f"  [{cat}] {slug} — {title} [{dom}]")
    return "\n".join(lines)


@tool
def head(slug: str) -> str:
    """Quick page summary: title, lead paragraph, section headings. 8x cheaper than cat(). Example: head('seller-isq')."""
    from src.query.indexer import parse_frontmatter, split_by_h2
    content = _read_page_from_disk(slug)
    if content is None:
        return f"Page '{slug}' not found."
    fm, body = parse_frontmatter(content)
    sections = split_by_h2(body)
    lead = ""
    headings = []
    for name, sec_body in sections:
        if name == "lead":
            lead = sec_body[:300]
        else:
            headings.append(name)
    source_count = body.count("[^")
    related = fm.get("related", [])
    owner = fm.get("owner", "")
    return (
        f"Title: {fm.get('title', slug)}\n"
        f"Type: {fm.get('page_type', '?')} | Domain: {fm.get('domain', '?')} | Status: {fm.get('status', '?')}\n"
        f"Owner: {owner}\n"
        f"Lead: {lead}\n"
        f"Sections: {', '.join(headings)}\n"
        f"Sources: {source_count} email references\n"
        f"Related: {', '.join(str(r) for r in related[:5])}"
    )


@tool
def qmd_search(query: str) -> str:
    """Hybrid search combining BM25 keyword matching + vector similarity (powered by QMD).
    Better than grep() for finding ALL pages about a topic.
    Use specific terms, not full sentences. Example: qmd_search('GLAdmin migration'), qmd_search('WhatsApp integration')."""
    from src.query.tools.qmd_search import qmd_search as _qmd_search
    results = _qmd_search(query, limit=10)
    if not results:
        return f"QMD search unavailable or no results for '{query}'. Try grep('{query}') instead."
    lines = [f"Hybrid search results for '{query}' ({len(results)} matches):"]
    for r in results:
        lines.append(f"  {r['slug']} — {r['title']} (score: {r['score']})")
    return "\n".join(lines)


@tool
def load_skill(name: str) -> str:
    """Load IndiaMART-specific knowledge into context. Skills contain info the LLM doesn't have.
    Available: search-strategy, contact-finder, wiki-navigation, qmd-usage, indiamart-context, gap-tracker.
    Call when you need specialized knowledge about how to search this wiki, find contacts, or navigate IndiaMART's structure."""
    skill_path = REPO_ROOT / "skills" / "query" / name / "SKILL.md"
    if not skill_path.exists():
        return f"Skill '{name}' not found. Available: search-strategy, contact-finder, wiki-navigation, qmd-usage, indiamart-context, gap-tracker"
    content = skill_path.read_text(encoding="utf-8", errors="replace")
    fm_end = content.find("---", content.find("---") + 3)
    if fm_end != -1:
        content = content[fm_end + 3:].strip()
    return content


@tool
def related_pages(slug: str) -> str:
    """Find pages connected to this page via wikilinks. Shows incoming links (pages that reference this one) and outgoing links (pages this one references). Example: related_pages('seller-isq')."""
    graph = _load_wiki_graph()
    entry = graph.get(slug)
    if not entry:
        return f"No wikilink data for '{slug}'. The page may exist but has no connections in wiki_graph.json."
    outgoing = entry.get("outgoing", [])
    incoming = entry.get("incoming", [])
    lines = [f"Wikilink connections for '{slug}':"]
    if outgoing:
        lines.append(f"\nOutgoing links ({len(outgoing)} pages this references):")
        for link in outgoing[:15]:
            lines.append(f"  → {link}")
    if incoming:
        lines.append(f"\nIncoming links ({len(incoming)} pages that reference this):")
        for link in incoming[:15]:
            lines.append(f"  ← {link}")
    if not outgoing and not incoming:
        lines.append("  No connections found (orphan page).")
    return "\n".join(lines)


@tool
def quality_check(question: str, answer: str, pages_read: str) -> str:
    """Verify answer quality. Pass the question, your answer, and comma-separated page slugs.
    Returns quality badges. Use after complex answers when you want verification."""
    from src.query.config import create_llm
    slugs = [s.strip() for s in pages_read.split(",") if s.strip()]
    page_summaries = []
    for slug in slugs[:10]:
        content = _page_cache.get(slug, "")
        if content:
            lines = [l for l in content.split("\n") if l.strip() and not l.startswith("---")][:5]
            page_summaries.append(f"[[{slug}]]: {' '.join(lines)[:300]}")

    qc_prompt = f"""\
Review this answer and return ONLY quality badges (one line each):

Question: {question}
Answer: {answer[:2000]}
Pages read: {chr(10).join(page_summaries)}

Checks:
1. SOURCES: Are wiki pages cited as [[slug]]? → ✅ Sources cited or ❌ Missing sources
2. ACCURACY: Do claims match page content? → ✅ Claims verified or ⚠️ Unverified claims
3. COMPLETENESS: Were relevant pages covered? → ✅ Comprehensive or ⚠️ May be incomplete
4. FORMAT: Appropriate format? → ✅ Format OK or ⚠️ Format mismatch

Return ONLY the 4 badge lines."""

    llm = create_llm()
    response = llm.invoke(qc_prompt)
    return response.content if response.content else "Quality check unavailable"


ALL_TOOLS = [ls, cat, grep, keyword_search, find, head, qmd_search, load_skill, related_pages, quality_check]
