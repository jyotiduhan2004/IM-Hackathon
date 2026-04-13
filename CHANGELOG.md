# Changelog

All notable changes to this project.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Dates are UTC. Newest first.

Detailed incident postmortems live under `docs/incidents/`.

---

## [Unreleased] — 2026-04-13

### Added
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
