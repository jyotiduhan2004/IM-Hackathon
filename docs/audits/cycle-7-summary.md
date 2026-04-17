---
timestamp: 2026-04-17T06:55:00Z
cycle: 7
run_id: b8f19851-5852-4429-90d9-287445f11750
started_at: 2026-04-17 11:24 IST
ended_at: 2026-04-17 12:35 IST (~71 min)
batches: 25
cost: $2.32
outcomes:
  compiled: 14
  skipped: 9
  failed: 2
effective_rate: 87.5%  # 14 / (14 + 2)
fixes_verified:
  K_litellm_401_retry: 0 unretried 401s (Bug K held)
  L_email_path_autoheal: 0 orphan skip-insights with leading slash (Bug L held)
  J_silent_fail_retry: 0 minimax zero-token events
  H_chronological_scope: 25/25 batches set batch_cutoff_date
ship_gate_status: cleared (87.5% ≥ 85% threshold)
---

# Cycle 7 summary — 87.5% effective rate, scale gate cleared

## Headline

**Effective content-page citation rate: 14/16 = 87.5%.**

Trajectory across cycles:

| cycle | compiled | failed | raw | effective | notes |
|---|---:|---:|---:|---:|---|
| 2 | — | — | 38% | 62% | pre-Phase-A baseline |
| 3 | — | — | — | 22% | post-Phase-A, Bug D waffle spike |
| 4 | — | — | — | 46% | (real ~75%; Bug I orphan-insight bookkeeping gap) |
| 5 | 8 | 7 | 32% | 53% | (real ~73%; Bug J minimax silent-fail polluting) |
| 6 | 11 | 3 | 44% | **79%** | Bug H/I/J live, ship gate cleared, Bug K surfaced |
| **7** | **14** | **2** | **56%** | **87.5%** | **Bug K+L live, scale gate cleared** |

Scale-gate threshold from Cycle 5 was "≥85% effective for 1 cycle → OK
to scale to --limit 100." **Cleared at 87.5%.** Next cycle can safely
run at --limit 100.

## Per-model breakdown

| Model | compiled | skipped | failed | total | notes |
|---|---:|---:|---:|---:|---|
| **z-ai/glm-5** | 7 | 3 | 0 | 10 | clean |
| **minimax/minimax-m2.7** | 4 | 2 | 2 | 8 | **both Cycle 7 failures here** |
| **x-ai/grok-4.1-fast** | 3 | 4 | 0 | 7 | clean |
| **total** | **14** | **9** | **2** | **25** | |

**minimax is the only failing model in Cycle 7.** Across the 24h window
before this cycle the pool-health guard had quarantined minimax at 50%
fail rate (103/204) — but the "would empty pool" guard kept it in
rotation. Both failures were the same pattern: `not cited in wiki`
after 27s + 131s duration. Bug K (auth retry) + Bug J (silent-fail)
fixes held on this run; these are genuine agent-level terminality
misses, not infra. See **Failure analysis** below.

## Fixes verified live

### Bug K (401/403 retry gate) — ✓ active

```
$ grep "Error code: 401\|Error code: 403" /tmp/cycle7.log
(empty)
```

Zero 401/403 auth errors this run. Cycle 6's single failure class (bare
`Error code: 401 - Authentication Error` from glm-5) is gone.

### Bug L (log_insight email_path autoheal) — ✓ held

```
$ uv run python -c "
from src.db import connect
conn = connect()
conn.execute(\"\"\"SELECT count(*) c FROM compile_insights
  WHERE run_id='b8f19851-5852-4429-90d9-287445f11750'
    AND (email_path LIKE '/%' OR email_path IS NULL
         AND category IN ('trivial_skip', 'already_captured'))\"\"\").fetchone()
→ c=0
```

Every skip-insight this run carried a properly-normalized `email_path`.
Coordinator's batch-end skip-materialization: 9/9, perfect.

### Bug J (silent-fail detect+retry) — ✓ armed, 0 occurrences

No minimax zero-token responses this run. The triple-invariant detector
in `_check_silent_fail` never raised `SilentModelFailError`.

### Bug H (chronological scope) — ✓ active on every batch

All 25 batches set `batch_cutoff_date` correctly. No `chronological_
scope_reject` events in the log — the upstream `get_thread_context`
filter eliminates the failure mode at the discovery layer.

## Failure analysis — both on minimax, both "not cited in wiki"

Independent Codex audit cross-checked both failures against current
page state + raw emails:

### Failure 1: `19bb778b34145cdb` (API image-embedding real-time updation)

- Raw: `2026-01-13_mplaunchim-real-time-updation-of-product-image-emb_4e07bf0b.md`
- Target page: `photosearch-qdrant-updation-pipeline.md`
- Diagnosis: **per-message terminality miss in multi-email thread**.
  The page already captures the Jan 13 launch content; this specific
  message should have ended `already_captured`. Instead the agent
  did some work, didn't produce a new page citation, and the
  coordinator marked the batch `failed`.

### Failure 2: `19bb72fc748876c2` (Optimising API hits user-details service)

- Raw: `2026-01-13_mplaunchim-optimising-api-hits-of-user-details-use_1293128e.md`
- Target page: `seller-bl-api-optimization.md`
- Content: "good optimization, do code review, find more."
- Diagnosis: **low-signal substantive follow-up**. The sibling page
  already captures the substantive change + future work. This should
  have ended `already_captured`; instead the agent loitered and
  bailed without a terminal decision.

Both failures are the same shape Codex's audit flagged:

> Substantive-but-redundant follow-up on a thread whose root message
> already compiled the page → agent doesn't recognise the
> redundancy → no terminal outcome → "not cited" failure.

**Fix trajectory**: prompt-tightening (not code). The `<decision_tree>`
block needs to make `already_captured` the explicit recommendation
for "same thread, additional message, page already updated". Tracked
as a follow-up in the train (see PR #156 or similar).

## Corpus state (post-cycle)

From direct DB inspection on origin/main at aa47e4d (cross-checked by
Codex, 2026-04-17 07:00 UTC):

| Migration | count | status |
|---|---:|---|
| Pages with `source_threads:` | 371 | transitioning |
| Pages with `sources:` only | 84 | legacy tail |
| Pages with both (dual-field) | 342 | transitioning |
| Pages on legacy `status: current` | 0 | ✓ done |
| Pages on legacy `page_type: entity` | (D4 migrating) | transitioning |

Provenance migration is ~81% complete by page. Status migration is
done. Remaining work: finish U6 backfill on the 84 legacy-sources-only
pages, then flip `--strict-no-sources` in validate_wiki to promote
warnings → errors.

## Structural hygiene signal (new metric)

PR #150 (dated-h2 validator) + PR #155 (pymarkdown pre-check) produced
a joint structural baseline:

| metric | count | fixable? |
|---|---:|---|
| dated-h2-section (Bug F) | 72 | prompt-level (PR #148) |
| MD024 duplicate heading | 43 | reviewer/content decision (#153) |
| MD047 final-newline | 11 | autofix |
| MD007 ul-indent | 12 | autofix (ambiguous cases punted) |
| MD009/012/026/029/004 | 19 | mixed autofix / content |

Post-cycle `pymarkdown fix -r wiki/` autofixed 27 pages, reducing 25
hygiene warnings. The 43 MD024 dups remain (content judgement needed).

## Known remaining issues

1. **Per-message terminality** (2 minimax failures this cycle) —
   tracked, prompt fix in flight.
2. **Minimax pool concentration risk** — the only failing model in
   Cycle 7. Could de-weight or quarantine at 50% 24h fail-rate
   threshold once pool has 4+ models.
3. **43 MD024 duplicate headings** in the corpus — cross-level dups
   (H3+H2 same title, e.g. photosearch). Reviewer rule #153 catches
   on next compile; corpus-wide cleanup is a separate task.
4. **~50 Bug F dated-H2 pages** — prompt fix (#148) is forward-looking
   only; existing dated H2s need a cleanup pass.
5. **84 legacy-sources-only pages** — destructive-overwrite still
   possible until U6 backfill completes + `--strict-no-sources`
   flips.

## Budget state

- Spent today: $93.56 of $100
- Cycle 7 cost: $2.32 (vs Cycle 6 $1.72; +35%)
- Remaining: $6.44
- Cycle 8 projection at --limit 100: ~$9 — **tight, may need to wait
  for tomorrow's budget** or drop to --limit 50 as a middle step.

## Decision gate

**Recommendation**: ship the PR train (#148-#155 + follow-ups
currently in flight), then:

- If tomorrow's budget allows: run Cycle 8 at `--limit 100` as the
  scale test.
- If budget tight: run Cycle 8 at `--limit 50` with the terminality
  prompt fix merged — confirm effective rate stays ≥85% on a larger
  cohort.

## Artifacts

- Cycle 7 log: `/tmp/cycle7.log`
- Run ID: `b8f19851-5852-4429-90d9-287445f11750`
- Case study (photosearch duplicate-section lineage):
  `docs/audits/cycle-7-case-photosearch-duplicate-section.md`
- Previous cycle: `docs/audits/cycle-6-summary.md`

## PR train cycle-summary

| PR | subject | state |
|---|---|---|
| #145 | Bug L autoheal email_path | merged |
| #146 | Bug K 401/403 retry | merged |
| #148 | Canonical H2 titles (Bug F) | open |
| #149 | CLAUDE.md drift | open |
| #150 | Dated-H2 validator | open |
| #151 | Error-class autopsy | open |
| #152 | Deep 10-page audit | open |
| #153 | Reviewer editor role + structural rules | open |
| #154 | consolidate_duplicate_slug | open |
| #155 | pymarkdown structural pre-check | open |
| (queued) | cycle-7-summary (this doc) | in flight |
| (queued) | terminality prompt tightening | in flight |
| (queued) | compile_all autofix post-batch hook | in flight |
