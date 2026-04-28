# Smoke run 99a267f4 — recursion-failure deep dive (2026-04-28)

**Run id:** `99a267f4-55f1-4fad-bca9-e23589bfab01`
**Final tally:** 134 attempts · 95 compiled · **26 failed** · 13 skipped · 19% fail rate.
**Investigation:** 5 parallel deep-trace agents (full Langfuse traces, no
shortcuts) + canonical `compare_models.py` and `trace_scorecard.py`
output.

## What the failures looked like

| Class | Count | Models | Symptom |
|-------|------:|--------|---------|
| **Recursion limit (150)** | 15 | glm-5.1 (15), glm-5 (0) | `Recursion limit of 150 reached` after 393-650s |
| **Connection error @ 2s** | 8 | grok-4.1-fast (8) | `Connection error.` instant fail |
| **Connection error @ 30342s (8.4h)** | 3 | glm-5 (3) | `Connection error.` after 8.4 *hours* |

The 26 failures cluster on TWO distinct root causes — neither one is
"the agent loops on a single tool call" (the prior assumption from
killed-smoke `bndt9m8p9`'s gate-loop diagnosis).

## Root cause #1 — recursion limit was insufficient (15 failures)

**Convergent finding from agents 1, 2, 5.**

LangGraph counts every *node visit* as a super-step, not every parent
turn. Today there are 3 active `after_model` middlewares
(`TodoListMiddleware`, `CheckMyWorkGateMiddleware`,
`terminal_decision_guard`). Each parent turn therefore costs ~5
super-steps:

    model → ToolNode → terminal_decision_guard → CheckMyWorkGate → TodoList

So `recursion_limit=150` = **30 model turns**. That is *not* enough
budget for a substantive 5-email batch that touches 2 existing pages,
runs 2 reviewer subagents (which share the parent's budget), and
fixes one critique-blocked summary-stale-date.

### Trace 1 evidence — `19b9e1972f3f97a0` (technical announcements)

Agent 1 read the full trace tool-by-tool. The agent **finished its
work cleanly** — clean validators, passing reviewer, `finish_reason=stop`
on its terminal AI message. Then the graph crashed at recursion-150
mid-final-state-commit.

Super-step accounting:

- 30 parent model turns × 3 `after_model` middlewares = 90 super-steps
- 30 model + ~29 ToolNode = 59 super-steps
- 3 reviewer subagents (5+7+3 model turns + their tools + their
  middlewares) ≈ 50 super-steps
- **Total ≈ 200, hit 150 mid-second-reviewer-cycle**

### Trace 2 evidence — `19be9885492d95b1` (Contextual BLNI)

Different mechanism, same outcome. Agent 2 found the agent did
`write_file` (full page rewrite) on turn 12, then pivoted to
incremental `edit_file` chains. The 6th `edit_file` failed with
`Error: String not found in file` — the `old_string` was built from
a stale mental model after 5 sequential edits without an intervening
`read_file`. Agent then burned 12 of 30 turns trying to clean up
self-inflicted duplicate sections.

Both threads benefit from the recursion bump. Trace 1 needs it to fit
healthy work + reviewers; trace 2 needs it for recovery headroom.

## Root cause #2 — macOS sleep froze the monotonic clock (11 failures)

**Convergent finding from agents 3, 4.**

`asyncio.wait_for` (used in `_ainvoke_with_timeout` for
`invoke_timeout_s=900`) and `future.result(timeout=)` (used in
`_run_with_timeout` for `--batch-timeout 900`) both rely on Python's
monotonic clock. On macOS, `time.monotonic()` = `CLOCK_UPTIME_RAW`
**freezes during system sleep**. So both timeouts paused with the
process.

### Smoking-gun timeline (UTC)

- 2026-04-27 23:27:48 — last activity (six `resolve_page.semantic`
  calls timing out at 45s each on glm-5 batch 30, mid post-write
  enrichment).
- **8h 19m of total log silence** — laptop asleep.
- 2026-04-28 07:47:20 — first activity post-wake. Three batches that
  had been queued at sleep-time fail in 9 seconds:
  - glm-5 batch 30: `Connection error.` (8.4h elapsed; the hung TCP
    socket finally returned a TLS reset).
  - grok batches 31-33: `Connection error.` at ~2s each (DNS not yet
    re-resolved post-wake; `langfuse.intermesh.net` returns
    `NameResolutionError` in the same 9-second window).
- Batch 34 onward: network back, compiles resume normally.

Smoke was started under `caffeinate -is`. That doesn't prevent
lid-close sleep; only `caffeinate -d` (display sleep) or system-level
`pmset` config does.

## The fix this PR ships

**Bump `recursion_limit` 150 → 250** in two places:

- `src/compile/compiler.py:2660` — `run_compilation` default.
- `scripts/compile_all.py:1839` — `--recursion-limit` CLI default.

Sizing rationale (per agents 1+5):

- Successful glm-5.1 batches at 5 emails finished in 18-30 model
  turns / 29-50 tool calls. At 5 super-steps per turn that's 90-150
  super-steps — exactly at the old limit's edge for the easy cases.
- The failing batches' final super-step trajectories projected to
  180-220. **250 covers them with headroom; 300 starts to mask
  pathological loops.** 250 is the lean target.
- The existing `_check_my_work_cache` per-write-epoch dedupe still
  catches genuine spirals (cache hit = no real edit between calls →
  agent gets a fast no-op). 250 doesn't disable that signal.

## What this PR does NOT fix (intentional)

1. **macOS sleep / monotonic-clock issue (11 failures).** Cheapest
   fix is operator: run smokes under `caffeinate -dis` (covers display
   + idle + system) and consider `pmset noidle` for long runs. A
   wall-clock watchdog using `threading.Timer` against `time.time()`
   is possible but requires a careful test harness — separate PR.
2. **`edit_file` staleness loop (Trace 2 mechanism).** Agent 2's
   recommendation: `EditStalenessMiddleware.after_tool` hook that
   injects a "re-read the file" reminder when `edit_file` returns
   `Error: String not found`, OR proactively after 3 consecutive
   `edit_file` calls on the same path without an intervening
   `read_file`. With 250 super-steps the recovery has more headroom,
   but the staleness hook is the durable structural fix. Filed as
   follow-up.
3. **Middleware-fan-out tax (Agent 1's secondary suggestion).**
   Collapsing the 3 `after_model` middlewares into one composite
   hook would save ~60 super-steps per batch (~30-40% reduction in
   graph traversal cost). Out of scope; future refactor.
4. **glm-5.1's specific cascading-summary-stale weakness (Agent 5).**
   Other models (minimax-m2.7, grok) compiled the same threads
   cleanly. glm-5.1 has 0/15 success on these 2 threads across runs.
   Could justify smaller `batch_size=2` for high-decision threads, or
   a per-model recursion override. Future work.

## What success looks like

Next smoke that lands the same threads should:

- glm-5.1 on `19b9e1972f3f97a0` finishes in 200-220 super-steps (under
  the new 250 ceiling).
- `Recursion limit of 250 reached` errors → 0, OR if they appear,
  they're on legitimately harder threads (≥6 system pages, ≥5
  decisions) that warrant a `batch_size=2` fix not a further bump.

If the next smoke still hits recursion on these 2 threads, the answer
is the staleness hook, not 300.
