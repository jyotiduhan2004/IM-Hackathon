---
name: search-strategy
description: "How to effectively search IndiaMART's wiki. Load when you need to find information."
---

## How to Search This Wiki

You have 3 search tools + 1 graph tool. Each has different strengths:

### qmd_search(query) — Hybrid Search (BEST for comprehensive discovery)
- Combines BM25 keyword matching + vector semantic similarity + RRF fusion
- Call with **specific terms**, NOT full sentences: `qmd_search("GLAdmin migration")` not `qmd_search("What are the GLAdmin migrations?")`
- Returns up to 10 results ranked by combined score
- **Use multiple queries with different terms** for better coverage:
  - `qmd_search("GLAdmin migration")` + `qmd_search("gladmin gin framework")` + `qmd_search("admin API migration")`
- If you get 10 results (the max), there may be MORE — search with different terms
- Does NOT index people/ pages — use keyword_search for person lookups
- First call may be slow (~15s model loading). Subsequent calls ~1-2s.

### grep(query) — Semantic Vector Search
- Pure embedding-based similarity (Chroma, all-MiniLM-L6-v2)
- Good for fuzzy/conceptual queries: `grep("improving buyer experience")`
- Returns top 10 matches with section-level granularity (each H2 section indexed separately)
- May miss exact keyword matches that QMD catches

### keyword_search(query) — Instant Title Match
- Scans wiki_tree.json in memory (1,089 entries)
- Matches against slug, title text (case-insensitive substring)
- Instant (0ms), zero cost
- Use for: exact names you know, person lookups, verifying slugs exist

### related_pages(slug) — Wikilink Graph Traversal
- Returns pages that link TO this page (incoming) and pages this page links TO (outgoing)
- Use after finding a key page to discover connected pages
- Based on pre-built wiki_graph.json (all [[wikilinks]] extracted)

## Search Strategy by Query Type

| Question Type | Best Approach |
|--------------|---------------|
| "What is X?" | qmd_search(X) → cat best match |
| "List all X" | qmd_search(X) + keyword_search(X) + read domain hub |
| "Compare X vs Y" | qmd_search(X), qmd_search(Y) → cat both |
| "Who is X?" | keyword_search(X) → cat person page → follow "Appears in" links |
| "Who owns X?" | cat(topic page) → check owner: field |
| "What changed?" | cat('changes') for activity feed, or find(domain=X) + head each |

## IndiaMART Acronyms (common in wiki)
- BL = BuyLead (lead from buyer to seller)
- MCAT = Microcatalog (product categorization)
- ISQ = Item Searchable Quantity
- NI = Not Interested (buyer feedback)
- PNS = Phone Number Service (caller/callee)
- GLID = Global Login ID (seller identifier)
- TOV = Transaction Order Value
- FCP = Free Customer Profile
- CTA = Call To Action
- PDP = Product Detail Page
