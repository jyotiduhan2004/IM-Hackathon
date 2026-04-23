# V12 50-email compile deep audit (2026-04-23)

**Scope.** First real-world compile with the full V12 prompt stack live
(`<concept_vs_thread>`, `<expert_questions>`, `<inline_citations>`,
`<revision_style>` sections, plus scorer v3). 50-email sample with an
expanded 5-model pool. Combines deterministic scorer v3 output, LLM-judge
(3 personas × 16 pages), Langfuse traces, pre/post diffs, and per-page
content sampling.

**Run metadata.**

- `compile_run_id`: `a1452a9c-9fd0-41b6-844c-450eef7c07bf`
- Window: `2026-04-23T10:29:09Z` → `2026-04-23T12:16:06Z` (~107 min wall)
- Model pool: `minimax/minimax-m2.7, z-ai/glm-5, x-ai/grok-4.1-fast,
  moonshotai/kimi-k2.6, qwen/qwen3.5-122b-a10b` (2 new)
- Batches: 50 — **17 compiled pages**, 32 trivial_skip /
  already_captured, 1 anomalous (batch 45)
- Cost: **$5.76** (`$0.34/compiled page`; $0.07 average trivial_skip)
- Pre-run snapshot: `.snapshots/pre-compile-20260423T102909Z/wiki/topics/`

Raw data artifacts (in `/tmp/`, not committed):
`v12_50_audit_data.md`, `lf_traces.json`, `lf_trace_dumps/*`,
`lf_trace_summary.json`, `batch_table.json`, `page_diffs.json`,
`task3_patterns.json`. Judge CSV committed at
`docs/feedback/judge-2026-04-23.csv`.

---

## 1. Headline — does V12 actually improve content?

**Yes on surface structure. No on semantic depth.**

- Scorer v3 mean-of-means: **6.94** on the 16 V12-touched pages
  (vs full-corpus pre-V12 mean of 5.86 on scorer v2, 4.04 on scorer v1).
  Net scorer delta: **+1.08 points** — **caveat (Claude review)**:
  scorer v2→v3 expanded the bad-list + rebalanced `graph_health`, so
  the numeric delta mixes rubrics. Direction is solid ("V12 shifts
  surface structure"), magnitude is approximate. An apples-to-apples
  re-score would re-run v3 on the pre-V12 snapshot at
  `.snapshots/pre-compile-20260423T102909Z/`; deferred as follow-up.
- Judge persona mean-of-means (newbie + pm + ia across 16 pages): ≈ **5.92**.
  Compared to the 13-page pre-V12 run (judge mean ≈ 5.5), a modest
  **+0.4** improvement.
- **Scorer-judge correlation is re-inverted on the extremes.**
  3 of 5 top-scorer pages land in the judge's bottom 5:

  | Page | Scorer | Judge (min across personas) |
  |---|---|---|
  | pns-calls-otherspaidvfcp-custtype-23 | 8.2 | 4 (ia=4, newbie=4, pm=6) |
  | seller-performance-dashboard-desktop-lms | 8.2 | 4 (ia=4) |
  | tov-display-improvement-high-buyleads | 7.8 | 4 (ia=4, newbie=4) |
  | photosearch-star-rating-feedback-popup | 5.4 | 7 (newbie=7, pm=8, ia=6) |
  | mobile-company-varnish-optimization | 4.6 | 7 (all three = 7) |

  Middle-band pages correlate; extremes don't. The scorer is over-rewarding
  pages with full canonical H2 shape (concept_shape=10,
  source_density=10) even when the content is thin. The judge is
  over-forgiving of thread-subject H2s when the content is rich.

**Conclusion**: V12 made the agent **adopt V12 structure (new sections,
footnotes, concept-style H2s)** but didn't shift **the content depth the
judge cares about (ownership, rollout state, decision tracking,
cross-linking)**. The structural ceiling caught up; the content floor
didn't rise as much.

---

## 2. What V12 actually changed

Data from per-page pre/post diff against `.snapshots/pre-compile-20260423T102909Z/`:

### Structural adoption (V12-U1 through V12-U4 teaching)

| Change | Pages | Detail |
|---|---:|---|
| New `## Recent changes` section added | **11/16** | V12-U4 working on update flows |
| New `## References` footnote section added | **9/16** | V12-U3 teaching landed; 2 legacy sections remain |
| New inline `[^msg-*]` footnotes | **13/16** | 77 total refs added (avg 4.8 per touched page) |
| Summary rewritten toward current-truth | **3/16** | **Load-bearing gap** (see §4) |
| Bad-list H2s removed (none added) | **0/16** | Agent did NOT clean up legacy thread-subject H2s |
| 2 net-new concept-shaped pages | pns-calls, tov-display | `Summary / Why it matters / How it works / Open questions / Recent changes / Related pages / References / Related` — canonical V12 shape |

**The two new pages are the clearest V12 success**. They were compiled
from scratch with the V12 stack and hit the target structure. They score
8.2 (scorer) but 4.3–4.7 (judge mean) — the shape is perfect, the
content is thin and doesn't answer the personas' questions.

### Agent behavior patterns (Langfuse traces)

Per-model tool-call + duration means over 50 batches:

| model | n | mean dur (s) | mean tools | mean in_tok |
|---|---:|---:|---:|---:|
| qwen/qwen3.5-122b-a10b | 5 | **38.4** | 17.2 | 533K |
| x-ai/grok-4.1-fast | 13 | 91.5 | 16.3 | 186K |
| minimax/minimax-m2.7 | 10 | 122.7 | 15.1 | 326K |
| moonshotai/kimi-k2.6 | 12 | 171.5 | 14.8 | 318K |
| z-ai/glm-5 | 10 | 168.5 | **25.8** | 520K |

- **qwen/qwen3.5-122b-a10b is the fastest** (mean 38s) on trivial_skip
  but only 5 batches — small sample, no data on full-compile behavior
  yet.
- **z-ai/glm-5 is the most tool-hungry** (25.8 calls/batch) —
  reads more, edits more. Batch 17 (z-ai/glm-5) ran 586s with 88 tool
  calls and 4 reviewer cycles, bouncing across 3 slugs (m-site /
  m-site-pdp / m-site-opening-enquiry-form) before settling. That's
  the in-batch churn the sibling-draft-check middleware
  (`src/compile/middleware/sibling_draft_check.py` — v11-U9) was
  designed to catch; evidently it isn't firing (or is configured
  too loosely).
- **moonshotai/kimi-k2.6 (new)**: middling. Compiled mcat-cleaning in
  707s (batch 46, 38 tools, 2 reviewer cycles) — longest batch in
  the run. **One anomalous batch 45** (`b3f4dd022eba`, kimi-k2.6):
  log shows `normalized 0 pages` but `Progress` counter incremented
  16→17 at batch 46 boundary, and no `edit_file`/`write_file`/`patch_page`
  call appears in the trace. Orphaned compile — worth a follow-up
  trace deep-dive.

Reviewer cycles on the 17 compiled batches: **11 with 1 cycle, 1 with
2 cycles (batch 46), 1 with 4 cycles (batch 17), 4 with 0 cycles**
(reviewer never invoked). The 0-cycle batches are the most
concerning — `pns-calls-otherspaidvfcp-custtype-23` and
`tov-display-improvement-high-buyleads` (the two newly-created pages)
were apparently never reviewer-checked, just written + returned.
Worth verifying the reviewer-gate middleware is still wired into
the `write_file` path for new pages.

---

## 3. Recurring anti-pattern inventory — 16 compiled pages

| Pattern | Hits | Pages |
|---|---:|---|
| `## QA Testing Results` H2 | **4** | mobile-company-varnish-optimization, msite-pdp-html-rewrite-phase-2, photosearch-star-rating-feedback-popup, m-site-opening-enquiry-form |
| `## Business Objective/Requirements` H2 | 3 | seller-bl-api-optimization, whatsapp-8181-carousel-ab-test, photosearch-star-rating-feedback-popup |
| `## Testing Results` (bare) H2 | 2 | seller-bl-api-optimization, whatsapp-8181-carousel-ab-test |
| `## Decision: <...>` H2 | 1 | photosearch-star-rating-feedback-popup (`Decision: Scale to 100%`) — **connects to #145**: this is a missed lazy-decision-page opportunity. Per CLAUDE.md the agent should mint a `[[decision/scale-photosearch-100pct]]` wikilink (post-stub materialisation), not inline it as an H2. |
| Body `## Related` AND frontmatter `related:` (dup) | **11/16** | all compiled topics except pns-calls, seller-performance-dashboard, tov-display, pns-call-summary, seller-bl-user-details |
| 0 inline `[^msg-*]` footnotes | 3 | gst-registration-..., seller-bl-user-details-..., m-site-opening-enquiry-form-... |
| Body `[[<name>-indiamart-com]]` email-slug wikilinks | **14/16**, **304 total** | all except pns-call-summary-lead-manager and buyer-searched-keywords-on-bl-card. Per `wiki/people/` directory scan these slugs DO resolve (auto-stub creates the person page on first wikilink), so they don't register as broken in `graph_health`. Readability/collision concern per judge persona findings in §5, not graph-breakage. |
| Summary kept verbatim (not rewritten for current-truth) | 11/16 | everything except 2 NEW pages + 3 partial rewrites |

**photosearch-star-rating-feedback-popup concentrates 4 anti-patterns** —
`Business Requirements`, `Background`, `QA Testing Results`,
`Decision: Scale to 100%`. `update_count: 11` (most-edited page in the
run). The agent keeps producing design-doc shape on this one. V12 did
NOT reshape it.

---

## 4. Why the scorer + judge disagree — 5 hypotheses validated against data

### H1 — Scorer rewards structural completeness; judge rewards content depth

**Evidence**: `pns-calls-otherspaidvfcp-custtype-23` has canonical V12
shape (8 H2s: Summary/Why it matters/How it works/Recent changes/Future
direction/Related pages/References/Related), scorer=8.2. Judge scores
4–6 because Summary is thin ("OthersPaidVFCP is a seller category for
GST un-verified paid sellers"), has only one source thread, no owner,
no rollout percentage. **Judge's "Missing" list**: team ownership,
metrics, rollout state.

**Conclusion**: Confirmed. Scorer is a structure detector.

### H2 — The scorer's `summary_currency` heuristic isn't catching weak summaries

**Evidence**: `mobile-company-varnish-optimization` summary is
"Enhanced cache performance and stability optimization for mobile
company pages on IndiaMART's m-site platform." — 16 words, no dates, no
metrics, no ownership. Scorer gives it currency=5 (neutral, no bad
tokens). Judge (newbie=7, pm=7, ia=7) scores it higher. **Wait — judge
gave it 7, scorer gave it 4.6 → this is the INVERSE of H1.**

**Re-examining**: the personas scored `mobile-company-varnish-
optimization` at 7 because it has **more detailed technical content**
and was written coherently. Scorer gave it 4.6 because it has
`## QA Testing Results` (anti-pattern, -2 concept_shape) +
0 incoming wikilinks (graph_health=0) + dup `## Related` (structural=4).

So: the scorer **over-penalizes structural smells when the content is
actually strong**, and **under-rewards weak content when the structure
is clean**.

### H3 — The judge is forgiving of thread-subject H2s when content is concrete

**Evidence**: `photosearch-star-rating-feedback-popup` has 4
anti-pattern H2s (`Business Requirements`, `Background`,
`QA Testing Results`, `Decision: Scale to 100%`) — scorer 5.4, judge
7.0. The judge saw Impact Analysis with a 114% feedback-volume lift
quote, 72% positive-rate data, and a concrete Jan 2 → Jan 9 rollout
timeline — it didn't care that the structure was design-doc shaped.

**Conclusion**: Confirmed. When the **prose carries the facts the
persona wants** (metrics, dates, stakeholder attribution), thread-subject
H2s are a style grievance not a content failure.

### H4 — Summary rewrite isn't happening on UPDATE flows

**Evidence**: Only 3 of 16 pages had summary-rewrite detected
(prefix-similarity < 0.7 between pre and post). 2 of those 3 were NEW
pages (no pre-Summary to rewrite). The 11 updates kept the
pre-V12 Summary verbatim. V12-U4 teaches "rewrite the relevant sentence
in the Summary to reflect current truth"; the agent isn't doing it on
existing pages.

**Possible cause**: the agent reads the Summary, sees it's
already-written, and skips per some internal rule ("don't re-litigate
prior content"). V12-U4 teaching is additive not enforcing — no
critique fails an unchanged Summary when new facts arrived.

**Conclusion**: **Most critical gap surfaced in this audit**. Current
V12 is a create-time intervention, not an update-time one.

### H5 — Reviewer isn't firing on NEW pages

**Evidence**: `pns-calls-otherspaidvfcp-custtype-23` and
`tov-display-improvement-high-buyleads` (the two newly-created V12-
shaped pages) had **0 reviewer cycles**. Scorer gave them 8.2 and 7.8.
Judge gave them 4.67 and 4.33. The reviewer might have caught the
thinness if it ran.

**Conclusion**: Needs code-level verification — is the reviewer gate
wired to `write_file` of a new topic, or only to `patch_page` on an
existing one? If the latter, that's a real system gap.
**Code pointer**: `src/compile/middleware/check_my_work_gate.py:92-136`
(gate middleware) + `src/compile/compiler.py` (agent loop setup where
middleware is wired in). Tier 1 #1 fix lives there.

---

## 5. What the judges actually said — pattern distillation

Qualitative findings recurring across personas on bottom-5 pages
(complete judge CSV at `docs/feedback/judge-2026-04-23.csv`):

**"Missing" list themes, ranked by frequency**:

1. **No owner / DRI** — flagged on **15/16 pages** by at least one persona.
   "Cannot tell who to ping for status or blockers."
2. **No current rollout state / deployment status** — flagged on 12/16.
   "POC data from Dec 2025 but last_compiled is April 2026; unclear if
   100% rollout has happened."
3. **Unresolved open-question footnotes** — `[^msg-748d8628]` on
   `buyer-searched-keywords-on-bl-card` references nothing (orphan
   footnote). Same pattern flagged on 3 other pages.
4. **Missing target dates / timelines** for open questions — flagged
   repeatedly. "Open questions read like permanently open."
5. **No link to companion systems / topics** — graph-isolated
   topics. Email-slug wikilinks exist but concept-level cross-links
   don't.

**"What works" themes**:

- Decision tables + PDP-score tables get high praise when present
  (`agentic-auditor-product-approval-grid`).
- Dated milestones with named-raiser + footnote combo (V12-U3 working).
- TL;DR sections land well for newbie persona when they front-load
  numbers.

---

## 6. Cost + throughput — is V12 affordable at scale?

- **Per compiled page**: $0.34 (LiteLLM budget; includes preflight + agent turns).
- **Per trivial_skip**: $0.07.
- **Overall run**: $5.76 for 50 emails → 17 compiled (**compile-rate = 17/50 ≈ 34%**).
- **Full 5348-email queue extrapolation** (linear, same compile rate):
  `5348 × (0.34 × 0.34_compile_rate + 0.66_skip_rate × 0.07)`
  = 5348 × (0.1156 + 0.0462) = 5348 × 0.1618 ≈ **~$866**.
  (Codex flagged the earlier `$858` number as base-mixed; recomputed
  with explicit rate.)
  Current budget ceiling is $150; needs **~5-6× top-up** before
  full-queue is feasible.
- **Runtime extrapolation**: 50 emails took ~107 min wall at
  single-batch-serial = 2.14 min per batch mean. 5348 batches ×
  2.14 min ≈ **191 hours ≈ 8 days** single-serial.
  Parallelization to `--batch-size 5` cuts wall-time roughly 5× to
  ~38 hours if LiteLLM rate limits don't bottleneck — **untested**.

**z-ai/glm-5 tool-count**: 25.8 avg is a real outlier. If it produced
25–30% better outcomes, worth the cost; data doesn't support that —
scorer + judge results are comparable across models. Consider weighting
glm-5 lower in the pool OR capping tool calls per batch.

**Anomalous batch 45** (`b3f4dd022eba`, kimi-k2.6): orphaned compile.
Worth a dedicated trace dive — may indicate a silent failure mode.

---

## 7. What to ship next — ranked by ship-first value

### Tier 1 (ship this week, behavior-changing)

1. **Verify reviewer-gate middleware fires on new-page `write_file`**.
   If not, wire it. The 0-cycle count on NEW pages is the most
   actionable single finding. Cost: one targeted PR; potential impact:
   catches thin new pages before they ship.

2. **Critique warning: `## QA Testing Results` / `## Business
   Requirements` / `## Business Objective`** as named anti-patterns.
   Currently only the scorer catches these. If critique warned the
   agent mid-batch, the reviewer cycle would self-correct. Cost: one
   PR to `src/compile/critique.py`; potential impact: eliminates 6+
   anti-pattern H2s per 16-page batch.

3. **Update-time summary rewrite enforcement**. Teach the agent in
   `<revision_style>` that Summary currency is a **per-batch check**,
   not a create-time artifact. OR: add a critique `summary_staleness`
   check that fails when `last_compiled` age > `source_threads`
   max-date by some threshold, indicating the Summary is stale.
   Cost: medium PR, needs design thinking.

### Tier 2 (ship next, quality-of-life)

4. **Dup-`## Related` critique warning** (not blocker). When
   frontmatter `related:` is populated AND body has `## Related`,
   warn. Doesn't force removal (body may have more entries); surfaces
   the pattern. 11/16 pages would trigger.

5. **Investigate batch 45 orphaned compile** (kimi-k2.6, trace
   `b3f4dd022eba`). One batch isn't a pattern, but the failure mode
   deserves to be understood before scaling.

6. **Reviewer rule: open-question footnotes must resolve**. If body
   contains `[^msg-*]`, the `## References` section must have a
   matching definition. Currently a scorer heuristic; promote to
   critique blocker.

### Tier 3 (later, higher-cost)

7. **Email-slug → canonical-name-slug migration**. 304 instances
   across 14/16 pages. Needs email→name mapping; likely agent-assisted
   per-page cleanup.

8. **Scorer-judge alignment**: scorer over-rewards structure,
   under-rewards depth. The path forward is NOT making the scorer
   smarter (it'll always be structural) — it's treating judge as
   the primary signal and the scorer as a cheap pre-filter.
   Concretely: run judge on the scorer's top decile after each
   compile to verify (cheap: ~$0.30/page × 30 pages = $9/run).

### Explicitly NOT shipping

- Big-bang corpus migration for dup-Related or email-slugs. Per the
  v12 north star "compile-forward self-healing" doctrine, new
  compiles should gradually fix these. The scorer will measure the
  improvement trend over time.

---

## 8. What this audit does NOT answer

- Whether V12 prompts deliver on **"institutional intuition
  transfer"** (v12 north star primary goal). The judge personas
  evaluate page-level usefulness, not corpus-level graph
  navigation. To measure that, we'd need a Wikipedia-style
  random-walk evaluation — "can a new joiner navigate from a
  domain page to answer a specific question in N hops?" — which
  this run doesn't do.
- Whether the model pool mix is optimal. 5 models × 50 batches ≈
  10 batches/model, not enough to distinguish quality. Need a
  head-to-head: same emails compiled by each model, judge-scored.
- Whether V12-U3 footnotes actually improve reader trust or just
  add noise. The new `[^msg-*]` refs are cited; whether the
  personas **use** them wasn't measured.

---

## 9. Open tasks tracked

- **#145** — Lazy-decision-creation flow design (pre-existing
  architectural gap; unaffected by this run).
- **#146** — (this audit; marking complete upon merge)
- New: **reviewer-gate verification on new-page write_file** (Tier 1, #1 above).
- New: **critique anti-pattern H2 rules** (Tier 1, #2 above).
- New: **update-time summary rewrite** (Tier 1, #3 above).
- New: **orphan-batch-45 investigation** (Tier 2, #5 above).

---

## Raw artifact paths (for follow-up dives)

> **Note on `/tmp/` paths**: artifacts under `/tmp/` are ephemeral —
> they survive this session only. Only paths under `docs/feedback/`,
> `docs/audits/`, and `.snapshots/` are committed/persisted. See the
> methodology note at the end for why the Langfuse trace dumps are
> kept in `/tmp/` for this audit.


- Judge CSV: `docs/feedback/judge-2026-04-23.csv` (48 rows, 16 pages × 3 personas)
- Judge MD: `docs/feedback/judge-2026-04-23.md` (per-page qualitative)
- Scorer CSV: `docs/feedback/scorer-2026-04-23.csv` (305 full corpus)
- Scorer 16-page run: `/tmp/v12_50compile_score/scorer-2026-04-23.md`
- Compile log: `/tmp/compile_50_run.log` (2017 lines)
- Langfuse dumps: `/tmp/lf_trace_dumps/*.json` (50 individual traces)
- Pre-run snapshot: `.snapshots/pre-compile-20260423T102909Z/wiki/`

## Audit methodology notes

Langfuse is self-hosted at `langfuse.intermesh.net` and does **not**
emit `totalCost` for LiteLLM-proxied models (no price table loaded).
All cost figures in §6 come from the LiteLLM team budget via
`$BUDGET_ENDPOINT` output, not Langfuse. `input_tokens` used as a
cost-proxy ranking when needed.

`wiki/topics/*.md` is gitignored. Pre/post diffs use the pre-compile
snapshot at `.snapshots/pre-compile-20260423T102909Z/` instead of
`git log`. Snapshot directory is preserved until the next compile.
