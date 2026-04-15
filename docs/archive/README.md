# docs/archive

Historical strategy docs and reviews that have been superseded by the current direction in `docs/NORTH-STAR.md` and the active proposal at `docs/proposal/NORTH-STAR-DRAFT.md`.

Files here are **not** authoritative — they're preserved as historical context for how we arrived at the current plan. Don't cite them as specs.

## What's here

### `2026-04-15-pre-proposal/`

Docs archived during the 2026-04-15 consolidation pass, when the Q&A with the project owner settled the "compiled curated wikipedia" direction (see `docs/proposal/NORTH-STAR-DRAFT.md`).

- **`issues/02-data-model.md`** — old data-model spec. Superseded: taxonomy reduced from 6 page types to 4 visible + 2 reference (decisions, people). See NORTH-STAR §1.
- **`issues/03-email-ingestor.md`** — Phase-0 ingestor spec. Current state lives in `src/ingest/` + the messages table; no forward-looking work here anymore.
- **`issues/04-wiki-compiler.md`** — Phase-0 compiler spec. The tool list in this doc is stale (mark_as_compiled, update_wiki_index, append_to_log are gone). Current toolbelt: see `CLAUDE.md`.
- **`issues/05-claude-md-agent-schema.md`** — old CLAUDE.md draft. Active version is in the repo root.
- **`issues/06-lint-agent.md`** — old lint spec. Current lint lives in `scripts/lint_wiki.py` + `scripts/validate_wiki.py`.
- **`issues/07-phased-delivery.md`** — old phased plan. Replaced by NORTH-STAR Phase ladder (§5).
- **`issues/08-phase1-live-ingestion.md`** — Gmail watch + Pub/Sub spec. **Demoted to Phase 2**; doc preserved here for when we pick it back up.
- **`issues/09-internal-wiki-structure.md`** — wiki IA doc. Folded into NORTH-STAR §1–§2.
- **`issues/10-phase1-implementation-plan.md`** — Phase 1 workstream doc. Work items merged into NORTH-STAR §5 Phase 1 PR list.
- **`issues/11-user-personas-and-knowledge-flows.md`** — personas doc. Personas as user research are valid; the `navigation_role` / "Ownership History" / "How the View Changed" section concepts are **not shipping** (the implementation cost isn't worth it before Phase 2). Archive as research.

- **`reviews/audit-*`** — pre-Phase-1 corpus audits (2026-04-12 / 04-13). The synthesis review (`audit-synthesis-20260413T040000Z.md` in the main file, 5 persona audits archived here) informed the Phase-1 direction but isn't load-bearing now.
- **`reviews/plan-24h-*`, `overnight-plan-*`** — dated plans, executed.
- **`reviews/ecosystem-scan-*`, `deepagents-learnings-*`, `edit-tool-research-*`, `source-dedup-plan-*`** — research snapshots.
- **`reviews/quality-trend-*`, `systemic-quality-*`, `improve-internal-*`, `coherence-*`** — operational quality snapshots.
- **`reviews/auto-repair-plan-*`** — mostly executed, archive.

### Still live in `docs/reviews/` (not archived)

- **`codex-catalog-review-20260413T080000Z.md`** — ongoing schema reference for the Postgres catalog.
- **`codex-priority-review-20260413T090000Z.md`** — some items still open (see NORTH-STAR §3 for provenance-split status).
- **`knowledge-vs-index-20260413T032000Z.md`** — the "sources in YAML" decision is still pending explicit resolution.
- **`tool-audit-20260413T050000Z.md`** — feeds the Phase 1 toolbelt work (`wiki_merge_pages`, `get_page_summary`, `get_thread_context` from NORTH-STAR §3 Stage 3).
- **`prompt-caching-20260413.md`** — canonical record of model-pool cache behavior.
