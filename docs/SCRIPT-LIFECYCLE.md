# Script lifecycle

`scripts/` is a mix of daily-hot tooling and one-shot migrations that piled up while we were shipping fast. We classify each script by lifecycle so garbage collection is cheap: hot-path scripts are protected, bootstrap-recovery scripts are parked but preserved, and one-shot-done scripts carry a retirement docstring block with a `Safe to delete after:` date. Once that date passes and the deletion gate holds, the script is removed in a follow-up PR. The docstring format is deliberately grep-friendly — see the "Monthly garbage day" procedure at the bottom.

## hot-path

Wired into the Makefile, CI, or invoked regularly enough that removing them would break a live workflow. No lifecycle marker — they're assumed to stay.

- `audit.py` — single-shot wiki + catalog audit report.
- `audit_50_traces.py` — reproducible 50-trace audit (scheduled / on-demand).
- `audit_systems_entities.py` — flags humans miscategorized as systems.
- `check_langfuse.py` — Langfuse health + trace-export smoke test (`make langfuse-smoke`).
- `compile_all.py` — primary compile driver (`make compile`).
- `compile_parallel.py` — parallel/async compile for large backlogs.
- `dipstick.py` — per-batch quality + cost + timing snapshot.
- `dump_agent_diagram.py` — refreshes `docs/architecture.md` (`make dump-agent-diagram`).
- `eval_harness.py` — before/after evaluator for the North-Star compile pipeline.
- `fix_broken_wikilinks.py` — conservative batch fixer for broken wikilinks.
- `format_wiki.py` — idempotent wiki-page formatter.
- `ingest_backlog.py` — Gmail → `raw/` ingestion (`make ingest`, `make ingest-all`).
- `init_db.py` — applies `src/db/schema.sql` to the configured DATABASE_URL.
- `lint_wiki.py` — wiki health checks (`make lint-wiki`, `make lint-wiki-fix`).
- `migrate_legacy_pages.py` — nightly incremental legacy-debt migration.
- `nightly_trace_audit.py` — nightly Langfuse-trace audit (`make audit-nightly`).
- `reconcile_compile_state.py` — reconciles `messages.compile_state` against real wiki provenance.
- `revert_bad_compiles.py` — reverts `compile_state='compiled'` without content-page evidence.
- `size_stats.py` — page-size + source-list diagnostics.
- `snapshot_wiki.py` — snapshot/restore wiki (`make snapshot*`).
- `stats.py` — pipeline stats: emails/day, compile coverage, cost/day.
- `trace_scorecard.py` — per-model North-Star scorecard.
- `validate_wiki.py` — hard validation, non-zero exit on broken pages (`make publish-gate`).
- `watch_and_compile.py` — live mode: poll Gmail + compile incrementally.
- `wiki_quality_metrics.py` — structured wiki quality metrics for CI gating.

## bootstrap-recovery

Rarely run, but required to rebuild the catalog from scratch (fresh env, disaster recovery, schema reset). Carry a `Lifecycle: bootstrap-recovery` marker so garbage day knows to skip them. **Do NOT delete.**

- `backfill_messages.py` — populates `messages` table from `raw/*.md` frontmatter.
- `backfill_wiki_pages.py` — populates `wiki_pages` from `wiki/**/*.md`.
- `backfill_users_threads_participants.py` — populates users / threads / participants from `raw/`.
- `backfill_source_threads_and_touches.py` — rebuilds `source_threads:` + `message_touched_pages` from disk.
- `backfill_stubs.py` — second-pass stub-filler for empty-`sources:` pages.

## one-shot-done

Ran once, served their purpose, should not need re-running. Each carries a `One-shot lifecycle:` docstring block with a `Safe to delete after:` date. Once the deletion gate holds for 7 consecutive days past that date, delete in a follow-up PR.

- `backfill_status_active.py` — flipped legacy `status` values (`current` → `active`, `contested` → `archived`).
- `backfill_trivial.py` — back-classified pending messages as trivial via `filter_trivial.classify`.
- `dedupe_sources.py` — collapsed same-thread `sources:` entries in wiki frontmatter.
- `merge_suffix_dupes.py` — merged `foo-new.md` / `foo-v2.md` variants back into canonical `foo.md`.
- `migrate_entity_slugs.py` — migrated display-name entity slugs to email-canonical slugs.
- `migrate_entities_to_people.py` — renamed `wiki/entities/` → `wiki/people/`.
- `repair_legacy_dup_slugs_2026_04_28.py` — superseded three confirmed legacy slug pairs (`alok-kumar2`, `vikram-varshney`, `samarth`) into their email-canonical / category-correct twins (STATUS.md F-013 backfill).
- `repair_lens_dup_2026_04_28.py` — consolidated `Lens.IndiaMART.md` + `lens-indiamart-com.md` into canonical `wiki/systems/indiamart-lens.md` and rewrote 24 inbound wikilinks (STATUS.md F-024 backfill).

## Monthly garbage day

Once a month (or any time the directory feels cluttered):

```bash
grep -r "Safe to delete after:" scripts/
```

For every hit whose date has passed, confirm the deletion gate still holds (e.g. `migrate_legacy_pages.py` has reported zero stragglers for 7 consecutive days), then open a deletion PR. One PR per script keeps the blast radius small and the revert cheap.
