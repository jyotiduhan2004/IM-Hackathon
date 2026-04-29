# Axis-2 review — PR #277 longitudinal dashboard, 2026-04-29

## VERDICT

**minor-followups** — three layers all land with the right shape and the killer cohort view is implemented as specified. Two real divergences from the brief (no `prompt_commit_sha` length normalization; helper duplication of `src/observability/langfuse_scores.py`) plus one outlier-population edge case worth catching before this becomes load-bearing for prompt-iteration decisions. Nothing blocks merge.

## Per-layer implementation check

**Layer 1 — JSONL history.** Spirit honored. `append_to_history` (`scripts/post_run_metrics.py:838-871`) writes one row per run with run_id / timestamp / prompt_commit_sha / model / pages_total / pages_compiled_this_run / metrics / archetype_dist. Append-only mode (`open("a", ...)`). Round-trip test passes (`tests/test_metrics_dashboard.py:79`). One **wrong-shape**: brief specified "short 7-char SHA"; `_prompt_commit_sha()` (`scripts/post_run_metrics.py:773`) uses `git log --format=%h`, which returns Git's auto-determined unique short SHA — typically 7 chars but not guaranteed (Git extends to 8-12 when collisions exist). Cohort grouping still works because the dashboard groups by exact-string match, but `git log --format=%h --abbrev=7` would honor the brief literally. Minor scope creep: row also includes `new_pages_window` and `archetype_dist` (not in the brief). Both are non-harmful and useful — I'd leave them.

**Layer 2 — Dashboard renderer.** Spirit honored. `scripts/render_metrics_dashboard.py:329-368` produces all 4 brief-required sections (latest, trend, cohort, outliers) plus the empty-history stub. Standalone CLI works (`--history` / `--output` flags, line 369-385). Auto-runs from `post_run_metrics.py:1108` and `:1170`. One real bug-class issue (Codex territory but worth flagging for spirit): the cohort table's `_render_cohort_table` (line 287-321) groups by SHA correctly but **tie-breaking on cohort newness uses `max(timestamp)` per cohort** — if two cohorts share the same latest run timestamp (rare but possible in test fixtures or back-to-back runs), Python's `sorted` is stable so the order is implementation-defined relative to dict insertion. Not flagged in tests.

**Layer 3 — Langfuse scores.** Spirit mostly honored, but **one wrong-shape**. `push_langfuse_scores` (`scripts/post_run_metrics.py:912-981`) instantiates `langfuse.Langfuse` directly with a `compile-metrics-YYYY-MM-DD` session. The brief says "reuse existing tooling where possible". `src/observability/langfuse_scores.py` already has `_build_client()` (line 580-608) doing exactly this, and `_safe_flush()` for safe shutdown. PR #277 duplicates both inline. The duplication is light (~25 lines) but it diverges from the constraint. The right move is to import `_build_client` and `_push_score` from the existing module (semantics differ — daily-aggregate session vs per-trace — but the client init + safe-flush are pure duplication).

## Killer view (per-prompt-version cohort table) check

**Implemented as specified.** `_render_cohort_table` (line 287-321) groups by `prompt_commit_sha`, computes median + mean + n per metric per cohort, renders top-5 cohorts by latest-timestamp-newest-first. Pre-PR-260 vs post-PR-260 will be readable as adjacent rows once both populations exist. The `median (mean, n)` format is dense but defensible. End-to-end smoke test (`tests/test_metrics_dashboard.py:319-358`) verifies cohort rendering with two SHAs.

## Direction-of-arrow check (per metric)

`METRIC_DIRECTION` (`scripts/render_metrics_dashboard.py:46-56`) — checked each against the metric definitions in `scripts/post_run_metrics.py:8-22`:

- M1 owner% target ≥80 → `"up"` ✓
- M2 lead-with-number target ≥70 → `"up"` ✓
- M3 active-teaching insights ≥1 → `"up"` ✓
- M4 cmw pre-write target 0% → `"down"` ✓ (the inversion the brief called out)
- M5 TL;DR target 0% → `"down"` ✓
- M6 strikethrough target 0% → `"down"` ✓
- M7 people-wikilinks median ≤2-3 → `"down"` ✓
- M8 reviewer pass-first ≥60 → `"up"` ✓
- M9 prompt tokens (lower=cheaper) → `"down"` ✓
- M10 distribution → excluded ✓

All 9 directions correct. Tests at `tests/test_metrics_dashboard.py:259-282` cover both up-better (M1) and down-better (M5) plus tolerance + None cases.

## Failure-mode check

- **langfuse-cli unreachable**: covered. `push_langfuse_scores` wraps `Langfuse()` init in try/except (`scripts/post_run_metrics.py:947`), per-score push in try/except (line 967), final flush in try/except (line 977). Tests `test_push_langfuse_scores_skips_when_disabled` / `_no_credentials` / `_per_metric_failure_does_not_break_others` (line 200-251).
- **JSONL write failure**: covered. `OSError` caught at the call sites in `main` (line 1109) and `emit_for_run` (line 1146) with warning + continue. Subsequent dashboard render still runs. **Gap**: only `OSError` is caught — `JSONEncodeError` (e.g. an exotic value in `archetype_dist`) would propagate. Worth widening to `(OSError, TypeError, ValueError)`.
- **Empty history**: covered. `render_dashboard` writes a stub when `read_history` returns `[]` (line 339-348). Test at line 290.
- **Non-UUID run_id**: covered. `emit_for_run` rejects non-UUID at line 1135-1141 (returns `None`). No test for the guard itself, but the unit-test denial-of-pollution path is implicitly checked since the audits dir won't be touched.

## Reusability check

- `nightly_trace_audit._list_recent_traces` / `_fetch_trace`: not needed for this PR (it consumes report objects, not traces directly). Already-correct from #262.
- `src.utils.extract_frontmatter` / `extract_body`: not used (this PR doesn't read pages — `_wiki_pages_total` only counts `.md` files). Acceptable.
- **`src/observability/langfuse_scores.py`**: **partially duplicated**. PR re-implements `_build_client()` semantics inline (`scripts/post_run_metrics.py:945-953`) and `_safe_flush()` semantics inline (line 977-980). `_push_score`'s wrapping pattern is also re-rolled (line 967-974). Recommend refactoring `_build_client()` and `_safe_flush()` to public helpers in `langfuse_scores.py` and importing them — keeps the single-source-of-truth invariant the file's docstring asserts.

## Test coverage gaps

22 new tests, all useful. Real gaps:

1. **Outlier population**: `_outliers` (line 217-244) computes `median(history)` and `pstdev(history)` excluding the latest row — that's the brief-correct "exclude latest from population" behavior. Verified. But the `pstdev(values)` with `len(values)==3` and tightly-clustered values can produce stdev ≈ 0; the `if sd == 0: continue` guard at line 235 catches exact-zero but not "near-zero", so a synthetic 7-row history with values `[0.50, 0.50, 0.50, 0.50, 0.50, 0.51, 0.99]` would flag outliers correctly but with absurdly large σ-multiples. Probably fine in production but worth a note.
2. **No test for `_resolve_run_meta` DB error path** — covered indirectly by the round-trip test mocking it, but the actual psycopg.Error branch (line 803) is untested.
3. **`_prompt_commit_sha` timeout / non-zero exit** — has a test for missing file but not the OS-level `subprocess.TimeoutExpired` branch.

## PR #262 minor follow-ups carryover

The 3 carryover items from PR #262's Axis-2 are **all still open**:

- **M2 regex `\d` too loose**: `NUMBER_PAT = re.compile(r"\d")` at `scripts/post_run_metrics.py:80` — unchanged.
- **M8 cycle-vs-trace semantics**: `_extract_metric_values` at `src/observability/langfuse_scores.py:159` and `compute_langfuse_metrics` at `scripts/post_run_metrics.py:462` — unchanged.
- **Test gaps for `_parse_prior_metrics` / langfuse-unreachable / archetype tag-precedence**: still open. PR #277 didn't touch the dipstick's existing test surface.

This PR isn't the place to fix them, but they should not get lost.

## Recommended actions before merge

1. Refactor `_build_client()` + `_safe_flush()` from `src/observability/langfuse_scores.py` into shared public helpers (or just import the private ones with a comment) so `push_langfuse_scores` stops carrying its own copy. ~15 minutes.
2. Pin `_prompt_commit_sha` to `--abbrev=7` to match the brief literally. ~1 minute.
3. Widen the JSONL-append exception clause from `OSError` to `(OSError, TypeError, ValueError)` so an exotic archetype label doesn't silently skip the row. ~1 minute.
4. (Optional) File a follow-up issue for the 3 PR #262 carryover items so they don't drift further.

Report saved to /Users/amtagrwl/git/email-knowledge-base/docs/audits/intent-review-pr277-2026-04-29.md
