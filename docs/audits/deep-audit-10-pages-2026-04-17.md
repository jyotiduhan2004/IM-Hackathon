---
audit_date: 2026-04-17
scope: 10 recently-compiled content pages
method: line-by-line wiki read + 2 raw-email spot-checks + persona-value scoring
---

# Deep audit — 10 recently-compiled pages

Per-page line-by-line walk across 8 topics, 2 systems. For each:
what the compile wrote, what's grounded in the raw, what's structurally
broken, and a 4-persona value score. Followed by cross-page pattern
analysis.

## Methodology

**Compile fidelity spot-check**: For page 4 (SEO Rework) I compared two
raw emails (Amarinder 2026-01-12 recommendations + Nishant Singhal
2026-01-13 snippet visibility) against the corresponding page
sections. The recommendations list and the 5-row snippet-visibility
table reproduced verbatim, numbers to the digit. Compile fidelity on
this page is high — the problem is **structure**, not content loss.

**Persona scores** (1-5):
- **PM / stakeholder**: decisions, metrics, owners, status
- **API owner debugging**: names endpoint, behaviour, known bugs, bug-IDs
- **New joiner**: TL;DR + what + why + who to ask
- **IA curator**: clean canonical H2s, no dated sections, live
  wikilinks, correct slug

## Per-page findings

### 1. `topics/qdrant-vector-recommendations-poc.md` — **3.7**

3 sources, 1 thread, 3 updates. 118 lines.

**Good**: Opens with a one-sentence definition (`The Qdrant Vector-Based
Product Recommendations POC is a proof of concept that evaluates…`).
Has a proper early-impact metrics table with concrete product-ID
examples. `## Open Questions` + `## Implementation credits` +
`## Ticket` — proper provenance.

**Bad**: `## Scaling Decision (Jan 16, 2026)` is a Bug F dated H2.
`### Questions from [[amarinder-indiamart-com]] (answered Jan 13, 2026)`
is a sub-H3 with an attribution — same class. Line 111 has a `\n-`
literal sequence that should be a newline+bullet; renders as
`(POC implementation)\n- [637697](...)` — compiler missed the escape.

**Score**: PM 4 · API 3 · New-joiner 4 · IA 3 = 3.7.

---

### 2. `topics/photosearch-star-rating-feedback-popup.md` — **2.5**

No `sources:` field (catalog-only page!), 1 thread, **8 updates**.
273 lines.

**Good**: Rich QA-bugs section with 6 specific bug-IDs (636151,
635996, 635992, 636143, 636239, 636245, 636230, 636218) linked to
ticketing. Clear rollout phasing (50% → 100%). Specific early-impact
metrics: `114% increase in feedback submission`, `72% positive`.

**Bad** (structural):
- **5 dated H2s**: `## Early Impact Analysis (January 5, 2026)`,
  `## Leadership Response and Goals (January 6, 2026)`,
  `## Decision: Scale to 100% (January 7, 2026)`,
  `## Feedback Frequency Design (January 13, 2026)`,
  plus several dated H3s.
- **Section duplicated verbatim**: `## Feedback Frequency Design
  (January 13, 2026)` appears twice (lines 96-99 + lines 261-266)
  with identical content. 8 updates × Bug F pattern → duplicate
  insert on re-compile.
- Bloat: 10-item "Next Steps" list restates bug IDs already enumerated
  in "Identified Bugs".

**Score**: PM 3 · API 4 · New-joiner 2 · IA 1 = 2.5. Content is there
but a reader has to wade through.

---

### 3. `topics/bl-purchase-whatsapp-9696.md` — **2.8**

5 sources, 2 threads, 6 updates. 173 lines.

**Good**: Tight rollout phasing (10% → 50%). Correct 4-column bug
table with ticket links + accountable owners. Cross-link to the buyer-
feedback sibling page. Specific-metric table (BL Approved, Callers/Sent,
etc.).

**Bad**:
- **Corrupted body fragment** at line 148: `d impact data` — orphan
  fragment from an earlier bullet that lost its leading text during
  an edit merge. Followed by a blank line then
  `- [[shobhna-verma-indiamart-com]] - QA Manager`. Looks like a
  destructive `edit_file` replacement cut mid-word.
- `## A/B/C Test: Product Images in BL Purchase Messages (Jan 13,
  2026)` — Bug F dated H2.
- `### Post-Launch QA Issues (Jan 14-21, 2026)` — dated H3 (inside a
  canonical "QA & Testing" H2, so lighter impact).

**Score**: PM 3 · API 4 · New-joiner 3 · IA 1 = 2.8.

---

### 4. `topics/seo-price-widget-faq-consolidation-msite-mcat-pages.md` — **2.6**

2 threads, 2 updates. 182 lines. **Worst Bug F offender in the corpus**
(surfaced in the validator baseline at PR #150).

**Good** (content is excellent): Full schema.org recommendation from
Amarinder preserved. Exact snippet-visibility table from Nishant
(verified against raw email `f4b758c3.md` — 100% number-match). QA
matrix (66/66 smoke, 14/14 feature) preserved with device details.
SEO/HTML validation score breakdown (8.2/10, per aspect).

**Bad** (structure is the whole story):
- **5 parallel dated H2s** — essentially one H2 per contributor email:
  - `## SEO Analysis Findings (December 30, 2025)`
  - `## SEO Recommendations (Amarinder Dhaliwal, 2026-01-12)`
  - `## Snippet Visibility Impact (Nishant Singhal, 2026-01-13)`
  - `## QA Testing Results (Rucha Patil, 2026-01-13)`
  - `## SEO & HTML Validation Score (Nishant Singhal, 2026-01-16)`
  - `## Organic Traffic Impact (Taher Saify, 2026-01-19)` (6th!)
- Fix shape (Bug F prompt PR #148): these should all roll into one
  `## Findings` section with dated bullets per contributor.
- `## Open Issues` has 3 dated H3s.

**Score**: PM 5 · API 3 · New-joiner 2 · IA 0 = 2.5. Paradoxically
this is the clearest page content-wise AND the worst-structured.

---

### 5. `topics/central-smart-orchestrator-api.md` — **3.0**

1 thread, **1 update** (single-shot compile). 237 lines.

**Good**: Full API surface (input/output params). Evolution narrative
(prev → new). Concrete KPI matrix (precision/recall/accuracy). Cost
analysis with before/after paisa figures. FP/FN tables with actual
image URLs.

**Bad**:
- **Table bleed across sections**: Lines 152-155 show `| TP2 | ... |
  Correctly rejected |` appearing INSIDE `## Meeting Minutes` but it's
  a continuation of the `## Testing Data` table above. Section
  boundary eaten by the table.
- 3 dated H2s: `Testing Data (January 8, 2026)`, `Model Performance
  Evaluation (Jan 20-22, 2026)`, `Meeting Minutes (Jan 22, 2026)`.

**Score**: PM 3 · API 5 · New-joiner 3 · IA 1 = 3.0. The API-owner
persona gets a lot here; IA pays the cost.

---

### 6. `topics/conversation-guardrails-poc-for-seller-buyer-chats.md` — **3.2**

1 thread, 5 updates. 91 lines.

**Good**: Clean canonical H2s (Problem Statement, Objective, What's
Been Done, Feedback). Defines 4 harmful-content categories inline.
Preserves the reviewer's model-evaluation dictum (~538 real + 45
spiked conversations, 4 models evaluated).

**Bad**:
- **Legacy `## Sources` body section** (lines 65-69) lists raw-paths
  inline — vestige of the old `sources:` rendering pattern. Should
  be dropped now that `source_threads:` + catalog own provenance.
- `## Feedback` is a weak heading — the content is "Alok + aa
  comments on the POC." Would be clearer as `## Reviewer feedback`
  or fold into the `## Open issues` pattern.

**Score**: PM 3 · API 2 · New-joiner 4 · IA 4 = 3.2.

---

### 7. `topics/vani-ad-calls-handling.md` — **4.2** ⭐

3 sources, 1 thread, 4 updates. 97 lines.

**Good** — **reference-quality page**:
- NO dated H2s. Canonical structure: Business Objective, Earlier Flow,
  Implementation, Performance Comparison, Testing Requirements, Bug
  Summary, Test Coverage, Tracking, Open Items, Related.
- Dated bullets live INSIDE canonical sections (`- Mohak Saxena
  requested Sanchit Joshi share estimated impact… (2026-01-13)`).
  This is exactly the Bug F GOOD pattern the prompt now teaches.
- Concrete phone number (8048638150), exact call-count deltas,
  4-bug table with ticket IDs.

**Bad**: Minor — "Open Items" could be called "Open questions" per the
Phase A template, but that's polish not drift.

**Score**: PM 4 · API 4 · New-joiner 5 · IA 4 = 4.2. Use this as the
reference-quality exemplar when showing the agent what a good page
looks like.

---

### 8. `topics/pns-intent-whatsapp-template-ab-test.md` — **3.5**

1 thread, 2 updates. 145 lines.

**Good**: Full A/B-test matrix (Template 1-3 vs Old Standard across 12
metrics). Mohak's 5-strategy location-capture proposal preserved as
structured sub-sections with Benefits each. Bug table with bug-ID.
Scaling decision (5% → 50%) with the date inline.

**Bad**: `## A/B Test Results (January 6-8, 2026)` — one dated H2.
The five location-capture strategies (lines 67-108) are well-organized
but are, in a sense, a speculative bonus section — if it was an agent-
authored synthesis vs. quoted from email, it blurs the "wiki compiles
evidence" contract. Worth trace-walking to verify.

**Score**: PM 4 · API 3 · New-joiner 4 · IA 3 = 3.5.

---

### 9. `systems/sonarqube.md` — **3.8**

1 thread, 6 updates. 85 lines.

**Good**: Canonical system-page H2s (Capabilities, Automation
Implementation, Current Status, Related). Enumerated sub-fields under
Capabilities (Vulnerabilities / Bugs / Code Smells / Security
Hotspots / Duplications) with examples — exactly the "durable noun"
system-page shape.

**Bad**: 2 dated sub-H3s under Automation Implementation
(`### Implementation Clarifications (January 5, 2026)`,
`### Known Issues (January 2026)`). These are inside canonical H2s so
they're less egregious than top-level Bug F — but still drift.
Interesting: `[[aa-indiamart-com|Amit Agarwal]]` uses the pipe-alias
form — only page in the set to do so.

**Score**: PM 4 · API 4 · New-joiner 4 · IA 3 = 3.8.

---

### 10. `systems/Lens.IndiaMART.md` + `systems/lens-indiamart-com.md` — **1.5** 🚨

**Duplicate slug problem** — two system pages for the same concept:

- `Lens.IndiaMART.md` (UPPERCASE filename violation): 11 sources,
  4 threads, update_count 2, 56 lines. Body has inline "Related
  Features" listing as well as a trailing `## Related` (duplicate).
- `lens-indiamart-com.md` (kebab-case, correct): 1 thread, 1 update,
  21 lines. Skeletal TL;DR + one wikilink.

Both carry `title: "Lens.IndiaMART"` — compile-time slug derivation
should have collapsed them. The uppercase file is the real content;
the kebab-case file is a stub that the agent probably created on a
later compile that didn't fuzzy-match the uppercase slug.

**This is an IA-breakage** — new joiner hits a 50/50 chance of the
skeletal stub and concludes the system is under-documented. **Fix**:
merge the content into `lens-indiamart-com.md`, redirect or delete
the uppercase file, add an alias map so future resolves hit the
canonical slug.

**Score**: PM 2 · API 2 · New-joiner 1 · IA 1 = 1.5.

## Cross-page pattern analysis

### Structural failure modes (prevalence in sample)

| Failure mode | Pages affected | Severity | Status |
|---|---|---|---|
| Bug F dated H2s (filing-cabinet structure) | 7/10 | High | Fix in flight (PR #148 prompt + PR #150 validator) |
| Duplicate content section (verbatim re-insert) | 1/10 (photosearch) | Medium | Not yet modeled. Likely: edit_file re-inserts on every update_count bump |
| Body fragment corruption (`d impact data`) | 1/10 (bl-purchase) | Medium | Not yet modeled. Destructive edit_file miss |
| Table bleed across section boundaries | 1/10 (smart-orchestrator) | Medium | Not yet modeled. Edit boundary lost |
| Legacy `## Sources` body section | 1/10 (guardrails) | Low | Drift from pre-catalog era |
| Duplicate slug (uppercase + kebab) | 1/10 (Lens) | High | Not yet modeled. Fuzzy-resolver gap |
| `\n-` literal (missed escape) | 1/10 (qdrant) | Low | Not yet modeled |

### What's working

1. **Content fidelity is high.** The SEO page's claimed metrics match
   the raw email to the digit (51 KWs, 82% → 76%, etc.). The agent
   reads + preserves evidence correctly — it's the *organization* of
   that evidence that's broken.
2. **TL;DR openers.** 8/10 pages open with a one-sentence definition
   rather than a heading. Phase A U3's `<self_review>` rule #2 is
   landing.
3. **Wikilink discipline.** Person-slug wikilinks (`[[pulkit-jakhar-
   indiamart-com]]`, etc.) are present and resolvable (per Bug E fix).
4. **Ticket IDs preserved.** Every operational page has a specific
   bug/ticket ID — API-owner persona is well-served.

### What's not working

1. **Structure mirrors email threads 1:1 instead of synthesizing.**
   The SEO page is the extreme case — 5 emails → 5 parallel H2s. The
   agent is **filing**, not **compiling**. Bug F prompt fix (#148)
   addresses the prompt-level guidance; PR #150 adds the validator
   metric so we can measure adoption.
2. **Incremental compile corrupts prose.** High-`update_count` pages
   show the worst symptoms (photosearch has 8 updates + verbatim-
   duplicate section; bl-purchase has 6 updates + body fragment).
   Hypothesis: `edit_file(old_string, new_string)` with partial
   matches re-inserts content + leaves fragments. Worth building a
   structured `patch_page` path that replaces an entire section
   atomically.
3. **Slug-derivation drift.** The `Lens.IndiaMART.md` vs
   `lens-indiamart-com.md` split is a resolver miss — `resolve_page`
   didn't fuzzy-match and the agent created a second stub. Either:
   (a) normalize-on-write (force-kebab), or (b) strengthen
   `resolve_page` to match `.`-separated alt forms.
4. **Dated H3/sub-sections are an unfixed subset of Bug F.** The
   prompt rule and the validator target H2s only. Sub-H3s with
   `(Jan 9, 2026)` appear in SonarQube, qdrant, bl-purchase. Should
   extend the rule or accept them as a lighter signal.

## Persona-value summary

| Persona | Mean score (10 pages) | Top complaint |
|---|---|---|
| PM / stakeholder | 3.5 | Decisions are buried under dated headers; "which one is current?" |
| API owner debugging | 3.4 | Bug table + ticket IDs present; miss: no "on-call / owner" footer block |
| New joiner | 3.2 | Dated H2s read as "events"; can't tell what the system IS vs. what happened |
| IA curator | 2.2 | Bug F dated H2s + duplicate slugs + corrupted fragments |

**IA is the weakest lens.** PRs #148 (prompt) + #150 (validator) target
the top IA complaint. The other 3 persona deltas come from synthesis
quality — need a deeper follow-up pass on the incremental-compile
corruption hypothesis.

## Recommendations (queued backlog items)

1. **Ship + measure**: land #148 + #150; re-run this 10-page audit
   after 2 Cycles of compile activity; track `dated-h2-section` count
   as the Bug F adoption metric. Expect 10/10 new pages with 0 dated
   H2s.
2. **Investigate `edit_file` corruption**: take the photosearch-
   duplicate-section case + the bl-purchase fragment case; walk the
   Langfuse trace to see which tool call produced the corrupted
   state. File as Bug M (or similar) if reproducible.
3. **Merge `Lens.IndiaMART.md` + `lens-indiamart-com.md`**: one-shot
   script to consolidate content, drop the uppercase file, add a slug
   alias. Low-risk, high IA win.
4. **Consider extending Bug F rule to H3**: current prompt + validator
   only flag H2. Sub-H3s with dates are a real but lighter signal —
   could be promoted to warning with a separate check.
5. **Add an "on-call / owner" frontmatter field + footer block**: the
   qdrant page has `owners:` — nothing else does. API-owner persona
   would benefit from a consistent "who runs this" surface.
