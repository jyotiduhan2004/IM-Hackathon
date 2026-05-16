---
audit_date: 2026-04-17
window: 2026-04-15 all-day UTC (pre-merge window for PRs #120, #132, #122, f44dcf0)
scope: observation-level ERROR events on compile traces
project: cmnwwg54000010707pnp3tvrz (pk-lf-cdb085c0)
---

# Error-class autopsy — 2026-04-15 compile traces

Pulled via `langfuse-cli api observations` + trace GET. Three distinct
error classes, all since fixed on main.

## Class 1 — worktree view-root mismatch (2 traces)

**Symptom**: `compile_run_id=da066d1f-0a54-4853-9aef-90e8f42f7430`,
2026-04-15 20:32 + 20:33 IST, both ~🚨 3, latencies 6.2s & 27.1s.

**Traces**: `8aab72c69ee6566aee8a130f2ca9425f` (grok),
`b3f03786d23f51960ce1ee12939ff417` (minimax).

**Full error string**:

```
Path:/Users/amtagrwl/git/email-knowledge-base/raw/2026-01-12_technical-mplaunchiminformational-announcing-the-i_8bc95911.md
outside root directory: /Users/amtagrwl/git/email-knowledge-base/.claude/worktrees/pr78-verify
```

**Root cause**: compile was launched from worktree `pr78-verify`, whose
`raw/` either didn't exist or wasn't symlinked into the main repo's
`raw/`. `find_new_sources` read raw_paths from Postgres `messages`
(shared state), but `FilesystemBackend(view_root=worktree)` rejected
the absolute path because it pointed outside the chroot.

**Fix**: F3 preflight guards (PR #132, task #51). Coordinator now
checks at startup:
1. `_preflight_raw_paths_exist` — fails fast if DB points at raw files
   the worktree can't see.
2. `_preflight_view_resolves_paths` — confirms view_root chroot
   resolves paths the agent will try to read.
3. `Preflight OK / raw_dir=… md_count=N` echoed to operator.

The silent-failure mode that this class represented is now a hard
startup error.

## Class 2 — LiteLLM instant-failures (6 traces)

**Symptom**: 6 traces on 2026-04-15, each 🚨 3, ℹ️ 1, latency 0.16–0.30s,
zero tokens.

**Traces + timestamps**:

| UTC | IST | id | shape |
|---|---|---|---|
| 12:16:49 | 17:46:49 | aa7d878f66897460ac44b94b65ca2d9b | 401 team-not-allowed |
| 12:16:50 | 17:46:50 | 861d805035f650e2928f29c3cefb1340 | 401 team-not-allowed |
| 12:33:16 | 18:03:16 | 43a760f01ff977e28cba61802af58dfe | 401 team-not-allowed |
| 12:44:54 | 18:14:54 | 11d65980673a7dfc722a3120191d8480 | 401 team-not-allowed |
| 12:44:55 | 18:14:55 | 09f74365cd949d13a74d3975f164b9a2 | **400 Invalid model name** (`z-ai/glm-5.1`) |
| 13:08:43 | 18:38:43 | 03869a557e0dacdc7dc888499c85aaf9 | 401 team-not-allowed |
| 14:02:56 | 19:32:56 | 096de2f6be44ab10d3013d8ebc35bc77 | 401 team-not-allowed |

**401 shape**:

```
Error code: 401 - {'error': {'message': "team not allowed to access
model. This team can only access models=['openai/gpt-4o',
'openai/gpt-4.1', … 'google/gemini-2.5-flash', …]"}}
```

**400 shape**:

```
Error code: 400 - {'error': {'message': "{'error':
'/chat/completions: Invalid model name passed in model=z-ai/glm-5.1.
Call `/v1/models` to view available models for your key.'}"}}
```

**Root cause**: `src/config.py` default `LLM_MODEL_POOL` at that time
included `z-ai/glm-5.1` (not on the LiteLLM team allowlist) and a
mis-labelled model variant. The `_is_model_unavailable_error` retry
gate caught the "team not allowed" phrasing but the compile_all loop
never retried here because the batch worker exited on the raised
exception.

**Fix**:
- `f44dcf0 fix(compile): drop z-ai/glm-5.1 from pool — LiteLLM returns
  400 Invalid model name` — killed the bad variant outright.
- PR #146 (Bug K, in-flight) — broadens `_is_model_unavailable_error`
  to catch bare `Error code: 401` / `Error code: 403` shapes, so
  _future_ team-allowlist violations retry cleanly instead of failing
  the batch.
- Today (2026-04-17) there's still 1 residual trace hitting the bare
  401 shape on `z-ai/glm-5`, which is exactly Bug K's failure mode.
  PR #146 is the fix.

## Class 3 — `find_new_sources` psycopg3 placeholder (1 trace)

**Trace**: `1f6edbf2bf09ce2f3108dac6a8e8cd04`, 2026-04-15 13:00:03 UTC
(18:30:03 IST), thread `19b9dc5eba08a10d`, latency 41.1s, 573 output
tokens.

**Full error string**:

```
only '%s', '%b', '%t' are allowed as placeholders, got '%''
```

**Tool call that triggered it**:

```json
{
  "name": "find_new_sources",
  "args": {
    "subject_contains": "Implemented Email Domain Alias Correction",
    "date_from": "2026-01-01",
    "date_to": "2026-01-08"
  }
}
```

**Root cause**: SQL built `subject ILIKE '%' || %s || '%'` and
psycopg3's strict query parser saw `%'` as an unknown placeholder
format ("got `%'`"), raising `ProgrammingError` before the bound
parameter was sent. 573 output tokens burned while the agent looped
trying to recover.

**Fix**: PR #120 (2026-04-16 20:18 IST). Wildcards are now wrapped in
Python (`f"%{subject_contains}%"`) and bound as a single `%s`
parameter:

```python
# src/db/messages.py:126-128
if subject_contains is not None:
    conditions.append("subject ILIKE %s")
    params.append(f"%{subject_contains}%")
```

No more `'%' || … || '%'` in the SQL string → parser happy.

## Summary

| class | traces | fix | status |
|---|---:|---|---|
| worktree view-root mismatch | 2 | PR #132 preflight | merged |
| LiteLLM 401 team-allowlist + 400 bad model | 6 | `f44dcf0` pool-drop + PR #146 retry-gate broadening | PR #146 in-flight |
| psycopg3 `%'` placeholder | 1 | PR #120 bound-param wrap | merged |

**Residual today (2026-04-17)**: 6 ERROR observations on 1 trace, all
`Error code: 401 - Authentication Error` on `z-ai/glm-5`. Exactly the
Bug K pattern PR #146 fixes. Once #146 lands the coordinator will
retry instead of failing.

**Follow-ups queued**: none — each class has a fix either merged or
pending. PR #146's broadening matcher also catches Class-2 residuals.
