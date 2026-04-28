# Smoke run `02c9d536` — follow-up improvements (2026-04-28)

Notes captured mid-run; **not** intended to ship inside the live smoke.
File these as separate PRs once the smoke completes and we have full
final numbers.

## Context

The 2026-04-28 40-thread smoke surfaced a NEW failure mode for
`moonshotai/kimi-k2.6`: **pre-write reconnaissance paralysis** on
decision-heavy threads.

Detail trace (Batch 4, thread `19be9883c6d921a6` — Real-Time D-Rank
DA/DB Removal, 17 emails, 7 stakeholders):

- Tool calls: 14 × `read_file`, 6 × `resolve_page`, 3 × grep, 2 × `get_page_summary`,
  1 × `create_entities`, 1 × `get_thread_context`.
- **Edits: 0. Writes: 0. `check_my_work`: 0. `log_insight`: 0.**
- 3 single LLM rounds consumed 575 s of the 900 s budget (64 %): 263 s,
  162 s, 150 s of pure model deliberation on 50 k+ input tokens.

The agent died in the *researching-which-page-to-edit* phase — it
correctly disambiguated multiple candidate pages, but kimi's
deliberation speed at this context size meant it never reached the
write step. Yesterday's "kimi 24/24 valid" was on easier threads where
this didn't bite.

`EditStalenessMiddleware` (PR #245) doesn't help here — the middleware
watches for an edit storm that never started.

## Proposed three-part nudge stack

User-suggested instinct (2026-04-28 ~20 IST). All three layers are
**orthogonal** — together they cover the failure modes the current
single 900 s wall-clock can't distinguish.

### 1. Raise batch wall-clock 900 s → 1800 s (`--batch-timeout`)

Doubling the ceiling gives kimi room to finish on slow-deliberation
threads where the work itself is legit. Per-trace: 575 s of LLM thinking
+ 152 s of tool I/O + ~100 s of edits would fit in 1800 s; not in 900.

**Cost:** each genuine hang now wastes 30 min instead of 15. Mitigated
by layers 2 and 3 below — neither relies on the wall-clock to detect a
problem.

### 2. Stuck-for-X-minutes timeout (no-tool-activity heartbeat)

Independent of `--batch-timeout`. Fire when:

- No tool call (any) has completed in the last `STUCK_AFTER_S` seconds
  (suggested **300 s** = 5 min).
- The Python process is genuinely idle on the LLM round — not blocked on
  a tool waiting for I/O.

This is the missing signal: today's wall-clock can't tell "kimi is
deliberating productively for 4 minutes" from "the LLM call is wedged".
With the heartbeat, a true hang gets killed at 5 min instead of 15.

Implementation sketch: a `threading.Timer` reset on every tool-call
return; if it fires, raise `StuckLLMRoundError` and the coordinator
treats it as `outcome='timeout'` like today's batch-timeout.

### 3. Read-without-write reminder (reconnaissance-paralysis nudge)

Inject a system message into the next LLM round when the per-batch
counter hits:

- `read_file` count ≥ **8** AND
- `edit_file` + `write_file` + `log_insight` count == 0

Message shape:

> You have read 8 files in this batch without producing any edit,
> write, or terminal-decision insight. The reconnaissance phase is
> long enough — pick a page now and either (a) commit a draft via
> `write_file` / `edit_file`, or (b) call `log_insight` with a
> terminal outcome (`already_captured`, `trivial_skip`, etc.) and
> return.

Mirrors the existing `terminal_decision_guard` middleware pattern —
*nudge*, not block.

This is exactly the shape that broke kimi today: 14 reads + 6 resolves +
0 commits. A reminder at read #8 would have either:

- Forced kimi into a `log_insight("trivial_skip")` decision (saving the
  900 s timeout entirely), OR
- Forced an `edit_file` attempt early enough that the staleness signal
  could fire OR the work could complete in budget.

## Why all three together (not just one)

| Layer | Catches | Misses |
|---|---|---|
| 1. 1800 s wall-clock | Slow but productive deliberation | Genuine hangs (still wastes 30 min) |
| 2. Stuck-for-5min | Hung LLM / network calls | Slow-but-active deliberation (no false-positive) |
| 3. Read-without-write | Reconnaissance paralysis | Real edit storms (those need EditStaleness) |

The current single `--batch-timeout=900` collapses all three failure
shapes into one "wasted 15 min" outcome and gives no actionable signal.

## Sizing (post-smoke validation needed)

- **STUCK_AFTER_S = 300**: chosen because the largest single LLM round
  observed today was 263 s. 300 s is just past P99 of legitimate
  deliberation; anything longer is genuine hang.
- **READ_THRESHOLD = 8**: today's failed batch hit 14 reads, the
  successful glm-5 baseline averages 5–7 reads. 8 splits the
  distribution.
- **--batch-timeout 1800**: covers today's 575 s thinking + 152 s tools
  + ~500 s edits/critique with headroom.

Post-smoke, recompute these against the full distribution:

```bash
uv run python scripts/compare_models.py --since 2026-04-28 --format json
```

Then revisit the threshold values before shipping.

## 5. Match openai.APITimeoutError + OpenrouterException in `_is_model_unavailable_error`

Diagnosed 2026-04-28 ~21:45 IST. Today's smoke surfaced 9 +
1 = 10 kimi failures that *should* have triggered pool-retry but
slipped through `_is_model_unavailable_error` because the matcher
doesn't recognize their shapes.

### Shape A: `Request timed out.`

`openai.APITimeoutError` raised when httpx ReadTimeout fires inside
the LangChain `ChatOpenAI` client. Today's signature was
`Error: Request timed out.` after exactly 360 s of dead air
(3 × 120 s — see #6 below).

Currently the matcher only catches `Error code: 401/403/5xx` and the
HTML `<title>NN ...</title>` shapes. `Request timed out.` slips
through as a "real failure", the batch dies, all 5 emails get marked
failed.

### Shape B: `OpenrouterException - Provider returned error`

Specific 400 shape that LiteLLM emits when OpenRouter returns an
upstream error AND no fallback model group is configured for the
target model (kimi has no LiteLLM-side fallback). The current matcher
catches `Error code: 400 - 'Invalid model name'` but not this 400.

### Fix shape

Extend `_is_model_unavailable_error` (`scripts/compile_all.py:1547`)
with two more patterns:

```python
# openai SDK ReadTimeout shape — propagated from httpx
if "Request timed out." in msg:
    return True
# LiteLLM proxy's "OpenRouter blipped, no fallback configured" 400
if "OpenrouterException - Provider returned" in msg:
    return True
```

Both should also be tested isinstance-style (`isinstance(exc,
openai.APITimeoutError)`) for the type-based path.

Costs: zero false-positive risk — both strings are LiteLLM/openai
SDK structured prefixes that don't appear in normal tool output.

## 6. Raise `ChatOpenAI(timeout=120)` to 300s — keep SDK retries

User-corrected 2026-04-28: the SDK's default 2 retries are sensible
— don't disable them. The bug is the per-request **timeout**: 120 s
is too aggressive for legitimate long-context rounds (kimi-on-50k
input can genuinely take 5+ minutes to think). A round that's
productively running gets killed at 120 s and then re-tried 2× more
on the same wedged-or-genuinely-slow request → ultimately fails.

### Fix

```python
# src/compile/compiler.py:2252
ChatOpenAI(
    model=...,
    timeout=300,  # 5 min — was 120s, too aggressive for long-context
    # max_retries left at SDK default 2 (3 total attempts)
)
```

### Total budget per ultimate-failure: 3 × 300 = 900 s

Equal to today's `--batch-timeout=900` — i.e. one truly stuck round
consumes the whole batch budget. That's acceptable because:
- The mid-run quarantine (#4 above) catches the *pattern* of "model
  is wedging repeatedly" and drops it from the pool — limiting how
  many ultimate-failure batches a bad model can burn through.
- Today's 120 s timeout was the failure-mode driver: most "Request
  timed out" today were the SDK killing legitimate 2-3 minute rounds
  too early. 300 s should let those rounds finish.

### When to revisit

If the next smoke shows that 300 s × 3 = 900 s still triggers
batch-timeout failures, the answer ISN'T "lower the timeout" or
"disable retries" — it's that the upstream provider is genuinely
broken and the mid-run quarantine should have caught it. Verify the
quarantine fired; if it did, the system is doing its job.

## 4. Mid-run model quarantine (the missing fast feedback loop)

User-flagged 2026-04-28 ~21:30 IST. Today's run made the gap obvious:
kimi-k2.6 failed 25 of 40 attempts (62%) BEFORE `_healthy_pool` could
re-evaluate, because the guard runs *only at run-start*. Confirmed in
`scripts/compile_all.py`:

- `_healthy_pool` defined at line 1472.
- Called from `_prepare_model_pool` at line 1759.
- `_prepare_model_pool` invoked once at line 1940 (run-start).
- Never called again inside the batch loop — the pool is frozen for
  the duration of the run.

This is wrong. A model that's failing 5/5 in a row should get dropped
mid-run, not at the next run-start.

### Proposed fix — re-call the existing `_healthy_pool` per-batch

User-corrected 2026-04-28: **don't write a new in-process rolling
window. Just call `_healthy_pool()` from the existing code path before
each batch picks a model.** The function already reads
`compile_attempts` from Postgres, which the in-progress run is
populating in real time. The 4h short-window guard
(`fail_rate > 0.80 AND total >= 5`) already catches today's pattern.

Implementation: `~3 LOC` in `scripts/compile_all.py` — call
`_prepare_model_pool` (or just `_healthy_pool`) before each batch's
random model pick instead of once at run-start. Caches the result for
the batch, picks fresh on the next batch.

**Sizing today's run with this rule:**
- After batch 4 (5 timeouts on D-Rank, all kimi), kimi's 4h-window
  fail_rate is 5/5 = 100% > 0.80 threshold → instant quarantine on
  the next batch's pool prep.
- Run continues with grok + glm-5 only. Budget +5h saved.
- Same code, same thresholds, same data source — no parallel
  abstractions to maintain.

## Filing

Four separate PRs (orthogonal change sets):

1. `--batch-timeout 1800` — `scripts/compile_all.py` default + `src/config.py`
   if applicable. ~5 LOC.
2. New `StuckHeartbeatMiddleware` — runs in parallel to the agent loop.
   ~80 LOC + tests.
3. New `ReconnaissanceParalysisMiddleware` (or extend
   `EditStalenessMiddleware` with a reverse trigger) — counts reads vs
   writes per batch. ~60 LOC + tests.

Order of impact (rough): **4 > 3 > 2 > 1**. The mid-run quarantine
saves the largest amount of wasted budget on bad-day model behavior;
the reminder fires before either timeout can; the heartbeat catches
true hangs; the wall-clock raise gives genuine slow work room.
