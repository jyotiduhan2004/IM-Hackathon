---
title: "Dual-persona deep audit — run ee90d7ce (2026-04-29)"
audit_kind: persona-deep-dual
run_id: ee90d7ce
sample_size: 10 of 26 pages (4 system + 6 topic)
personas:
  - "PM (director-forwardability)"
  - "Engineer (week-2 ramp-up)"
post_pr: post-#281 (qmd 60s), post-#282 (boy-scout repair), post-#284 (References backfill)
compare_to: run-8fa45533, run-5928c151, run-3e88f996
---

# Dual-persona deep audit — run ee90d7ce

## Sample selection (10 of 26)

All 4 system pages + 6 topics from scorer ranks (`/tmp/run-ee90d7ce-scorer/scorer-2026-04-29.csv`):

| Slug | Type | Mean | Tier |
|---|---|---|---|
| systems/auditmate | system | n/a | — |
| systems/buyermy | system | n/a | — |
| systems/indiamart-n8n-nodes | system | n/a | — |
| systems/photosearch | system | n/a | — |
| contextual-blni-feedback | topic | 8.6 | top |
| business-credentials-filter-search-api | topic | 8.4 | top |
| msite-search-product-card-redesign-ab-test | topic | 7.4 | mid |
| msite-mcat-html-rewrite-city-pages | topic | 7.2 | mid |
| dir-desktop-mcat-price-widget-qna-faq-schema-poc | topic | 6.6 | bottom |
| whatsapp-photo-buylead-optimization | topic | 6.4 | bottom |

Scoring: 0–3 per persona. PM = director-forwardable as-is (3) → needs full rewrite (0). Engineer = ramp-ready (3) → still needs to ask 5 questions (0).

## Per-page scores

| Slug | PM | Eng | Rationale |
|---|---|---|---|
| systems/auditmate | **2** | **2** | PM: detailed feature catalogue with usage gaps + business-team feedback dated 2026-02-02; **owner missing in frontmatter**; lead is platform description, not state+date+metric. Eng: API response sample (`price_unit_absurd` JSON), Kibana dashboard URL, dashboard URL (`audit.indiamart.com`); no repo, no namespace, no auth. |
| systems/buyermy | **1** | **0** | PM: lead is a one-liner ("Buyer platform at IndiaMART") — no current state, no metric, no date. Page is a launch-list, not a system reference. **Owner missing**. Eng: zero technical surface — no APIs, no endpoints, no namespaces. Lowest-quality system page in the sample. |
| systems/indiamart-n8n-nodes | **3** | **2** | PM: full 5W lead — what (community node), when (2026-02-05), credentials URL, status (security hardening in progress); owner in frontmatter; gated open question with target date 2026-02-12. Eng: credentials URL named, two operations specified, n8n ecosystem context (500k workflows). No repo URL, no auth contract, no rate-limit. |
| systems/photosearch | **1** | **1** | PM: no lead paragraph (jumps straight to `## Overview`); no current state, no metric, no date; **owner missing**. Eng: components named (Qdrant, updation pipeline, object detection) with wikilinks, but no technical detail — looks more like a TOC than a system reference. |
| contextual-blni-feedback | **3** | **2** | PM: exemplary lead — what+when (2026-01-27)+platforms+metric (+2.09% txns)+90% accuracy; gated open questions; named owner. Eng: real engineering signals — `eto_ofr_rejected` table, `reject_reason = 17` / `= 1` codes, 3 platform bug IDs (640565, 638983, 638981) with status. No repo, no API path, no Kibana. |
| business-credentials-filter-search-api | **3** | **2** | PM: lead leads with state+date+50%-real-estate metric; gated open question with owner+target. Eng: API field path (`filterData → businessCredentials`) and dynamic-render contract (`label`, `options[].displayName`). No repo / endpoint URL / payload sample. (Same scores as 5928c151 / 8fa45533 — page is unchanged.) |
| msite-search-product-card-redesign-ab-test | **3** | **1** | PM: A/B-test exemplar — variant assignment (GA IDs ending 2 ≈10% traffic), 5 numbered metrics (+7.44% PDP, −52.73% Company, −5.93% Enq+Call, +1.02% per-PV), Gestalt-rooted root cause, named-iteration recommendations. Owner in FM. Eng: only frontend + analytics anchors; no flag, no repo, no GA experiment ID, no event names. |
| msite-mcat-html-rewrite-city-pages | **3** | **2** | PM: lead has GA, 100% rollout, 52% payload reduction, before/after byte counts; gated open question with owner+target. Eng: real numbers (45.3→20.8 KB, 27.2 KB breakdown by AppShell/Critical CSS/MCAT/HTML), `m.indiamart.com/city/*` route pattern, bug ID 643410, GLIDs and devices for QA. No repo, no Varnish config, no CWV before-numbers. |
| dir-desktop-mcat-price-widget-qna-faq-schema-poc | **2** | **2** | PM: scope-honest ("POC live on 1 MCAT page"); CWV numbers (LCP 1.1s, CLS 0.01, INP 102ms); owner+target for rollout. Lead reads as launch-snapshot — no leadership-pending block. Eng: Rich Results Test URL, sample URLs, CWV with field/lab framing, bug ID 643266, testlink URL. Best Eng surface among bottom-tier topics. No repo, no schema markup snippet. |
| whatsapp-photo-buylead-optimization | **2** | **1** | PM: tradeoff explicit (Photo BL 6→16%, calls 27.7→26.8%); thread-closure event documented (2026-02-12). Lead has 5W. Owner present. Eng: flow described in prose only (5-step before/after), no chatbot config, no MCAT-conditional rule, no nudge-timer infra, no `is_auto_responder_enabled` style flag. 5 bare bug IDs (642655, 642531, 642668, 638983, 640565). |
| **Mean** | **2.30** | **1.50** | |

## Headline

- **Director-forwardable (PM ≥ 2):** **8 / 10** (80%). PM = 3 on **5 of 10** pages.
- **Ramp-ready (Eng ≥ 2):** **6 / 10** (60%). Eng = 3 on **0 of 10** — first regression on the ceiling vs. 8fa45533 (which had 1).
- **Both PM ≤ 1 cases are system pages** (`buyermy`, `photosearch`). Topic floor held — 0 topics at PM ≤ 1.
- **Top page:** `contextual-blni-feedback` and `indiamart-n8n-nodes` (PM=3). The n8n page is the cleanest small-system template; the BLNI page is the cleanest A/B-impact topic.

## 3-run comparison

| Metric | 5928c151 | 8fa45533 | ee90d7ce | Δ vs prior |
|---|---|---|---|---|
| PM mean | 2.13 | 2.50 | **2.30** | −0.20 |
| Eng mean | 1.50 | 1.88 | **1.50** | −0.38 |
| PM=3 ceiling | 1/8 | 4/8 | 5/10 | flat at 50% |
| Eng=3 ceiling | 0/8 | 1/8 | **0/10** | regression of −1 |
| PM ≥ 2 (forwardable) | 8/8 | 8/8 | **8/10** | regression: 2 system pages fall below |
| Eng ≥ 2 (ramp) | 4/8 | 6/8 | 6/10 | proportionally flat |

PM ceiling held at ~50% PM=3 (good — V12 lead-paragraph contract is durable). PM mean dipped because two legacy / sparsely-touched system pages (`buyermy`, `photosearch`) lack the V12 lead-paragraph and `owner:` field. Engineer ceiling fell back to 0 — the 8fa45533 Eng=3 was driven entirely by `buyleads-ni-grid-apis` having Swagger URLs + GKE flag + GLADMIN path, and **no system page in this run carries that density**. Without an `engineering_surface:` frontmatter contract, system pages drift to whatever the agent last wrote.

## All-4-systems verdict

This is the first time we have 4 system pages in a single audit. **Quality is bimodal, not consistent.**

- **Strongest:** `indiamart-n8n-nodes` — written under the V12 contract: lead has 5W in one paragraph, owner in frontmatter, gated open question with target date, `## Why it matters` / `## Open questions` / `## Recent changes` / `## References` template followed exactly. PM=3 / Eng=2.
- **Strong:** `auditmate` — long, detailed, real engineering signals (JSON sample, Kibana URL, dashboard URL, 4 detection-gap tables). Misses owner-in-frontmatter and a clean lead paragraph. PM=2 / Eng=2.
- **Weakest:** `buyermy` — one-line lead, no owner, no metric, no Why-it-matters, no Open-questions, no References block. Reads as an index, not a reference. PM=1 / Eng=0.
- **Weak:** `photosearch` — has structure but no lead paragraph (starts with `## Overview`), no owner, no Why-it-matters, no Recent-changes, no References block. Looks like a TOC of components, not a system reference. PM=1 / Eng=1.

The split is sharp: **2 of 4 systems follow the V12 system-page template (`auditmate`, `indiamart-n8n-nodes`); 2 of 4 are pre-V12 stubs that never got rewritten** (`buyermy`, `photosearch`). The agent is not maintaining system pages on the same contract as topic pages.

| System page | Why-it-matters | Open-questions | Recent-changes | References | Owner FM | Lead paragraph (V12) |
|---|---|---|---|---|---|---|
| auditmate | NO | YES | YES | YES | NO | partial |
| buyermy | NO | NO | NO | NO | NO | NO |
| indiamart-n8n-nodes | YES | YES | YES | YES | YES | YES |
| photosearch | NO | NO | NO | NO | NO | NO |

The `Recent changes` + `References` columns map exactly to whether the agent touched the page in the V12+#284 era. **`buyermy` and `photosearch` weren't compiled in this run** — they're being included in the page list as pre-existing system pages, not freshly produced. The compile pass touched topic pages that *reference* these systems (`responsive-layout-buyermy`, `track-order-buyermy-desktop`, `llm-object-detection-photosearch`) but the system pages themselves were not refreshed.

## Cross-domain wikilinks (system ← topic)

**Improving.** 6 of 22 topic pages have a `[[<system>]]` wikilink to one of the 4 systems in this run:

| Topic | Links to |
|---|---|
| auditmate-location-biztype-checks | auditmate (3×) |
| llm-object-detection-photosearch | photosearch (2×) |
| responsive-layout-buyermy-multi-resolution | buyermy (2×) |
| track-order-buyermy-desktop | buyermy (2×) |
| human-product-edit-rights-revocation | auditmate (2×) |
| foreign-buyer-name-capture-msite | buyermy (1×) |

`whatsapp-photo-buylead-optimization` references `[[system/whatsapp-9696-bot]]` in frontmatter `related:`. So the topic→system bidirectional graph is forming when the topic *is about* the system. The 8fa45533 finding ("topics don't link to systems even when relevant") is partially closed.

**Still missing:** `dir-desktop-mcat-price-widget-qna-faq-schema-poc` mentions `dir.indiamart.com` and the DIR catalog system — but only links `[[dir-indiamart-com]]` (a person/email page), not a `[[systems/dir]]`. There is no `dir` system page yet, so this is an absence-of-page issue, not an absence-of-link issue.

## Carry-over deficiencies (n=10)

| Item | 5928c151 | 8fa45533 | ee90d7ce |
|---|---|---|---|
| `engineering_surface:` frontmatter | 0/8 | 0/8 | **0/10** — no movement |
| Bare ticket integers (vs WP URLs) | 7/8 had at least one bare | 5/8 | **4/10 with bare; 0/10 with WP URL** — auto-linker still not shipped |
| Diagrams (mermaid/graph) | 0/8 | 0/8 | **0/10** — none |
| Owner missing in frontmatter | 2/8 | 2/8 | **3/10** — driven by stale system pages (`auditmate`, `buyermy`, `photosearch`) |
| System pages missing Why-it-matters / Open-questions consistency | 1 of 2 systems | 1 of 2 systems | **2 of 4 systems** — gap got worse with more systems in scope |

`bare ticket` count this run: contextual-blni-feedback (1), business-credentials (5), msite-search-card (2), whatsapp-photo (5). Total 13 bare integers across 4 pages, **0 turned into URLs**. Same gap as 5928c151. PR-B (ticket auto-linker) still hasn't shipped.

## Three quotes per persona

### PM — strong (forwardable as-is)

`indiamart-n8n-nodes`, lead:

> "IndiaMART n8n nodes are community-contributed nodes for the n8n open-source workflow automation platform. They provide two operations: IndiaMART Search […] and IndiaMART Post Requirement […]. Launched on 2026-02-05 and available for installation in n8n instances; security hardening in progress to prevent scraping and unfair usage."

What works: small-system 5W in one paragraph — what (community nodes), where (n8n ecosystem), when (2026-02-05), why (decentralized integration), what's-pending (security hardening). Owner + gated open question follow.

### PM — mid (long, dense, no top-of-page state)

`auditmate`, opening:

> "AuditMate is a centralized Product Audit Platform designed to bring transparency, speed, and structure to how product audits are performed across Business and Tech teams on IndiaMART."

What's missing: this is a feature pitch, not a current-state lead. There is no date, no metric, no leadership-pending fact in the first paragraph. The page has all the data — 6–7 sec processing time, 30s outlier latency, 2 detection-gap tables, business-team usability feedback from 2026-02-02. But a director skimming the lead can't forward it without scrolling.

### PM — weak (no lead paragraph at all)

`photosearch`, opening:

> "## Overview / See [[lens-2-0-hybrid-photosearch]] for Lens 2.0 Hybrid upgrade (launched Jan 2026). / Photosearch is IndiaMART's image-based search system that allows users to search for products by uploading or capturing photos."

What's missing: no `last_compiled` recency marker (file is from 2026-04-14, before V12 prompt edits), no owner, no current-state paragraph, no metric, no Why-it-matters block. The page doesn't even start with prose — it starts with an `## Overview` H2. PM ctrl-F'ing for "owner" or any metric finds nothing in the lead.

### Engineer — strong (ramp-ready surface)

`contextual-blni-feedback`, "How it works" + "Bugs":

> "For A-rank MCATs, selecting Wrong Mapping logs `reject_reason = 17` in `eto_ofr_rejected`; for non-A/BA MCATs, Wrong Category continues to log `reject_reason = 1`. […] Bug 640565 Msite Wrong Mapping not visible. Bug 638983 Android scroll/swipe issue. Bug 638981 iOS reordering."

What works: real DB table + actual constant values + 3 platform-tagged bug IDs with status. A new engineer asked to fix Msite bug 640565 has the table column to query and the dispositions to test against.

### Engineer — mid (real anchors, narrow scope)

`msite-mcat-html-rewrite-city-pages`, body:

> "Average HTML reduction: 52% (e.g., 45.3 KB → 20.8 KB on Delhi TMT bar page). / Post-rollout size example (m.indiamart.com/city/surat/t-shirts.html): 27.2 KB total (AppShell 3.3 KB, Critical CSS 3.5 KB, MCAT data 11.1 KB, HTML/scripts 7.5 KB)."

What works: real byte counts, real route pattern, before/after on a named page, 4-component breakdown of post-rollout payload. A perf engineer can A/B against this baseline. What's missing: no LCP/FCP/TTFB/TBT *numbers* (just listed as "improving"), no Varnish config path, no rendering-pipeline diagram.

### Engineer — weak (zero technical surface)

`systems/buyermy`, full body length: ~95 lines of which ~85 are wikilinks and "Recent Launches" bullet list:

> "Buyer platform at IndiaMART. // Features: Hosts the [[dynamic-smart-rfq-form]] for limited MCATs (Master Categories) // Quote Comparison widget with API optimization for improved page load performance // Smart Advanced Search Bar with sticky visibility on scroll-up […]"

What's missing: no API endpoints, no repo path, no namespace, no service map, no auth model, no environments. A new buyer-side engineer reading the BuyerMY system page learns *what features ship* (and is then forwarded to per-feature topic pages), but cannot find the system itself. This is a system page that's a feature index — not a system reference.

## What V12 fixed and what it didn't (this run)

- **PM-side, durable on topic pages:** lead-paragraph contract held on 6/6 sampled topics. Owner-in-frontmatter held on 6/6 sampled topics. The `Open questions` gates remained on 5/6 sampled topics.
- **System-page contract is the new gap:** the V12 prompt taught the agent to write topic pages well, but **system pages compiled in earlier eras don't get rewritten** when downstream topics touch them. `auditmate` (last touched 2026-04-23, mostly V12-shaped) and `indiamart-n8n-nodes` (V12-shaped) work; `buyermy` (last touched 2026-04-15, no V12 lead) and `photosearch` (last touched 2026-04-14, no V12 lead) do not. The boy-scout repair pass (#282) and References backfill (#284) operate on touched pages only — they don't trigger rewrites of stale system pages that nothing in the current batch is updating.
- **Eng-side: same as before.** No `engineering_surface:` frontmatter. No ticket auto-linker (4/10 pages have bare integers). No diagrams (0/10). All 3 are coordinator-side PRs that never shipped.

**Top recommendation:** the next high-leverage move is *not* another prompt edit. It is a coordinator-side **system-page V12-compliance lint** that flags any system page missing `## Why it matters / ## Open questions / ## Recent changes / ## References` and pushes it to the agent's next compile batch — same model as the topic-page archetype enforcer. `buyermy` and `photosearch` would be auto-queued. Pair with the long-pending PR-A (`engineering_surface:` field) and PR-B (ticket auto-linker).

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-ee90d7ce-personas-2026-04-29.md
