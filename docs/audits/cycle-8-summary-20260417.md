---
timestamp: 2026-04-17T09:21:00Z
cycle: 8
run_id: 56433cbb-aff8-4bbf-a7d0-6a6c77b83159
started_at: 2026-04-17 12:50 IST
ended_at: 2026-04-17 14:51 IST (~121 min)
batches: 20
cost: unknown
outcomes:
  compiled: 8
  skipped: 11
  failed: 1
effective_rate: 88.9%  # 8 / (8 + 1)
fixes_verified:
  H_chronological_scope: not checked in this audit
  J_silent_fail_retry: held on the observed retry path
  K_litellm_401_retry: not exercised in this run
  L_email_path_autoheal: partial — leading-slash normalization held, missing-email_path recovery still failed
observations:
  reviewer_path: healthy on successful content writes
  already_captured: working in live runs
  observability_gap: audit scripts still lag runtime behavior
ship_gate_status: mixed (runtime healthier than scorecard; observability needs correction)
---

# Cycle 8 Summary — 2026-04-17

## Scope

This audit reconstructs the latest completed cycle from the live system rather
than relying on the checked-in markdown audits alone.

Code snapshot reviewed:

- `origin/main` at `3fecf7d` (`feat(validate): pymarkdown structural pre-check (MD024 + friends) (#155)`)

Data sources used:

- Postgres `compile_attempts`
- Postgres `messages`
- Postgres `message_touched_pages`
- Postgres `compile_insights`
- Selected Langfuse compile traces
- Selected reviewer-subagent traces
- Current wiki pages on disk
- Raw email files on disk

Cycle label:

- Treat `run_id=56433cbb-aff8-4bbf-a7d0-6a6c77b83159` as the completed
  Cycle 8 run, based on chronology.
- Immediate prior comparison runs:
  - `b8f19851-5852-4429-90d9-287445f11750`
  - `90fbcef7-4540-44ac-b517-f08a93d11c35`

## Executive Read

We are closer to the North Star than the current automated audit output makes
it look.

The core compile loop is no longer dominated by the old filing-cabinet failure
mode. The latest completed run mostly reached terminal outcomes cleanly:

- content-page compiles are happening
- `already_captured` is working in real runs
- reviewer-subagent traces exist and can return clean `pass` verdicts

The main remaining problems are narrower:

1. terminality on low-signal follow-up emails is still brittle
2. same-thread duplicate topic pages are causing merge ambiguity
3. the observability scripts are stale enough to misstate the system's health

## Run Summary

### Latest completed run (Cycle 8)

- `run_id`: `56433cbb-aff8-4bbf-a7d0-6a6c77b83159`
- window: `2026-04-17 12:50 IST` → `2026-04-17 14:51 IST`
- raw `compile_attempts` rows: `21`
- unique messages after latest-attempt normalization: `20`

Normalized terminal outcomes:

| Outcome | Count |
|---|---:|
| `compiled` | 8 |
| `skipped` | 11 |
| `failed` | 1 |

Interpretation:

- The extra raw row came from a retry path where one message first failed on
  `minimax/minimax-m2.7` with a `200-empty` LiteLLM response, then succeeded on
  `z-ai/glm-5`.
- So the latest run is not "21 independent outcomes"; it is "20 unique
  messages, 19 of which ended terminally".

### Immediate comparison runs

| Run | Compiled | Skipped | Failed | Effective |
|---|---:|---:|---:|---:|
| `56433cbb-aff8-4bbf-a7d0-6a6c77b83159` | 8 | 11 | 1 | 88.9% |
| `b8f19851-5852-4429-90d9-287445f11750` | 14 | 9 | 2 | 87.5% |
| `90fbcef7-4540-44ac-b517-f08a93d11c35` | 11 | 11 | 3 | 78.6% |

The trend across these three runs is:

- fewer hard failures
- more correct terminal no-op outcomes
- fewer cases of messages being left in limbo

## Model Mix (latest-attempt view, last 10 hours)

Computed from the latest attempt per message in the last 10 hours:

This table spans all runs in that window, not just `56433cbb-aff8-4bbf-a7d0-6a6c77b83159`.

| Model | Compiled | Skipped | Failed |
|---|---:|---:|---:|
| `z-ai/glm-5` | 16 | 16 | 1 |
| `minimax/minimax-m2.7` | 13 | 9 | 0 |
| `x-ai/grok-4.1-fast` | 12 | 16 | 0 |

Read:

- `z-ai/glm-5` is carrying the largest share of successful compiles, but it
  still owns the only residual failure in this latest normalized window.
- `x-ai/grok-4.1-fast` is skewing heavily toward terminal no-ops, which is not
  automatically bad; many of those are valid `already_captured` / `trivial_skip`
  outcomes.
- `minimax/minimax-m2.7` is materially healthier than the earlier cycle notes
  suggested, once retries are normalized.

## Key Findings

### 1. Observability is now lagging the runtime

The biggest mismatch is not in the compiler itself but in the scripts we use to
grade it.

Current issues:

- `scripts/audit_50_traces.py` still treats `write_draft_page` as the proxy for
  "attempted content page". That was reasonable for an earlier design, but it
  no longer matches the current agent behavior. Real content writes are now
  happening through `write_file`, `edit_file`, `patch_page`, and ultimately the
  `message_touched_pages` catalog.
- The same audit still treats leading-slash virtual filesystem paths like
  `/raw/...` and `/wiki/...` as absolute-path violations, even though the
  current prompt explicitly teaches those virtual roots as the correct
  agent-visible namespace for filesystem tools. This does NOT apply to
  `log_insight.email_path`, which is a coordinator-facing identifier whose
  canonical form is `raw/...` without the leading slash.
- `scripts/trace_scorecard.py::_list_traces_by_run` maps
  `(run_id, thread_id) -> trace_id`. That key is not unique once a single run
  processes multiple single-email batches from the same thread. This can
  overwrite traces and undercount the very threads we most care about.

Practical effect:

- the generated audit can say "0 content attempts" while the DB clearly shows
  compiled messages touching topic/system pages
- the trace-derived read is currently more pessimistic than the actual system

### 2. The remaining miss is a terminality problem, not a reviewer problem

The clearest live miss in Cycle 8 is the Seller BL API optimization thread
(`19bb72fc748876c2`).

The failing path did **not** look like:

- page write happened
- reviewer passed bad content
- coordinator mis-marked filing-cabinet output as compiled

Instead it looked like:

- the model decided the email was likely a no-op / low-signal follow-up
- it tried to `log_insight(trivial_skip)`
- it omitted `email_path`
- `log_insight` returned a clear coordinator-facing error
- the model retried badly and never converged

That is a real residual bug, but it is a much narrower one than the old
failing-cabinet story. The recovery path is intelligible to a human operator,
but not self-healing enough for the model in a single turn.

### 3. Same-thread page fragmentation is now a top quality risk

Thread `19bb72fc748876c2` currently spans two active topic pages:

- `seller-bl-api-optimization`
- `seller-bl-user-details-verification-api-optimization`

That split is causing two classes of trouble:

- later follow-up emails become ambiguous merge targets
- a substantive thread can look "already covered" and "not yet merged" at the
  same time depending on which page the model resolves first

This is now a stronger source of failure than the old person-page filing
cabinet issue.

### 4. Reviewer traces exist and are healthy on the success path

The reviewer subagent is no longer hypothetical. In the successful compile path
for thread `19bb1ec15f6e5589`, the writer updated the topic page and invoked the
reviewer, which returned `verdict="pass"`.

That matters because it narrows the debugging surface:

- reviewer path works
- compile path can write good pages
- the remaining misses are mostly happening before reviewer involvement

### 5. `already_captured` is doing useful real work

Cycle 8 shows multiple clean `already_captured` outcomes that match the actual
wiki state:

- Seller VANI follow-up
- PhotoSearch Qdrant follow-up
- several short acknowledgments / escalations on already-covered topics

That is progress toward the North Star. The system is learning to say
"substantive thread, but no new page delta" instead of either forcing an
unhelpful edit or leaving the message pending forever.

## Case-Study Anchors

Detailed write-ups are in `docs/audits/cycle-8-case-studies-20260417.md`.

Headline examples:

1. **Seller BL API optimization**
   - residual miss
   - duplicate-page fragmentation
   - brittle `log_insight` recovery
2. **Seller VANI**
   - good `already_captured` path
   - correct terminal skip
3. **Seller CustType capture**
   - good content write
   - reviewer pass
   - correct compile outcome

## Recommended Next Work

### Immediate

1. Fix the observability scripts before using them as the headline read.
   - replace `write_draft_page` as the proxy for content attempts
   - stop treating `/raw/...` and `/wiki/...` as path violations
   - stop collapsing traces by `(run_id, thread_id)` alone

2. Harden `log_insight` recovery for skip categories.
   - if `email_path` is omitted in a single-email batch, auto-fill it
   - or at minimum surface the exact missing path in the error in a way the
     model reliably copies on the next call

3. Add a same-thread duplicate-page guard.
   - if an active page already cites the current thread, bias heavily toward
     merge or `already_captured`
   - if a new page is about to be created for a thread already represented by
     an active page, force reviewer scrutiny or block

### Next wave

4. Add `get_thread_summary(scope="up_to_current")`.
   - the remaining failures are long-thread reasoning failures
   - this would augment `get_thread_context`, not replace `resolve_page`
   - this is the highest-leverage tool addition now

5. Run the next deep-dive loop against the biggest failed thread clusters.
   Current heavy-failure clusters include multiple failed or uncited latest
   attempts in the current audit window:
   - `19b4f4a7e8fb81a6`
   - `19ae8af3083f8aab`
   - `19bbb36128aa24c6`
   - `19ba16e2d8ee0bbf`
   - `19ba2506d17efe7c`

## North-Star Read

We are closer to the North Star.

What changed relative to the earlier cycle-4/5/6-era failure mode:

- the system is not primarily filing into person pages anymore
- terminal skip outcomes are real and useful
- reviewer traces are present and functioning
- content pages are being updated and cited from the catalog

What is still missing:

- fully reliable no-op terminality
- deterministic suppression of same-thread duplicate topic pages
- observability that matches the runtime architecture

## Should the North Star Extend?

Not yet.

The right move is to finish the current North Star with better terminality,
dedupe, and honest observability before adding more personas or another layer
of product ambition.

If we extend anything right now, it should be the acceptance criteria:

- every actionable email ends terminally
- same-thread duplicate topic creation is blocked or explicitly reviewed
- audit scripts measure the real runtime, not an older prompt/tool regime
