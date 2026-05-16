---
timestamp: 2026-04-17T09:21:00Z
cycle: 8
run_id: 56433cbb-aff8-4bbf-a7d0-6a6c77b83159
scope: thread-level case studies from the latest completed cycle
method: raw email + DB state + wiki page + Langfuse trace cross-check
---

# Cycle 8 Case Studies — 2026-04-17

## Scope

This document turns the latest live audit into concrete thread-level case
studies. Each case study cross-checks:

- raw email content
- current DB state
- current wiki pages
- Langfuse compile trace(s)
- reviewer-subagent trace(s), when present

The goal is not "count more traces". It is to explain what actually happened in
representative good and bad paths.

## Case 1 — Seller BL API optimization

### Thread

- `thread_id`: `19bb72fc748876c2`

### Raw emails examined

- `raw/2026-01-13_mplaunchim-optimising-api-hits-of-user-details-use_12e13a44.md`
- `raw/2026-01-13_mplaunchim-optimising-api-hits-of-user-details-use_1293128e.md`
- `raw/2026-01-13_mplaunchim-optimising-api-hits-of-user-details-use_c3fa161b.md`
- `raw/2026-01-13_mplaunchim-optimising-api-hits-of-user-details-use_cc1ac043.md`

### Current page state

Two active topic pages exist for the same thread/concept:

- `wiki/topics/seller-bl-api-optimization.md`
- `wiki/topics/seller-bl-user-details-verification-api-optimization.md`

Both cite the same `source_threads` value: `19bb72fc748876c2`.

### DB state

Within this thread we currently see four terminal shapes:

- compiled into `seller-bl-api-optimization`
- compiled into `seller-bl-user-details-verification-api-optimization`
- skipped with no page touch
- failed with no page touch

That mixed outcome inside one thread is the smell.

### Trace evidence

Representative compile trace:

- `trace_id`: `6569afd9026319d70b719888fe9aff5f`
- `name`: `compile:z-ai/glm-5:19bb72fc7488`
- `run_id`: `56433cbb-aff8-4bbf-a7d0-6a6c77b83159`

What happened in the trace:

1. The model correctly recognized the low-signal nature of the follow-up and
   tried to choose a terminal no-op.
2. It called `log_insight(category="trivial_skip")` without `email_path`.
3. `log_insight` returned the correct error:
   - `email_path is required for category='trivial_skip' ...`
4. The model repeated the same malformed call multiple times instead of
   recovering.
5. It then drifted into extra reads and lookups without ever converging on a
   clean terminal action.

### What the raw emails actually say

- The root announcement email has the real product facts: 99% and 90%
  reductions, cookie storage, 30-minute verification polling, go-live, and
  ticket `637216`.
- `1293128e`
  is a short appreciation plus a future-looking code-review suggestion.
- `c3fa161b`
  adds one real follow-up concern: security of storing the info in cookies.
- `cc1ac043`
  escalates the security review to named reviewers.

### Diagnosis

This is **not** the old filing-cabinet bug.

It is a combination of:

1. **same-thread duplicate topic pages**
   - the thread has been split across two active pages
   - later messages have ambiguous merge targets
2. **brittle no-op terminality**
   - the model reached for the right tool (`log_insight`)
   - but failed to recover from a missing `email_path`

### What to fix

1. If an active page already cites the current `thread_id`, block creation of a
   second active page on the same thread unless the reviewer explicitly blesses
   the split.
2. Make `log_insight` self-heal skip categories in single-email batches.
3. Add a thread-level merge bias so later replies in the same thread default to
   merge or `already_captured`.

## Case 2 — Seller VANI (`already_captured` working)

### Thread

- `thread_id`: `19bb83c1c8120c79`

### Raw email examined

- `raw/2026-01-14_mplaunchim-seller-vani---ready-for-testing_6630667c.md`

### Current page state

- `wiki/systems/seller-vani.md`

The current page already captures:

- leadership feedback
- pricing question
- integration thoughts
- rerouting / rollout direction
- next-step planning

### Trace evidence

Representative compile trace:

- `trace_id`: `42c453f882fc6a4b7ae25bd68a6237b6`
- `name`: `compile:x-ai/grok-4.1-fast:19bb83c1c812`
- `run_id`: `56433cbb-aff8-4bbf-a7d0-6a6c77b83159`

Observed sequence:

1. The model looked up the existing system page.
2. It called `log_insight(category="already_captured", ...)`.
3. The tool succeeded and auto-corrected the supplied path from
   `/raw/...` to `raw/...`.
4. The message ended as a terminal skip.

### Why this is correct

The raw email is:

- a short escalation ping (`+ankit / thard`)
- asking the named owners to work out a plan
- entirely anchored in already-discussed direction from the previous thread
  messages

This email is substantive in context, but it does not require a new page delta.
That is exactly the job `already_captured` is supposed to do.

### Diagnosis

This is a healthy no-op path and should be treated as the target behavior for
similar follow-up emails.

Clarification: the leading-slash form is correct for filesystem tools like
`read_file("/raw/...")`, but `log_insight.email_path` is a coordinator-facing
message identifier, so the canonical form is `raw/...` without the slash.

### What to preserve

1. Keep `already_captured` as a distinct category from `trivial_skip`.
2. Keep the path auto-correction on `log_insight`; it helped here.
3. Use this trace as the positive reference when tuning the next prompt/tool
   pass.

## Case 3 — Seller CustType capture (writer + reviewer healthy)

### Thread

- `thread_id`: `19bb1ec15f6e5589`

### Resulting page

- `wiki/topics/seller-custtype-realtime-capture-payment-page.md`

### Trace evidence

Representative compile trace:

- `trace_id`: `8f42a68927923c6df1caf10a17c793bf`
- `name`: `compile:z-ai/glm-5:19bb1ec15f6e`
- `run_id`: `56433cbb-aff8-4bbf-a7d0-6a6c77b83159`

Observed sequence:

1. The writer updated the topic page with new current-state information.
2. The writer invoked the reviewer subagent through `task(...)`.
3. The reviewer read the actual page file.
4. The reviewer returned:
   - `verdict: "pass"`
   - no blockers
   - no warnings
5. The message ended `compiled`.

### Why this matters

This trace shows the healthy path that earlier cycles were missing:

- the writer made a real content edit
- the reviewer was actually invoked
- the reviewer had the needed context to evaluate the page
- the system ended in a clean terminal compile

### Diagnosis

The reviewer path is not the current bottleneck. When the writer commits to a
page update, the reviewer can behave correctly.

### What to preserve

1. Keep reviewer invocation on meaningful content edits.
2. Focus future debugging on pre-review decision quality, not reviewer removal
   or major reviewer redesign.

## Cross-case synthesis

Across the three cases, the pattern is:

- **Good compile path**: writer edits page, reviewer passes, terminal compile
- **Good no-op path**: writer recognizes already-covered content, logs
  `already_captured`, terminal skip
- **Bad remaining path**: writer tries to take a no-op path, but terminality is
  fragile and duplicate pages make the decision harder than it should be

That is materially different from the older failure story where every path
degraded into person-page filing and reviewer-blind overwrites.

## Immediate implications

1. The next fixes should target terminality and merge discipline.
2. The reviewer should stay in place.
3. Same-thread dedupe is now more important than another large prompt rewrite.
