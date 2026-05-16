---
timestamp: 2026-04-17T05:15:00Z
cycle: 4
case_n: 2
run_id: 03d525c8-2bd9-464a-9ba9-a017bb5cace8
bugs_surfaced: [I]
severity: high
impact: "invalidates the cycle-4 46% effective-rate headline"
representative_trace: /tmp/trace_buyer_enrich.json
representative_thread: 19ba19da001af1d0
representative_raw: raw/2026-01-09_mplaunchim-buyer-enrichment-prompt-on-buyermy-dash_35d42f30.md
---

# Cycle 4 Case #2 — Skip-insight orphans: `log_insight` drops `email_path`

## Finding in one sentence

**31 of 43 (72%) skip-type insights in Cycle 4 had `email_path=None`**,
so the coordinator couldn't match them back to messages — the agent's
decisive skip decisions got silently dropped, and the coordinator
fell back to `outcome='failed'` with `error='not cited in wiki'`.

## The discrepancy

Cycle 4 summary claimed:

| Outcome | count |
|---|---:|
| compiled | 6 |
| **skipped** | **12** |
| failed | 7 |
| total | 25 |

Real picture from `compile_insights` table:

| insight category | total rows | with `email_path` | without |
|---|---:|---:|---:|
| `already_captured` | 17 | 8 | **9** |
| `trivial_skip` | 26 | 4 | **22** |
| **total skip-type** | **43** | **12** | **31** |

Every insight with `email_path` set → `outcome='skipped'` ✓ (coordinator
works). Every one without → nothing happened.

## Where the 31 came from (grouped by message text)

```
n=21  "Congratulatory acknowledgment from Gyaneshwar Mongha..."
n=5   "Amit Agarwal's reply acknowledges the SonarQube..."  (split 4+1)
n=1   "Mohak's Jan 9 reply..."
n=1   "Neeraj Agrawal's questions about DSPy optimization..."
n=1   "Neeraj's +1 reply..."
n=1   "Vaibhav's one-line reply requesting CustType-wise stats..."
n=1   "Email was already compiled on 2026-04-13. Content about Java..."
```

**8 unique decisions → 31 duplicate log_insight calls** (one email
triggered 21 identical calls — recursion symptom, not debouncing).

## Failed batches mapped to orphan insights

```
thread                failed batch raw                                        likely orphan
19b6eb14bcb68611      2026-01-05_..._7cb400f3.md + 2026-01-08_..._c4613c75.md  sonarqube (rows 50-55)
19ba19da001af1d0      2026-01-09_..._35d42f30.md  (buyer-enrichment)           Mohak (row 81), Gyaneshwar ×21
19adf462f1a5b7e2      2026-01-08_..._58b4077d.md  (quality agent)              Neeraj DSPy
19bb55f3f80f8a9c      2026-01-13_..._1b353f6d.md  (ABC test)                   Vaibhav
```

**5 of the 7 "failed/pending" batches were decided-skip**. The agent
made the right call; the tool signature lost the evidence.

## Trace walk: Buyer Enrichment (thread `19ba19da001af1d0`)

`/tmp/trace_buyer_enrich.json`, 45 observations, 1 `log_insight`:

```python
log_insight(
    category='already_captured',
    message="Mohak's Jan 9 reply ('This has to be evolved and expanded
    further. Pls discuss') is already captured in the Leadership
    Feedback section of buyer-enrichment-prompt-buyermy. All other
    content in this thread (Monarch's announcement, Sumit's bug
    matrix, Raghav's foreign-buyer field suggestion, Shobhna's
    date/time bug) is also already on that page.",
    # email_path NOT PROVIDED
)
→ {"ok": true, "id": 81}
```

Tool returned success. Message landed in DB. But `email_path=None` →
coordinator's `_insights_skip_paths(run_id)` set is a no-match →
message stays pending → outcome `failed`.

## Why the agent drops `email_path`

1. **Tool signature**: `email_path: str | None = None` (optional).
2. **Prompt Example 6 shows it** — but as one parameter among several,
   not called out as required.
3. **Multi-email batch cognitive model**: agent thinks in terms of
   "the thread" (one summary) not "per message" (N decisions). When
   the prompt says "decide each email", it still feels one insight
   message covers the batch.
4. **No feedback loop**: `log_insight` returns `{ok: true}` regardless.
   Agent sees success, moves on. No signal that the skip didn't take.

## Bug I (NEW) — `log_insight` accepts orphan skips

**Contract**: when `category ∈ {trivial_skip, already_captured}`, the
insight MUST name a specific raw path. Otherwise the coordinator cannot
materialize the skip.

**Fix** (U1 of case-2 follow-up): reject the call at the tool boundary.

```python
SKIP_CATEGORIES = {"trivial_skip", "already_captured"}

if category in SKIP_CATEGORIES and not email_path:
    return {
        "ok": False,
        "error": (
            f"email_path is required for category={category!r}. "
            f"Call log_insight once per email you're skipping, "
            f"with email_path='raw/...md'. See Example 5/6 in prompt."
        ),
    }
```

**Why this over coordinator inference**: when batch has >1 email, the
coordinator can't tell which insight belongs to which message. Pushing
the constraint to the tool forces the agent to be explicit, and the
per-email loop is what the prompt already teaches (step 10 terminal
decision).

## Impact on reported metrics

**Cycle 4 headline `46%` was UNDER-STATED the bug, not over-stated:**

- Skipped emails the coordinator captured: 12
- Skipped emails lost to orphan insights: 5 (of 7 "failed")
- Real rate if fix had been in place: `compiled / (total - all_skipped)`
  = `6 / (25 - 17)` = **6/8 = 75% content-page citation rate**
  (vs. the reported 6/13 = 46%)

The agent's judgment was **better** than the metric showed. The writer
is converging; the bookkeeping path is leaking.

## Priority & sequencing

This is the highest-leverage fix for Cycle 5:

1. **Bug I fix** (tool-level require) — 10-line diff, ships today
2. **Cycle 5 re-measurement** — expect reported effective rate to jump
   naturally as orphans become real skips
3. **Then** layer Bug F/G/H fixes from Case 1

## Artifacts

- Langfuse trace (buyer_enrich): `/tmp/trace_buyer_enrich.json` (1.5MB)
- DB queries (reproducible):
  ```sql
  SELECT category, email_path IS NULL AS orphan, COUNT(*)
  FROM compile_insights
  WHERE run_id='03d525c8-2bd9-464a-9ba9-a017bb5cace8'
    AND category IN ('trivial_skip','already_captured')
  GROUP BY 1,2;
  ```
