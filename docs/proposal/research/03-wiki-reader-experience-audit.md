# Wiki Reader-Experience Audit

Generated: 2026-04-15. Read-only research pass.

Source: full corpus survey of `wiki/` (709 pages), sampled 15 representative pages across types, scored on filing-cabinet vs useful-wiki axes.

---

## 1. Corpus statistics

| Category | Count | Stubs (<500B) | % Stubs | Avg body | Median |
|---|---:|---:|---:|---:|---:|
| Topics | 177 | 0 | 0.0% | 4,680B | 3,469B |
| Entities | 462 | 87 | 18.8% | 1,660B | 883B |
| Systems | 69 | 27 | 39.1% | 1,369B | 773B |
| Policies | 1 | — | — | — | — |
| Timelines | 0 | — | — | — | — |
| Conflicts | 0 | — | — | — | — |
| **Total** | **709** | **114** | **16.1%** | — | — |

Note: timelines and conflicts have ZERO pages despite being first-class categories in every strategy doc.

---

## 2. Reader-experience scoring (sampled 15 pages)

Filing-cabinet score: 1 = pure synthesis, 5 = email dump.
Useful-wiki score: 1 = useless to a new joiner, 5 = actually answers a question.

| Page | Type | Size | FC | UW | Pattern |
|---|---|---:|:---:|:---:|---|
| `auditmate-sellerim-integration.md` | topic | 21.1K | 4 | 3 | Email dump in tables |
| `bulk-filter-relevancy-improvement-desktop.md` | topic | 8.4K | 2 | 4 | Good synthesis with evolution |
| `isq-auto-upload-pipeline.md` | topic | 1.1K | 5 | 1 | Bare stub |
| `component-as-a-service.md` | topic | 8.4K | 2 | 4 | Well-structured conceptual |
| (mid-range topic) | topic | ~7K | 2 | 4 | Progressive disclosure |
| (random mid-range topic) | topic | ~3-4K | 3 | 3 | Thin synthesis |
| `ankit-jain-indiamart-com.md` | entity | 259B | 5 | 1 | Empty shell |
| `kundan-kishore-indiamart-com.md` | entity | 12.2K | 5 | 1 | Raw source list only |
| `divyanshu-singh-indiamart-com.md` | entity | 14.2K | 5 | 1 | Source citations, no synthesis |
| `sumitagarwal-indiamart-com.md` | entity | 16.7K | 5 | 1 | Listing only |
| `neeraj-agrawal-indiamart-com.md` | entity | 11.0K | 5 | 1 | Filing cabinet |
| `da-indiamart-com.md` | entity | 11.0K | 5 | 1 | Email source metadata |
| `dialogflow.md` | system | ~0.5K | 4 | 2 | Minimal definition |
| `imllm-endpoint.md` | system | ~0.8K | 3 | 3 | Thin but contextual |
| `pns-team.md` | system | ~0.6K | 5 | 1 | Email address only |

---

## 3. Common failure patterns

1. **Entity pages as source filing systems** (every sampled entity scored 5/1): pages list 80+ raw email citations in metadata but contain zero prose about role/decisions/contributions. A reader learns "this person appeared in these 80 emails" but not "who is this person and what do they do."
2. **Large topics that are email transcripts in tables** (e.g. `auditmate-sellerim-integration.md`): preserves verbatim bug matrices, test results, issue lists. Valuable as audit trail; reads like a compiled inbox. Prose is sparse; tables dominate. No "what does this mean" section.
3. **System pages as mailing-list stubs** (27 of 69 systems = 39.1% sub-500-byte): pages contain only "Email: X@company.com" or one-line definitions. References, not knowledge.
4. **Navigation as frontmatter, not prose**: inline wikilinks work, but readers must scan `related:` frontmatter or follow links to build context. Pages don't explain "why you should care" or "what's the current state."
5. **Landing pages are placeholders**: `topics/index.md`, `systems/index.md`, `home.md` all marked "(Placeholder — curated view comes in a later PR)". Zero discovery hubs.

---

## 4. Best-pattern exemplars (templates we should scale)

1. **`bulk-filter-relevancy-improvement-desktop.md`** (8.4K topic) — opens with business objective, explains background, details implementation, includes structured test scenarios + impact metrics, documents evolution (observations + proposals from multiple stakeholders), closes with owned next-steps. Accessible to a new joiner.
2. **`component-as-a-service.md`** (8.4K topic) — defines concept upfront, explains architecture with examples, compares alternatives (CaaS vs Module Federation vs NPM), walks through deployment strategies, documents POC status + leadership direction + next steps. Reads like an RFC.
3. **`imllm-endpoint.md`** (0.8K system) — minimal but meaningful: one-sentence definition, one Usage section linking to where it's actually used, cross-reference. Template for stub pages that might later grow.

---

## 5. Bottom-line score

**~15-20% of the corpus would pass a "useful wiki" bar.**

- ~0% of entity pages (all are filing cabinets)
- ~40% of topic pages (larger ones synthesize well; smaller are stubs)
- ~5% of system pages (almost all are stubs or mailbox references)

Landing pages (`home.md`, `topics/index.md`, etc.) are at 0% — placeholders are not a wiki yet.

---

## 6. Progressive disclosure — concrete recommendation

At page **TL;DR** (top 150 words): one-sentence definition ("X is a…"), current status (e.g. "Launched Jan 9, 2026"), 1-2 key links ("Learn more: [[related-topic]]"). No tables, no source metadata. Purpose: "Do I need to read this page?"

At **Detail** level: sections explaining "Why this exists," "How it works," "Current state," "Owned next steps." Include structured data (bug matrices, metric tables, test results) as markdown tables. Link to entities and systems. Purpose: "What's really happening and why should I act?"

At **Sources** level: collapsed `<details>` block (currently auto-generated by `mkdocs_hooks.py`) showing each raw email's headers + 2,500-char excerpt with role tags. Purpose: "Verify the claim against the original."

---

**Current state**: the wiki is a half-built email archive. Topics are closer to useful (many have synthesis + tables); entities and systems are filing cabinets. Landing pages don't exist yet. Compiler prompts optimize for "preserve tables + link entities" but not for "write like a human wiki reader might want to find it."
