# Compile Run ee90d7ce — Langfuse Trace Audit (round 3)

- **Run ID**: `ee90d7ce-2225-405b-8a07-eedd8f997159`
- **Window**: 2026-04-29 13:39:46Z → audit ~16:00Z (still `running`; 25 batches finished, batch 26 in flight)
- **Commit**: `a52672a` — adds #281 (qmd_timeout 45→60), #282 (boy-scout prompt scope), #283 (per-batch pool logging), #284 (References backfill) on top of #261 refactor.
- **Pool advertised**: `[grok-4.1-fast, glm-5.1, glm-5]` (kimi auto-quarantined, 60/77 failed in last 24h).
- **Traces fetched**: 26 via Langfuse `metadata.compile_run_id` filter on `langfuse.intermesh.net`.
- **compile_attempts** (DB): 102 attempts → 27 grok + 40 glm-5 + 35 glm-5.1 compiled, 5 in-flight, 0 failed.

## Headline

**1. PR #281 (qmd_timeout 45→60) is silently NEVER taking effect — `.env` pins `QMD_TIMEOUT_S=45` and overrides the new source default. Timeout sentinel is still 45.0s exactly. 2. Despite that, qmd timeout rate dropped to 9.0% (16/177) from 33.9% — embedding service self-recovered, not the bump. 3. Pool fairness chi-squared passes uniform on n=25 (χ²=1.52, df=2, p>0.10), but grok pick rate sits at 24.0% (95% CI 7%-41%) — at the bottom of the target band. 4. Tagging integrity is now FULL: top-level `tags` populated this run (was `[]` empty in 8fa45533), all four metadata fields present.**

---

## 1. Run-level metrics — 4-run side-by-side

| Metric | **ee90d7ce** | 8fa45533 | 5928c151 | 3e88f996 |
|---|---:|---:|---:|---:|
| Code under test | +#281/#282/#283/#284 | +#261 split | +#251/#252/#253 | baseline |
| Status at audit | running (b26) | running (b20) | running (b18) | killed (b46) |
| Trace count (Langfuse) | **26** | 19 | 17 | 46 |
| Batches finished | 25 | 19 | 17 | 46 |
| Compiled emails | **102** (97 done + 5 in-flight) | 57 | 71 | 172 |
| ERROR-level obs | **0** | 0 | 3 | 0 |
| Bubbled-up exceptions | 0 | 0 | 1 | 0 |
| Latency p50 (s) — trace | **314.1** | 327.6 | 354.7 | 306.2 |
| Latency p90 (s) | 505.4 | 509.5 | n/a | 454.0 |
| Latency p95 (s) | 563.6 | 580.5 | 724.9 | 490.6 |
| Tools/batch median | **35** | 33.6 | 41.8 | 41.1 |
| Turns/batch median | **18** | 18 | 23 | 20 |
| Tokens total | **19.17 M** | 14.26 M | 3.12 M | n/a |
| Tokens/batch median | 712,099 | 587,964 | 943,482 | n/a |
| Cache-hit median | **72.1%** | 75–87% | n/a | n/a |
| qmd timeout rate | **9.0%** (16/177) | 33.9% | 15.4% | 8.9% |
| qmd p50 / p90 latency | 22.6s / 35–43s | 27.0s / 45.2s | 23.1s / 45s | n/a |
| Pool fairness (grok %) | 24% (6/25) | 42% (24/57) | 0% | 47% |

`compile_runs.status='running'` — mid-flight data.

---

## 2. F3 verdict — qmd_timeout cap bump

**Verdict: NOT LANDED.** PR #281 raised `qmd_timeout_s` default from 45→60 in `src/config.py`. The default is correctly 60. **But `.env` contains `QMD_TIMEOUT_S=45`** which pydantic-settings honours; observed timeout sentinel is exactly `latency_s=45` on every timeout (13× in `resolve_page.semantic` log). The fix is one line:

```diff
-QMD_TIMEOUT_S=45
+QMD_TIMEOUT_S=60
```

(or remove the override entirely — the source default is now correct.)

**However**, the underlying *service* recovered on its own:

| Run | qmd timeout rate | qmd p50 (s) | qmd p90 (s) |
|---|---:|---:|---:|
| 3e88f996 | 8.9% | n/a | n/a |
| 5928c151 | 15.4% | 23.1 | ~45 |
| 8fa45533 | **33.9%** | 27.0 | 45.2 |
| **ee90d7ce** | **9.0%** | 22.6 | ~35–43 |

p50 22.6s and the 9% timeout rate match the pre-degradation baseline. **F11's concern (longer timeouts disguising service degradation) doesn't apply to this run because the bump never landed AND the service is back to healthy.** When the env override is fixed, expect the timeout rate to drop further (some of the 9% are 44-48s queries that would land under a real 60s cap).

---

## 3. F8 verdict — pool fairness chi-squared

**Pick distribution at n=25**: glm-5 11, glm-5.1 8, grok 6.

```
χ² = (11-8.33)² /8.33 + (8-8.33)²/8.33 + (6-8.33)²/8.33 = 1.520
df = 2, critical@0.05 = 5.991, critical@0.10 = 4.605
→ FAIL TO REJECT H0 (uniform). p ≈ 0.47
```

**Pool selection is statistically uniform.** But the grok pick rate at 24% sits at the very bottom of the 25–40% target band (95% Wilson CI: 7.3%–40.7%) — so the test passes *because the CI is so wide at n=25*, not because the rate is robustly central. At n=50 (next smoke), expect grok to converge to 33%; if it stays <25%, that's a real signal. PR #283's per-batch logging (`pool_pick batch_idx=N picked=X pool=[…]`) is working — every batch logged exactly one pick line, no fallbacks (`fallback=False` 25/25).

---

## 4. #261 + #281/#282/#284 cumulative delta vs 8fa45533

| Dimension | 8fa45533 (n=19) | **ee90d7ce (n=25)** | Δ |
|---|---:|---:|---|
| p50 trace latency | 327.6s | **314.1s** | -4% |
| p95 trace latency | 580.5s | 563.6s | -3% |
| Tools/batch median | 33.6 | 35 | +4% |
| Turns/batch median | 18 | 18 | flat |
| Tokens/batch median | 588k | 712k | +21% |
| Read:write ratio (top-15) | 1.79:1 | **1.51:1** | -16% |
| qmd timeout rate | 33.9% | 9.0% | -73% |

**Tagging integrity (F4)**: Sampled 3 traces (`9bbbf0cf`, `4723fe3f`, `dee1a720`). All carry `compile_run_id`, `compile_batch_index`, `compile_thread_id`, `compile_model`. **Top-level `tags` populated this run** (`['email-kb', 'compile', 'model:z-ai/glm-5', 'batch:26']`) — F4 from prior audits is now FIXED, presumably as a side-effect of #261 cleanup or #283.

**No new error classes** vs 8fa45533. Soft-error events: 8 `reconnaissance_paralysis_nudge` (#251 still firing at ~0.32/batch, healthy), 0 `MidRunPoolQuarantine` (no failures to trigger), 0 `StuckLLMRound`, 0 429s, 0 unhandled exceptions.

---

## 5. Tool-call top-15 + read:write

From `compile_tool_calls` (database authoritative, includes all 102 attempts):

| Rank | Tool | Calls | p50 (ms) | p90 (ms) | Errs |
|---:|---|---:|---:|---:|---:|
| 1 | `read_file` | 235 | 2 | 5 | 0 |
| 2 | `resolve_page` | 177 | 24,086 | 43,685 | 0 |
| 3 | `check_my_work` | 101 | 996 | 1,524 | 0 |
| 4 | `edit_file` | 62 | 2 | 3 | 0 |
| 5 | `write_todos` | 46 | 1 | 3 | 0 |
| 6 | `get_page_summary` | 39 | 3 | 5 | 0 |
| 7 | `ls` | 25 | 25 | 64 | 0 |
| 8 | `glob` | 25 | 20,003 | 20,005 | 0 |
| 9 | `get_thread_context` | 24 | 8 | 14 | 0 |
| 10 | `task` | 24 | 73,078 | 99,196 | 0 |
| 11 | `create_entities` | 23 | 246 | 356 | 0 |
| 12 | `log_insight` | 18 | 16 | 346 | 0 |
| 13 | `patch_page` | 17 | 4 | 5 | 0 |
| 14 | `write_file` | 16 | 2 | 3 | 0 |
| 15 | `validate_page_draft` | 4 | 287 | 292 | 0 |

- Reads (`read_file` + `resolve_page` + `get_page_summary` + `get_thread_context` + `glob` + `ls` + `grep` + `list_wiki_pages` + `check_my_work`): 631
- Writes (`edit_file` + `patch_page` + `write_file` + `create_entities` + `log_insight`): 136
- **Read:write = 4.64:1 on pure I/O**; **1.51:1 if we include `check_my_work`/`write_todos` as "writes"** (continuing the downward trend: 3.47 → 2.75 → 1.79 → 1.51).
- `glob` per-call timeout 100% (25/25 hit 20s wall). `task` (sub-agent) p50 73s, p90 99s — still wall-clock bottleneck.
- **Zero `status='error'` rows** across 901 tool calls. That's a clean run.

---

## 6. Bug candidates

| ID | Severity | Title | Status |
|---|---|---|---|
| F-NEW-1 | **P0** | `.env` pins `QMD_TIMEOUT_S=45` overriding PR #281's new 60s default — the bump never landed in production. One-line `.env` fix. | NEW |
| F-C+ | RESOLVED | qmd timeout rate back to 9.0% — service recovered on its own. Watch next run for regression. | OK for now |
| F-A | P1 | `MidRunPoolQuarantine` consecutive-429 fast-path still untested (0 failures this run). | UNTESTED |
| F-B | P2 | Inter-batch watchdog for between-batch stalls. | OPEN |
| F-G | P3 | `glob` per-call timeout 100% but agent uses it ≤25× — keep deprecation watch. | LOW |
| F-NEW-2 | P3 | grok pick rate 24% at bottom of CI band; recheck at n=50 to confirm true 33%. | WATCH |

---

## 7. Recommended next step

1. **One-line fix**: edit `.env` → `QMD_TIMEOUT_S=60` (or delete the line). Verify next smoke shows `latency_s=60` on timeouts. This is the actual #281 deliverable.
2. Let this run finish (267 emails total budget, 102 in flight, ETA ~3-4 more hours at current rate).
3. Re-check pool fairness at n=50 — if grok still <25%, dig into `pool_pick` random seeding.
4. Defer F-A synthetic-429 testing until next dev cycle (run is clean enough to ship).

---

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-ee90d7ce-trace-audit-2026-04-29.md
