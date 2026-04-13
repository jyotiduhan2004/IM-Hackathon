# Changelog

All notable changes to this project.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Dates are UTC. Newest first.

Detailed incident postmortems live under `docs/incidents/`.

---

## [Unreleased] — 2026-04-13

### Changed
- MkDocs viewer now uses an explicit `nav:` tree instead of filesystem
  auto-discovery. Top level is `Home` / `Topics` / `Products & Platforms`
  / `Policies` / `People` / `Changes` / `About`. The internal
  `page_type: system` stays, but the reader-facing label becomes
  `Products & Platforms`. Adds section landing pages (`wiki/home.md`,
  `wiki/{topics,systems,policies,entities}/index.md`, `wiki/about.md`)
  and an `exclude_docs: '_drafts/**'` rule so later draft-review work
  can stage pages without polluting the build. Covers Workstream 2 /
  PR 2 in `docs/issues/10-phase1-implementation-plan.md`.

### Added
- `scripts/wiki_quality_metrics.py`: CI-friendly structured wiki
  quality metrics for release gates (Phase 1 plan Workstream 6).
  Emits a single-line summary plus JSON with page counts by type,
  stub counts, topic-to-entity ratio, orphan count, pages reachable
  only from `index.md`, and avg body size by type. Gates on
  `--min-topic-ratio` (default `0.3`) so CI can fail the build when
  the topic/entity ratio collapses. Complements `scripts/audit.py`'s
  prose report; this one is designed to be diff-able and
  release-gate-parseable.
- `resolve_page` compiler tool + `src/db/wiki_pages.lookup_page` helper:
  canonical slug/title/canonical-entity-email lookup against the
  `wiki_pages` catalog. Replaces the agent's habit of grep/ls'ing the
  `wiki/` filesystem to decide whether a page already exists, so the
  compiler can consult the catalog before creating a new page and avoid
  duplicate slugs. Resolution order is slug → case-insensitive title →
  entity email; confidences are 1.0 / 0.9 / 1.0 respectively. Response
  includes `status` so the agent can distinguish `current` from
  `superseded`/`contested` pages. Registered alongside the existing
  `list_wiki_pages` / `create_entity` tools in `create_compiler`.
- `scripts/audit_systems_entities.py`: CLI that flags + relocates human
  pages accidentally filed under `wiki/systems/` (closes #43). Dry-run
  by default; `--confirm` runs `git mv` (falls back to `shutil.move`) to
  move each flagged page into `wiki/entities/`. Detects misclassification
  via frontmatter (`email:` populated, `page_type: entity`) and a simple
  slug heuristic (`firstname-lastname[digits]` minus a system-word
  stop-list). Wikilink rewrites are not required — `mkdocs-roamlinks-plugin`
  resolves `[[slug]]` by whole-tree filename search, so a move between
  `systems/` and `entities/` keeps every inbound link working.
  `scripts/validate_wiki.py` now hard-errors on any `wiki/systems/*.md`
  with a populated `email:` field, wired as an error in `validate_page`.
- Per-batch stall detection in `scripts/compile_all.py`: new
  `--batch-timeout` flag (default 900s, matches the overnight wrapper;
  pass `0` to disable, `click.IntRange(min=0)` rejects negatives) wraps
  each `run_compilation` call in a
  `concurrent.futures.ThreadPoolExecutor`, so a single hung batch
  (slow OTel export, stuck LLM provider, rare deadlock) no longer
  freezes an interactive compile loop. On timeout the existing
  batch-failure path kicks in — `_mark_batch_failed` + a `failed` row
  in `wiki/log.md` with a `TimeoutError: batch exceeded Ns
  (thread=...)` note — and the loop proceeds to the next batch. Stuck
  worker threads are orphaned at process exit (Python threads are
  cooperative; `shutdown(wait=False)` only, no `cancel_futures=True`
  because the orphaned socket runs to completion regardless);
  documented in the helper's docstring as acceptable.
- GCP Phase A viewer deploy scaffolding (PR #36): `Dockerfile` +
  `nginx.conf` (python:3.12-slim builder → nginx:alpine runtime);
  `.dockerignore` + `.gcloudignore` scoped so `mkdocs_hooks.py` can still
  inline the Sources block from `raw/*.md` at build time; idempotent
  `scripts/gcp/bootstrap.sh` (GCS bucket with versioning + 180-day
  noncurrent lifecycle + required APIs); `scripts/gcp/deploy-viewer.sh`
  (single-call `gcloud run deploy --iap` + domain-scoped
  `iap.httpsResourceAccessor` grant); `make bootstrap` / `make publish`;
  phased plan in `docs/gcp-migration.md`. Defaults target
  `voice-eval-stack-im` / `asia-south1` / bucket `indiamart-email-kb`,
  IAP gated to `domain:indiamart.com`. Existing `Indiamart AI` OAuth
  brand reused; the IAP OAuth Admin API was permanently shut down
  2026-03-19 so new projects fall back to a Google-managed OAuth
  client by default.
- Per-batch prompt-caching + token-efficiency stats: `BatchStatsCallback`
  (`src/compile/cache_stats.py`) extends LangChain's standard
  `UsageMetadataCallbackHandler` (langchain-core ≥0.3.49) with a
  tool-call counter. Standard handler aggregates `input_tokens` /
  `output_tokens` / `cache_read` / `cache_creation` per model name;
  our subclass adds `tool_calls` (which the standard handler doesn't
  track) and a flat `snapshot()` for log lines. `compile_all.py` prints
  `model=… cache=N/M (X%) writes=… turns=… tools=… tools/turn=…
  total_tok=…` after each batch and writes it into `wiki/log.md`.
  Surfaces when a model silently stops caching (the glm-5.1 surprise
  today — see `docs/reviews/prompt-caching-20260413.md`).
- `--model-pool a,b,c` flag on `compile_all.py` — random pick per batch,
  sticky for the whole batch, so the cache-stats line above lets us
  compare model behaviour A/B-style on the actual workload. Promoted
  from `docs/BACKLOG.md` § Per-batch random model A/B (now buildable
  because the cache-stats instrumentation makes the comparison
  measurable).
- Per-source role annotation on rendered entity pages (From ✍️ / To 📬 /
  CC 📋 / body 💬) in `mkdocs_hooks.py::_render_raw_source`.
- 15-minute per-batch timeout wrapper in `scripts/compile_overnight.sh`.
- Five-persona blind-audit framework (newbie, PM, information-architecture,
  fact-check, journalist) producing independent reports + synthesis under
  `docs/reviews/`.
- Architectural plan for separating wiki prose from email provenance
  (SQLite catalog) — see GitHub issue #8.
- Research notes: deepagents library patterns, edit-tool behavior,
  inline-citation proposal, Anthropic engineering reading list, QMD
  (Tobi Lütke) as a local semantic search layer.
- `docs/incidents/` directory holding postmortems (carved out of CHANGELOG).

### Changed
- Wiki `## Sources` block is now wrapped in a collapsed `<details markdown="1">`
  so evidence stays on the page without scrolling past the synthesized prose
  (`mkdocs_hooks.py`). Entity pages with more than 20 sources render only the
  10 newest (tail of the chronologically-ordered list) plus a
  `+N older sources not shown` hint, so high-volume contacts no longer drown
  the main content. Topic pages are not capped — entities are the only
  sources-heavy pages observed in audits. Covered by
  `tests/test_mkdocs_sources_rendering.py` with fixtures under
  `tests/fixtures/sources_fixture/wiki/`.
- `COMPILER_SYSTEM_PROMPT` gains two Phase 1 taxonomy sections:
  "Topic vs system" (what-is-happening vs what-is-this-thing, with Lens
  and WhatsApp 9696 worked examples) and "Entity evidence strength"
  (From/To/quoted owner = strong; CC-only or incidental first-name
  mention = weak, skip on entity pages). Reconciled against existing
  "Populate sources exhaustively" / "Source completeness for entity pages"
  sections: CC-only emails still belong in `sources:` for audit-trail
  citation, but do NOT justify creating a new entity page or writing new
  prose about the person.
- Default `LLM_MODEL` reverted `z-ai/glm-5.1` → `z-ai/glm-4.6`. glm-5.1
  does NOT cache prompts through OpenRouter on this proxy (zero cached
  tokens across 5 sequential identical-prompt calls), while glm-4.6 caches
  ~20% of our 3000-token system prompt, compounding to a ~3.66× cost
  delta on a batch. Switch back to glm-5.1 once OpenRouter / Intermesh
  enables caching for `z-ai/glm-5.x` routes. See
  `docs/reviews/prompt-caching-20260413.md`.
- CHANGELOG restructured to Keep-a-Changelog format. Historical
  postmortems moved to `docs/incidents/2026-04-13-phase0-bootstrap.md`.

### Fixed
- Overnight compile zombies: cleaned up `compile_all` processes that
  survived the shell's `Killed: 9` after gtimeout didn't propagate
  through `uv run`.
- Removed unused `yaml` import in `scripts/backfill_stubs.py`.
- `scripts/snapshot_wiki.py::reset-raw-compiled` replaced a broken
  `content.split("---", 2)` frontmatter parse with
  `src/utils.py::extract_frontmatter`. The old parser mangled any raw
  file whose subject or body contained a literal `---` (e.g.
  `Informational---Transforming SonarQube`). Codex flagged this in
  `docs/reviews/codex-priority-review-20260413T090000Z.md`.
- Langfuse SDK pinned to `>=3,<4` to match the self-hosted server at
  `langfuse.intermesh.net` (v3.140.0). The v4 SDK renamed the legacy
  `langfuse.callback` module and expects response fields the server
  doesn't emit, so the LangChain callback wouldn't instantiate.
  Smoke-tested end-to-end: a real LiteLLM call via the callback
  handler lands a trace in the `email-kb-wiki` project.
- Langfuse span-export hang safeguard: `get_langfuse_handler()` now
  configures both the OTel pipeline and the Langfuse client via env
  vars (`OTEL_BSP_EXPORT_TIMEOUT=2000`, `OTEL_EXPORTER_OTLP_TIMEOUT=2`,
  `LANGFUSE_TIMEOUT=2`, `LANGFUSE_FLUSH_AT=50`,
  `LANGFUSE_FLUSH_INTERVAL=5`). Caps each span-export attempt at ~2s so
  a slow server degrades tracing to best-effort rather than stalling
  `compile_all` for minutes (previously observed 8+ min hangs when the
  server's `/api/public/otel/v1/traces` was slow).
  Initial implementation tried passing `timeout`/`flush_at`/`flush_interval`
  to a `Langfuse(...)` constructor — Claude's review caught that
  CallbackHandler in v3.14.6 doesn't accept a `langfuse_client` arg and
  instantiates its own client, so those values were dead code. Env vars
  are the only working path.

### Changed
- Default `LANGFUSE_ENABLED=false` in `.env.example` until the
  server-side OTLP hang (issue #17) is resolved on the self-hosted
  Langfuse instance. The compile pipeline has bounded export timeouts
  so enabling is safe — it just means traces may drop while the server
  is slow.
