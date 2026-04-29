---
title: "Cross-run synthesis — 4 smoke runs (3e88f996 → 5928c151 → 8fa45533 → ee90d7ce)"
audit_kind: cross-run-synthesis
audit_date: 2026-04-29
runs:
  - id: 3e88f996-3ee7-4653-b7b0-156c6c960201
    label: pre-V12 baseline
    commit: pre-#251/#252/#253
  - id: 5928c151-1109-4c24-abc2-088e9e0693a6
    label: V12 prompts landed
    commit: c1db495 (post-#259/#260/#262)
  - id: 8fa45533-e518-4d3c-98f7-1cdbf6e34d6d
    label: compile-module split
    commit: f4f7ccd (post-#261)
  - id: ee90d7ce
    label: F1/F2/F3 fixes
    commit: post-#281/#282/#283/#284 (mid-run audit pending)
---

# Cross-run synthesis — 4 smoke runs (2026-04-29)

This is the meta-doc for the audit cycle that ran 2026-04-28 → 2026-04-29.
Four smoke runs, four parallel audits per run, ~20 source documents
consolidated. Inputs cited inline as `(run-X audit doc, finding N)`.

## 1. Master metrics table

Columns are runs in chronological order. Cells with `—` indicate the
metric wasn't measured in that audit. `n/a` indicates not applicable
(e.g. judge audit deferred for that run).

### Run-level

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| Compiled at audit (n) | 153 (final) | 84 (mid) | 65 (mid, 57 audited) | 90 (mid) |
| Pages touched | 42 (4 sys + 38 topic) | 18 topic | 19 (2 sys + 17 topic) | 27 (4 sys + 22 topic + 1 pending) |
| Cost / email | $0.086 | $0.107 | $0.092 | **$0.071** |
| p50 latency / batch | 5.1 min | ~5.9 min | 5.5 min | — |
| p95 latency / batch | 8.2 min | 12.1 min | **9.7 min (–20%)** | — |
| Fatal failures (batches) | 0 | 5 (1 batch, b4 429) | **0** | 0 (per dipstick) |
| Skipped (insight:trivial) | 11 | — | 8 | — |
| outcome=NULL (in-flight) | 5 (kill) | 5 (wedged b17) | 5 (b20 in flight) | — |

Source: trace audits — 3e88f996 §1, 5928c151 §1, 8fa45533 §1; ee90d7ce
dipstick `docs/runs/smoke-ee90d7ce-mid.md`.

### Page-quality

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| Heuristic mean (topics) | **7.76** | 7.67 | 7.65 | **7.52** |
| Heuristic mean (systems) | 8.15 | n/a | n/a (not scored) | — |
| F1 — body refs / 0 defs | 2/42 (4.7%) | 9/18 (50%) | **11/19 (58%)** | — |
| F1 — any missing defs | 4.7% | n/a | **13/19 (68%)** | — |
| Orphan defs (def w/o body cite) | not measured | not measured | 3/19 (12 orphans on 1 page) | — |
| Empty `## References` heading | 0 | 0 | 1 (catalog-search-misc) | — |
| Empty frontmatter `{}` | 2/42 (4.8%) | **0** | 0 | — |
| Placeholder leaks (`(preserve existing)`) | 2/42 | 0 | 0 | — |
| Past-tense lead defect | 7 short leads + 4 H2-first | 1 (soi-product-gst) | 0 in deep-read | — |
| Boy-scout deep-read | 0–1/3 | 0–1/3 | 1/19 | — |
| People-slug leakage (worst page) | 71 links | 41 (company-api) | 60 (bl-purchase-wa9696) | — |
| Broken outgoing wikilinks | 1 page | 7 (mostly path-shape) | **0/19** | 10 pages flagged in dipstick |
| Duplicate `## Related` blocks | 33/42 (79%) | ~5/18 (most have FM+H2 dup) | every multi-pass page | — |

Source: page-quality audits per run; F1 figures from each run's "F1
sweep" section.

### Persona

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| PM mean (0–3) | 0.78 | 2.13 | **2.50** | — |
| PM=3 count | 0/9 | 1/8 | **4/8** | — |
| PM ≥ 2 (forwardable) | 0/9 (0%) | 8/8 (100%) | 8/8 (100%) | — |
| Eng mean (0–3) | ~0.85 | 1.50 | **1.88** | — |
| Eng=3 count | 0/9 | 0/8 | **1/8** (`buyleads-ni-grid-apis`) | — |
| Eng ≥ 2 (ramp-ready) | 0/9 (0%) | 4/8 (50%) | **6/8 (75%)** | — |
| Owner field in frontmatter | 0/9 | 6/8 (75%) | 7/8 | — |

Source: persona audits — 3e88f996 PM/eng splits, 5928c151 personas
§"Headline", 8fa45533 personas §"Headline".

### LLM judge (n=5 per run)

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| Judge mean | n/a | 7.13 | 5.93 | (in flight) |
| Heuristic mean (same sample) | n/a | 7.68 | 7.76 | — |
| Pearson r (heur vs judge) | n/a | **−0.758** | **+0.518** | — |
| Cost (5 pages × 3 personas) | n/a | $1.50 | $1.50 | — |

Top weakness patterns (judge):

| Pattern | 5928c151 | 8fa45533 |
|---|---|---|
| Stale dates / unresolved open Qs on `active` pages | dominant smell, 5/5 | pervasive, 5/5 |
| Entity-slug fragmentation (numeric / email-suffix) | flagged on multi pages | 3/5 pages |
| Missing system-layer wikilinks | 2 of 5 explicit | 4/5 explicit |
| Duplicate `## Related` body block | not surfaced | flagged on every IA-persona run |

Source: judge audits — 5928c151 LLM-judge §"Strongest weaknesses";
8fa45533 LLM-judge §"Weakness patterns".

### Middleware

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| ReconnaissanceParalysis fires | n/a (pre-#251) | 7 | 7 | — |
| EditStaleness proactive fires | n/a | 11 | — | — |
| EditStaleness reactive fires | n/a | 3 | — | — |
| StuckHeartbeat fires | n/a | 0 (b17 stalled inter-batch) | 0 (no need) | — |
| MidRunPoolQuarantine fires | n/a | 0 (b4 429 didn't trigger) | 0 (no need; pool healthy) | — |
| CheckMyWorkGate blocks | not measured | 27 (1.59/batch) | not measured | — |
| Read:write (incl cmw/log/todos) | 3.47:1 | 2.75:1 | **1.79:1** | — |
| Read:write (pure write) | 3.9:1 grok / 3.8:1 glm | not split | 5.38:1 | — |

Source: trace audits §3 / §4 of each.

### Pool

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| Pool advertised | grok + glm-5 (kimi quarantined) | grok + glm-5.1 + glm-5 | grok + glm-5.1 + glm-5 | grok + glm-5.1 + glm-5 |
| grok pick % | 47% (22/46) | **0%** (0/17) | 42% (24/57) | — |
| glm-5.1 pick % | n/a | 41% (7/17) | 40% (23/57) | — |
| glm-5 pick % | 53% (24/46) | 59% (10/17) | 26% (15/57) | — |
| kimi status | auto-quarantined (60/88 fail) | auto-quarantined | auto-quarantined | auto-quarantined |
| Pool fairness | OK | broken (grok=0) | **restored** | observable via #283 log |

Source: trace audits §1; "Pool fairness" rows.

### Tool calls (top-5)

| Run | 1st | 2nd | 3rd | 4th | 5th |
|---|---|---|---|---|---|
| 3e88f996 | read_file 28% | resolve_page 17% | check_my_work 10% | edit_file 8% | glob 6% |
| 5928c151 | read_file 29% | resolve_page 13% | check_my_work 11% | edit_file 11% | log_insight 7% |
| 8fa45533 | read_file 28% | resolve_page **18%** | check_my_work 11% | edit_file 8% | get_page_summary 5% |
| ee90d7ce | (in flight) | | | | |

Calls/turn median:

| Run | tools/batch | turns/batch |
|---|---:|---:|
| 3e88f996 | 41 | 20 |
| 5928c151 | 42 | 23 |
| 8fa45533 | **34 (–20%)** | **18 (–22%)** |

### qmd / resolve_page

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---:|---:|---:|---:|
| qmd_timeout_s config | 45 | 45 | 45 | **60 (#281)** |
| `resolve_page` 45s timeout rate | 8.9% (28/316) | 15.4% (14/91) | **33.9% (38/112)** | — |
| `resolve_page` p50 (s) | n/a | 23.10 | 26.96 | — |
| `resolve_page` p90 (s) | n/a | n/a | **45.15** | — |
| `glob` calls | 105 | 17 | 19 | — |
| `glob` timeout rate | 68% | 88% | **95%** | — |

Source: trace audits §6 (8fa45533), §2 (5928c151), §1 (3e88f996).

---

## 2. PR-to-effect mapping

| PR | What it shipped | Run that first carried it | Measured effect | Verdict |
|---|---|---|---|---|
| **#259** | V12 mechanical prompt reorg (no behavior change) | 5928c151 | Carried with #260 — see below | enabling |
| **#260** | V12 lead-paragraph + open-gates contract | 5928c151 | **PM forwardable 0/9 → 8/8 (100%); PM=3 0 → 1; eng ramp-ready 0/9 → 4/8** (5928c151 personas §Headline) | **DELIVERED — single biggest win in the cycle** |
| **#261** | Compile-module split (`src/compile/` → `wiki + agent + coordinator`) — no behavior change | 8fa45533 | **0 quality regression**; tools/batch −20%, turns −22%, p95 latency −20%; observation tagging fully intact (8fa45533 trace §2) | **DELIVERED — refactor + free latency win** |
| **#262** | Post-run dipstick for prompt-revamp signals | 5928c151 | Observability — surfaced F1 visibility | enabling |
| **#280** | IST page timestamps | (orthogonal to compile) | Reader-side cosmetic | shipped |
| **#281** | `qmd_timeout_s` 45 → 60 | ee90d7ce | UNVERIFIED at audit point. Withdrawn-then-shipped: 8fa45533 audit (F11) explicitly recommended diagnosing the embedding service *before* bumping the cap; the bump shipped anyway. **Risk:** masks service degradation rather than fixing it. | **AT RISK — wrong layer fix** |
| **#282** | Boy-scout prompt scope-tightening + repair verbs | ee90d7ce | UNVERIFIED at audit point. Direct response to F2 (5928c151 + 8fa45533 findings). Will need next deep-read to confirm. | **PENDING VERIFICATION** |
| **#283** | Per-batch effective-pool + pick log | ee90d7ce | Pure observability win — closes F8 (grok under-pick). No behavior change. | **DELIVERED (observability)** |
| **#284** | Coordinator-side `## References` backfill | ee90d7ce | UNVERIFIED at audit point. Direct response to F1 / F1+ (8fa45533 found 58% pure-F1 + 12 orphan defs). If it works, this single PR moves ~11 of every 19 pages from "unverifiable" to "verified". **Highest-leverage change of the cycle.** | **PENDING VERIFICATION — biggest stake** |
| **#286/#287/#288** | mkdocs viewer Docker-build fixes (src/ in build context) | (orthogonal) | Viewer infra | shipped |

Cross-cycle deferred:

- **F4** (consecutive-429 short-circuit on `MidRunPoolQuarantineMiddleware`) — STILL UNTESTED across 8fa45533 + ee90d7ce because pool stayed healthy both runs. Synthetic-injection test recommended (8fa45533 trace §3, F-A).
- **F5** (inter-batch dispatcher watchdog) — UNTESTED, no inter-batch stalls reproduced after 5928c151's b17 wedge.
- **F6** (heuristic ⊥ judge anti-correlation) — DEMOTED on 8fa45533 (r flipped −0.758 → +0.518; small-n noise on prior round).
- **F7** (`engineering_surface:` structured frontmatter) — empirically validated by `buyleads-ni-grid-apis` Eng=3 (8fa45533 personas §"System-pages finding") but not yet shipped.

---

## 3. Three trends across runs

### 3.1 Monotonically improving

- **Latency p95** (5928c151 12.1 → 8fa45533 9.7 min) and **tools/batch** (42 → 34) and **turns/batch** (23 → 18) — #261 refactor delivered a measurable agent-overhead reduction even with "no behavior change" branding. (8fa45533 trace §1, §8.)
- **Read:write ratio** (3.47 → 2.75 → **1.79**) — #251 ReconnaissanceParalysis is compounding across runs. (8fa45533 trace §4.)
- **Persona scores** — PM mean 0.78 → 2.13 → 2.50; Eng mean 0.85 → 1.50 → 1.88. PM=3 count 0 → 1 → 4. First Eng=3 page ever in 8fa45533. (5928c151 + 8fa45533 personas vs 3e88f996 PM-persona §Headline.)
- **`glob` call count** (105 → 17 → 19) — agent prompt is mostly avoiding the slug-lookup shape now, even though per-call timeout rate climbed (68 → 88 → 95%). Wall-clock waste fell from 23.6 min to ~6 min. (3e88f996 trace F1; 8fa45533 trace §5.)
- **Cost/email** (0.086 → 0.107 → 0.092 → **0.071**) — non-monotonic but trending down after the V12 cost spike. ee90d7ce dipstick shows $0.071/email which is the lowest of the four; attribution is partly model-mix shift (more grok), partly prompt-cache (75–87% hit rates per 8fa45533 trace §7). (5928c151 findings F9; ee90d7ce dipstick.)

### 3.2 Flat (no discernible change)

- **Heuristic mean** sits at 7.65–7.76 across all four runs (3e88f996 7.76, 5928c151 7.67, 8fa45533 7.65, ee90d7ce 7.52). Heuristic is saturated and weak as a signal — it cannot distinguish V11 from V12 prompts. (5928c151 findings F6; 8fa45533 findings P2 demote.)
- **People-slug leakage** — every page in every run carries `[[*-indiamart-com]]` body wikilinks. Worst-page count drifts (71 → 41 → 60) but the structural defect is unchanged. (3e88f996 page-quality §5; 5928c151 page-quality §"Top 5 patterns" P5; 8fa45533 page-quality §"60-link outlier".)
- **Diagrams** — 0 across every persona deep-read sample. (3e88f996 eng-persona P8; 5928c151 personas; 8fa45533 personas §"What V12 still hasn't fixed".)
- **Boy-scout score** — 0–1/3 across all three runs that measured it. Clause at `prompts.py:324` never started firing usefully; #282 is the first attempt to fix this. (5928c151 page-quality boy-scout summary; 8fa45533 page-quality boy-scout column.)
- **Engineering-surface** — repo paths, GCP project IDs, namespaces, alert names: still absent across topic pages. Only `buyleads-ni-grid-apis` (a system page) ships engineer-actionable URLs. F7 still unbuilt. (3e88f996 eng-persona P5; 8fa45533 personas §"What V12 still hasn't fixed".)

### 3.3 Regressing or zigzagging

- **F1 citation plumbing** — refs without defs has gone 4.7% → 50% → 58% → unknown. The single largest regression of the cycle. #260's prompt change (mkdocs auto-renders References) removed agent's instruction to write the section, but the mkdocs hook doesn't write back to source. **Stake on #284**: the deterministic backfill must close this gap, otherwise V12 net-shipped a worse wiki than V11. (5928c151 findings F1; 8fa45533 findings F1+.)
- **`resolve_page` 45s timeout rate** — 8.9% → 15.4% → **33.9%** across three runs. **Embedding/qmd service tier is degrading**, not a code bug. #281's bump 45 → 60 is a symptom-treatment; root-cause diagnosis was deferred. (5928c151 findings F3; 8fa45533 findings F11 / trace §6.)
- **`glob` per-call timeout rate** — 68% → 88% → 95%. Climbing, but call count fell so wall-clock impact is still down. (8fa45533 trace §5.)
- **Heur-judge correlation** — flipped sign (−0.758 → +0.518) at n=5; this is statistical noise, not a real swing. F6's "scorer is broken" framing didn't survive the second run. (8fa45533 LLM-judge §"Correlation comparison".)
- **Multi-pass page rot** — 8fa45533 surfaced **orphan-defs** as a new defect on `update_count > 1` pages (`bl-purchase-whatsapp-9696` carries 7 orphan defs from prior compile passes). This wasn't measurable in 3e88f996 / 5928c151 because most pages there were greenfield. As more pages enter their second/third compile, this defect grows. F1+ proposes the same coordinator hook to GC orphan defs. (8fa45533 page-quality §"60-link outlier".)
- **Pool fairness** — 47% grok → 0% → 42%. The 0% in 5928c151 was small-n noise (F8, demoted). #283's per-batch log makes future regressions detectable. (5928c151 findings F8; 8fa45533 findings §"What's working".)

---

## 4. What the next round should test

Priorities are ordered by stakes-on-the-fix, not by ease.

1. **Did #284 close F1?** This is the single highest-leverage change in the cycle. The next audit must measure: (a) % of pages with body refs and 0 defs, (b) % of pages with `body_refs > defs` (any missing), (c) orphan-def count on `update_count > 1` pages — i.e. did the reverse-GC leg of the F1+ proposal land. Target: F1 pure-rate < 5% (back to V11 baseline), orphan-def count = 0 across all pages. Without this, the V12 cycle ends as "we made the wiki harder to verify."

2. **Did #281 actually help, or just shift the cliff?** `resolve_page` timeout rate at 60 s should be measured — if it sits >25% even at the new cap, that's evidence the cap-bump is wrong-layer; the qmd / qdrant service needs upstream attention. If timeout rate drops to <10% at 60 s, the cap was the issue. Either result is informative.

3. **Did #282 move boy-scout score off 0–1?** Run a deep-read sample of pages that have `update_count > 1` (multi-pass) and score them on: (a) was a previously-broken wikilink fixed in this pass? (b) was a stale Summary refreshed? (c) were duplicate `## Related` blocks consolidated? Boy-scout has been inert for 2 runs; verify that #282's repair-verb expansion actually triggers.

4. **Run an LLM-judge on system pages.** F15 was filed in 8fa45533 — `judge_wiki.py` is glob-locked to `wiki/topics/`. The single Eng=3 page (`buyleads-ni-grid-apis`) is a system page; we cannot audit its persona-readability without the judge fix. Patch + run on the 4 system pages in ee90d7ce.

5. **Persona deep-read on 8 pages spanning ee90d7ce.** PM mean has gone 0.78 → 2.13 → 2.50. Has it kept rising or saturated? Eng mean 0.85 → 1.50 → 1.88 — same question. The cycle's net story depends on whether PM=3 / Eng=3 page counts kept climbing.

6. **Validate F4 with synthetic 429 injection.** Both 8fa45533 and (per dipstick) ee90d7ce had 0 batch failures, so the consecutive-429 fast-path on `MidRunPoolQuarantineMiddleware` is **untested in production**. The next test should inject a 429 burst in dev and confirm the fast-path quarantines the model. Untested middleware is a tomorrow-incident waiting to happen.

7. **Do not** re-run the heuristic-vs-judge anti-correlation experiment as if it were a live finding. It was small-n noise; the second run flipped sign. Future judge runs should expand n (≥10 pages) before drawing correlation conclusions.

---

## Cross-references

- 3e88f996: `docs/audits/run-3e88f996-{summary,pm-persona,engineer-persona,page-quality,trace}-audit-2026-04-29.md`
- 5928c151: `docs/audits/run-5928c151-{findings,personas,page-quality,llm-judge,trace}-audit-2026-04-29.md`
- 8fa45533: `docs/audits/run-8fa45533-{findings,personas,page-quality,llm-judge,trace}-audit-2026-04-29.md` (in worktree `audit-8fa45533`)
- ee90d7ce: `/tmp/run-ee90d7ce-pages.txt`, `/tmp/run-ee90d7ce-scorer/scorer-2026-04-29.csv`, `docs/runs/smoke-ee90d7ce-mid.md`. Page-quality / personas / judge / trace audits in flight.
- PR refs: #259, #260, #261, #262, #281, #282, #283, #284 (all 2026-04-28 → 2026-04-29 cycle).

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-ee90d7ce-cross-run-synthesis-2026-04-29.md
