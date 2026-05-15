---
name: qmd-usage
description: "How to use QMD hybrid search effectively. Load when using qmd_search tool."
---

## What is QMD?

QMD (Query Markdown Documents) is a local hybrid search engine that combines:
1. **BM25** — Full-text keyword search (finds exact word matches)
2. **Vector embeddings** — Semantic similarity search (finds conceptually similar content)
3. **RRF (Reciprocal Rank Fusion)** — Merges both ranked lists so pages matching BOTH keyword AND semantics rank highest

This is more powerful than either keyword search or vector search alone.

## How to Use qmd_search() Effectively

### DO:
- Use **specific terms**: `qmd_search("GLAdmin gin migration")`
- Use **multiple queries** with different angles:
  - `qmd_search("seller ISQ")` then `qmd_search("item searchable quantity")`
- Search for **acronyms AND full names**: `qmd_search("BL notification")` + `qmd_search("buylead notification")`

### DON'T:
- Don't use full sentences: `qmd_search("What are all the migrations happening?")` — BM25 will match irrelevant words like "are" and "the"
- Don't rely on a single query — always try 2-3 variants

## What QMD Indexes
- `wiki/topics/` — 434 concept pages
- `wiki/systems/` — 110 product/platform pages
- `wiki/policies/` — policy pages
- `wiki/decisions/` — decision pages
- `wiki/domains/` — 8 domain hub pages

**NOT indexed:** `wiki/people/` — For person lookups, use `keyword_search()` instead.

## Result Format
Each result has:
- `slug` — page identifier (use with `cat(slug)` to read full page)
- `title` — human-readable page name
- `score` — relevance score (1.0 = best match, 0.1 = weak match)

## When to Use QMD vs Other Search

| Need | Best Tool |
|------|-----------|
| Find ALL pages about a topic | qmd_search (comprehensive) |
| Fuzzy conceptual search | grep (pure semantic) |
| Exact name/title lookup | keyword_search (instant) |
| Find connected pages | related_pages (graph) |
