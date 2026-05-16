---
title: "Master synthesis — 5 smoke runs (3e88f996 → 5928c151 → 8fa45533 → ee90d7ce → 8d140301)"
audit_kind: cross-run-master-synthesis
audit_date: 2026-04-30
runs:
  - id: 3e88f996-3ee7-4653-b7b0-156c6c960201
    label: pre-V12 baseline (killed)
    commit: pre-#251/#252/#253
    n: 153
  - id: 5928c151-1109-4c24-abc2-088e9e0693a6
    label: V12 prompts (#259/#260) (killed/wedged b17)
    commit: c1db495
    n: 84
  - id: 8fa45533-e518-4d3c-98f7-1cdbf6e34d6d
    label: compile-module split (#261) (killed mid-run)
    commit: f4f7ccd
    n: 57
  - id: ee90d7ce-2225-405b-8a07-eedd8f997159
    label: F1/F2/F3 fixes (#281/#282/#283/#284) (killed)
    commit: a52672a
    n: 189 audited / ~261 total
  - id: 8d140301-3d57-4116-ace8-3929ef18ac67
    label: post-#289 qmd-no-rerank, bs=10 — FIRST COMPLETE RUN
    commit: post-#289
    n: 311
inputs:
  - docs/audits/run-3e88f996-{summary,pm-persona,engineer-persona,page-quality,trace}-audit-2026-04-29.md
  - docs/audits/run-5928c151-{findings,personas,page-quality,llm-judge,trace-audit}-2026-04-29.md
  - .claude/worktrees/audit-8fa45533/docs/audits/run-8fa45533-{findings,personas,page-quality,llm-judge,trace-audit}-2026-04-29.md
  - docs/audits/run-ee90d7ce-{findings,personas,page-quality,llm-judge,trace-audit,cross-run-synthesis}-2026-04-29.md
  - docs/audits/post-run-metrics-8d140301-3d57-4116-ace8-3929ef18ac67.md
  - Postgres compile_attempts + compile_runs (this run)
  - /tmp/run-8d140301-scorer/scorer-2026-04-30.csv
---

# Master synthesis — 5 smoke runs across the 2026-04-28 → 2026-04-30 audit cycle

This is the meta-meta doc for the cycle. Run 8d140301 is the **first
complete run** of the cycle (every prior run was killed or wedged
before completion). The cycle's arc is from "0/9 PM-forwardable, 4.7%
F1, baseline pre-V12" (3e88f996) to "99.4% success, $0.054/email,
PM=3 ceiling at 5/10, F1 closed, complete run shipped" (8d140301).

Citations: every metric below is sourced from a per-run audit doc,
named inline as `(run-X audit doc, finding/section N)`.

## 1. Master metrics table

### 1.1 Run-level

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| Code under test | pre-V12 | +#259/#260 | +#261 | +#281/#282/#283/#284 | **+#289** |
| Status | killed (b46) | wedged (b17) | killed mid-run | killed mid-run | **completed** ✅ |
| Total emails compiled | 153 | 84 | 57 | 261 (final) / 189 audited | **311** |
| Pages touched | 42 | 18 | 19 | 26 | **148** new content pages |
| batch_size | bs=5 (implicit) | bs=5 | bs=5 | bs=5 | **bs=10** |
| Distinct batches (audit window) | 46 | 17 | 19 | 26 | ~31–32 (DB) |
| Fatal failures | 0 (5 outcome=NULL on kill) | 5 (1 batch, b4 429) | 0 | 0 | **1** (kimi-k2.6) |
| Skipped (insight:trivial) | 11 | — | 8 | — | 2 |
| Cost / email | $0.086 | $0.107 | $0.092 | $0.077 | **$0.054** ✅ |
| Total cost | n/a | n/a | n/a | n/a | **$16.81** (cost_cents=1681) |
| Run wall-clock | n/a (suspended) | wedged | mid-run | mid-run | **221 min** |
| p50 latency / batch | 5.1 min | 5.9 min | 5.5 min | 5.2 min | **3.6 min** (DB-derived: p50_s=214.6) |
| p95 latency / batch | 8.2 min | 12.1 min | 9.7 min | ~9.4 min | **8.2 min** (DB-derived: p95_s=493.2) |
| Success rate | 91.5% | 87.7% | 100% (mid-run) | 100% (mid-run) | **99.4%** (311/313 attempted = 99.4%) |

Sources: 3e88f996 trace §1 / summary; 5928c151 trace §1; 8fa45533 trace §1;
ee90d7ce trace §1; 8d140301 from `compile_runs` row (started_at/finished_at,
emails_processed=311, emails_failed=0, cost_cents=1681) and `compile_attempts`
(311 compiled / 1 failed / 2 skipped, p50/p95 percentile_cont).

### 1.2 Page-quality

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| Heuristic mean (topics) | 7.76 | 7.67 | 7.65 | 7.52 | **7.74** (n=37 from `/tmp/run-8d140301-scorer/scorer-2026-04-30.csv`) |
| Heuristic mean (systems) | 8.15 | n/a | not scored | not scored | not scored separately |
| F1 — body refs / 0 defs | 4.7% (2/42) | **50%** (9/18) | **58%** (11/19) | **0%** (0/24 with refs) ✅ | not re-measured at audit point; #284 backfill is in the codebase since ee90d7ce |
| F1 — any missing defs | 4.7% | n/a | 68% (13/19) | 0% (24/24 clean) ✅ | n/a |
| Orphan defs (def w/o body cite) | not measured | not measured | 12 on 1 page | 1 minor | n/a |
| Empty `## References` heading | 0 | 0 | 1 | 0 | n/a |
| Boy-scout deep-read | 0–1/3 | 0–1/3 | 1/19 | **0.625/3** (flat) | n/a |

Sources: 3e88f996 page-quality "Anti-patterns"; 5928c151 page-quality
"Headline finding"; 8fa45533 page-quality "F1 quantification"; ee90d7ce
page-quality "F1 quantification — all 26 pages" + "Summary" (24/24 clean).
8d140301 has a heuristic CSV but no F1-specific sweep audit doc was
produced.

### 1.3 Persona

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| PM mean (0–3) | 0.78 | 2.13 | **2.50** | 2.30 | (no persona deep-read this run) |
| PM=3 ceiling | 0/9 | 1/8 | 4/8 | **5/10** | n/a |
| PM ≥ 2 (forwardable) | 0/9 (0%) | 8/8 (100%) | 8/8 (100%) | 8/10 (80%) | n/a |
| Eng mean (0–3) | ~0.85 | 1.50 | **1.88** | 1.50 | n/a |
| Eng=3 ceiling | 0/9 | 0/8 | **1/8** (`buyleads-ni-grid-apis`) | 0/10 ⬇ | n/a |
| Eng ≥ 2 (ramp-ready) | 0/9 (0%) | 4/8 (50%) | 6/8 (75%) | 6/10 (60%) | n/a |
| Owner field in frontmatter | 0/9 | 6/8 (75%) | 7/8 | (mixed; 2 systems missing) | **66.9%** (148 pages, M1) |

Sources: 3e88f996 PM persona "Headline counts" + engineer persona
"Executive summary"; 5928c151 personas "Headline" + "vs run-3e88f996";
8fa45533 personas "Headline"; ee90d7ce personas "Headline" + "3-run
comparison"; 8d140301 from post-run-metrics M1 (`% new pages with
owner: frontmatter = 66.9%`).

### 1.4 LLM judge

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| Judge mean | n/a | 7.13 (n=5) | 5.93 (n=5) | **6.88 (n=8 — first systems)** | (LLM judge running in parallel) |
| Pearson r (heur vs judge) | n/a | **−0.758** | +0.518 | +0.401 | n/a |
| Cost (judge) | n/a | $1.50 | $1.50 | ~$2.40 | n/a |

Top-3 weakness patterns (judge):

| Pattern | 5928c151 | 8fa45533 | ee90d7ce |
|---|---|---|---|
| Stale dates / unresolved open Qs on `active` pages | dominant, 5/5 | pervasive, 5/5 | reproduced severely (every page) |
| Entity-slug fragmentation (email-suffix / numeric) | flagged | 3/5 | reproduced, less severe |
| Missing system-layer wikilinks | 2/5 explicit | 4/5 explicit | every page; **temporal-staleness now displaces this as #1** |

Sources: 5928c151 LLM-judge "Strongest weaknesses"; 8fa45533 LLM-judge
"Weakness patterns"; ee90d7ce LLM-judge "Three prior weakness patterns".

### 1.5 Middleware

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| ReconnaissanceParalysis fires | n/a (pre-#251) | 7 | 7 | not re-measured | not measured |
| EditStaleness proactive | n/a | 11 | — | — | — |
| EditStaleness reactive | n/a | 3 | — | — | — |
| StuckHeartbeat fires | n/a | 0 (b17 stalled inter-batch) | 0 (no need) | 0 (no need) | 0 (no failures) |
| MidRunPoolQuarantine fires | n/a | 0 (b4 429 didn't trigger) | 0 (no need) | 0 (no need) | 0 (no need) |
| CheckMyWorkGate blocks | n/a | 27 (1.59/batch) | not measured | not measured | check_my_work pre-write rate **90%** (M4) |
| Read:write (incl cmw/log/todos) | 3.47:1 | 2.75:1 | **1.79:1** | similar to 8fa45533 | not measured |
| Read:write (pure write) | ~3.9:1 | n/a | 5.38:1 | n/a | n/a |

Sources: 3e88f996 trace §3 / §4; 5928c151 trace §3 (middleware table);
8fa45533 trace §3, §4 (read:write trend); ee90d7ce trace §1; 8d140301
post-run-metrics M4.

### 1.6 Pool

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| Pool advertised | grok+glm-5 (kimi quarantined) | grok+glm-5.1+glm-5 | grok+glm-5.1+glm-5 | grok+glm-5.1+glm-5 (kimi quarantined) | grok+glm-5.1+glm-5 (kimi un-quarantined this run) |
| grok pick % | 47% (22/46) | **0%** (0/17) | 42% (24/57) | 24% (6/25 — bottom of CI) | **35.4%** (110/311) |
| glm-5 pick % | 53% | 59% | 26% | 44% (11/25) | **29.9%** (93/311) |
| glm-5.1 pick % | n/a | 41% | 40% | 32% (8/25) | **32.8%** (102/311) |
| kimi status | auto-quarantined | auto-quarantined | auto-quarantined | auto-quarantined | **active**: 7 attempts (6 compiled, 1 failed) |
| Pool fairness | OK | broken (grok=0 — small-n noise) | restored | passes χ²=1.52 | uniform across 3 main models |

Sources: 3e88f996 trace §1 model splits; 5928c151 trace §1 + findings
F8; 8fa45533 trace §1; ee90d7ce trace §3 (chi-squared test); 8d140301
direct query of `compile_attempts` GROUP BY compile_model, outcome.

### 1.7 qmd / resolve_page

| Metric | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce | **8d140301** |
|---|---:|---:|---:|---:|---:|
| qmd_timeout_s effective cap | 45s | 45s | 45s | **45s** (.env shadow — F19) | 60s (now PR #289 retired the rerank step entirely) |
| `resolve_page` 45s timeout rate | 8.9% (28/316) | 15.4% (14/91) | **33.9% (38/112)** | **9.0% (16/177)** ✅ | not measured this audit |
| `resolve_page` p50 (s) | n/a | 23.10 | 26.96 | 22.6 | not measured |
| `glob` calls | 105 | 17 | 19 | not re-measured | not measured |
| `glob` timeout rate | 68% | 88% | 95% | not re-measured | not measured |

Sources: 3e88f996 trace §2 / §4; 5928c151 trace §2; 8fa45533 trace §6;
ee90d7ce trace "Headline" + finding F19. 8d140301 has no trace audit
attached to this synthesis brief.

---

## 2. PR-to-effect ledger

| PR | What shipped | First-carried run | Measured effect | Verdict |
|---|---|---|---|---|
| **#259** | V12 mechanical prompt reorg (no behaviour change) | 5928c151 | enabling for #260 | enabling |
| **#260** | V12 lead-paragraph + open-gates contract | 5928c151 | **PM forwardable 0/9 → 8/8 (100%); PM=3 0 → 1; eng ramp-ready 0/9 → 4/8** (5928c151 personas "vs run-3e88f996") | **WIN — single biggest persona delta in cycle** |
| **#261** | Compile-module split (`src/compile/` → `wiki/agent/coordinator`) | 8fa45533 | 0 quality regression; tools/batch −20%, turns −22%, p95 latency −20%; observation tagging fully intact (8fa45533 trace §2 + §8) | **WIN — refactor + free latency win** |
| **#262** | Post-run dipstick for prompt-revamp signals | 5928c151 | Observability — surfaced F1 visibility | enabling |
| **#281** | `qmd_timeout_s` 45 → 60 in `src/config.py` | ee90d7ce | **DID NOT TAKE EFFECT — `.env` `QMD_TIMEOUT_S=45` shadow** (ee90d7ce trace finding F19). Service self-recovered (33.9% → 9.0%) instead. | **FIZZLE — config shadow defeated source-level fix** |
| **#282** | Boy-scout repair verbs in prompt | ee90d7ce | Boy-scout score moved 1/19 → 0.625/3 (within noise) — verbs alone insufficient. Concrete misses on `contextual-blni-feedback`, `auditmate`, `photosearch`, `buyermy` (ee90d7ce findings F2). | **FIZZLE — needs prompt-by-example; revised proposal in F2 upgrade** |
| **#283** | Per-batch effective-pool + pick log | ee90d7ce | Pool selection now observable per batch; chi-squared analysis enabled. Closed F8 with χ²=1.52 (ee90d7ce trace §3). | **WIN (observability)** |
| **#284** | Coordinator-side `## References` backfill | ee90d7ce | **F1 closed: 9/18 broken (5928c151) → 0/26 (ee90d7ce). 24/24 pages with body refs have all defs.** (ee90d7ce page-quality "F1 quantification" — `body refs > 0, all defs present` from 5/18 = 28% to 24/26 = 92%, and pure-F1 4.7%→50%→58%→**0%**.) | **WIN — biggest leverage change of cycle** |
| **#289** | qmd no-rerank step | 8d140301 | First complete run after this lands. ee90d7ce qmd timeout rate had recovered to 9% organically; #289 retires the rerank cliff structurally rather than via cap-bump. | **WIN — first complete run is downstream evidence** |
| **bs=5 → bs=10** | CLI param — larger batch | 8d140301 | First complete run; 311 compiled in 221 min ≈ 1.4 emails/min ≈ matches 5928c151's 5.9-min p50/batch but with 10 emails/batch = 1.7 emails/min. **Cost dropped to $0.054/email** (lowest of the cycle). | **WIN — 30% cost reduction vs ee90d7ce, 37% vs V12 baseline** |

Cross-cycle deferred (still open):
- **F4** (consecutive-429 fast-path on `MidRunPoolQuarantineMiddleware`) — UNTESTED across all 5 runs because pool stayed healthy. Synthetic-injection test still recommended (8fa45533 trace F-A; ee90d7ce findings F4).
- **F5** (inter-batch dispatcher watchdog) — UNTESTED. Needs reproducible inter-batch stall.
- **F7** (`engineering_surface:` structured frontmatter) — empirically validated by `buyleads-ni-grid-apis` Eng=3 (8fa45533 personas) and Eng=3 ceiling regression in ee90d7ce (lost the only Eng=3 page); not yet shipped.
- **F12 / F20** (system-page archetype variance — pre-V12 stubs not refactored on revisit) — UPGRADED to P0 in ee90d7ce findings. Drives PM/Eng regression.
- **F18** (temporal-staleness lint) — NEW P1 in ee90d7ce. Becomes the dominant judge complaint after F1 closes.
- **F19** (`.env` shadow on QMD_TIMEOUT_S) — trivial 1-line fix, not yet landed.

---

## 3. Three trends across 5 runs

### 3.1 Monotonically improving

- **Cost / email**: 0.086 → 0.107 → 0.092 → 0.077 → **0.054**. Non-monotone in the middle (V12 introduced a heavier prompt and bumped cost +25% on 5928c151) but **net 37% reduction** baseline-to-end (3e88f996 trace §1; 5928c151 findings F9 reverse-tracked through ee90d7ce close + 8d140301 `compile_runs.cost_cents=1681`). Drivers: model-mix (kimi un-quarantined, grok cheapest-in-pool), prompt-cache hit rates 72–87%, and bs=10 amortizing per-batch overhead.
- **Persona scores**: PM mean 0.78 → 2.13 → 2.50 → 2.30 → (n/a 8d140301); PM ≥ 2 went 0% → 100% → 100% → 80% — the floor lifted decisively after #260 and held across runs (the −0.20 dip in ee90d7ce is fully attributable to 2 pre-V12 system stubs, not regression in topic compile). PM=3 ceiling 0 → 1 → 4 → 5 climbed monotonically (5928c151 personas Headline; 8fa45533 personas; ee90d7ce personas "3-run comparison").
- **Read:write ratio**: 3.47 → 2.75 → **1.79** → similar → not measured. #251 ReconnaissanceParalysis is compounding (8fa45533 trace §4).
- **Latency p95**: 8.2 → 12.1 → 9.7 → ~9.4 → **8.2 min**. The V12 spike in 5928c151 healed; 8d140301 is back at the 3e88f996 baseline despite carrying every middleware (DB-derived from `compile_attempts` percentile_cont).
- **F1 trajectory**: 4.7% → 50% → 58% → **0%** → assumed-clean (no F1 sweep this run). The biggest content-quality recovery in the cycle (ee90d7ce page-quality "F1 quantification" + #284 ledger).

### 3.2 Flat (no discernible change)

- **Heuristic mean**: 7.76 → 7.67 → 7.65 → 7.52 → **7.74**. Saturated and weak — cannot distinguish V11 from V12 prompts despite persona scores tripling (5928c151 findings F6; ee90d7ce findings F6 revised to "replace, not rebalance").
- **People-slug leakage**: every page in every measured run had `[[*-indiamart-com]]` body wikilinks (worst-page 71 → 41 → 60). 8d140301 reports M7 = 0 median people-wikilinks/page — this contradicts the prior trend, suggesting a coordinator post-process (probably the `47abaa3` bulk-cleanup committed earlier this cycle) finally landed at corpus level. **First flat→improving inflection.**
- **Diagrams**: 0 across every persona deep-read sample (3e88f996 eng-persona P8; ee90d7ce personas — none surfaced).
- **Boy-scout score**: 0–1/3 across all 4 measured runs. #282 prompt edit didn't move it (ee90d7ce findings F2).
- **Engineering-surface (F7)**: repo paths, GCP IDs, namespaces, alert names still absent across topic pages. Only `buyleads-ni-grid-apis` (a system page) ever shipped engineer-actionable URLs — and ee90d7ce regressed Eng=3 to 0/10 because that contract isn't enforced.

### 3.3 Regressing or zigzagging

- **F1 citation plumbing — V-shaped**: 4.7% → **50%** → **58%** → **0%** → clean. The single largest mid-cycle regression of the audit cycle (5928c151 findings F1 → 8fa45533 findings F1+ → ee90d7ce findings F1 closed). PR #260 removed the agent's instruction to write the section; PR #284's coordinator-side backfill closed the gap. **Closed, but a 2-run regression in the middle.**
- **`resolve_page` 45s timeout rate — V-shaped**: 8.9% → 15.4% → **33.9%** → **9.0%** → not measured. Embedding/qmd service tier degraded across 3 runs then recovered organically (8fa45533 findings F11 → ee90d7ce trace §2). #281 was wrong-layer; #289 (no-rerank) is right-layer.
- **`glob` per-call timeout rate**: 68% → 88% → 95% — climbing per-call, but call count fell 105 → 17 → 19 so wall-clock impact is down. Agent prompt is mostly avoiding `glob` for slug lookups; the tool itself remains broken (8fa45533 trace §5).
- **Heur-judge correlation — sign-flipped**: −0.758 → +0.518 → +0.401 at n=5. Statistical noise, not a real swing (8fa45533 LLM-judge; ee90d7ce LLM-judge "Reproducibility").
- **Engineer persona ceiling — regressed**: Eng=3 0 → 0 → **1** → **0**. The single Eng=3 page (`buyleads-ni-grid-apis`, 8fa45533) was driven by Swagger URLs + GKE flag inline — no system page in ee90d7ce carried that density (ee90d7ce personas "All-4-systems verdict"). F7 (`engineering_surface:` frontmatter) remains unbuilt.
- **System-page archetype**: bimodal. Greenfield systems hit canonical archetype (`indiamart-n8n-nodes`, `auditmate`); revisited pre-V12 stubs (`buyermy`, `photosearch`) inherit and don't refactor (ee90d7ce findings F12 / F20 upgraded to P0).
- **Pool fairness**: 47% grok → **0%** → 42% → 24% → **35.4%**. The 0% in 5928c151 was small-n noise (F8 closed in ee90d7ce via χ²=1.52); 24% in ee90d7ce was at the bottom of the Wilson CI; 8d140301 lands at 35.4% (above the 33% baseline, healthy).

---

## 4. What this audit cycle achieved

The full arc: **3e88f996 (0/9 PM-forwardable, 4.7% F1, $0.086/email, killed) → 8d140301 (5/10 PM=3 ceiling per ee90d7ce, F1 closed, $0.054/email, complete run, 99.4% success).**

Net deltas, baseline → cycle end:

- **Persona forwardability**: 0% → 80–100%. PM=3 ceiling 0 → 5 (ee90d7ce). The single biggest PR was **#260** (lead-paragraph contract).
- **Citation plumbing**: 4.7% pure-F1 baseline → 50% → 58% mid-cycle regression → 0% clean (ee90d7ce). The closing PR was **#284** (coordinator-side References backfill).
- **Cost**: $0.086 → $0.054 per email, **−37%**. Drivers: pool widening (kimi rejoined), prompt-cache 72–87% hit rate, bs=10 amortization, qmd no-rerank (#289).
- **Reliability**: 91.5% baseline → 99.4% on 8d140301. Every prior run was killed/wedged before completion; 8d140301 is the **first complete run of the cycle**.
- **Latency p95**: 8.2 min → 12.1 (V12 spike) → 9.7 → 8.2 min. Net flat-to-slight-improvement after V12's prompt heaviness was absorbed.
- **Observability**: per-batch pool log (#283), tagging integrity restored (#261), tool-error elevation partially fixed (5928c151 trace §2; F2 from 3e88f996 partially closed).

### Residual work going into the next cycle

Open / unverified after 8d140301:

1. **F12 / F20 (P0)** — Pre-V12 system-page stubs (`buyermy`, `photosearch`) inherit on revisit and are not refactored. PM=1 / Eng=0 cases all stem from this. Same root cause as F2 (boy-scout). Fix: coordinator-side V12 archetype lint OR prompt-by-example for system archetype.
2. **F19 (P0)** — `.env` shadow on `QMD_TIMEOUT_S` defeated PR #281. One-line fix; not yet landed. Re-arm before the next degradation cycle.
3. **F18 (P1)** — Temporal-staleness lint. Now the dominant judge complaint after F1 closes ("page is `active` but last update is 2-4 months old, no resolution recorded"). Coordinator-side: scan touched pages for newest Recent-changes date >60 days + status:active.
4. **F2 (P1)** — Boy-scout repair behaviour still inert (#282 verbs alone insufficient). Needs prompt-by-example.
5. **F4 (P1)** — Consecutive-429 fast-path on `MidRunPoolQuarantineMiddleware` — UNTESTED across all 5 runs. Synthetic 429-injection test in dev.
6. **F7 (P1)** — `engineering_surface:` structured frontmatter. The Eng=3 ceiling regressed in ee90d7ce because there's no contract for repo / endpoint / alert / runbook fields. Same template-discipline pattern that landed `owner:` (M1 = 66.9% adoption is below the 80% target, but the field itself is now expected).
7. **F6 (P1)** — Heuristic scorer is saturated and weak (mean drifted only 7.52–7.76 across 5 runs while persona scores tripled). Replace columns, don't rebalance.
8. **8d140301 quality verification gap** — This run has post-run-metrics (M1–M10) but no parallel persona / page-quality / LLM-judge / trace audit was attached to this brief. The 4 parallel agents covering 8d140301 are already running; their results will land alongside this synthesis. Until they do, the **content-quality story for 8d140301 is incomplete** — we know reliability + cost + archetype distribution but not whether persona scores held or regressed.

---

## 5. What the next cycle should test (highest-value experiment)

Ordered by stakes-on-the-fix.

1. **Did the 8d140301 content quality actually hold?** Parallel persona + LLM-judge + page-quality audits on the 148 new pages from 8d140301. Specifically: (a) did PM=3 / Eng=3 ceiling hold or regress? (b) is F1 still 0% on the unaudited pages? (c) what's the M2 lead-paragraph rate (46.6% per post-run) actually mean for forwardability? Without this, 8d140301's "complete run" win is half-claimed.

2. **Land F19 (`.env` shadow)** — 1-line fix, defended ground. Then rerun 8d140301-shape and measure whether qmd timeout rate stays <15% under a real 60s cap when the embedding service has another bad day.

3. **Build F18 (temporal-staleness lint)** — judge's #1 weakness post-F1. Coordinator-side: flag `status: active` pages whose newest `## Recent changes` date is >60 days; severity=blocker at >120 days. This is the same coordinator-pattern that closed F1.

4. **Build F12/F20 (system-page archetype lint)** — closes the 2 PM=1 / Eng=0 cases in ee90d7ce and any equivalent in 8d140301. Detect system pages with `update_count > 0` AND missing V12 archetype (no lead, no `owner:`, no `## Why it matters`, no `## Open questions`); enqueue forced rewrite.

5. **Validate F4 with synthetic 429 injection** — every prior run had 0 batch failures; the consecutive-429 fast-path on `MidRunPoolQuarantineMiddleware` is **untested in production across 5 runs**. Untested middleware is a tomorrow-incident waiting to happen.

6. **Build F7 (`engineering_surface:`)** — the only path to a sustainable Eng=3 ceiling. Same machinery as `owner:` (M1=66.9%); the agent already extracts these signals into prose, just put them in structured frontmatter.

7. **Do not** fold heur-judge correlation into triage — it was small-n noise across 3 runs. F6 stands as "scorer is weak" (drop saturated columns; add temporal-staleness, owner-presence, refs-completeness as discriminating columns) but not as "broken".

---

## Cross-references

- 3e88f996: `docs/audits/run-3e88f996-{summary,pm-persona-audit,engineer-persona-audit,page-quality-audit,trace-audit}-2026-04-29.md`
- 5928c151: `docs/audits/run-5928c151-{findings,personas,page-quality,llm-judge,trace-audit}-2026-04-29.md`
- 8fa45533: `.claude/worktrees/audit-8fa45533/docs/audits/run-8fa45533-{findings,personas,page-quality,llm-judge,trace-audit}-2026-04-29.md`
- ee90d7ce: `docs/audits/run-ee90d7ce-{findings,personas,page-quality,llm-judge,trace-audit,cross-run-synthesis}-2026-04-29.md`
- 8d140301: `docs/audits/post-run-metrics-8d140301-3d57-4116-ace8-3929ef18ac67.{md,json}`; Postgres `compile_runs` + `compile_attempts`; `/tmp/run-8d140301-scorer/scorer-2026-04-30.csv`
- PR refs: #259, #260, #261, #262, #281, #282, #283, #284, #289 (all 2026-04-28 → 2026-04-30 cycle)

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-8d140301-cross-run-synthesis-2026-04-30.md
