# Axis-2 review — PR #262 metrics dipstick, 2026-04-29

## VERDICT
minor-followups

The brief lands cleanly: 10 metrics, reuse of existing helpers, auto-compare
column, best-effort failure, end-of-run wiring. Two spirit gaps and three
small wrong-shape items are worth a follow-up; nothing blocking.

## Per-metric implementation check (M1-M10)

- **M1 owner frontmatter** — spirit honored. `_has_owner_frontmatter`
  (`scripts/post_run_metrics.py:175-182`) handles str / list / blank /
  None correctly; tests cover all branches.
- **M2 lead+number+2 sentences** — partial spirit gap. Brief says "a
  lead paragraph mentioning a number". Implementation
  (`:185-215`) defines "number" as `re.compile(r"\d")` — so `2026-04-15`,
  `Q1`, an order-id, or a phone number all count. Worked example
  "Live on 12% of verified buyers" passes; "rolled out in Q3" also
  passes (likely intended) but so does "Tracked PR #1234" (likely not).
  Minor; tighten to `\d+\s*(%|x|×|M|K|stores|sellers|...)` later if
  noise shows up.
- **M3 active-teaching insights/email** — spirit honored. The four
  categories from the brief (`prompt_ambiguity`, `tool_gap`,
  `structure_suggestion`, `question_for_human`) are exactly the
  `ACTIVE_TEACHING_CATEGORIES` set (`:97-99`). Per-batch denominator
  uses `emails_processed` from `compile_runs`.
- **M4 check_my_work pre-write rate** — spirit honored. Tool sequence
  walked from observations (`:415-458`); flag set when `check_my_work`
  fires before any write tool (`write_file`, `edit_file`,
  `patch_page`, `write_draft_page`). The 4-tool write set is the
  right superset.
- **M5 ## TL;DR H2** — clean.
- **M6 strikethrough** — clean. `~~[^~\n]+~~` correctly excludes
  single tildes.
- **M7 median people-wikilinks** — clean. Counts only `[[people/...]]`
  including aliased form. Median over per-page counts.
- **M8 reviewer pass-on-first-cycle** — wrong-shape, possible spirit
  gap. Brief says "pass-on-first-cycle". Implementation
  (`:496-498`) divides pass-count by traces-with-any-verdict — it has
  no notion of "first cycle". A single trace with multiple
  reviewer calls and a final fail counts as one fail; a trace with
  three pass-then-fail iterations would count as fail too. Whether
  that matches "first cycle" depends on whether one trace == one
  cycle in the langfuse model; not obvious from the code. Worth a
  comment or rename to "reviewer pass rate (per trace)".
- **M9 prompt tokens** — clean. Sums `usage.input` /
  `usage.promptTokens` over GENERATION observations, averaged per
  trace.
- **M10 archetype distribution** — wrong-shape (small). Detection
  order is **directory > tags > body H2 keyword > "other"**
  (`:233-258`). For decisions/policies/systems the directory wins
  (correct: those are sufficient evidence). For topics/ pages the
  signal is tag synonyms or an `## H2` whose first word is `Launch /
  Bug / etc`. Since the prompt revamp doesn't yet emit those tags or
  H2s consistently, the baseline showed 62.8% "other" — which is the
  honest result, but the brief frames M10 as a behavior to *track*,
  not a behavior to enforce; that's fine.

## Reusability check

- `nightly_trace_audit._list_recent_traces` / `_fetch_trace`: **yes**,
  imported directly (`scripts/post_run_metrics.py:60-61`). The
  `langfuse-cli@0.0.8` pin, retry/backoff, and timeout policy stay in
  one place. `REVIEWER_VERDICT_PAT` is reused from
  `src.observability.trace_signals` (`:417`).
- `extract_frontmatter` / `extract_body`: **yes**, imported from
  `src.utils` (`:63-64`).
- New duplication of existing logic: **none material.** The local
  `_trace_signals` helper extracts tool-sequence + reviewer-verdict +
  token-usage in a slightly different shape than
  `nightly_trace_audit._extract_tool_seq` + `TierASignals`, but the
  fields differ (M4 needs the pre-write flag) so a small redundancy
  is justified.

## Auto-comparison check

- Glob: `_find_prior_report` (`:642-652`) takes the most-recent
  `post-run-metrics-*.md` excluding the file currently being written.
  Correct.
- Parse: `_parse_prior_metrics` (`:655-675`) regex-matches `| Mn |
  ... | <number>%? |` — only the first three columns. Verified by
  hand: `% in match.group(0)` correctly captures whether the value
  cell ended in `%` (not the target cell). M-rows with `-` value are
  skipped. Good.
- Δ column: `_delta_str` (`:678-685`) renders `+10.0pp` for pct,
  `+0.50` for raw. `is_pct` driven by current metric's unit, not by
  prior — so a unit change between reports would render a misleading
  delta. Low risk; flag if metric units ever change.

## Failure-mode check

- **langfuse unreachable**: `_list_recent_traces` returns `[]`,
  `compute_langfuse_metrics` sets `warning = "langfuse list empty /
  unreachable"` and three `None` rates (`:474-476`). Report still
  emits; M4/M8/M9 show `-`. Confirmed by the baseline report's
  `--skip-langfuse` output.
- **DB unreachable**: M3 catches `psycopg.Error` (`:384`) and
  returns `(None, 0, "db query failed: ...")`. Warning surfaces in
  the partial-report banner. Good.
- **wiki/ empty**: `_new_pages_since` returns `[]`,
  `compute_filesystem_metrics([])` returns the `None`-filled dict
  (`:264-272`). Report renders `-` for FS metrics. Test
  `test_compute_filesystem_metrics_handles_empty_set` covers it.
- **Partial-report emission**: confirmed — warnings block at
  `render_markdown` (`:702-706`) lists each collection failure. Never
  blocks the compile loop (`emit_for_run` swallows all exceptions
  `:900-902`).

## Wire-into-compile_all check

- Triggered only on completed runs: **yes** —
  `scripts/compile_all.py:2514` `if run_status == "completed":` gates
  the call. `failed`/`killed` runs skip it.
- Doesn't block compile: **yes** — wrapped in `try/except
  ImportError`; `emit_for_run` itself swallows everything else.
- Standalone CLI: **yes** — `--run-id`, `--since`, `--skip-langfuse`,
  `--no-compare`, `--output` all wired. PR description's three quick
  invocations all map to flag combinations.
- One small wart: the wiring catches only `ImportError`
  (`compile_all.py:2529`). If `emit_for_run` itself raises despite
  its broad catch (e.g. a `SystemExit` from click in some odd
  reentrant case), it would bubble. The library entry-point's bare
  `except Exception` is the real safety net so this is mostly fine.

## Test coverage gaps (if any)

- 32 tests claimed. Counted ~30 in the file — close enough; the
  parametrized M1 test counts as 9 cases. Coverage is honest:
  filesystem metrics + rendering + glob.
- **Gap 1**: no test asserts that `_new_pages_since` filters by
  mtime / ctime — only past+future cutoffs. The "M1's denominator
  is NEW pages, not all pages" guarantee is validated by file-tree
  selection, not by an explicit test of the filter rejecting an
  older mtime page (the comment at `tests/test_post_run_metrics.py:222-225`
  acknowledges this; macOS `os.utime` limits force the omission).
  Acceptable.
- **Gap 2**: no test exercises the langfuse-unreachable path → the
  baseline report covers it manually, but a unit test mocking
  `_list_recent_traces` to `[]` would catch a future regression in
  one second.
- **Gap 3**: no test for `_parse_prior_metrics` — the regex is
  load-bearing for the Δ column. A 6-line test with a fixture string
  would be cheap insurance.
- **Gap 4**: no test for archetype-detection edge cases beyond the
  four basic paths — e.g. a topic with `tags: [decision, launch]`
  (currently returns "decision" by ARCHETYPE_TAG_MAP order; intended
  but unverified). Worth one parametrize case.

## Recommended actions before merge

1. (Optional, M2) Tighten the number regex once we see a baseline of
   false positives in production. Ship as-is; iterate.
2. (Optional, M8) Rename label from "reviewer pass-on-first-cycle"
   to "reviewer pass rate (per trace)" OR add a code comment
   explaining the trace = cycle assumption — the brief's wording
   suggests a stricter semantics.
3. (Cheap) Add the 3 small unit tests called out above
   (`_parse_prior_metrics`, langfuse-empty, archetype tag-precedence).
4. None of the above are blocking — recommend merge as-is and stack
   the follow-ups onto the next PR (JSONL history + dashboard).

Report saved to /Users/amtagrwl/git/email-knowledge-base/docs/audits/intent-review-pr262-2026-04-29.md
