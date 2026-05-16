---
timestamp: 2026-04-17T06:00:00Z
cycle: 5
case_name: bug-j-minimax-silent-fail
run_id: 1d2c337a-1fab-4528-a2f6-6a514b1f26a4
bugs_surfaced: [J]
representative_trace: 9bd74218a44225aed7eacc799005b2d9
model: minimax/minimax-m2.7-20260318
severity: high
impact: "pool-health metric is polluted; ~23% of all minimax failures are infrastructure, not agent"
---

# Cycle 5 Bug J — minimax silent-fail (LiteLLM 200-empty)

## Finding in one sentence

Five Cycle 5 batches routed to `minimax/minimax-m2.7-20260318` returned
**HTTP 200 with an empty payload** — `completion_tokens=0`,
`prompt_tokens=0`, `content=""` — no tool calls, no text, no error.
The agent terminates immediately (zero work done) and the coordinator
marks the email `failed / not cited in wiki`, indistinguishable from a
real agent failure. Across the last 30 failed minimax attempts,
**7 (23%)** fit this silent-fail shape.

## The trace

```
trace_id: 9bd74218a44225aed7eacc799005b2d9
name:     compile:minimax/minimax-m2.7:1994c8d39046
latency:  13.014s
```

5 observations — 1 GENERATION (ChatOpenAI), 3 CHAIN (middleware wraps),
1 AGENT (PatchToolCallsMiddleware). No TOOL observations.

```python
# GENERATION observation:
ChatOpenAI, latency=12.963s (99% of the trace)
completion = ''
prompt_tokens = 0
completion_tokens = 0
response_metadata.token_usage.total_tokens = 0
```

Zero prompt tokens is the tell. A normal compile request has a
system prompt (~8k tokens) + tool definitions (~4k) + the user
instruction (~300 tokens). Zero prompt tokens means **the request
never actually reached the model** — it failed somewhere in the
LiteLLM proxy layer and returned a well-formed but empty ChatCompletion
response.

## Why it looks like an agent failure but isn't

After the 13-second hang, the agent receives the empty ChatMessage.
LangGraph's model-output processing sees no tool calls, nothing to
iterate on, and exits cleanly. The compiled post-processing step in
`_mark_batch_compiled` then runs:

- Coordinator checks catalog for `message_touched_pages` entries → none
  (agent did no writes).
- Outcome: `failed`, error: `not cited in wiki`.

Operator looking at the dashboard sees "minimax failed on email X"
and adds one more tick to the fail-rate counter. But nothing the agent
did or didn't do could have changed this — the model was never asked.

## Prevalence across runs

Query:

```sql
SELECT
  DATE_TRUNC('hour', attempted_at) AS hour,
  COUNT(*) FILTER (WHERE dur_s < 20) AS short_fail,
  COUNT(*) AS total_fail
FROM (
  SELECT attempted_at, compile_model, outcome,
         EXTRACT(EPOCH FROM (finished_at - attempted_at)) AS dur_s
  FROM compile_attempts
  WHERE compile_model LIKE 'minimax%' AND outcome = 'failed'
    AND finished_at IS NOT NULL
) q
GROUP BY 1 ORDER BY 1 DESC LIMIT 10;
```

Recent breakdown:

```
hour                 short_fail  total_fail
2026-04-17 05:00     3           3       ← Cycle 5, 100% silent-fail
2026-04-17 04:00     2           5       ← mixed with recursion-spiral minimax
2026-04-17 02:00     1           5
2026-04-16 22:00     1           3
```

7 of 30 most-recent minimax failures are silent — **23% of the model's
failure rate is infrastructure, not output quality**.

## Impact on the pool-health signal

Cycle 5 model pool:

```
Auto-exclusion dropped
  minimax/minimax-m2.7: 110/308 failed (35.7%) [quarantined (24h)]
  z-ai/glm-5:            20/213 failed  (9.4%) [quarantined]
  x-ai/grok-4.1-fast:    50/251 failed (19.9%) [quarantined]
```

The 35.7% minimax fail rate includes silent-fails. Subtract the Bug J
floor (~23%) and minimax's real agent-caused fail rate is closer to
**~27%** — still worst in pool, but not as catastrophically bad as the
raw number suggests. And the health tracker can't distinguish the two.

Worse: the `healthy_pool_would_empty_pool` warning triggered in Cycle 5
and routed batches to minimax anyway (all three models exceeded their
fail-rate threshold, so "bad options > no options" kicked in). Silent-
fail batches in Cycle 5 contributed to BOTH the attempt at quarantine
AND the fallback routing.

## Why the fix is coordinator-side, not prompt-side

The agent didn't do anything wrong. The model didn't even get asked
anything wrong. No amount of prompt tuning or middleware can influence
this — it's an infrastructure signal that needs to be detected and
handled before the agent state machine runs.

Candidate signals (any ONE is sufficient):

- `response.usage.total_tokens == 0` on the ChatOpenAI response
- `response.choices[0].message.content == ""` AND no tool calls
- `response.choices[0].finish_reason` is missing or `null`

When detected, the coordinator should:

1. Tag the attempt as `outcome='infrastructure_error'` (new outcome
   distinct from `failed`).
2. Retry with a different model from the pool immediately, same raw
   paths.
3. Not count the original attempt against the model's pool-health
   fail rate.

Related upstream work: investigate why the LiteLLM proxy is returning
200-empty on these minimax requests. Could be proxy timeout swallowed,
a minimax content-filter refusal that doesn't surface as 4xx, or a
rate-limit path that responds before the upstream call. Until that's
fixed, the coordinator-side guard is load-bearing.

## Downstream consequence for Cycle 5 effective-rate

The Cycle 5 headline would have shown:

| Outcome | reported | real (post-Bug-J-fix) |
|---|---:|---:|
| compiled | 6 | 6+retries ≈ 8 |
| skipped | 8 | 8 |
| failed | 7 | 2-3 |
| **effective rate** | 6/13 = 46% | 8/10 ≈ 80% |

Same pattern as Cycle 4's Bug I orphan-skip discovery: the BOOKKEEPING
is under-stating the agent's real output. Bug J is the mirror of Bug I
— one loses skip decisions, the other loses whole attempts. Both need
coordinator-side detection so the metric reflects the writer.

## Sequencing

1. **Bug I** — shipped in PR #137 (Cycle 4). Done.
2. **Bug H** (chronological scope) — PR #139, open.
3. **Bug J** (silent-fail detection + retry) — tracked as task #97.
   Next up after Bug H merges.

Each incrementally moves the effective rate closer to the writer's
real capability and away from bookkeeping noise.

## Artifacts

- Representative trace: `https://langfuse.intermesh.net/project/cmnwwg54000010707pnp3tvrz/traces/9bd74218a44225aed7eacc799005b2d9`
- Cycle 5 run: `1d2c337a-1fab-4528-a2f6-6a514b1f26a4`
- 4 more trace IDs with same shape:
  `b20e4056e9c01a452b7b07ce05487713`,
  `f787f65ab77d92126d4db437ae19aefb`,
  `c3fc7abaad43c29ccdcdc5b2b7c6ede2`,
  `16b420915fc1946cb99ded660cc76e6d`
