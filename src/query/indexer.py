"""Index wiki pages into Chroma by H2 section.

Each wiki page is split by ## headings. Each section becomes one Chroma
document with metadata (slug, title, domain, section_name, page_type).
Only 'active' pages are indexed — superseded/archived are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from src.query.config import CHROMA_COLLECTION_NAME, CHROMA_PERSIST_DIR, WIKI_CATEGORIES, WIKI_DIR

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown page."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fm_raw = match.group(1)
    body = text[match.end():]
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def split_by_h2(body: str) -> list[tuple[str, str]]:
    """Split markdown body into (section_name, section_body) pairs.

    Content before the first H2 is returned as ("lead", ...).
    """
    sections: list[tuple[str, str]] = []
    matches = list(_H2_RE.finditer(body))

    if not matches:
        stripped = body.strip()
        if stripped:
            sections.append(("lead", stripped))
        return sections

    lead = body[: matches[0].start()].strip()
    if lead:
        sections.append(("lead", lead))

    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_body = body[start:end].strip()
        if section_body:
            sections.append((heading, section_body))

    return sections


def iter_wiki_pages(wiki_dir: Path | None = None) -> list[dict]:
    """Iterate all wiki pages and return parsed metadata + sections."""
    wiki_dir = wiki_dir or WIKI_DIR
    pages = []

    for category in WIKI_CATEGORIES:
        cat_dir = wiki_dir / category
        if not cat_dir.exists():
            continue
        for page_path in sorted(cat_dir.glob("*.md")):
            if page_path.name == "index.md" or page_path.name.startswith(".") or page_path.name.startswith("_"):
                continue
            try:
                text = page_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                print(f"  SKIP (read error): {page_path.name} — {e}")
                continue
            fm, body = parse_frontmatter(text)

            if fm.get("status") in ("superseded", "archived"):
                continue

            slug = fm.get("slug", page_path.stem)
            title = fm.get("title", page_path.stem.replace("-", " ").title())
            domain = fm.get("domain", fm.get("domains", [""])[0] if isinstance(fm.get("domains"), list) else "")
            page_type = fm.get("page_type", category.rstrip("s"))
            tags = fm.get("tags", [])
            aliases = fm.get("aliases", [])
            summary = fm.get("summary", "")

            sections = split_by_h2(body)
            pages.append({
                "path": str(page_path),
                "slug": slug,
                "title": title,
                "domain": domain,
                "page_type": page_type,
                "category": category,
                "status": fm.get("status", "active"),
                "tags": tags,
                "aliases": aliases,
                "summary": summary,
                "sections": sections,
                "full_body": body,
            })

    return pages


def build_chroma_index(wiki_dir: Path | None = None, persist_dir: Path | None = None) -> "chromadb.Collection":
    """Build (or rebuild) the Chroma index from wiki pages.

    Each H2 section becomes one document with metadata for filtering.
    Returns the Chroma collection.
    """
    import chromadb

    wiki_dir = wiki_dir or WIKI_DIR
    persist_dir = persist_dir or CHROMA_PERSIST_DIR

    client = chromadb.PersistentClient(path=str(persist_dir))

    try:
        client.delete_collection(CHROMA_COLLECTION_NAME)
    except Exception:
        pass

    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    pages = iter_wiki_pages(wiki_dir)
    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    doc_count = 0
    for page in pages:
        for section_name, section_body in page["sections"]:
            doc_id = f"{page['slug']}::{section_name.lower().replace(' ', '-')}"
            doc_text = f"# {page['title']}\n## {section_name}\n{section_body}"

            documents.append(doc_text)
            metadatas.append({
                "slug": page["slug"],
                "title": page["title"],
                "page_type": page["page_type"],
                "category": page["category"],
                "domain": page["domain"],
                "section": section_name,
                "status": page["status"],
                "tags": ",".join(page["tags"]) if page["tags"] else "",
            })
            ids.append(doc_id)
            doc_count += 1

    if documents:
        batch_size = 100
        for i in range(0, len(documents), batch_size):
            collection.add(
                documents=documents[i : i + batch_size],
                metadatas=metadatas[i : i + batch_size],
                ids=ids[i : i + batch_size],
            )

    print(f"Indexed {doc_count} sections from {len(pages)} pages into Chroma")
    return collection


def build_wiki_tree(wiki_dir: Path | None = None) -> dict:
    """Build wiki_tree.json — a lightweight index of all pages.

    Loaded at session start for instant ls/find/keyword_search.
    Format: {category: {slug: {title, domain, type}}}
    """
    import json

    wiki_dir = wiki_dir or WIKI_DIR
    pages = iter_wiki_pages(wiki_dir)

    tree: dict[str, dict] = {}
    for cat in WIKI_CATEGORIES:
        tree[cat] = {}

    for page in pages:
        cat = page["category"]
        entry = {
            "title": page["title"],
            "domain": page["domain"],
            "type": page["page_type"],
        }
        # Tags/aliases/summary not yet in all pages — enable when frontmatter is backfilled
        # if page.get("tags"):
        #     entry["tags"] = page["tags"]
        # if page.get("aliases"):
        #     entry["aliases"] = page["aliases"]
        # if page.get("summary"):
        #     entry["summary"] = page["summary"]
        tree[cat][page["slug"]] = entry

    # Add domain summary
    domain_counts: dict[str, int] = {}
    for page in pages:
        d = page["domain"]
        if d:
            domain_counts[d] = domain_counts.get(d, 0) + 1

    tree["domains"] = {
        d: {"title": d.replace("-", " ").title(), "page_count": c}
        for d, c in sorted(domain_counts.items())
    }

    tree_path = wiki_dir / "wiki_tree.json"
    tree_path.write_text(json.dumps(tree, indent=2), encoding="utf-8")
    print(f"Built wiki_tree.json with {sum(len(v) for v in tree.values())} entries")

    return tree


def build_wiki_graph(wiki_dir: Path | None = None) -> dict:
    """Build wiki_graph.json — wikilink connections between pages.

    Scans all pages for [[...]] wikilinks and builds:
    {slug: {outgoing: [linked_slugs], incoming: [linking_slugs]}}
    """
    import json
    import re

    wiki_dir = wiki_dir or WIKI_DIR
    _WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')

    pages = iter_wiki_pages(wiki_dir)
    graph: dict[str, dict[str, list]] = {}

    for page in pages:
        slug = page["slug"]
        if slug not in graph:
            graph[slug] = {"outgoing": [], "incoming": []}

        body = page.get("full_body", "")
        fm_related = page.get("related", []) if isinstance(page, dict) else []

        outgoing = set()
        for match in _WIKILINK_RE.finditer(body):
            link = match.group(1)
            link_slug = link.split("/")[-1] if "/" in link else link
            if link_slug != slug:
                outgoing.add(link_slug)

        for rel in fm_related:
            if isinstance(rel, str):
                rel_clean = rel.strip("[]").split("/")[-1]
                if rel_clean != slug:
                    outgoing.add(rel_clean)

        graph[slug]["outgoing"] = sorted(outgoing)

        for target in outgoing:
            if target not in graph:
                graph[target] = {"outgoing": [], "incoming": []}
            if slug not in graph[target]["incoming"]:
                graph[target]["incoming"].append(slug)

    for slug_data in graph.values():
        slug_data["incoming"] = sorted(slug_data["incoming"])

    graph_path = wiki_dir / "wiki_graph.json"
    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    total_edges = sum(len(v["outgoing"]) for v in graph.values())
    print(f"Built wiki_graph.json with {len(graph)} pages, {total_edges} edges")

    return graph


def get_chroma_collection(persist_dir: Path | None = None) -> "chromadb.Collection":
    """Load existing Chroma collection (for querying)."""
    import chromadb

    persist_dir = persist_dir or CHROMA_PERSIST_DIR
    client = chromadb.PersistentClient(path=str(persist_dir))
    return client.get_collection(name=CHROMA_COLLECTION_NAME)
