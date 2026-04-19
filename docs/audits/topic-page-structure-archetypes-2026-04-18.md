# Topic Page Structure Archetypes — What the Corpus Actually Looks Like

**Date:** 2026-04-18
**Scope:** `wiki/topics/*.md` (n=304), `wiki/systems/*.md` (n=99), random-stride sample of 20 raw emails.
**Sampling:** Full corpus for wiki pages (no sampling); stride sampling of raw (every 337th of 6759, deterministic).
**Live compile:** `6a2a89ae-…` was running during this audit. Read-only; zero mutations.

## Headline

**Recommendation: Option C, with a B-shaped transition.** The v9-U1 schema is not a "prescription agents are ignoring" — it's a prescription **no compiled page satisfies**, not once, across 406 pages. The single biggest data point: **0 of 303 topic pages with H2s meet the 5-of-8 canonical bar.** Meanwhile, **218 of 303 (72%) converge on a stable design-doc shape** that was never taught. The schema is wrong for the content this wiki actually absorbs. Ship a replacement schema that matches the organic shape, and keep v9-U1 only as an opt-in alternative for concept pages (the minority case).

---

## 1. Bucket counts (whole corpus, not a sample)

Classifier thresholds loosened from the task brief to match organic frequency (≥3 design-doc hits OR ≥2 hits on short pages; ≥5 canonical hits as stated):

### Topics (n=304)

| Bucket | Count | % |
|---|---|---|
| **design_doc** (Overview / Background / Problem / Solution / Implementation / Results / Way forward) | **204** | **67.1%** |
| unclassified (still mostly design-doc-adjacent, short pages) | 76 | 25.0% |
| design_doc_with_thread_leakage (e.g. "Launch Announcement", "QA Testing Results" as H2s) | 14 | 4.6% |
| bug_incident (Bug details / Impact / Fix) | 5 | 1.6% |
| thread_narrative (named/dated parentheticals) | 2 | 0.7% |
| initiative | 1 | 0.3% |
| report_dashboard | 1 | 0.3% |
| empty (zero H2s) | 1 | 0.3% |
| **canonical_v9u1 (≥5 of 8)** | **0** | **0.0%** |
| decision_log | 0 | 0.0% |

### Systems (n=99)

| Bucket | Count | % |
|---|---|---|
| unclassified (mostly short: "Overview / Features / Related") | 73 | 73.7% |
| empty (just frontmatter + `## Related`) | 19 | 19.2% |
| design_doc | 7 | 7.1% |
| canonical_v9u1 | 0 | 0.0% |

### Frequency of top H2s in topics

```
 283  ## Related          (97.4% of topics have this)
 114  ## Overview          (111 open with it)
  77  ## Business Objective
  52  ## Technical Details
  45  ## Expected Impact
  41  ## Background
  41  ## Implementation
  38  ## Implementation Details
  32  ## Problem Statement
  32  ## Way Forward
  21  ## Testing
  21  ## TL;DR
  14  ## Test Results
  13  ## Impact
  13  ## Solution
  13  ## Known Issues
  13  ## Performance Metrics
  12  ## Open Questions     ← v9-U1 slot, present on only 3.9% of pages
   9  ## Related Pages
```

For reference, **0** topics have `## Current state`, **0** have `## Why it matters`, **0** have `## Key decisions`, **0** have `## Recent changes` (rare; a few pages have it but it doesn't rank top-30). This isn't agents forgetting — it's agents consistently organising emails in a completely different taxonomy.

### Openers are overwhelmingly stable

| Lead H2 | Count |
|---|---|
| Overview | 111 |
| Business Objective | 52 |
| TL;DR | 21 |
| Background | 15 |
| Problem Statement | 11 |
| Objective | 8 |
| (Summary, v9-U1 default) | **1** |

And **59.7%** of topics have a lead paragraph before any H2 (pre-heading body), which is *also* a strong opener pattern.

### Domain × bucket

design_doc dominates every domain. No archetype is domain-specific. The only slight tilt: `engineering-productivity` and `ai-automation` have disproportionate representation in unclassified because their H2s lean idiosyncratic ("Guardrails", "Scaling Plans", "Workflow transformation") — but their shape is still Problem → Solution → Status.

---

## 2. Deep reads — 10 pages, does the shape serve the content?

### Design-doc bucket

**`removing-negative-mcats-locations-from-bl-recommendation.md`** — Overview / Background / Definitions / Implementation / Impact Expected / Impact Results / Technical Details / Testing Criteria / Test Results / Way Forward / Approval Feedback / Related. Reads as a complete MPLaunch-style writeup: the launch, the data table proving the impact (16% NI/txn drop), test scenarios, approval quote. The H2s are *doing work* — they're what a reader scans for. Forcing this into Summary/Current state/Why it matters would lose the Impact-Expected vs Impact-Results tension, and bury the 29% BBL number.

**`adsense-notice-prevention-unmapped-pdp.md`** — Problem statement / Solution / Platforms launched / Technical details / Testing / Expected impact. Six H2s, short page. Perfect fit. Summary/Current state would be filler here.

### Design-doc-with-thread-leakage

**`auditmate-history-feature.md`** — Overview / Implemented Functionality / Testing / **Launch Announcement** / Issues and Suggestions. The "Launch Announcement" section contains the business-objective, implementation-details, limitations, feedback — it's a whole design-doc nested inside a launch-narrative H2. This *is* a compile failure: the agent paraphrased the launch email's structure instead of promoting its content to page-level H2s. The content is good; the shape is muddled.

**`astbuy-whatsapp-cta-ab-test.md`** — Business Objective / Previous Implementation / Test Variants / Implementation Details / QA Testing / Test Results & Decision / Open Questions / Key People. Reads excellently — A/B test narrative, verdict, unresolved tension. The "Open Questions / Disagreements" H2 with Nitin's two concerns is genuinely useful; the v9-U1 `Open questions` slot would work here verbatim. This is design-doc + one canonical slot.

### Bug/incident

**`android-blni-popup-bug.md`** — lead para / Bug details / Impact / Related. 23 lines. Pure incident. v9-U1 would be absurd — this page doesn't need `Why it matters` or `Key decisions`. What it needs is exactly what it has.

### Unclassified (but shape-consistent)

**`ai-powered-code-review-upload-api-scm-pipeline.md`** — Overview / Project Timeline / Technical Implementation / Success metrics and learnings / Key Stakeholders / Outcome / Documentation / Related Systems / Related. An initiative page. The "Project Timeline" is a chronological spine, "Outcome" replaces "Current state". Works well. A different archetype from pure design-doc — this is closer to **initiative/rollout** shape.

**`ai-driven-pdp-experiment.md`** — Workflow transformation / AI-generated design components / Rollout approach / Future directions / Open questions / Related. Idiosyncratic H2s that match the email's unique framing (before-vs-after workflow comparison). Forcing Summary/Current state here would kill the narrative's core payload.

**`api-knowledge-agent.md`** — Current Status / Known Issues / Planned Improvements / Related Discussions / Related. 34 lines. This is genuinely a **system-lite** page (a tool + its state) — it reads like a product snapshot, not an event. v9-U1's `Current state` would fit here, but `Why it matters` wouldn't.

**`automated-pre-bs-ticket-creation-n8n.md`** — Overview / Business Context / Objective / Problem Before Automation / Implementation / Performance Metrics / Timeline & Key Decisions / Technical Details / Way Ahead / Team. 10 H2s, deep confusion matrices, chronological decisions embedded in "Timeline & Key Decisions". This is a **hybrid** — design-doc + embedded decision-log. The "Key decisions" idea survives, but inside a combined section; splitting it out would harm readability.

**`company-api-category-navigation-rearchitecture.md`** — TL;DR / Background / What's Changed / Performance metrics / Current status / Action required — Frontend validation / Rollout plan / Stakeholders / Related. **This page is the closest organic cousin of v9-U1** yet. TL;DR as `Summary`, Background as `Why it matters`, Current status matches, Rollout plan has the decisions embedded. If v9-U1 had been "TL;DR / Background / What changed / Metrics / Current status / Rollout / Related" instead, 100+ more pages would already conform. The *labels* are wrong, not the spirit.

### Thread-narrative

**`lens-indiamart-fully-logged-in-user-flow.md`** — Bugs identified / Bug 637878 root cause / KPIs monitored / Impact analysis / **Launch Announcement** / Related. Half-bug-log, half-launch-writeup. The compile inherited the thread's raw sections. Real fix: extract the bug-log into a sibling page and the launch into a tight design-doc. The schema isn't at fault here — the compile is.

**Verdict from deep reads:** 7 of 10 pages have structure that **serves the content better than v9-U1 would**. 2 of 10 (the thread-leakage ones) are compile failures regardless of schema. 1 of 10 (`astbuy-whatsapp-cta-ab-test`) is design-doc + one canonical slot working in harmony. **None of the 10 read worse than v9-U1 would make them read.**

---

## 3. Raw email archetypes (n=20, stride 337)

Classification of what each email IS (not what a page would be):

| Kind | n | Examples |
|---|---|---|
| launch announcement (new feature, metrics requested) | 8 | #3 ASTBUY CTA; #14 Notebook LLM; #16 Claude Code VSCode; #15 Context-Aware Buyer Bot; #11 PhotoSearch Feedback APIs |
| reply / acknowledgment (thumbs-up, "noted", "awaited") | 6 | #1, #4, #7, #12, #17 — these are the "trivial skip" set |
| metric / impact update (post-launch numbers) | 3 | #5 logging memory; #9 video coverage scaling; #19 Dynamic CTA metrics |
| bug report + verification (test matrix, bug IDs) | 2 | #13 Call Now CTA; #20 Intent-Based GST Blocker |
| status check / escalation | 2 | #2 Pre-BS followup; #18 Barge-In impact ask |
| scaling/rollout decision | 1 | #6 WA-9696 to 100% |

Of the 14 non-trivial emails, 12 are variants of: "we built/launched X, here's the impact, here's what's next, here's QA." That is design-doc in email form. Only 2 are decision-centric, 0 are policy.

**What structure absorbs 10–20 of these into one concept page?** The page `automated-pre-bs-ticket-creation-n8n.md` — compiled from 5 emails of mixed kinds (launch + status check + metric update + follow-up) — has **Business Context / Objective / Problem Before / Implementation / Performance Metrics / Timeline & Key Decisions / Way Ahead / Team**. That's the shape that soaks up the raw's variety. Not v9-U1.

---

## 4. Recommendation — Option C with B-shaped transition

### Option C (chosen): Replace v9-U1 as the topic default

New canonical topic shape, keyed to what actually works:

```
topic (default):
  - Summary            # or TL;DR — keep both as acceptable
  - Background         # why this exists, problem framing
  - What changed       # or Implementation / Solution
  - Metrics / Impact   # expected + observed
  - Current status     # v9-U1's 'Current state' — keep this slot
  - Open questions     # keep this slot; 12 topics use it today
  - Related            # 97.4% of pages already have this
```

Seven slots. Matches 7 of the top-10 organic H2s. The labels "Summary" and "Current status" let pages that *did* adopt v9-U1 roll forward unchanged. The four dropped slots (Why it matters, Key decisions, Recent changes, References) were near-zero adoption already.

### Why not Option A (tighten reviewer/critique against v9-U1)

Because you'd be retraining 300 pages and every future page to a shape that no writer organically produces. With canonical compliance at 0/303, this isn't a drift — it's a rejection. A reviewer forcing v9-U1 would either (a) produce cosmetic rename-wrappers ("## Summary" around content that was already working as a lead paragraph), or (b) keep flagging genuinely good pages as non-compliant forever. The prompt/validator drift the Cycle-9 audit already flagged would deepen.

### Why not pure Option B (multi-shape declared in frontmatter)

Partial appeal: `page_shape: design-doc | bug | incident | concept` would capture that `android-blni-popup-bug` (5 H2s) and `automated-pre-bs-ticket-creation-n8n` (10 H2s) are structurally different animals. But:

- 72% of the corpus is already design-doc. A declared-shape system is solving a minority problem for the 5% bug/incident set.
- Adding a frontmatter field is another axis agents can get wrong. The Cycle-9 learning ("every LLM-claimed state transition needs independent evidence") applies: shape declarations would need a computer to verify they match body H2s.
- Start with one new canonical; add a second only if the bug-shape set grows past 20 pages. It currently sits at 5.

### The B-shaped transition inside C

Embed a narrow escape hatch in the single-shape rule: **if the page has <50 body lines AND has H2s `Bug details` + `Impact`, skip the full design-doc gate.** That covers the 5 bug pages without inviting shape-sprawl. No frontmatter, just a length + header signature the validator can detect deterministically — exactly the coordinator-does-what-a-computer-does-perfectly split.

### Migration plan (low-cost)

1. Update `SUGGESTED_SECTIONS["topic"]` to the 7 new labels.
2. Update `critique._check_suggested_h2_sections` substring-match list: `Summary|TL;DR|Overview`, `Background`, `What changed|Implementation|Solution`, `Metrics|Impact|Results`, `Current status|Current state`, `Open questions`, `Related`.
3. Update `REVIEWER_SYSTEM_PROMPT` structure_mismatch/filing_cabinet text to match.
4. Update the compile prompt's topic-shape guidance.
5. Leave existing pages alone. The new schema will gently pull new compiles toward convergence with the current majority shape.

Cost to agent prompt: negligible. Cost to existing pages: zero (they already comply with the new shape at ~70%+). Cost to reviewer false-positive rate: should plummet.

---

## 5. "Don't break these" — working patterns from the wiki as-is

1. **`## Related` ubiquity (97.4% of topics).** It's the single most reliable landmark in the wiki. Keep the label, keep it last, keep the bullet-wikilink format.
2. **Lead-paragraph openers (59.7% of topics).** The short pre-H2 sentence that summarises the page works beautifully; readers know what they're looking at before scanning headings. Do not force this into a `## Summary` wrapper.
3. **TL;DR pages (21 of them, heavy-data topics).** Used on the longest, most quantitative pages (`company-api-category-navigation`, `msite-city-mcat-price-widget`, `component-as-a-service` reaches 368 body lines). TL;DR is the right label for those. Treat it as a valid `Summary` synonym.
4. **Tables for impact/metrics.** Every strong page has a "before/after" or "variant/impact" table. `removing-negative-mcats`, `astbuy-whatsapp-cta-ab-test`, `bl-search-text-match-relaxation-pmcat` all carry their value through these. Any canonical-shape fight should not affect table discipline.
5. **Multi-H2 depth on substantive topics.** Pages with 8–12 H2s are legitimately richer; the distribution peaks at 6–7 H2s and tails up to 18. Capping H2 count would destroy information density.
6. **Wikilink density in narrative prose (not just Related).** `[[person-slug]]` and `[[system/x]]` inline in "## Test Results" paragraphs ("Tested by [[anmol-goyal-indiamart-com]]") create the graph the wiki is supposed to be. Keep.
7. **Credited attribution of dated facts.** "2026-01-14 (Rehan Atiqulla [[rehan-atiqulla-indiamart-com]])" lines inside Implementation/Monitoring sections. Gives provenance without a separate `Recent changes` feed. Better than v9-U1's `Recent changes` slot in practice.
8. **Open Questions slot (when used).** The 12 topics that carry this slot preserve genuine unresolved tension (`astbuy-whatsapp-cta`, `ai-driven-pdp`, `agentic-auditor`). It's the one v9-U1 section that already works organically; keep it in the new schema.
9. **`source_threads` and `sources` in frontmatter.** Provenance back to Gmail thread IDs + raw paths is load-bearing for query-time trust. Do not touch.
10. **`domain` field as a single-string primary** (even when `domain_candidates` is plural). The rollup pages depend on this being scalar; leave it alone.

---

## Appendix — data provenance

- Harvest script: `/tmp/audit-structure/harvest.py` (all wiki pages, no sampling).
- Classifier v2: `/tmp/audit-structure/cluster_v2.py`.
- Raw email sampler: `/tmp/audit-structure/sample_raw.py` (stride 337 over 6759 files).
- Final stats: `/tmp/audit-structure/final_stats.py`.
- Canonical schema source: `src/compile/section_shapes.py:SUGGESTED_SECTIONS`.
- No wiki/, raw/, or DB mutations during this audit.
