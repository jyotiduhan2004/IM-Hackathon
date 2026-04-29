# Run ee90d7ce — page-quality + boyscout audit (2026-04-29, round 3)

Run ID: `ee90d7ce-2225-405b-8a07-eedd8f997159`
Commit: `a52672a` (post-#284 coordinator References backfill, post-#282 boy-scout
prompt repair verbs, post-#281 qmd_timeout 45s→60s, post-#283 per-batch pool
logging).
Pages touched: 26 (22 topics + **4 systems**, 2× more systems than 5928c151).
Method: heuristic scorer (`/tmp/run-ee90d7ce-scorer/scorer-2026-04-29.{csv,md}`,
mean 7.52/10 across 22 topics) + scripted F1 sweep across all 26 pages
(`/tmp/audit_f1_check.py`) + manual deep-read of 8 pages (2 top + 2 bottom
topics + 4 systems) + people-slug leakage scan
(`/tmp/audit_people_leak.py`).

## Headline

**F1 fix landed cleanly: 24/24 pages with body refs have all defs present (was
9/18 broken last run, 50%). 0 missing-def, 1 minor orphan def. F2 boy-scout
prompt has not yet moved the needle on observable repair behavior — score
1/8 on deep-read, vs 1/19 last run. System-page archetype is split: 2 of
4 are first-Eng-grade reference pages (auditmate, indiamart-n8n-nodes), 2
are still stubs (buyermy is shallow, photosearch is unfixed legacy).**

The P0 finding from 5928c151 (broken citation plumbing) is closed. The P1
finding (no boy-scout repair behavior) is unchanged — the prompt edit didn't
meaningfully change agent behavior at this n.

---

## F1 quantification — all 26 pages

`/tmp/audit_f1_check.py` walks every page, strips fenced code blocks, counts
inline `[^msg-...]` body refs, counts `[^msg-...]:` definitions, computes the
set difference both ways. Run output:

| Slug | body_refs | unique_refs | defs | missing_def | orphan_defs | has `## References` |
|---|---:|---:|---:|---:|---:|---:|
| systems/auditmate | 13 | 6 | 6 | 0 | 0 | yes |
| systems/buyermy | 0 | 0 | 0 | 0 | 0 | no |
| systems/indiamart-n8n-nodes | 4 | 2 | 2 | 0 | 0 | yes |
| systems/photosearch | 0 | 0 | 0 | 0 | 0 | no |
| topics/all-india-ensemble-sellers-tov-boosting-poc | 24 | 10 | 10 | 0 | 0 | yes |
| topics/android-app-v1366-my-products-missing-config-nudge | 7 | 3 | 3 | 0 | 0 | yes |
| topics/auditmate-location-biztype-checks | 4 | 4 | 4 | 0 | 0 | yes |
| topics/business-credentials-filter-search-api | 17 | 9 | 9 | 0 | 0 | yes |
| topics/contextual-blni-feedback | 16 | 6 | 6 | 0 | 0 | yes |
| topics/dir-desktop-mcat-price-widget-qna-faq-schema-poc | 14 | 3 | 3 | 0 | 0 | yes |
| topics/foreign-buyer-name-capture-msite | 8 | 5 | 5 | 0 | 0 | yes |
| topics/homepage-top-products-recency-sorting | 6 | 2 | 2 | 0 | 0 | yes |
| topics/human-product-edit-rights-revocation | 7 | 3 | 3 | 0 | 0 | yes |
| topics/im-insta-pro-auto-responder-matchmaking | 28 | 16 | 16 | 0 | 0 | yes |
| topics/im-payment-protection-messaging-vani-calls | 3 | 3 | 3 | 0 | 0 | yes |
| topics/llm-object-detection-photosearch | 22 | 6 | 6 | 0 | 0 | yes |
| topics/msite-mcat-html-rewrite-city-pages | 8 | 2 | 2 | 0 | 0 | yes |
| topics/msite-search-product-card-redesign-ab-test | 14 | 5 | 5 | 0 | 0 | yes |
| topics/product-outlier-detection-google-ads | 2 | 1 | 1 | 0 | 0 | yes |
| topics/responsive-layout-buyermy-multi-resolution | 10 | 5 | 5 | 0 | 0 | yes |
| topics/search-relevance-category-filters-all-india-searches | 7 | 3 | 3 | 0 | 0 | yes |
| topics/seller-specs-scaleup | 16 | 6 | 6 | 0 | 0 | yes |
| topics/top-seller-near-you-widget-ui-revamp | 8 | 4 | 4 | 0 | 0 | yes |
| **topics/track-order-buyermy-desktop** | **18** | **9** | **10** | **0** | **1** | yes |
| topics/trade-search-page-migration-php-react | 6 | 1 | 1 | 0 | 0 | yes |
| topics/whatsapp-photo-buylead-optimization | 24 | 9 | 9 | 0 | 0 | yes |

**Summary**:

| | run-5928c151 (n=18) | run-ee90d7ce (n=26) | Delta |
|---|---|---|---|
| body refs > 0, no `## References` | 9 (50%) | **0** | **fixed** |
| body refs > 0, partial defs | 4 (22%) | **0** | **fixed** |
| body refs > 0, all defs present | 5 (28%) | **24 (92%)** | **+64pp** |
| pages with orphan defs | n/a (not measured) | 1 | net new metric |
| no body refs at all | 0 | 2 (both system stubs) | n/a |

The single orphan: `track-order-buyermy-desktop` defines `[^msg-20431a00]`
(`raw/2026-01-31_mplaunchim-track-order-feature-live-on-buyermy-des_20431a00.md`)
in `## References` but no inline body marker uses it. This is the inverse
failure mode of last run — the coordinator hook backfills a def from the
sources frontmatter / messages table, and the agent forgot to cite that
source inline. Cost: low (reader still sees the source listed). Indicates
the backfill is union-style not strict-validate.

**F1 fix landing rate: 100%**. PR #284 closes the P0 finding cleanly.

---

## F2 boy-scout — per-page score (8 pages)

Sample: 4 deep-read topic pages spanning the scorer rank distribution, plus
all 4 system pages. Boy-scout signal scored 0–3:

- **3** = agent fixed multiple pre-existing defects beyond the immediate task
- **2** = agent fixed at least one pre-existing defect (broken wikilink, stale
  Summary, dup section, missing citation)
- **1** = agent left structure tidy on new content but did not repair anything
- **0** = agent left or introduced fixable defects

Update vs greenfield is determined by `update_count`: pages with
`update_count >= 1` had a prior version this run could improve.

| Page | update_count | Bucket | Boyscout | Quote / observation |
|---|---:|---|:---:|---|
| topics/contextual-blni-feedback | 3 | top (8.6) | **0** | **Update page**, but still ships duplicate body sections: L113 `## Related pages` (2 entries) AND L127 `## Related` (4 entries). The two sections drift — `## Related` has `[[ajit-choudhary]]` and `[[srijita-choudhury1-indiamart-com]]` not in `## Related pages`. Boy-scout target. Not consolidated. |
| topics/business-credentials-filter-search-api | 0 | top (8.4) | 1 | Greenfield. Clean structure, full References section. People-slug leakage in body `## Related` (5 person links: `[[aditi-garg-indiamart-com]]`, `[[navendra-singh-indiamart-com]]`, etc.). Nothing to "fix" because greenfield. |
| topics/dir-desktop-mcat-price-widget-qna-faq-schema-poc | 0 | bottom (6.6) | 1 | Greenfield. Compact, well-structured (54 lines). Lead has all 5W. Body Related leaks 4 people incl. `[[dir-indiamart-com]]` (which is a system slug, not a person — wikilink-shape error left in by the agent). |
| topics/whatsapp-photo-buylead-optimization | 0 | bottom (6.4) | 1 | Greenfield. Frontmatter `related:` includes `'[[topic/buylead-whatsapp-display]]'` — flagged broken in 5928c151, **now resolves** (`wiki/topics/buylead-whatsapp-display.md` exists). Either the agent backfilled the target or the corpus grew naturally; either way, no broken-link inheritance. |
| systems/auditmate | 3 | system | **0** | **Update page**, 3rd revision, with full `## References` and detail-rich. But the agent left two parallel content blocks: `## Scope Limitations` (L128) AND `## Known Gaps & Pending Enhancements` (L212) repeat the same Detection-Gaps-Identified table at 50% overlap (e.g., "Photo-Name mismatch not detected" appears at L139 AND in the table at L229). Pre-existing dup the agent should have collapsed. Not consolidated. |
| systems/buyermy | 4 | system | **0** | **Update page**, 4th revision. Lead is one sentence: *"Buyer platform at IndiaMART."* No 5W. No Recent changes section. No References section. Has `# BuyerMY` H1 below frontmatter (structurally redundant — title is in YAML, every other system page omits this). Boy-scout opportunity to backfill 5W lead, missed. |
| systems/indiamart-n8n-nodes | 0 (greenfield) | system | 1 | Greenfield. **Best system-page lead in the run**: *"IndiaMART n8n nodes are community-contributed nodes for the n8n open-source workflow automation platform. They provide two operations: IndiaMART Search… and IndiaMART Post Requirement…. Launched on 2026-02-05… security hardening in progress…"* — full 5W in 60 words. References section present. Tight 55 lines. |
| systems/photosearch | 1 | system | **0** | **Update page**. Frontmatter still has `domain_candidates: # ambiguous: review manually` left from a prior wave — exactly the kind of pre-existing TODO boy-scout should resolve. Unchanged. Lead starts with `## Overview` (no above-the-fold 5W on a system page about Photosearch). No `## Recent changes` despite the page having 27 source raw emails spanning Jan→Apr 2026. No References section. The C- stub archetype, unfixed. |

**Boy-scout summary: 1 page (n8n-nodes) at 1/3 (clean greenfield), 4 pages
at 1/3 (compact greenfield, no repair), 3 pages at 0/3 (update pages where
known pre-existing defects were left in place). Mean: 0.625/3.**

Compared to 5928c151 (1/19 boy-scout, ~0.6/3 mean), this is essentially flat.
The prompt edit in #282 (explicit repair verbs) did not produce observable
repair behavior at n=8 deep-reads. **Conclusion: F2 fix has not landed.**

The clearest counter-example: `contextual-blni-feedback` is on its 3rd
revision, has `update_count: 3`, and *still* ships parallel `## Related
pages` + `## Related` blocks. If the agent reads "scan for unresolved
wikilinks" as a trigger, it's not doing the equivalent for "scan for
duplicate sections" — the verbs in #282 may not be hitting the right
behavioral surface.

---

## System-page archetype delta

4 system pages this run vs 2 last run (1 was C- stub `bl-display-service`,
1 was `lens-2-0-hybrid-photosearch` outside the deep-read).

Continuity check on `bl-display-service` (not touched this run, so reads as
"is the prior C- stub still C-?"): unchanged, mtime 2026-04-29 17:18, 20
lines, lead is a single sentence + 1 Role + 2 Recent changes entries, full
References section. As of run 5928c151 it was a C- stub; as of now it is
still a C- stub (zero growth — corpus doesn't yet have new bl-display data
to compile in).

The 4 system pages this run, ranked by archetype quality:

1. **`indiamart-n8n-nodes`** — A. Best system page in the run. Single source
   thread, two emails, but the agent treated it like a Wikipedia entry: 5W
   lead, Role / Why it matters / Recent changes / Open questions /
   Dependencies / Known issues / Related / References. 55 lines of dense
   reference content. This is the archetype.
2. **`auditmate`** — B+. Detail-rich (281 lines), every claim cited, full
   References section with descriptive captions. But two parallel sections
   (`## Scope Limitations` and `## Known Gaps & Pending Enhancements`) host
   ~50% overlapping detection-gap content, suggesting the agent grew the
   page by appending across waves rather than refactoring. Update_count=3
   should have meant at least one consolidation pass; it didn't happen.
3. **`buyermy`** — C. 4th revision but reads like a stub. One-sentence lead
   (*"Buyer platform at IndiaMART."*), bullet-list `## Features`, then a
   chronological `## Recent Launches` block (not the canonical `## Recent
   changes`), no References, no Open questions. Treats system page as a
   table of contents rather than a Wikipedia page about the system itself.
4. **`photosearch`** — C-. Same archetype as `bl-display-service` last run.
   Stub lead under `## Overview`, no `## Recent changes`, no References,
   `domain_candidates` TODO left unresolved in frontmatter. Inherits 27
   source emails in frontmatter but the body surfaces almost none of them.

**Verdict**: archetype is **not yet self-stabilizing**. 1 of 4 hits the
n8n-nodes shape (compact reference page), 1 of 4 is detail-rich but
structurally bloated, and 2 of 4 are stubs. The pattern is that **first
encounter with a system page** (greenfield, like n8n-nodes) tends to nail
the archetype because the agent decides the structure from scratch under
the V12 prompt; **revisits to legacy stubs** (buyermy, photosearch — both
have `update_count >= 1`) inherit the prior shape and don't repair it. This
is consistent with the F2 boy-scout finding — the prompt teaches good
greenfield structure but does not produce the repair-on-revisit behavior
the V12+#282 prompt asks for.

---

## Top 5 patterns

### 1. Pattern (strength): F1 citation plumbing is now reliable

The single biggest defect from 5928c151 (50% of pages with broken
footnotes) is closed. Coordinator-side `## References` backfill (#284) is
the reason — it computes `message_id → raw path` deterministically from
the messages table, so the agent's reasoning budget no longer pays the
bookkeeping cost. Exactly the "stable identity → coordinator, not LLM"
rule from CLAUDE.md.

### 2. Pattern (problem): Boy-scout repair behavior unchanged

8 deep-read pages, 4 of which were updates (`update_count >= 1`):
`contextual-blni-feedback` (3), `auditmate` (3), `buyermy` (4),
`photosearch` (1). All 4 have at least one pre-existing structural defect
the agent should have repaired in this pass. **None did.** The repair
verbs in #282 did not fire. Hypothesis: the trigger phrasing in the
prompt ("scan for…") is too far from a behavioral hook the agent acts on;
the prompt likely needs an explicit repair *example* in the workflow
section showing a before/after.

### 3. Pattern (problem): System-page archetype only stable for greenfield

`indiamart-n8n-nodes` (greenfield) hits the canonical archetype perfectly;
`auditmate` (3rd revision), `buyermy` (4th), `photosearch` (1st-after-
import) all carry forward shape from prior waves. Greenfield wins because
the agent chooses structure freshly from the prompt; revisits inherit and
don't refactor. Boy-scout's job *is* to refactor inherited shape — see #2.

### 4. Pattern (mixed): People-slug leakage in body Related is rare-but-not-fixed

23 of 26 pages still have at least one person link in body `## Related`,
worst case `auditmate-location-biztype-checks` with 9. Compared to
5928c151's "every page has it, worst 41" the absolute counts are smaller
(corpus has more topic-slugs to crowd out person-slugs), but the *rate*
(pages with any leakage) is still ~88%. This is not the F1/F2 fix area;
flagging because the leakage represents a coordinator-side cleanup
opportunity that's been on the backlog since the 3e88f996 audit.

### 5. Pattern (strength): 5W leads are reliable on greenfield topic pages

All 4 deep-read topic pages (greenfield + update) open with a lead that
names what + where + when + why + who. Best-of-run example, 
`whatsapp-photo-buylead-optimization`: *"WhatsApp Photo BuyLead
Optimization restructures the WhatsApp 9696 buyer journey to prompt
photo sharing before the 'requirement sent' confirmation, replacing the
previous flow where photos were requested only after the buyer had
already received a thank-you message. Photo BL generation rose from 6%
to 16% post-launch (2026-02-05), with MCAT-conditional prompting and a
skip-photo option live; the calls rate dropped from 27.7% to 26.8%,
mitigated by the skip-photo addition."* — restructure-what + when +
quantitative anchor + tradeoff + mitigation in 80 words. The V12 prompt
edits from 5928c151 still hold.

The same is *not* true for system pages — `buyermy` opens with *"Buyer
platform at IndiaMART."* (5 words). System pages need their own
archetype example in the prompt, or the agent treats them as table-of-
contents indices rather than reference articles about the system.

---

## Comparison vs run-5928c151

| Dimension | run-5928c151 (18p) | run-ee90d7ce (26p) | Delta |
|---|---|---|---|
| F1 — body refs without defs | 9/18 (50%) | **0/26** | **fixed (PR #284)** |
| F1 — partial def coverage | 4/18 (22%) | **0/26** | **fixed** |
| F1 — clean refs == defs | 5/18 (28%) | 24/26 (92%) | **+64pp** |
| F1 — orphan defs (def without body ref) | not measured | 1/26 | new failure mode (low cost, union backfill) |
| F2 — boy-scout repair score | 0.6/3 mean | 0.625/3 mean | unchanged |
| Dup `## Related pages` + `## Related` body blocks | 4/18 (22%) | 1/26 (4%) | **better** |
| People-slug leakage (any) | 18/18 (100%) | 23/26 (88%) | better |
| 5W coverage on topic-page leads | 6/6 deep-reads strong | 4/4 deep-read topics strong | unchanged |
| Past-tense narrative leads | 1 case found (soi-product-gst) | 0 cases in deep-read sample | better |
| System-page archetype consistent | n/a (1 page) | 1/4 hits archetype | new finding |
| Heuristic mean (topics) | 7.67 | 7.52 | -0.15 (within noise; smaller per-page max because no 8.6+ outlier here is 8.6) |

**Net**: PR #284 closed F1 cleanly (the headline win). PR #282 has not
landed F2 (boy-scout repair) at observable scale — agent behavior on
update pages is unchanged. PR #281 (timeout 45→60s) is reported as
infrastructure-quality not page-quality.

---

## Recommendations (ordered by leverage)

1. **F2 boy-scout — prompt by example, not by verb.** #282 added "scan for…"
   verbs but the agent didn't act on them. Try adding a concrete worked
   example in the workflow section: a before/after diff showing a page
   with two `## Related` blocks before, one consolidated block after, with
   a one-line rationale. The agent learns from examples reliably and from
   verbs unreliably (this is the "examples beat rules" instinct from
   MEMORY).
2. **Coordinator-side dup-section collapser.** Mirror PR #284's deterministic
   pattern: a coordinator hook that scans every modified page for two
   sibling H2 headings whose names normalize to the same string (`Related`
   ≈ `Related pages`, `References` ≈ `Sources`) and merges them. This
   removes 1 of 26 cases this wave and prevents the next wave's regression.
3. **System-page archetype example in prompt.** The greenfield path produces
   `indiamart-n8n-nodes` (clean); the revisit path produces `buyermy`
   (stub). Adding the n8n-nodes shape as the canonical system-page example
   in the prompt would give the agent a target shape to refactor *toward*
   on revisits. Pair with #1 above.
4. **Photosearch + buyermy explicit refactor.** Since the boy-scout prompt
   isn't reliably refactoring on revisit, queue these two pages for an
   explicit refactor pass (could be a one-shot script, could be a targeted
   compile run). They're both reference-grade systems whose stub-shape is
   carrying through every wave.
5. **Track orphan defs as a low-priority metric.** 1 of 26 this wave. The
   coordinator backfill is union-style (adds defs from the sources
   frontmatter); the agent forgot to cite one of those sources inline.
   Either tighten backfill to "only defs for inline-cited refs" (cheap),
   or accept orphan defs as harmless (also fine — reader still sees the
   raw path listed).

---

## Inputs / artefacts

- Page list: `/tmp/run-ee90d7ce-pages.txt` (26 lines)
- Topics-only: `/tmp/run-ee90d7ce-topics.txt`
- Scorer output: `/tmp/run-ee90d7ce-scorer/scorer-2026-04-29.{csv,md}` (mean
  7.52, n=22 topics)
- F1 sweep script: `/tmp/audit_f1_check.py`
- People-leak sweep: `/tmp/audit_people_leak.py`
- Comparison audit: `docs/audits/run-5928c151-page-quality-2026-04-29.md`
- Findings doc: `docs/audits/run-5928c151-findings-2026-04-29.md`

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-ee90d7ce-page-quality-2026-04-29.md
