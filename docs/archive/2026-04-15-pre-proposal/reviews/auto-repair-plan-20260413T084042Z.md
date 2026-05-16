# Auto-Repair Plan — 2026-04-13T08:40Z

Ranked by frequency × impact. Counts from CHANGELOG, 6 dipsticks, 8 reviews.
Existing: `validate_wiki.py`, `lint_wiki.py --fix`, `merge_suffix_dupes.py`,
`backfill_stubs.py`, snapshot wrap.

Legend: V=validator · L=lint-fix · H=hook · M=merge · P=prompt · T=timeout.

| # | Pattern | Detect | Seen | Root cause | Auto fix | Effort |
|---|---|---|---|---|---|---|
| 1 | `-new`/`-v2`/`-copy` suffix-dup | `check_duplicate_suffix_variants` live | 4/6 dipsticks; `nikhil-rathore-new` across 4 overnight runs | Agent can't see canonical stems | M+P: wire `merge_suffix_dupes.py` into `compile_all.py` post-step; inject `ls wiki/entities wiki/systems` into prompt | ~40 LOC, 1h |
| 2 | Broken wikilinks | `check_broken_wikilinks`, `normalize_wikilinks` live | Grew 1→17 across audits; `[[buylead]]`, `althi-naveen` recur | Case drift + missing stubs; grows with wiki | L+H: run `lint_wiki --fix` after every batch, before validator | 5 LOC, <30min |
| 3 | Stub never backfilled (`sources: []`) | `backfill_stubs._is_stub` live | Every audit: `lucky-agarwal`, `amarinder-s-dhaliwal`, `wazuh-mcp` | Prompt says grep; agent doesn't; no retry | H+V: append `backfill_stubs --recompile` to `make pipeline`; hard-fail empty-sources entity | 25 LOC, 1h |
| 4 | Frontmatter corruption | `validate_page` live | 3 consecutive dipsticks; once corrupted 18 pages | `edit_file` YAML clipping; auto-stamp preserved damage | H+P: `repair_frontmatter.py` reconstructs `page_type` from dir, `title` from slug, sources from raw grep; auto-invoke on `validate --list-bad`. Prompt: forbid `edit_file` on frontmatter | 120 LOC, 2h |
| 5 | Hot-thread compile stalls | No TCP/budget/write activity >15min | CHANGELOG: iters 5+6 hung ~28min | Hot entity pages >20KB × 2–3 loaded >80k ctx; LiteLLM silent timeout | T+L: `timeout 900` in `compile_overnight.sh`; `asyncio.wait_for(600)` in `compile_parallel.py`; entity-bloat lint (>12KB or >50 sources) + compactor truncating `sources:` to recent N | 90 LOC, 2h |
| 6 | Bug/ticket table loss | Count `^\|.+\|$` rows in source vs wiki | Every audit: `ios-performance-fix`, `dynamic-smart-rfq-form` (12 bugs), `auditmate`, `trustpulse` 75% loss | Compiler summarises to prose | V+P: `tables` tag in raw frontmatter via `parser.py`; `check_table_rows_preserved` validator; prompt "copy tables byte-for-byte" | 90 LOC, 3h |
| 7 | Trailing duplicate H2 block | Hash adjacent EOF H2s | 2 audits: `neeraj-agrawal`, `crashagent`, `mohak-saxena` | Chunking race in agent write | L: `dedupe_trailing_blocks` in `lint_wiki --fix` | 40 LOC, 1h |
| 8 | Sources undercounting (habit-CCs) | Grep raw From/To/CC for email + "First Last" vs `sources:` | `sandeep-garg` 1 vs 54; `amit-agarwal` sparse | CC/signature mentions not linked | H: wire `backfill_stubs --refresh-sources` into pipeline | 1 line |
| 9 | Budget exhaustion mid-run | `fetch_budget()` interim check | Improvement review flagged; overnight-plan accepts risk | Retry storms on 402 | H: abort if `spent/max > 0.85` every N batches | 15 LOC, 30min |
| 10 | `.snapshots/` unbounded | `ls .snapshots/` | Flagged in improvement review | No retention | H: `snapshot_wiki prune --keep 10` end-of-run | 20 LOC, 30min |
| 11 | Doc/code drift (README vaporware) | Path-exists check vs README layout | 8 drift points; 5 BACKLOG items shipped un-struck | Append-only docs | H+P: `check_docs_coherence.py` pre-commit; "strike BACKLOG same commit" rule in CLAUDE.md | 60 LOC, 1.5h |

Already blocking (no action): `check_duplicates` (byte-identical bodies),
`check_page_type_mismatch` (dir vs frontmatter) — both single-incident, not
recurring.

## Wiring — one Makefile target

`Makefile::pipeline` post-compile order: `validate_wiki` (hard) →
`merge_suffix_dupes` (#1) → `lint_wiki --fix` (#2, #7) →
`backfill_stubs --recompile` (#3, #8) → `repair_frontmatter` (#4) →
`snapshot_wiki prune` (#10) → `validate_wiki` (re-assert). Wrap compile calls
in `timeout 900` + `asyncio.wait_for(600)` (#5, #9). Inject existing-slug list
into prompt (#1).

Net new: 4 scripts + ~80 LOC wiring = 6–8h. Removes ~80% of hand-fix cycles.

Report path: `/Users/amtagrwl/git/email-knowledge-base/docs/reviews/auto-repair-plan-20260413T084042Z.md`
