---
title: "Persona deep audit — deployed wiki, 2026-04-28"
audit_kind: persona-blind
deployed_url: https://email-kb-viewer-kntbneg73q-el.a.run.app
revision: email-kb-viewer-00013-nmn
post_pr: 246
---

# Persona deep audit — deployed wiki (2026-04-28)

## Setup

Ran 5 personas against the deployed reader (Cloud Run revision `email-kb-viewer-00013-nmn`, post-#246). Each persona was given a single goal and let loose on the published wiki — no prior knowledge of structure, no curated path. Verdicts: PASS = goal accomplished without friction; PARTIAL = answer reachable but path was indirect or required inference; FAIL = persona could not satisfy the goal.

| # | Persona | Goal | Verdict |
|---|---|---|---|
| P1 | New-joiner engineer | "What is MCAT?" (definition + role in IndiaMART) | PARTIAL |
| P2 | Product manager | "Current rollout state of WhatsApp 9696, who owns it, what's open" | PASS |
| P3 | Engineer joining Lens team | "What is Lens.IndiaMART, and what's been happening recently" | PARTIAL |
| P4 | Trust & Safety analyst | "Current logic for blocking sellers on GST/KYC" | PASS (with broken provenance) |
| P5 | Search-first lurker | "What does 'BL' mean?" (acronym discovery) | PARTIAL |

Headline: **2 PASS, 3 PARTIAL, 0 FAIL** — the wiki *can* be used; navigation works; pages exist. The PARTIALs all share one cause: missing foundational reference layer (glossary, owner/DRI fields, recent-activity feed per system).

## P1 — New-joiner engineer · MCAT

Goal: figure out what MCAT is and how it fits into IndiaMART's marketplace.

Path: Home → "Marketplace & Discovery" hub (blurb mentions MCAT) → 41 topic listings, all using MCAT but none defining it → search "MCAT" → 204 hits, all specific projects.

Findings:
- **No glossary surface.** A `wiki/glossary.md` was deliberately removed 2026-04-24 (regex extractor produced misleading defs). Nothing replaced it for cases where the persona needs the definition, not the activity log.
- **`systems/dir.md` has the closest thing** to a one-liner ("Mcat (Master Catalog) pages"), but the persona has no signal to navigate there from the search results.
- **Search returns activity, not definitions.** Top 5 hits are all rollout/A-B test topics. The new-joiner has to infer "MCAT = master category" from many incidental mentions.

Verdict: **PARTIAL**. The persona can eventually triangulate, but every other persona-blind audit at IndiaMART would do the same triangulation. A 5-line glossary entry would close this.

## P2 — Product manager · WhatsApp 9696

Goal: identify the current rollout state, owner, and open items for WhatsApp 9696.

Path: Home → AI Agents & Automation hub → top page `whatsapp9696-agentic-buyer-chatbot` → page reveals everything in one read.

What worked:
- TL;DR-quality `Overview` paragraph
- `Version 2.0 (Current)` section with launch date, coverage (10% of GLIDs ending in 9), target (40%)
- **Performance metrics block** — intent accuracy 93%, resolution 85%, p95 latency ~11s vs target <5s
- `Open Items` section ("CSAT implementation status — raised by mohak-saxena…")
- Tech team listed by role: V1.2 devs, V2.0 devs, audits, testing, direction & guidance
- Bug matrix with ticket IDs + statuses
- Management feedback quoted from named senders

What didn't:
- **No `owner:` / `DRI:` frontmatter.** Team is listed in body prose under "Development Team". A PM wanting "who do I ping?" has to read multiple sub-sections to figure out the right name. Confirms F-066.
- **No `target_date:` on open items.** "CSAT implementation status" has no by-when. Confirms F-068.
- **Dual `## Related` body section + frontmatter `related:`** — both render below the page. Confirms F-069.

Verdict: **PASS**. Best-quality page seen in the audit. The depth gap (F-065/F-066/F-068) is real but the page is still actionable.

## P3 — Engineer joining Lens team · Lens.IndiaMART

Goal: understand what Lens is and what's been happening recently.

Path: directly to `/systems/indiamart-lens/` (post-#241 consolidation).

What worked:
- TL;DR present and clear
- Overview links out to 4 specific topics (hybrid search, logged-in flow, city filters, right-click extension)
- `Related` lists 8 wikilinks
- 3 source threads listed at the bottom

What didn't:
- **No "current rollout state" or "what changed last week" feed.** Engineer must click through 8 related topics to assemble a recent-activity timeline.
- **No team / owner / DRI.** Lens has had multiple launches; not clear who runs it now.
- The `Browser Extension` H2 is *one* feature buried in a system page that should have a higher-level "what is shipping where" map.

Verdict: **PARTIAL**. Page is good as a primer, missing as a "what's the state of play" reference. Same gap class as P2.

## P4 — Trust & Safety analyst · KYC blocker

Goal: find current logic for blocking non-compliant sellers on GST/KYC.

Path: Home → Trust, Safety & Compliance hub → top page `centralized-gst-kyc-blocker-logic`.

What worked:
- `Overview`, `Business Objective`, `Story So Far` (earlier flow), `Implementation Details`, per-platform rollout (Android v13.7.1), per-user-type blocker logic (Paid vs Free seller, BuyLeads / BMC / Bizfeed / LMS), test cases (9 total, 2 failed), bugs identified with ticket IDs (644922, 644892), expected impact.
- Persona has everything needed to write a policy summary or audit report.

What didn't:
- **Source link broken in the deployed bundle.** Page footer says: `📚 Sources (1) raw/2026-04-06_launchim-mplaunchim-indiamart-android-app-1371c_6ed8f857.md (file missing)`. The `raw/` rsync didn't include this file or the link logic doesn't resolve. Provenance traceability is part of the trust contract — this is a real find.
- **`Related` section lists 8 *people*, not topics or systems.** A T&S analyst needs to see linked policies / decisions / related Trust+Safety topics — getting names instead is wrong.

Verdict: **PASS** for content quality, but **FAIL on provenance**. The "I want to verify the source email" handle is broken.

## P5 — Search-first lurker · "BL"

Goal: discover what the acronym BL means.

Path: search "BL" from any page.

Findings:
- 330 matching documents
- Top 5 results: `BL Search…`, `Search Context to BL/Enquiry Forms`, `HRS attribute blacklist…`, `Blacklist Data Insertion…`, `Bulk Blacklist…`. **Substring-matching false positives** ("blacklist", "blocking") flood the relevant hits.
- The first result's snippet *does* contain "BuyLead (BL)" so a careful reader could infer the meaning without clicking through.
- No glossary surface to land on.

Verdict: **PARTIAL**. Inferable but not first-class.

## Cross-cutting structural findings

### S1 (structural)

**[NEW] Source files broken on deploy.** P4's "(file missing)" is reproducible: `make publish` rsyncs `raw/` and `wiki/`, but page-rendering links to specific `raw/*.md` files seem to 404 in the viewer. Either the rsync excluded them, or the viewer's resolver doesn't map `raw/...` to the deployed bucket path. **Severity: S1** — this is the wiki-as-evidence contract.

**[CONFIRMED F-070, scope] 159 pages "Uncategorized" on home.** Page count from the regenerated index: `Uncategorized 159 pages total`. That's ~37% of the topic+system corpus with no `domain:` / `domains:` / matching `tags:`. Backfill work, but the home-page card surfaces the gap.

**[NEW] Wikilink leakage in domain hub snippets.** `/domains/` index shows a domain card preview ending with raw `[[topic...]]` syntax instead of resolved title — first-paragraph extractor leaks unresolved wikilinks. **Severity: S2.**

**[CONFIRMED F-064/F-066] Owner / DRI / target-date are body-only.** Both P2 and P3 hit this. Frontmatter has `domain:`, `status:`, `source_threads:` — but no `owner:`, no `dri:`, no `target_date:`. The structured query "who owns X" is not answerable.

### S2 (quality)

**[NEW] Search relevance — substring vs token.** P5's "BL" returned 330 matches because the lunr-style search treats "BL" as a substring matched inside "blacklist". For 2-3 letter acronyms (BL, BLNI, ISQ, MCAT) this is a recurring problem. Material for MkDocs's instant-search has a `min_search_length` knob worth tuning, or pre-tokenized aliases would help.

**[CONFIRMED F-069] Tags duplicate Related.** P2's WhatsApp 9696 page shows the SAME list of items twice — once under a `Tags` heading (the body-rendered tags) and once under `Related` (the frontmatter list). Reader sees a long list, then the same long list again.

**[CONFIRMED F-090 corollary] Person-only "Related".** P4's KYC page Related is 8 people, no topics or systems. The `Related` section semantics drift between pages (sometimes wikilinks to topics, sometimes a name list, sometimes both). No discipline.

**Domain hub metadata reads weirdly.** `/domains/marketplace-discovery/` shows: "Active · 0 sources · last compiled unknown · status: active". Hub pages are coordinator-generated; treating them like regular pages with `0 sources` reads like a bug to readers. Should either drop the meta block on hubs or fill it with "auto-generated · last build 2026-04-28".

### S3 (polish)

- "Recent changes" on home is the same list of 10 pages from earlier today. After the first compile of the morning every entry is dated identically — dedup or by-week grouping would land better.
- Domain hub blurbs (`BuyMer, BuyLeads, search UX, and buyer-side WhatsApp.`) are sentence-fragments not real overviews. Each hub deserves a 1-paragraph intro.

## What would close the PARTIALs

In rough leverage order:

1. **Glossary surface, but evidence-based.** A `wiki/glossary.md` (or per-letter pages) generated by an LLM pass over the corpus, not regex. Closes P1 and P5.
2. **`owner:` and `dri:` frontmatter, validated.** Closes the "who runs this?" gap on every PASS-content/missing-meta page. Already in F-066.
3. **`raw/` bucket rsync fix.** Trace whether `make publish` actually pushes raw or whether the `raw/...` link in pages needs a path-rewrite to the deployed structure. Verify on P4.
4. **Tighter "Related" section discipline.** Topic/system pages should split into `Related topics`, `Related systems`, `Mentioned people`. The current melted mass loses structure.
5. **Search tokenization for 2-letter acronyms.** Either pre-pretokenize known acronyms as their own search alias rows, or raise `min_search_length` and add a glossary fallback prompt.

## Headline

**Wiki is usable today** — every persona reached *something*, and the structurally-expensive build (compile pipeline, domain hubs, landing pages, search) all works. The PARTIALs are about **depth and discipline**, not navigation. F-089 is closed (this audit is the proof). The next visible cliff for users is the glossary + ownership gap.
