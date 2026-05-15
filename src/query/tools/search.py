"""Chroma-based semantic search over wiki pages."""

from __future__ import annotations

from src.query.config import SEARCH_TOP_K
from src.query.indexer import get_chroma_collection


def search_wiki(query: str, top_k: int = SEARCH_TOP_K, domain: str | None = None) -> list[dict]:
    """Search wiki pages semantically via Chroma.

    Returns a list of matching sections with metadata.
    Optionally filter by domain.
    """
    collection = get_chroma_collection()

    where_filter = None
    if domain:
        where_filter = {"domain": domain}

    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where=where_filter,
    )

    hits: list[dict] = []
    if not results["documents"] or not results["documents"][0]:
        return hits

    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        distance = results["distances"][0][i] if results["distances"] else 0.0
        hits.append({
            "content": doc,
            "slug": meta.get("slug", ""),
            "title": meta.get("title", ""),
            "section": meta.get("section", ""),
            "domain": meta.get("domain", ""),
            "page_type": meta.get("page_type", ""),
            "score": round(1 - distance, 4),
        })

    return hits
