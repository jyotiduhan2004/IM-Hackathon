---
timestamp: 2026-04-18T09:00:00Z
cycle: 9
run_id: ee584b6f-89f5-4240-81a4-8925ac8d545f
started_at: 2026-04-17 17:10 IST
ended_at: 2026-04-17 18:30 IST (~80 min)
cost_usd: 1.11
budget_remaining_usd: 2.65
outcomes:
  compiled: 15
  content_page_cited: 15
  orphan_skip_insights: 0
  duplicate_topic_pages_created: 0
citation_rate: 100%        # 15 / 15
baseline_citation_rate: "38-62%"   # Cycles 6-8 range
delivery_prs: [164, 165, 166, 167, 168, 169, 170, 171]
post_cycle_refactors: [172, 173, 174, 175, 176, 177]
fixes_verified:
  U1_scorecard_truth: held (no `write_draft_page` proxy; catalog-backed)
  U2_viewer_status_normalise: held (no orphan stub pages surfaced)
  U6_log_insight_autoheal: held (0 missing-email_path failures vs Cycle 8 Seller BL miss)
  U7_same_thread_topic_guard: held (0 new same-thread duplicate topics)
  U8_thread_context_concise: held (token budget down, no content loss)
observations:
  reviewer_path: healthy; verdict=pass on all 15 compiled
  new_quality_risk: prompt ↔ validator drift (see section below)
  langfuse_bugs_surfaced: 4 (coordinator in prompt, resolve_page ordering, glob timeouts, merge handoff)
ship_gate_status: green for Cycle 10 once v9 plan lands
---

# Cycle 9 Summary — 2026-04-18

## Scope

This post-mortem documents Cycle 9 — the first run end-to-end validating the
v8 measurement + guard + self-heal work (PRs #164–#171). It compares the run
against the Cycle 8 baseline documented in
`docs/audits/cycle-8-summary-20260417.md`, maps each v8 unit to the failure
mode it fixed, and enumerates the remaining drift that motivates v9 before
Cycle 10 scales.

Code snapshot reviewed:

- `origin/main` at `a6dd4d5` (post PRs #164–#177; U1–U8 landed, refactors
  #172–#177 landed on top)

Data sources used:

- Postgres `compile_attempts`
- Postgres `messages`
- Postgres `message_touched_pages`
- Postgres `compile_insights`
- `wiki/log.md` batch rows for Cycle 9 window
- Live wiki pages produced by the run
- 2026-04-17 user comments on Langfuse traces (mined across the Apr 13–17 window)
- `docs/audits/cycle-8-summary-20260417.md` (baseline)

Cycle label:

- Treat `run_id=ee584b6f-89f5-4240-81a4-8925ac8d545f` as Cycle 9 based on
  chronology — it is the first run where 100% of compiled messages landed on a
  content-type wiki page.
- Baseline comparison: Cycle 8 run `56433cbb-aff8-4bbf-a7d0-6a6c77b83159`
  (the headline run in `cycle-8-summary-20260417.md`).

## Executive Read

The v8 work hit the target. Content-page citation rate on compiled messages
went from 38–62% across Cycles 6–8 to **100% on 15/15** in Cycle 9. Zero
orphan skip-insights. Zero same-thread duplicate topic creations. Reviewer
passed on every compiled page. The measurement layer no longer lies; the
guard middleware no longer lets near-duplicates through; `log_insight`
self-heals the single-email omission that dominated Cycle 8's Seller BL miss.

The win is real, but it exposes the next bottleneck: **prompt ↔ validator
drift**. Spot-checks of 2 Cycle 9 pages show correct content and good
synthesis, yet each is missing 6/8 required H2 sections, the `domain:`
frontmatter field, and the 2-sentence lead paragraph that the validator
enforces. Every fresh compile adds to the validator backlog (now 399
`domain-missing` / 306 `sections-missing` / 263 `broken-wikilinks` / 244
`lead-paragraph` pages) because the prompt simply does not teach what the
validator checks.

Budget remaining is **$2.65 of $100** after Cycle 9 consumed $1.11. Cycle 10
requires a top-up and should not scale until v9 closes the drift, the
four Langfuse-surfaced tool/prompt issues, and the backlog backfills.

## Run Summary

### Cycle 9 headline

- `run_id`: `ee584b6f-89f5-4240-81a4-8925ac8d545f`
- window: `2026-04-17 17:10 IST` → `2026-04-17 18:30 IST`
- `compile_attempts` rows in window: 15 compiled, 0 failed
- unique messages compiled: 15
- content-type pages touched: 15 (1.0 ratio)
- cost: $1.11

| Outcome | Count |
|---|---:|
| `compiled` (content-page cited) | 15 |
| `compiled` (trivial-skip, already_captured) | — (not the denominator here) |
| `failed` | 0 |

Interpretation:

- The 15/15 ratio is measured against the corrected denominator from PR #170
  (v8-U1): non-trivial compile attempts where the reviewer reached terminal
  `pass` + the catalog shows a `message_touched_pages` row pointing to a
  topic/system/policy/decision page for the email. Trivial-skip and
  already_captured outcomes are excluded from both numerator and denominator
  — those are valid terminal no-ops, not content attempts.
- Zero reviewer `merge_candidates` verdicts became orphan content this run —
  but 6/93 reviewer verdicts across Apr 13–17 did emit `merge_candidates` that
  no tool can act on today. That gap is tracked as a v9 item (plan U14), not a
  Cycle 9 regression.

### Baseline: Cycle 8 for comparison

| Metric | Cycle 8 | Cycle 9 | Delta |
|---|---:|---:|---:|
| Compiled (content-page cited) | 8 | 15 | +7 |
| Effective rate (compiled / (compiled + failed)) | 88.9% | 100% | +11.1pp |
| Content-page citation rate on compiled | 38–62%\* | 100% | +38pp min |
| Orphan skip-insights in window | 31 | 0 | -31 |
| Same-thread duplicate topic pages created | 2 (Seller BL case) | 0 | -2 |
| Reviewer verdicts = `pass` on compiled | partial (not every compile reached reviewer) | 15/15 | cleaner |

\*Cycle 8's citation rate was a range because the headline scorecard
undercounted: `scripts/audit_50_traces.py` used `write_draft_page` tool calls
as the proxy for content attempts, but the current agent writes via
`write_file` / `edit_file` / `patch_page`. The 38–62% band is reconstructed
from `message_touched_pages` against compiled rows across Cycles 6, 7, and 8
— wider confidence interval is a function of the mismatched measurement, not
of runtime instability.

### Immediate comparison runs

| Run | Compiled | Content-cited | Citation rate |
|---|---:|---:|---:|
| `ee584b6f-89f5-4240-81a4-8925ac8d545f` (Cycle 9) | 15 | 15 | 100% |
| `56433cbb-aff8-4bbf-a7d0-6a6c77b83159` (Cycle 8) | 8 | ≈5 | ~62% |
| `b8f19851-5852-4429-90d9-287445f11750` | 14 | ≈9 | ~64% |
| `90fbcef7-4540-44ac-b517-f08a93d11c35` | 11 | ≈4 | ~38% |

Read:

- The Cycle 9 step is a phase change, not a trend continuation — citation
  rate goes from noisy 38–64% to a clean 100%.
- The headline change is not one fix: it is the *combination* of measurement
  honesty (U1), status vocabulary alignment (U2), single-email self-heal
  (U6), same-thread guard (U7), and concise thread context (U8) landing
  together. Each one on its own would have lifted the rate a little; the
  compound effect is what crossed 100%.

## Which v8 PRs Delivered the Win

The v8 plan decomposed into 8 parallel units. All 8 landed as PRs #164–#171
ahead of Cycle 9, plus 6 follow-on refactors (#172–#177) that did not ship
behavior but moved files the v9 plan references.

### Shipping units (land-order)

| PR | Unit | One-line | Impact on Cycle 9 |
|---|---|---|---|
| #164 | v8-U4 | `fix(reviewer): drop dead audit-doc link for duplicate_section rule` | Reviewer no longer emits broken links to archived audit docs. Quality-of-life for reviewer output parsing. |
| #165 | v8-U3 | `chore(tests): retire test_agent_toolbox.py` | Retired a stale duplicate-of-parser-surface test. Kept CI honest. |
| #166 | v8-U5 | `chore(compiler): clarify coordinator-owned helper docstrings` | Documented the invariant that certain helpers are coordinator-owned, not agent-callable. No runtime change; surface-level hygiene. |
| #167 | v8-U6 | `feat(log_insight): infer email_path in single-email batches` | **Self-heals the exact Cycle 8 failure mode** in the Seller BL thread (`19bb72fc748876c2`). Agent forgets `email_path`; tool reads `current_batch_email_path` from ContextVar and fills it. 0 failures in Cycle 9 vs 1 brittle-retry miss in Cycle 8. |
| #168 | v8-U2 | `fix(viewer): normalise page-summary status + stub defaults to new vocab` | Stub pages and page-summaries now default to `status: active` using the current 3-value vocabulary (active/superseded/archived). No orphan stubs with `draft`/`pending` status leaked into Cycle 9. |
| #169 | v8-U8 | `feat(tools): add response_format=concise to get_thread_context + promote shared strip_quoted` | Concise thread context shaves tokens on long threads without dropping context; the `strip_quoted` helper now lives in `src/utils/` and is reused by `get_thread_context` + reviewer. Helped the 15/15 run stay inside cache-friendly token budgets — median cache hit across Cycle 9 batches ran 60–90%. |
| #170 | v8-U1 | `fix(audit): batch-aware truth fixes in scorecard + 50-trace audit` | The foundational fix. Replaces the `write_draft_page` proxy with catalog-backed "did the message touch a content-type page?" join. Without this, the 100% Cycle 9 claim would have been unverifiable. 50-trace audit now groups by `trace_id` rather than `(run_id, thread_id)` (the Cycle 8 bug that overwrote same-thread traces). |
| #171 | v8-U7 | `feat(middleware): same-thread duplicate-topic guard` | `SameThreadTopicGuardMiddleware`: a catalog-truth 3-way join (`message_touched_pages` JOIN `messages` JOIN `wiki_pages`) plus in-run tracking rejects `write_file` / `create_page` attempts that would produce a second active topic page for a thread already represented in the catalog. The Cycle 8 exemplar (thread `19bb72fc748876c2` → `seller-bl-api-optimization` vs `seller-bl-user-details-verification-api-optimization` vs the legacy `seller-bl-api-hit-optimisation`) is exactly the pattern this blocks going forward. 0 new same-thread duplicates in Cycle 9. |

### Follow-on refactors (behavior-neutral, land-order)

These shipped between the Cycle 9 run and this writeup. They move files the
v9 plan references; calling them out here so v9 worker prompts point at the
right paths.

| PR | One-line | Why it matters for v9 |
|---|---|---|
| #172 | `refactor(utils): shared wikilink parser + fix #anchor false-positive` | Wikilink validator no longer false-flags `[[slug#anchor]]` as broken. The shared parser lives in `src/utils/` and v9 validator work will reuse it. |
| #173 | `feat(ci): add hygiene checks — file-size budget, dup fixtures, one-shot expiry` | New invariants: files ≥1500 LOC warn (ceiling), `compiler.py` grandfathered at 3500, `compile_all.py` at 2500. One-shot scripts must carry `Safe to delete after: YYYY-MM-DD` in the docstring. v9 backfill scripts (U3/U4/U5) must comply. |
| #174 | `chore(scripts): classify script lifecycle + retire 2 confirmed one-shots` | Established the pattern for labeling new scripts. |
| #175 | `refactor(compile): extract main() phase helpers + dedupe frontmatter stamp` | `scripts/compile_all.py` is now phase-sliced. v9's post-batch hooks (U14 merge-candidate queue, U13 per-observation scores) land inside the new helpers. |
| #176 | `refactor(compile): extract landing, draft, raw-access from compiler.py` | **Moves `resolve_page` to `src/compile/tools/raw_access.py:65`.** v9-U11's fix target (`search_pages` ranking) stays in `src/db/wiki_pages.py`, but callers now route through the extracted module. |
| #177 | `refactor(tests): consolidate duplicated fixtures` | New `tests/conftest.py` fixtures (`compile_all_module`, `mini_wiki`) and `tests/_script_loader.py::load_script`. v9 tests MUST reuse these — the hygiene checker from #173 will flag duplicates. |

Cycle 9 ran on origin/main *before* #172–#177 landed, so the citation-rate
result is attributable to U1–U8 alone. The refactors are bookkeeping for
downstream v9 worker prompts.

## What Still Burns Budget (Motivates v9)

### 1. Prompt ↔ validator drift

`scripts/validate_wiki.py` enforces, per topic page:

- A required H2 section sequence: Summary → Current state → Why it matters
  → Key decisions → Recent changes → Open questions → Related pages →
  References.
- A lead paragraph ≥ 2 complete sentences before the first H2.
- A `domain:` frontmatter field from the canonical 8-domain set.

`src/compile/prompts.py` teaches **none** of these. Not in `<page_types>`,
not in `<few_shots>`, not anywhere. Result: every new page lands with
validator errors on day one.

Spot-check evidence from Cycle 9 output:

**`wiki/topics/seller-performance-dashboard-desktop-lms.md`** (batch 18,
thread `19b3586381fbd310`):

- Content quality: ✓ (synthesised IM INSTA 50% rollout, adoption metrics,
  impact numbers, Shyam Taparia's decision to scale to 100%).
- Required sections present: 2 of 8 (`Current state`, `Related`).
- Missing: `Summary` (H2), `Why it matters`, `Key decisions` (uses
  `## Decisions` instead), `Recent changes`, `Open questions`, `References`.
- Missing `domain:` frontmatter field entirely.
- No 2-sentence lead paragraph — single sentence only.
- `last_compiled` + `updated_by` stamped correctly by the coordinator.

**`wiki/topics/bl-email-alert-district-matchmaking.md`** (batch 5 follow-up,
thread `19bb5b85e264f94d`):

- Content quality: ✓ (distance-band tables, QA test counts, DB tables,
  rationale for city→district shift beyond 50 km).
- Required sections present: 2 of 8 (`TL;DR`, `Background` — neither is in
  the enforced list).
- Missing: `Summary`, `Current state`, `Why it matters`, `Key decisions`,
  `Recent changes`, `Open questions`, `Related pages`, `References`.
- Missing `domain:` frontmatter.
- Lead paragraph is a single sentence.

Both pages pass reviewer (content is good). Both fail
`scripts/validate_wiki.py` on sections + domain + lead paragraph. Cycle 10
at full queue scale (5348 pending messages) would produce several thousand
more pages with the same drift.

The fix is prompt-side (v9-U1 in the plan): teach the required H2 sequence,
teach the lead paragraph, teach the 8 canonical domains, mirror the exact
wording from `validate_wiki.py` so future validator changes can be reflected
in the prompt in a single edit. Not a validator change — the validator spec
is the truth.

### 2. Validator backlog (snapshot at run boundary)

From the Cycle 9-tail `make lint-wiki` run:

| Check | Pages affected |
|---|---:|
| `domain-missing` frontmatter | 399 |
| `sections-missing` required H2 | 306 |
| `broken-wikilinks` (1600+ links total) | 263 |
| `lead-paragraph-missing` (≥ 2 sentences) | 244 |
| `dated-H2-section` titles (Bug F) | 74 pages / 207 sections |
| `legacy-sources-only` (missing `source_threads`) | 2 |
| `legacy-index` (wiki/entities/index.md still present) | 1 |
| `frontmatter-malformed` | 6 |

These are not Cycle-9-new. The drift has been accumulating since before the
measurement layer could see it. v9 waves B/C address them: U3 domain
backfill script (399 pages), U4 dated-H2 rewriter (74 pages / 207 sections),
U5 entities→people data migration (collapses the legacy-index + feeds the
eventual 338-ref code rename).

### 3. Four Langfuse-surfaced issues (2026-04-17 user comments)

User annotations on Langfuse traces during the Apr 13–17 window surfaced
four concrete bugs in the live system. Each was validated against trace
volume before being written into the v9 plan.

#### 3a. "Coordinator" vocabulary leaks into the system prompt

Trace `3fd068698912c753a53f31a6fee2945e` shows the rendered prompt contains
15 references to the word "coordinator" across 7 distinct lines
(`src/compile/prompts.py` lines 62, 83, 149, 154, 247, 327, 529). "The
coordinator handles that", "the coordinator marks", etc. The LLM has no
mental model for "coordinator" — it is internal engineering vocabulary. When
the prompt says "the coordinator will stamp `last_compiled`", the agent's
only grounding is "something I shouldn't worry about", which is the correct
behavior but for the wrong reason. Worse, references to
`ContextVar`, `middleware`, and `AgentMiddleware` leak through in similar
passive voice. v9-U1 strips all of this and rewrites as direct second-person
prohibitions ("NEVER call `mark_as_compiled` — it is not a tool").

#### 3b. `resolve_page` returns candidates in alphabetical order on 58% of misses

Of 329 eligible `resolve_page` misses (queries where `exists=False` AND ≥3
candidates were returned), **191 (58%)** returned candidates in monotonic-
lowercase order. The root cause is `search_pages` in
`src/db/wiki_pages.py`: after the tier ranking (slug = / title = /
starts-with / substring) it tiebreaks on `page_id ASC`, which for
batch-inserted pages is effectively alphabetical on slug. Exemplar: trace
`b6b6e3cca97e48976a0e7841f709b6c3`, query `"price-widget"` returns
`auditmate-...` as the top candidate instead of `bottom-price-widget-...`
or `seo-price-widget-...`. The agent acts on the first candidate;
alphabetical ordering actively misleads it on any query that does not hit a
unique exact match. v9-U11 fixes the tiebreaker — trigram similarity if
`pg_trgm` is available (check `SELECT * FROM pg_extension WHERE extname =
'pg_trgm'`), touch-count recency as fallback.

Note: #176 moved `resolve_page` to `src/compile/tools/raw_access.py:65`,
but the underlying `search_pages` call still lives in `src/db/wiki_pages.py`
where the fix needs to go.

#### 3c. Glob timeouts at 24.5% rate

**69 of 282 (24.5%)** glob calls timed out at the hardcoded 20s limit in
the deepagents `filesystem.py:58`. p50 latency 7.6s, p95 27s. **100% of
timeouts are on `**/<slug>.md` patterns** — the agent uses glob as a fuzzy
slug lookup, which is exactly what `resolve_page` is designed for. Net cost:
69 × 20s ≈ 23 minutes of wall-clock burned per 5-day window, plus model
retries on the timeout output. v9-U12 either retires glob from the agent
surface (preferred — the agent already has `list_wiki_pages`, `grep`,
`resolve_page`, `ls`, `read_file`) or narrows it via middleware that rejects
`**/<slug>.md` patterns with a tool-message nudge toward `resolve_page`.

#### 3d. Reviewer emits `merge_candidates`, agent has no merge tool

**6 of 93 (6.4%)** reviewer verdicts in the window carried non-empty
`merge_candidates`. None could be acted on. `wiki_merge_pages` is referenced
in `CLAUDE.md` but does not exist in code. Cycle 8 exemplar trace
`97bf63ebfe1c5d01`: Seller BL thread, 3 duplicate active pages, reviewer
verdict = `block` with 3 merge candidates attached. The reviewer is doing
the right thing; the system just drops the output on the floor. v9-U14
closes this as a coordinator-driven queue (`wiki/merge_candidates.md`
append-only) plus a manual `scripts/apply_merge_candidate.py --dry-run`,
deferring a true agent-side `wiki_merge_pages` tool until the queue
generates enough signal to justify one.

## Case-Study Anchors

### The thread the v7 middleware now guards: `19bb72fc748876c2`

Seller BL user details verification API optimization. Cycle 8's headline
miss: the thread fragmented into two active topic pages
(`seller-bl-api-optimization` and
`seller-bl-user-details-verification-api-optimization`), plus a legacy
`seller-bl-api-hit-optimisation` from an earlier cycle — three pages for
one conceptual project. Follow-up emails on the thread were ambiguous merge
targets; later compiles could see the thread as "already covered" or "not
yet merged" depending on which page `resolve_page` returned first (see §3b).

Cycle 9: the `SameThreadTopicGuardMiddleware` from PR #171 blocked new
same-thread topic creation. No new duplicate was added to the pile. The
three legacy pages remain on disk and still need human-assisted merging —
the merge workflow is v9-U14 — but the *accretion* stopped. That is the
bar for declaring the guard working: no new debt while the old debt
drains.

### The good-path: `19b3586381fbd310` (Seller Performance Dashboard)

Batch 18 of the Cycle 9 window. Single substantive email from Shyam
Taparia announcing the 50% rollout of the Seller Performance Dashboard in
Desktop LMS to IM INSTA sellers with adoption metrics (35% using at least
one filter), impact numbers (+3.5% enquiries responded within 24 hours
overall, +67.3% for top-10 power users), and a decision to scale to 100%.

Outcome: new topic page `seller-performance-dashboard-desktop-lms.md`
written and cited in `message_touched_pages`. Reviewer verdict = `pass`.
Coordinator stamped `last_compiled` correctly. Content is good.

Caveat (see §1 above): the page ships with 2/8 required H2 sections, no
`domain:`, and a single-sentence lead paragraph. Reviewer pass ≠ validator
pass. Fixing the gap is v9-U1.

### The good-path-with-polish: `19bb5b85e264f94d` (BL district matchmaking)

Two-email thread; the second email landed in Cycle 9 as `update_count=2`
on the existing `bl-email-alert-district-matchmaking.md` (created earlier
from raw file `2026-01-13_mplaunchim-enhancement-in-email-alerting-logic-pri_7262cd91.md`).
`resolve_page` correctly surfaced the existing page; the agent grew it
rather than creating a duplicate. Reviewer passed.

This is exactly the "grow an existing concept page" behavior the
deduplication story relies on. Drift (same validator issues as above)
aside, the workflow ran as designed.

## Post-Cycle-9 → Cycle 10 Gate

v9 plan (`/Users/amtagrwl/.claude/plans/sparkling-skipping-fiddle.md`)
decomposes into 14 parallel units:

### Wave A — Cycle 10 preflight (MUST)

- **U1** — prompt ↔ validator alignment + internals strip (closes §1 + §3a).
- **U2** — scorecard effective-rate denominator fix (160% scorecard bug).

### Wave B — backlog cleanup scripts

- **U3** — 399-page `domain:` backfill.
- **U4** — 74-page / 207-section dated-H2 rewriter.
- **U5** — `wiki/entities/` → `wiki/people/` data migration (legacy-index +
  6 malformed frontmatters).
- **U6** — N+1 connection fix in `backfill_source_threads_and_touches`.

### Wave C — reader features

- **U7** — domains as multi-value frontmatter tags.
- **U8** — home page 8 domain cards (depends on U3 + U7).

### Wave D — prompt compression

- **U9** — merge `<workflow>` + `<decision_tree>` (~1200 tokens saved,
  conflicts with U1 so lands last).

### Wave E — writeup

- **U10** — this document.

### Wave F — Langfuse-surfaced tool/prompt issues

- **U11** — `resolve_page` relevance ranking (closes §3b).
- **U12** — glob retire/narrow + latency instrumentation (closes §3c).
- **U13** — per-observation Langfuse scores for the new regressions.
- **U14** — merge-candidate handoff (closes §3d).

Delivery order: U1 alone first (behavior-critical, smoke-test on
`--limit 1` before the parallel wave), then U2/U3/U4/U5/U6/U10/U11/U12/U13/
U14 in parallel (disjoint files), then U7, then U8, then U9 last.

Cycle 10 runs at `--limit 100` first against the 5348 pending queue to
validate the v9 work under load, then scales to full. Budget top-up to
~$100 required before any scaling pass — the $2.65 remaining won't cover
100 batches at Cycle 9's observed $0.074/message rate.

## Risks Carried Forward

1. **U1 is behavior-critical** — a bad prompt change could regress Cycle 10
   below Cycle 9's 100% citation rate. Mitigation: `--limit 1` smoke before
   parallel wave.
2. **U11 depends on `pg_trgm`** — worker spikes on extension presence first.
3. **U12 retirement may regress legitimate glob use** — the 100%-timeout-on-
   `**/<slug>.md` read suggests no legitimate pattern exists, but double-
   check the last 30 days before retirement. Narrowing is the safer fallback.
4. **U14 is optional for Cycle 10** — 6/93 unacted merges is small signal.
   Defer the queue if the coordinator change turns out risky.
5. **Validator backfills (U3, U4, U5) are one-shot scripts** — each requires
   the `Safe to delete after: YYYY-MM-DD` docstring marker per PR #173, plus
   `--dry-run` default and explicit `--commit`. U5 mutates both filesystem
   and DB; snapshot before first `--commit`.

## North-Star Read

Cycle 9 validates that the v8 thesis — measurement + guard + self-heal —
was the right focus. Citation rate is a North Star metric for "does the
system actually write to the wiki instead of skipping or filing into
people pages?" We're now at the ceiling of that metric.

What changed versus Cycle 8:

- measurement no longer lies about content attempts
- same-thread duplicate creation is actively blocked
- `log_insight` self-heals the dominant single-email failure mode
- stub/page-summary status vocabulary is consistent with the 3-value spec
- thread context is concise without losing information

What is now the dominant cost, in order:

- prompt ↔ validator drift (every compile adds debt)
- `resolve_page` misranking (agent acts on misleading candidates)
- glob timeouts (wall-clock waste)
- merge queue non-existent (reviewer signal unacted)

All four are in v9. None are architectural; all are tractable with the
effort in the plan.

## Should the North Star Extend?

Not yet. The Cycle 8 writeup asked the same question and deferred. Cycle 9
confirms the deferral was correct. The North Star continues to be:

- every actionable email ends terminally
- same-thread duplicate topic creation is blocked or explicitly reviewed
- audit scripts measure the real runtime
- **new**: pages that land on disk pass the validator they're written against

The fourth criterion is the Cycle 9 lesson. Add it to the acceptance list
before Cycle 10 scale.
