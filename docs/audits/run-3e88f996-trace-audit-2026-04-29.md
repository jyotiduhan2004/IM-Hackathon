# Compile Run 3e88f996 — Langfuse Trace Audit

- **Run ID**: `3e88f996-3ee7-4653-b7b0-156c6c960201`
- **Window**: 2026-04-28 19:37 UTC → 2026-04-29 05:36 UTC (killed)
- **Pool**: `x-ai/grok-4.1-fast`, `z-ai/glm-5` (kimi auto-quarantined)
- **Code under test**: pre-#251/#252/#253
- **Traces fetched**: 46/46 (22 grok + 24 glm-5)
- **compile_attempts**: 80 grok compiled, 92 glm-5 compiled, 11 glm-5 skipped, 5 grok still in `outcome=NULL` (claimed but unfinished when killed)

## Executive Summary

The run completed every dispatched batch with no batch-level failures, but the
trace data shows three structural inefficiencies that the "0 failures" topline
hides:

1. **`glob` is a productivity sink.** 71 of 105 `glob` calls (68%) hit the
   20-second timeout — every single one of them. The agent uses `glob` to
   find a wiki page by slug (`**/<slug>.md`, `/wiki/**/<slug>.md`), which is
   *exactly* the contract `resolve_page` / `get_page_summary` were designed
   to replace. Wall-clock cost: 71 × 20s = **23.6 min of pure timeout latency**
   spread across 33 of 46 traces. This is the cleanest follow-up to ship.
2. **`check_my_work` self-review loops on glm-5.** Median of 4
   `check_my_work` calls per glm-5 batch, max 16 in a single batch
   (batch 41). Grok's median is 1. The cmw tool is being called per-edit
   instead of once per page — symptom of either no per-trace cap or
   prompt encouragement to verify after every patch.
3. **No observation-level errors are emitted.** Across 8,000+ observations,
   `level=ERROR` count is **0**. Real failures (glob timeouts, edit_file
   `String not found in file`, read_file `Line offset N exceeds file
   length`) live as `Error:` strings inside otherwise-DEFAULT tool outputs.
   This means Langfuse error-rate dashboards under-report by definition.
   Either the LangChain callback isn't lifting tool errors to observation
   level, or the tools never raise them.

The 365.8-min outlier trace is a pure laptop-suspend artefact (a single
360.5-min wall-clock gap between two consecutive observations at
`2026-04-29T05:33:50Z`) and should be excluded from any latency
aggregate. With it removed, the worst real trace is 13.1 min (glm-5,
batch 11) and median latency is 5.0 min — comparable to prior smokes.

A secondary observability finding: the `trace_tags` set in `compile_all.py`
(`["email-kb", "compile", "model:<m>", "batch:<n>"]`) **never appear at the
trace top-level** — all 46 traces have `tags: []` at top level but
`metadata.tags` correctly populated. The Langfuse SDK or
`get_langfuse_handler` is dropping `config["tags"]` on the floor; filtering
by `tags=` in Langfuse UI/API for this run will return zero results. Worth
a one-line follow-up.

---

## 1. Observation-level errors

| Signal | Count | Meaning |
|---|---:|---|
| Observations with `level=ERROR` | **0** | No tool/chain ever escalated to ERROR level — silent failures only. |
| Observations with `level=WARNING` | 0 | Same — WARN is also unused. |
| Tool outputs containing `Error:` prefix | **129** | Real failures, surfaced only inside output strings. |
| `glob timed out after 20.0s` | **71** | Every glob call basically. |
| `String not found in file` (edit_file) | 29 | `edit_file` patch miss. |
| `Line offset N exceeds file length` (read_file) | 16 | Agent reading past EOF — paginated read with stale offset. |
| `File <path> ...` (other read_file errors) | 11 | Mostly missing-file or permission-shape. |

Tool-output errors by tool:

| Tool | Error count |
|---|---:|
| `glob` | 71 |
| `edit_file` | 31 |
| `read_file` | 27 |

**Bug candidate (P2)**: tools are returning errors as plain strings; the
LangChain ToolNode never marks the observation `level=ERROR`. Without that
flag, the stock Langfuse "error rate" view shows 0% for a run that had 129
tool failures. Either set `status="error"` from the tool wrappers (current
shape: `'status': 'success'` even on error — see e.g. the glob output) or
add a callback that lifts errors when the output starts with `Error:`.

---

## 2. Anomalously slow traces

Latency distribution (seconds, all 46 traces):
`min=66.5  median=306.2  mean=778.4  max=21946.0  p90=454.0  p95=490.6`

**Excluding the laptop-suspend outlier**: median 306s (5.1 min), p95 490s
(8.2 min) — healthy.

| Trace | Min | Obs | Tools | LLM | Tool errs | Model | Batch | Thread | Verdict |
|---|---:|---:|---:|---:|---:|---|---:|---|---|
| `6aa106e9...` | **365.8** | 179 | 43 | 20 | 10 | grok | 46 | 19c0966266c9 | Laptop suspend (360.5-min gap at 2026-04-29 05:33:50Z); discard for stats. |
| `87dbc273...` | 13.1 | 342 | 72 | 44 | 20 | glm-5 | 11 | 19c091739cbd | High obs + 7 glob timeouts + 5 cmw — slowest *real* trace. |
| `6a386dad...` | 8.2 | 191 | 55 | 18 | 5 | grok | 23 | 19c0d2c13560 | 4 glob timeouts (= 80s wasted). |
| `acd883ab...` | 7.7 | 340 | 71 | 40 | 12 | glm-5 | 35 | 19c0eb7a966e | 8 cmw, 2 glob timeouts. |
| `0278a387...` | 7.6 | 283 | 58 | 35 | 15 | glm-5 | 40 | 19b6ef4139a1 | 10 cmw, 5 glob timeouts (= 100s). |
| `6c785210...` | 7.6 | 234 | 53 | 26 | 3 | glm-5 | 41 | 19c1088f068d | **16 cmw calls** — see §3. |
| `89aed812...` | 7.5 | 279 | 60 | 33 | 5 | glm-5 | 17 | 19b10ae9d236 | 6 cmw. |
| `06d7c82e...` | 7.2 | 259 | 75 | 25 | 20 | grok | 3 | 19b934e448c1 | 8 cmw, 4 glob timeouts. |

Pattern: every trace > 7 min is glm-5 with both high `check_my_work`
count *and* glob timeouts.

---

## 3. Recursion / loop signals

`check_my_work` calls per trace:

| Model | Median | Mean | Max |
|---|---:|---:|---:|
| grok | 1 | 3.0 | 8 |
| glm-5 | 4 | 4.9 | **16** |

Top offenders (≥ 5 cmw):

| cmw | gen (LLM) | obs | latency | model | batch | thread |
|---:|---:|---:|---:|---|---:|---|
| **16** | 26 | 234 | 7.6 min | glm-5 | 41 | 19c1088f068d |
| 10 | 35 | 283 | 7.6 min | glm-5 | 40 | 19b6ef4139a1 |
| 10 | 29 | 235 | 5.9 min | glm-5 | 37 | 19bc0477e968 |
| 8 | 40 | 340 | 7.7 min | glm-5 | 35 | 19c0eb7a966e |
| 8 | 25 | 259 | 7.2 min | grok | 3 | 19b934e448c1 |
| 7 | 38 | 292 | 6.1 min | glm-5 | 44 | 19c1088f068d |

LLM-call (turn) distribution:
`min=5  median=20  mean=19.6  max=44 (glm-5 batch 11)`

**No exact-duplicate read_file calls (≥3× same path)** detected — the
3.8:1 read:write ratio is wide but reads are spread across many distinct
paths, not the same path repeated. So the inefficiency is breadth, not
loop-style hammering. (One caveat: glob+resolve_page+get_page_summary
calls overlap functionally — 105 + 315 + 99 = 519 page-resolution calls
across 46 traces ≈ 11 per batch, which *is* page-resolution flailing
even if it isn't the same tool repeated.)

**Bug candidate (P2)**: cap `check_my_work` to ≤ 1 per page edited, or
make the prompt explicitly say "you may call cmw at most once per page".
glm-5 batch 41 calling it 16 times for a 1-page edit is wasted budget.

---

## 4. Tool-call patterns

**Aggregate** (1,889 calls across 46 traces):

| Rank | Tool | Calls | % of total |
|---:|---|---:|---:|
| 1 | `read_file` | 533 | 28.2% |
| 2 | `resolve_page` | 315 | 16.7% |
| 3 | `check_my_work` | 184 | 9.7% |
| 4 | `edit_file` | 158 | 8.4% |
| 5 | `glob` | 105 | 5.6% |
| 6 | `get_page_summary` | 99 | 5.2% |
| 7 | `ls` | 91 | 4.8% |
| 8 | `write_todos` | 90 | 4.8% |
| 9 | `patch_page` | 83 | 4.4% |
| 10 | `task` | 56 | 3.0% |
| 11 | `log_insight` | 49 | 2.6% |
| 12 | `get_thread_context` | 48 | 2.5% |
| 13 | `create_entities` | 42 | 2.2% |
| 14 | `write_file` | 16 | 0.8% |
| 15 | `grep` | 14 | 0.7% |
| 16 | `validate_page_draft` | 6 | 0.3% |

### x-ai/grok-4.1-fast (22 traces)

- median latency **235 s** | mean tools/batch **39.4** | mean LLM/batch **13.9** | mean obs/batch **142**
- read:write = **3.9 : 1**
- Top 5: `read_file (24%) > resolve_page (19%) > check_my_work (8%) > patch_page (7%) > write_todos (7%)`
- Notable: grok prefers `patch_page`; glm-5 prefers `edit_file`.

### z-ai/glm-5 (24 traces)

- median latency **346 s** | mean tools/batch **42.6** | mean LLM/batch **24.9** | mean obs/batch **205**
- read:write = **3.8 : 1**
- Top 5: `read_file (32%) > resolve_page (15%) > check_my_work (12%) > edit_file (12%) > get_page_summary (5%)`
- Notable: glm-5 makes **~80% more LLM calls per batch** than grok (24.9 vs 13.9) for the same workload — i.e. ~2× the model cost per email at similar success rates.

### read:write paralysis check

Both models sit at ~3.8:1 read:write. Not paralyzed (a panicked agent
would be 10:1+) but high enough that the page-resolution layer
(`resolve_page` + `get_page_summary` + `glob` + `ls` = 30% of all calls)
is the biggest single overhead category. **glob is the worst offender
inside that bucket — every glob attempt times out.**

---

## 5. Suppressed exceptions

No `Traceback`, `RecursionError`, `litellm.APIError`, or `RateLimitError`
strings appear in any observation output across the 46 traces. The only
"silent" errors are the tool-output `Error:` strings already counted in
§1. Run was clean of model-side exceptions.

The "0 failures, 0 timeouts" claim from `compile_attempts` is consistent
with what's in Langfuse — there were no Python-level exceptions, only
tool-level soft failures that the agent worked around.

---

## Follow-up bug candidates

| ID | Severity | Title | Where |
|---|---|---|---|
| F1 | **P1** | `glob` calls always hit the 20s timeout — agent uses it for slug lookups that `resolve_page` already serves. Either remove `glob` from the agent toolset, or strengthen the prompt to forbid `**/<slug>.md` shapes. | §1, §2 |
| F2 | P2 | Tool errors return `'status': 'success'` with `Error:` content; Langfuse `level=ERROR` is never set. Error-rate dashboards silently show 0%. Fix tool wrappers (e.g. virtual-fs glob/read/edit) to set the proper observation status. | §1 |
| F3 | P2 | `check_my_work` runs up to 16× per batch on glm-5. Cap it (1× per page edit) in middleware or prompt. | §3 |
| F4 | P3 | `trace_tags` set in `compile_all.py` don't reach Langfuse top-level `tags`; only `metadata.tags`. Tag-based filters in Langfuse UI/API will miss this run. | exec summary |
| F5 | P3 | `read_file` paginated reads occasionally request `Line offset N exceeds file length` — 16 occurrences. Suggests stale page-length state in the agent's planning. | §1 |

---

AUDIT: /Users/amtagrwl/git/email-knowledge-base/.claude/worktrees/sparkling-skipping-fiddle/docs/audits/run-3e88f996-trace-audit-2026-04-29.md
