---
name: article-structure
description: "Load when creating or updating a wiki page. Enforces flexible template, tags, aliases, glossary auto-linking, inline link context."
metadata:
  type: compile
  version: 1.0
---

## Flexible Template

### Mandatory Sections
Every page MUST have:
1. **Lead paragraph** (untitled, after H1) — 2-4 sentences: what this is, why it matters, current status
2. **Key Details** — the substantive content, organized by subtopic (NOT by email/date)
3. **Sources** — email references at the bottom, not cluttering every paragraph

### Standard Optional Sections
Add these ONLY when relevant content exists. Do not create empty sections:
- **Overview** — context for newcomers, assume zero prior knowledge
- **Current Status** — what's live, pilot percentages, key metrics
- **Decisions & Changes** — what was decided, by whom, when
- **Open Questions** — unresolved items, blockers
- **Related Pages** — cross-links to related wiki pages

## Frontmatter Requirements

```yaml
---
title: "Human-Readable Title"
page_type: topic          # topic | system | policy | decision
domain: marketplace-discovery  # one of 8 domains
status: active            # active | superseded | draft
tags: [seller, isq, quality]  # cross-cutting tags for discovery
aliases: [ISQ, "Seller ISQ"]  # common abbreviations/alternate names
summary: "One-line summary for agent head() tool"
related:
  - buyer-enrichment
  - buylead-quality
---
```

### Required fields: title, page_type, domain, status
### New fields to add: tags, aliases (if abbreviations exist), summary

## Content Rules

### Glossary Auto-Linking
First occurrence of a glossary term on each page should be linked:
- `GLID` → `[[glossary#GLID|GLID]]` (first occurrence only)
- Subsequent mentions of the same term stay as plain text

### Inline Link Context
First wikilink to another page should include a brief parenthetical:
- `[[buyer-trust]] (initiative to verify seller identity through document checks)` — first mention
- `[[buyer-trust]]` — subsequent mentions on the same page

### Writing Style (Wikipedia-Derived)
- Organize by SUBTOPIC, not by email thread or date
- Use inverted pyramid: most important information first in each section
- State context explicitly — assume the reader has zero prior knowledge of this topic
- Use tables for comparisons, lists for sequences, prose for narratives
- No promotional language, no superlatives ("groundbreaking", "revolutionary")
- Light on sources: reference section at bottom, not inline citations in every sentence

## Gotchas
- Don't force the template on existing pages (forward-only). Only apply to new pages.
- If a page has only 1-2 sentences of content, it's still valid — it will grow as more emails come in.
- The `summary` field is new. It should be a single sentence that an agent's head() tool can return for quick scanning.
