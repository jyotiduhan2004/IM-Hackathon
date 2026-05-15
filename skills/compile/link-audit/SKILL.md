---
name: link-audit
description: "Load post-compilation to audit wikilink graph. Finds orphan pages, dead-end pages, broken links. Suggests cross-links."
metadata:
  type: compile
  version: 1.0
---

## When to Run
After each compilation batch completes. Run `scripts/audit_graph.py` to get a report.

## What It Checks

### 1. Orphan Pages (0 incoming links)
Pages that no other page links to. These are invisible to navigation.
- **Fix:** Add a link from the domain hub page (wiki/domains/*.md)
- **Fix:** Add a link from a related topic page's "Related Pages" section

### 2. Dead-End Pages (0 outgoing links)
Pages that don't link to anything else. These trap readers.
- **Fix:** Add a "Related Pages" section with 2-3 relevant wikilinks
- **Fix:** Add inline wikilinks where related topics are mentioned in the text

### 3. Broken Links (target doesn't exist)
Wikilinks pointing to pages that don't exist (red links).
- **Fix:** Find the closest matching slug and update the link
- **Fix:** If no match exists, remove the broken link or create a stub page

### 4. Weak Hub Pages
Domain hub pages that don't link to all pages in their domain.
- **Fix:** Regenerate hub pages to include all active pages

## Priority
- Broken links: fix immediately (they confuse both agents and humans)
- Orphan pages: fix in next compilation batch
- Dead-end pages: fix when the page is next updated
- Weak hubs: regenerate periodically

## Gotchas
- People pages (wiki/people/) are intentionally low-connectivity. Don't flag them as orphans.
- Some broken links are to pages that were superseded. Check if a redirect/alias should be added instead.
- The audit script outputs JSON for programmatic consumption and a human-readable summary.
