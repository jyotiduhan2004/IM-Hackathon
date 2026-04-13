# Auto-Repair Plan — 2026-04-13T08:40Z

Catalog of recurring failure modes in `email-knowledge-base` with automated-fix
proposals. Ranked by frequency × blast-radius. Counts drawn from `CHANGELOG.md`,
`docs/BACKLOG.md`, `docs/runs/*.md` (6 dipsticks), `docs/reviews/*.md` (8 reports).

Existing automation: `scripts/validate_wiki.py` (blocking), `scripts/lint_wiki.py
--fix` (advisory/auto-fix), `scripts/merge_suffix_dupes.py`,
`scripts/backfill_stubs.py`, `compile_all.py` snapshot + post-validator.

Legend: (V) validator check · (L) lint auto-fix · (H) post-compile hook · (M)
merge script · (P) prompt rule · (T) timeout wrapper.

---

## Ranked failure modes

### 1. `-new` / `-v2` / `-copy` suffix-dup pages
- **Detect**: `scripts/validate_wiki.py::check_duplicate_suffix_variants` already
  flags; regex `^(.+?)-(new|v\d+|copy|latest|updated)$` against sibling stems.
- **Seen**: 4 of 6 dipsticks — `nikhil-rathore` / `nikhil-rathore-new` persistent
  across 4 consecutive overnight runs (22:06, 22:52, 23:28, 01:17).
- **Root cause**: agent prompt drift — compiler writes new page when it fails to
  discover the canonical slug because stems directory wasn't passed to it.
- **Auto fix**: (M+H) wire existing `scripts/merge_suffix_dupes.py` into
  `compile_all.py`/`compile_parallel.py` post-step (runs before validator). Also
  (P) inject `ls wiki/entities wiki/systems` into compiler system prompt so the
  agent sees canonical stems pre-write.
- **Effort**: ~30 LOC to invoke merge_suffix_dupes from compile pipeline; 10 LOC
  for prompt injection. ~1h.

### 2. Broken wikilinks
- **Detect**: `validate_wiki.py::check_broken_wikilinks` exists;
  `lint_wiki.py::create_missing_stubs` + `normalize_wikilinks` auto-fix exist.
- **Seen**: baseline 1–2 dead links (audit-1), grew to 17 across 9 files by
  2026-04-13 plan-24h; `[[buylead]]`, `althi-naveen`, `Lens.IndiaMART`,
  `ishu-garg` recur.
- **Root cause**: title-case + stub-not-created; grows as wiki grows.
- **Auto fix**: (L+H) run `make lint-wiki-fix` (normalize + create stubs) inside
  `compile_all.py` AFTER every batch and BEFORE validator. Today it's documented
  in plan but not wired as a compile-step.
- **Effort**: 5 LOC in `compile_all.py` to subprocess the lint --fix. <30min.

### 3. Stub pages never backfilled (`sources: []` / `last_compiled: "stub"`)
- **Detect**: `backfill_stubs.py::_is_stub` already exists.
- **Seen**: Every audit (3 of 3) flags same victims: `lucky-agarwal`,
  `amarinder-s-dhaliwal`, `wazuh-mcp`, `pratik-ahuja`. Trend report: "same
  recommendation every audit".
- **Root cause**: compiler prompt tells agent to grep but doesn't; no retry loop.
- **Auto fix**: (H) append `backfill_stubs.py --recompile` to Makefile
  `pipeline` target and to `compile_all.py` end-of-run; (V) promote "entity with
  empty sources" to hard-error in `validate_wiki.py` so regressions fail loudly.
- **Effort**: script already shipped; wiring = ~10 LOC + 1 validator rule (~15
  LOC). <1h.

### 4. Frontmatter corruption (orphan / missing required fields)
- **Detect**: `validate_wiki.py::validate_page` flags orphan + missing fields.
- **Seen**: 3 dipsticks in a row — `nadeem-suhaib.md` missing
  `page_type/status/title`, `photosearch.md` no parseable YAML,
  `ashish-verma.md`, `julee-kumari.md`, plus `pns-number-display-m-site-pdp.md`,
  `rucha-patil.md` missing `last_compiled`. CHANGELOG: 18 pages once corrupted
  at once.
- **Root cause**: Deep Agents `edit_file` mid-edit YAML clipping + auto-stamp
  that used to preserve corruption (partially fixed — still recurs).
- **Auto fix**: (H) repair-on-detect tool —
  `scripts/repair_frontmatter.py <path>` that reconstructs `page_type` from
  directory, `title` from slug, `status: current`, greps raw for sources, leaves
  body intact. Invoked automatically when `validate_wiki.py --list-bad` yields
  hits. (P) Tighten prompt — forbid `edit_file` on frontmatter; require
  `stamp_page_compiled_at` or full-rewrite with yaml.safe_dump.
- **Effort**: new 120-line script + 2-line validator integration + 5-line prompt
  rule. ~2h.

### 5. Hot-thread compile stalls (entity-page bloat → context overflow)
- **Detect**: no TCP activity / no budget delta / no file writes for >15min.
- **Seen**: CHANGELOG: iterations 5+6 hung on 10-email SonarQube thread; killed
  manually after ~28min. BACKLOG "Compile stall detection" open.
- **Root cause**: Himanshu-Jain-style entity pages ballooned to 20–26KB with
  100+ sources; LiteLLM silently times out at >80k context.
- **Auto fix**: (T) `timeout 900 uv run python scripts/compile_all.py ...`
  wrapper in `compile_overnight.sh`; (T) `asyncio.wait_for(..., timeout=600)`
  around each `agent.invoke` in `compile_parallel.py`; (L) new
  `lint_wiki.py::check_entity_bloat` — fail if any entity >12KB or >50 sources.
  Auto-fix pass that truncates `sources:` to most-recent N + "see git for full
  history".
- **Effort**: timeout wrappers <10 LOC; bloat check ~30 LOC; compactor
  ~60 LOC. ~2h.

### 6. Verbatim table (bug/ticket matrix) loss
- **Detect**: grep `^\|.+\|\s*$` count in source raw; compare to wiki page.
- **Seen**: Every audit (3 of 3) flags same regression — `ios-performance-fix`,
  `dynamic-smart-rfq-form` (12 bugs), `auditmate` (4 + ticket 646247),
  `trustpulse` (75% content loss).
- **Root cause**: compiler summarises tables into prose; prompt says "preserve"
  but has no enforcement.
- **Auto fix**: (V) `validate_wiki.py::check_table_rows_preserved` — for each
  source in `sources:`, count markdown table rows; fail if wiki has fewer; (P)
  strong prompt rule "copy bug/ticket tables byte-for-byte"; (L) pre-step that
  extracts tables to `raw/*.md` frontmatter as `tables: [{headers,rows}]` and
  prompt references them explicitly.
- **Effort**: validator rule ~40 LOC; ingest-side table tag ~50 LOC. ~3h.

### 7. Duplicate bodies (byte-identical)
- **Detect**: `validate_wiki.py::check_duplicates` already present.
- **Seen**: Once (audit-1): `export-indiamart.md == tawk-to.md`. Fixed, not
  recurred since `ae5f0e1`.
- **Auto fix**: already blocking. No action needed; keep gate in place.
- **Effort**: 0.

### 8. Page-type mismatch (directory vs frontmatter)
- **Detect**: `validate_wiki.py::validate_page` covers it;
  `lint_wiki.py::check_page_type_mismatch` too.
- **Seen**: 2 cases (audit-1) — fixed and not recurred.
- **Auto fix**: (L) auto-move script — if page in `entities/` has
  `page_type: system`, move file to `systems/` and rewrite incoming wikilinks.
  Today is manual.
- **Effort**: ~40 LOC addendum to `lint_wiki.py --fix`. ~1h.

### 9. Trailing-block / duplicate H2 rendering artefact
- **Detect**: regex for identical adjacent H2 blocks at EOF.
- **Seen**: 2 audits — `neeraj-agrawal`, `crashagent`, `mohak-saxena`. Chunking
  race — same class, new manifestation each audit.
- **Root cause**: agent writes duplicate Team/Related block at EOF.
- **Auto fix**: (L) `lint_wiki.py::dedupe_trailing_blocks` — hash adjacent H2
  sections, keep last, drop earlier identical ones. Run in --fix mode.
- **Effort**: ~40 LOC. ~1h.

### 10. Doc/code drift (README lists vaporware modules; BACKLOG shipped items
not struck)
- **Detect**: static list — check `src/compile/relations.py`,
  `src/wiki/search.py` etc. exist when README references them.
- **Seen**: coherence review flagged 8 drift points; 5 BACKLOG items shipped but
  not struck through.
- **Auto fix**: (H) `scripts/check_docs_coherence.py` — parses README "Project
  layout", asserts each path exists; fails CI / pre-commit if drift. Strike-
  through enforcement is harder; (P) add "Strike BACKLOG items the same commit
  as the fix" to CLAUDE.md self-improvement section.
- **Effort**: 60-line linter. ~1.5h.

### 11. Sources undercounting (entity in 54 raws, wiki cites 1)
- **Detect**: for each entity slug, grep `raw/*.md` for email + "First Last"
  in From/To/CC; diff from `sources:` count.
- **Seen**: `sandeep-garg` 1 vs 54, `amit-agarwal` sparse, `pratik-ahuja` 0→3
  (real = ~6).
- **Auto fix**: (H) run `backfill_stubs.py --refresh-sources --category
  entities` after every compile. Already works; just needs wiring — currently
  only runs when user invokes manually.
- **Effort**: 1 line in pipeline. <10min.

### 12. Budget exhaustion mid-run (no mid-run check)
- **Detect**: `src/budget.py` snapshot before/after; no interim check.
- **Seen**: improvement-internal flagged; overnight-plan accepts as risk.
- **Auto fix**: (H) `compile_all.py` checks `fetch_budget()` every N batches;
  aborts if `spent/max > 0.85`. Prevents retry-storm blowouts.
- **Effort**: ~15 LOC. ~30min.

### 13. Snapshot growth (`.snapshots/` unbounded)
- **Detect**: `ls .snapshots/ | wc -l` after each run.
- **Seen**: flagged in improvement-internal; no retention policy.
- **Auto fix**: (H) `snapshot_wiki.py prune --keep 10` in `compile_all.py`
  end-of-run.
- **Effort**: ~20 LOC. ~30min.

---

## Wiring summary (one Makefile target)

Add to `Makefile::pipeline` post-compile chain, in order:

```
validate_wiki   (hard-fail gates: frontmatter, dupes, suffix-twins, broken links)
  → merge_suffix_dupes     (auto-merge #1 variants)
  → lint_wiki --fix        (normalize wikilinks + create stubs, auto-move #8, dedupe #9)
  → backfill_stubs --recompile  (fill #3, refresh #11)
  → repair_frontmatter     (fix #4 remnants)
  → snapshot_wiki prune    (#13)
  → validate_wiki          (re-assert clean)
```

Wrap all compile calls in `timeout 900` and `asyncio.wait_for(600)` (#5, #12).

**Total net-new effort**: ~5 small scripts + ~80 LOC pipeline wiring = 6–8h of
work, removes ~80% of hand-fix cycles observed across 8 review documents and 6
dipsticks.

Report path: `/Users/amtagrwl/git/email-knowledge-base/docs/reviews/auto-repair-plan-20260413T084042Z.md`
