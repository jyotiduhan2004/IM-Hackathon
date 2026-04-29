# Run 3e88f996 — page-quality audit (2026-04-29)

Run ID: `3e88f996-3ee7-4653-b7b0-156c6c960201`
Pages touched: 42 (4 systems + 38 topics)
Method: heuristic scorer (`scripts/score_wiki.py`) + manual review of 5 pages against raw email threads + scripted anti-pattern sweeps. No LLM judge calls (budget gate respected).

## Executive summary

**Overall grade: B (good, but with a tail).** Mean-of-means heuristic score is **7.76/10** across the 37 topic pages the scorer covers and **8.15/10** across the 4 system pages. No page scored below 6.4; none above 8.6. The corpus has no low-tier disaster pages, but it also has no clear excellence anchors — the 8-point ceiling is real, and it traces to four concrete problems the scorer can already point at:

1. **Two pages shipped with empty frontmatter `{}`.** `wiki/topics/component-as-a-service.md` and `wiki/topics/whatsapp-cta-msite-company-photo-pages.md` carry no `title`, `page_type`, `status`, `source_threads`, or `last_compiled` — they will not appear in domain rollups, will not be indexed, and the run-id linkage to messages exists only because the coordinator wrote `message_touched_pages` rows, not because the page itself records its provenance.
2. **Two pages have an explicit LLM placeholder leaked into committed content.** `wiki/systems/gladmin.md:45` and `wiki/topics/agentic-auditor-product-approval-grid.md:125` literally contain the line `... (preserve existing)` — an instruction-template artefact the agent emitted instead of the section it was meant to retain. Both are in production.
3. **Citation-density bimodal.** 7 of 42 pages have `< 3` `[^msg-...]` footnotes per 50 body lines; two of those (`auditmate-sellerim-integration`, `buyer-seller-introduction-limit-reduction-10-to-9`) carry **zero** inline footnotes despite drawing on 8 and 23 raw emails respectively. Their `## References` section is missing entirely. The page is well-written but unverifiable.
4. **People-slug leakage is endemic.** 32 of 42 pages (76%) carry `[[*-indiamart-com]]` wikilinks somewhere in the body or `Related` section; the worst (`msite-pdp-html-rewrite-phase-2-...`) carries 71 of them. The `score_structural_smells` heuristic only penalizes when the count exceeds 9, so most of the corpus is being given a free pass on a smell the LLM-judge persona was demonstrably flagging. The fix already exists upstream (people pages are reference-only and shouldn't appear in topic-page bodies) but has not propagated.

The four anti-patterns above explain virtually every page in the bottom 10. The top of the distribution is genuinely good — `whatsapp9696-agentic-buyer-chatbot`, `auditmate`, `seller-ads-exclusive-product-level-lead-service`, and `agentic-auditor-product-approval-grid` are doing real cross-thread synthesis with footnotes, version history, and current-state framing. The problem is volume and consistency, not ceiling.

## Score distribution

Heuristic-scorer output (`scripts/score_wiki.py`, no LLM):

| Mean bucket | Count (37 topics + 1 caas + 4 systems) |
|---|---|
| 9.0–10.0 | 0 |
| 8.0–8.99 | 19 |
| 7.0–7.99 | 21 |
| 6.0–6.99 | 2 |
| < 6.0 | 0 |

Per-heuristic mean across the 37 scored topics:

| Heuristic | Mean | Notes |
|---|---|---|
| concept_shape | 9.7 | Almost everyone passes — the bad-H2 list is too narrow. `msite-pdp-html-rewrite-phase-2-...` lost 4 points from `Testing results` repeating 3× plus `QA Testing Results`. |
| summary_currency | 5.16 | The corpus reads as past-tense narrative more than current-state prose. Bottom: `centralized-runtime-app-permission-tracking` at 2/10, `buyer-searched-keywords-on-bl-card` at 3/10. |
| source_density | 9.86 | Frontmatter `sources:` lists are well-populated, so the heuristic is saturating. This score is *not* counting inline footnote density (see "Citation density" below for a finer-grained measure). |
| graph_health | 7.95 | One broken outgoing wikilink across all 42 (`export-homepage-inline-bl-form` → `[[bl-form]]` or similar; the heuristic flagged it, score dropped to 2). |
| structural_smells | 6.11 | Dragged down by the email-slug-wikilink penalty firing on most pages. |

Top 5 by mean: `gladmin` (8.6, system), `seller-ads` (8.6, system), `seller-ads-exclusivity` (8.6), `whatsapp-cta-msite-company-photo-pages` (8.6 — but see anti-patterns; this is a scorer false positive), `auditmate` (8.2, system).

Bottom 5 by mean: `component-as-a-service` (6.4), `auditmate-sellerim-integration` (6.8), `export-homepage-inline-bl-form` (7.0), `msite-pdp-html-rewrite-phase-2-...` (7.0), `ai-driven-interactive-media-player-desktop` (7.2).

Full per-page CSV: `/tmp/run-3e88f996-score/scorer-2026-04-29.csv` (37 topics) + `/tmp/run-3e88f996-full-metrics.json` (all 42, with citation density + section presence).

## Anti-patterns

### 1. Empty frontmatter `{}` (2 of 42 pages)

These pages have YAML delimiters but no parsed frontmatter — no `title`, no `page_type`, no `source_threads`, no `last_compiled`. They will be invisible to domain rollups and the index regenerator.

- `wiki/topics/component-as-a-service.md` — L1-3: `---\n{}\n---`. The page itself is 381 lines and information-dense (architecture, comparison matrix, AI critiques from ChatGPT/Grok/Gemini, ownership model, recent changes with footnotes). The agent wrote a strong page and then erased the metadata.
- `wiki/topics/whatsapp-cta-msite-company-photo-pages.md` — L1-3: `---\n{}\n---`. The page is **11 lines total**, contains only a `## Stakeholders` block, has 2 footnote refs but no `## References` section to resolve them. This is a stub the scorer cannot recognize as a stub because it scores 8.6 (max-out on graph_health, source_density saturating on 0 frontmatter sources via the body, no penalized H2s, no duplicate sections).

### 2. LLM placeholder string leaked into committed content (2 of 42 pages)

Both pages literally contain the line `... (preserve existing)` — an artefact of the instruction template the agent didn't substitute.

- `wiki/systems/gladmin.md:45` — under `### GLAdmin SOA Migrations`, where a list of migrations was supposed to be preserved. The list does appear two lines later under a duplicate `## GLAdmin SOA Migrations` (note the H2 vs the sibling H3 above), but the placeholder line was emitted alongside the actual content.
- `wiki/topics/agentic-auditor-product-approval-grid.md:125` — inside the `## References` block, between the last footnote definition (line 124) and a stale `## Related` (line 127). The agent appears to have decided to truncate the references list and signal that with a placeholder, then continued writing.

### 3. Missing Summary / first-paragraph (4 of 42 pages start directly with H2)

The compile prompt expects a 2-4 sentence current-state lead paragraph between frontmatter and the first H2. Four pages skip it:

- `wiki/topics/buyer-seller-introduction-limit-reduction-10-to-9.md` L41-44 — frontmatter ends, blank line, then `## Overview`. The Overview content is good, but it's not the lead paragraph the scorer expects (and the page is 240 lines covering a 23-message thread — a Summary is overdue).
- `wiki/topics/mcat-cleaning-via-categorization-auditor.md` L29-31 — directly starts `## Overview`. (The frontmatter on this page is also broken: line 27 reads `- 19bc6405dac3c327 - 19c041ec0a9b246f` — two thread IDs concatenated as one string. YAML parses it as a single 4-thread list when the intent was 5.)
- `wiki/topics/msite-pdp-html-rewrite-phase-2-dom-simplification-size-reduction.md` L26-28 — straight into `## Overview`.
- `wiki/topics/whatsapp-cta-msite-company-photo-pages.md` L4-5 — straight into `## Stakeholders` (see anti-pattern #1; the page is also empty otherwise).

In addition, 7 pages have lead paragraphs that are present but suspiciously short (`< 30 words`): `auditmate` (27w), `gladmin` (2w — literally just `URL: https://gladmin.intermesh.net`), `android-app-v1369-seller-nps-feedback-form` (26w), `auditmate-sellerim-integration` (22w), `bl-purchase-whatsapp-9696` (20w), `export-homepage-inline-bl-form` (20w), `lens-2-0-hybrid-photosearch` (22w).

### 4. Citation density `< 0.5` per 50 body lines (2 of 42 pages — both with zero footnotes)

The user-defined threshold (`< 0.5 per 50 lines`) catches the worst offenders.

- `wiki/topics/auditmate-sellerim-integration.md` — 356 body lines, **0 footnotes**, `## References` section absent. The page draws on 8 raw emails (frontmatter `sources:`) but cites none of them inline. Examples like the bug matrices (L43-71) and the post-2026-01-07 enhancements (L72-89) are unattributed.
- `wiki/topics/buyer-seller-introduction-limit-reduction-10-to-9.md` — 204 body lines, **0 footnotes**, `## References` section absent. The page draws on 23 raw emails (one thread) and contains specific quantitative claims (`3.2% BL Approval increase`, `9% decrease in 9+ introduction buyers`, `1.56% loss/redistribution if reduced to 8`) that have no source link.

5 more pages sit in the 0.5–3 footnotes/50-lines danger zone: `component-as-a-service` (0.53), `revamp-features-section-paid-services-screen-desktop` (0.88), `android-app-v1369-seller-nps-feedback-form` (2.17), `thankyou-screen-soi-journey-revamp` (2.50), `msite-pdp-html-rewrite-phase-2-...` (2.80). For 480-line pages drawn from 5+ raw emails, that's still under-cited.

### 5. People-slug wikilink leakage in topic prose / Related (32 of 42 pages, 76%)

The user instructions explicitly state people pages are reference-only and should not appear in primary nav. The `score_structural_smells` heuristic penalizes this only when ≥ 3 hits, capping at 4 points off — but the leakage is far more pervasive than the penalty captures. Top offenders (count of `[[*-indiamart-com]]` links):

- `wiki/topics/msite-pdp-html-rewrite-phase-2-dom-simplification-size-reduction.md`: **71** people-slug links
- `wiki/topics/bl-purchase-whatsapp-9696.md`: **56**
- `wiki/topics/mcat-cleaning-via-categorization-auditor.md`: **50**
- `wiki/topics/whatsapp9696-agentic-buyer-chatbot.md`: **47**
- `wiki/topics/agentic-auditor-product-approval-grid.md`: **44**
- `wiki/topics/component-as-a-service.md`: **43**
- `wiki/topics/auditmate-sellerim-integration.md`: **35**

Most of these come from the `## Related` block at the bottom of each page, which is being filled with every person who touched the thread. The Stakeholders sections inside the body bear another large share. This is not bad data — just the wrong shape: a Wikipedia-style wiki shouldn't have a 30-name "see also" of email addresses on every concept page.

### 6. Duplicate `## Related` block — frontmatter `related:` plus body `## Related` (33 of 42, 79%)

Almost every page maintains two parallel sets of related links: a YAML list in frontmatter (`related:`) and an H2 section in the body (`## Related`). They drift apart — `agentic-auditor-product-approval-grid.md` frontmatter has 2 entries (`auditmate`, `auditor-11-no-photo-version-rollout`) but the body `## Related` has 17 (mostly people slugs). The structural-smells heuristic penalizes this with a flat -2 and the prompt either needs to pick one canonical location or the coordinator needs to dedupe deterministically.

### 7. Empty `## Open questions` and duplicate H2s — both clean

Zero pages had < 10 words under `## Open questions` and zero pages had duplicate H2s from the strict allowlist (`Recent Changes`, `Current State`, `Overview`, `Related`, etc.). The `gladmin` case in anti-pattern #2 has the duplicate `### GLAdmin SOA Migrations` (H3) plus `## GLAdmin SOA Migrations` (H2), which the H2-only smell-checker doesn't catch — that's a tooling gap worth noting but the page itself is rare.

### 8. Recent changes 10+ entries — clean

Zero pages had ≥ 10 dated entries under `## Recent changes`. The largest are `whatsapp9696-agentic-buyer-chatbot` (8 entries) and `agentic-auditor-product-approval-grid` (7), both well-organized.

### 9. Broken outgoing wikilinks — almost clean

One page has a broken wikilink: `wiki/topics/export-homepage-inline-bl-form.md` — the heuristic flagged 1 broken target. Worth fixing but not systemic.

## Source-coverage findings

I sampled 5 pages — picked one likely-weak (long but no footnotes), one with 4 source threads (multi-thread synthesis test), one system page with 5 source threads, one mid-tier multi-thread topic, one small multi-thread topic.

### `wiki/topics/whatsapp9696-agentic-buyer-chatbot.md` (4 threads, 24 raw msgs, 13 sources cited)

**Excellent synthesis.** The page reads as a single coherent concept, not four stapled threads. Version 1.1, 1.2, 2.0 are merged into a single Version History with an explicit "Discovery-first vs Buylead-first" architectural pivot at V2.0. Quantitative metrics (40% scaling 2026-02-04, 85% accuracy, 90% leadership target, ~15% gap) are pulled from the Feb 4 thread and reconciled with earlier latency-target conversations (`thread 19b74046c8f60167`, line 47 `[^msg-1b5ac220]`).
**Gap:** the early `19b277e6fc6624ea` thread (Jan 8 1% POC rollout) is condensed to Recent-changes bullets; the substantive content of Mohak Saxena's "Let's take feedback from the buyer themselves. There is no mandate forcing the buyer to specify the journey" critique (Dec 25 reply, in raw `5164edc9.md`) does not appear anywhere on the page. That's a UX-mandate decision that's worth its own line.

### `wiki/topics/buyer-seller-introduction-limit-reduction-10-to-9.md` (1 thread, 23 raw msgs, 23 sources cited)

**Strong technical capture, zero attribution.** All the numbers are there: the 19-test-case suite (L114-141), the 5 DB-level case samples with channel mixes (L168-174), the +3.2% BL Approval / 9% decrease in 9+ introduction buyers (L186-191), the 9-to-8 follow-up analysis showing 1.56% / 40K weekly txn loss (L201-207). The Leadership Discussion section even captures Amit Jain → Dinesh Agarwal → Ankit Dalmia exchange.
**Gap:** **0 inline footnotes**, **no `## References` section**. None of the quantitative claims trace back to a specific raw email. With 23 raw messages in the thread, this is the page where the audit-trail mattered most. From the raw mails, what's missed: Pratik Ahuja and Himanshu Jain were looped in on 2026-01-05 (raw `cbea4e63.md`) — the page lists them in `## Related` but doesn't note their role.

### `wiki/systems/auditmate.md` (5 threads, 33 raw msgs, only 4 inline cited)

**Solid, but under-cites.** Captures business problem, UI structure, attributes audited, performance latency (6-7s typical, 30s+ outliers), the unit/price absurd integration with API response shape (L113-121), the audit-history feature, and detailed gap matrix from the 2026-02-02 business-team feedback. 4 inline footnotes on a page covering 33 raw messages.
**Gap:** the entire `19bc0d07ef5ac143` thread (UI/UX enhancements, 6 messages, raw `65413a7f.md` etc.) is folded into the page narrative without a single direct citation. The "pencil cursor" detail, the "zoom + elevate interaction" on error click, the "Add Price CTA" — all from that thread, none footnoted. Similarly the 2026-01-07 unit/price absurd thread (`19b97bb9814b4e67`) feeds into L106-121 with zero inline ref.

### `wiki/topics/agentic-auditor-product-approval-grid.md` (2 threads, 22 raw msgs, ~12 sources cited)

**Good substance, but shipped with `... (preserve existing)` placeholder leak (L125)** plus a stale `## Related` block at the bottom (L127-145, 17 entries — mostly people slugs). The Decision Grid table (L29-36) and the PDP error scoring rubric (L38-58) are accurate per `raw/2026-01-12_..._8c611371.md`. The visibility-flag launch (L63) is correctly attributed to `[^msg-aeced455]` (raw 2026-01-29 announcement).
**Gap:** the page does not capture Manik Garg's Feb-2 acknowledgment of BUG 641560 closure beyond a one-liner in Recent changes (L87) — the actual test breakdown (25/25 passed, smoke + launch, with seller-flow verification on Jute Shopping bags 321409573 and Luminous Inverter Battery 321242398) is summarized but the rationale ("paid seller flows verified per Vikram Varshney's 2026-01-30 ask") chain is implicit. A reader can't easily tell why those particular GLIDs were chosen.

### `wiki/topics/seller-ads-exclusive-product-level-lead-service.md` (2 threads, 9 raw msgs, 5 sources cited)

**Cleanest of the 5.** Pricing model (L43-49), POC results (11/12 sellers reaching 7+ conversions in week, L33-34), process flow (L52-65), expected impact, and a tight 3-bullet Recent changes block. 3 inline footnotes for ~120 lines of body. The only gap is that Akash Reddy Devarapalli, Anuj Sharma's "👍" / "Good work team" reactions are captured in Credits but the substantive feedback from Dinesh Agarwal's 2026-01-30 reply isn't quoted (the Open question "business launch timing" in L70 is the closest the page comes).

## Top 5 worst pages (read these first if you're triaging)

1. **`wiki/topics/component-as-a-service.md`** — Empty frontmatter `{}` despite being 381 lines of substantive content. Will be invisible to rollups and index. Mean 6.4. Fix: stamp frontmatter.
2. **`wiki/topics/whatsapp-cta-msite-company-photo-pages.md`** — Empty frontmatter `{}` AND only 11 lines of body, no Summary, no References despite 2 footnote refs. Mean 8.6 (scorer false positive — the structural-smells heuristic doesn't penalize "page is functionally a stub"). Fix: either complete the page or mark `status: stub`.
3. **`wiki/systems/gladmin.md`** — `... (preserve existing)` placeholder string at L45, plus duplicate `### GLAdmin SOA Migrations` (H3, L43) followed by `## GLAdmin SOA Migrations` (H2, L47), plus a 2-word lead paragraph (`URL: https://gladmin.intermesh.net`). System page on the production wiki.
4. **`wiki/topics/agentic-auditor-product-approval-grid.md`** — `... (preserve existing)` placeholder at L125, stale `## Related` block at L127 with 17 entries (15 of them people slugs). Otherwise high-quality content.
5. **`wiki/topics/auditmate-sellerim-integration.md`** — 356 body lines, **zero inline footnotes**, no `## References` section, frontmatter `sources:` lists 8 raw emails that the page doesn't cite. Bug matrices (15 + 6 bugs with IDs and statuses) read as authoritative but are not traceable to any specific email.

## Top 5 pages worth reading as positive examples

1. **`wiki/topics/whatsapp9696-agentic-buyer-chatbot.md`** — Strongest cross-thread synthesis (4 threads → 1 coherent narrative). Version history with explicit V1→V2 architectural pivot. 41 inline footnotes for 232 body lines. Current-state Summary at L48 names traffic share, accuracy, latency, feedback collection. This is the shape the corpus should be heading toward.
2. **`wiki/topics/seller-ads-exclusive-product-level-lead-service.md`** — Tight, well-cited (3 footnotes for 125 lines), pricing tiers + process flow + expected impact in current-state prose. Only ~4 people slugs in the body. Mean 8.0, but reads better than the higher-mean pages.
3. **`wiki/topics/seller-ads-exclusivity.md`** — Clean structural smells (8/10), no people-slug leakage, no FM+body Related dup, just the right level of detail. Mean 8.6.
4. **`wiki/systems/auditmate.md`** — Despite under-citing (4 footnotes on a 5-thread page), the page successfully synthesizes the announcement, performance data, gap analysis, audit-history feature, and pending enhancements into a single concept page. Open Questions section at L247-252 is genuinely substantive — not boilerplate.
5. **`wiki/topics/lms-replytowhatsapp-consumer-migration.md`** — Highest structural-smells score (10/10) — no duplicate H2s, no empty sections, no email-slug links, no FM+body Related dup. Compact, cited, current-state lead.

## Recommendations (ordered by leverage)

1. **Add a coordinator-side guard against empty frontmatter.** If the parsed YAML is `None` or `{}`, fail the compile attempt and re-queue. The 2 pages that shipped this state are concept-rich content with a metadata bug — they're salvageable in seconds if the coordinator refuses to accept them.
2. **String-search-block for `(preserve existing)` and similar template artefacts in the post-batch validator.** A cheap regex on `\.\.\. \(preserve` would have caught both leaks.
3. **Tighten the people-slug structural-smells heuristic.** The current cap (-4 max) does not differentiate `auditmate.md` (6 leaks) from `msite-pdp-html-rewrite-phase-2-...` (71 leaks). Either move the penalty to per-link with no cap, or add a separate hard limit ("> 15 people-slug links → page is wrong shape").
4. **Either drop the body `## Related` section or drop the frontmatter `related:` field.** Maintaining both produces drift — 33 of 42 pages already have inconsistent sets.
5. **Backfill `## References` sections on pages with `> 200` body lines and `< 5` inline footnotes.** That's a deterministic post-process the coordinator can run: every `[^msg-xxx]` ref in the body should resolve to a `raw/...` path under `## References`. Pages without a single ref but with a populated `sources:` frontmatter are the priority targets.

## Inputs / artefacts

- Page list: `/tmp/run-3e88f996-pages.txt` (42 lines)
- Topic-page scorer output: `/tmp/run-3e88f996-score/scorer-2026-04-29.csv`, `.md`
- Full per-page metrics (incl. systems, citation density, section-presence flags): `/tmp/run-3e88f996-full-metrics.json`

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-3e88f996-page-quality-audit-2026-04-29.md
