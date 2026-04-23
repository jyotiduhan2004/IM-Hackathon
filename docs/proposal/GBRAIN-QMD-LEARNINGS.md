---
title: Gbrain + qmd — what to borrow, what to skip, what mistakes to avoid
audit_date: 2026-04-19
status: draft_proposal
author: Claude (for Amit)
scope: gbrain v0.1.0 → v0.12.2 full CHANGELOG + docs + DeepWiki; qmd v0.1 → current
related:
  - docs/NORTH-STAR.md
  - docs/audits/v12-north-star-2026-04-19.md
  - docs/audits/topic-page-structure-archetypes-2026-04-18.md
  - docs/audits/cycle-10-smoke-30-2026-04-18.md
---

# Gbrain + qmd — what to borrow, what to skip, what mistakes to avoid

This doc is a calibrated read of **what gbrain actually is**, **what gbrain actually did wrong** (their own `CHANGELOG.md` is a confession), and **what of it helps us given v12's concept-page reframe**. It ends with a priority list, a Langfuse+wiki probe list, and an open-questions section.

This is the second pass. The first pass was built on web-search summaries; this one is grounded in gbrain's CHANGELOG v0.1.0 → v0.12.2 (2026-04-05 → 2026-04-19), deepwiki structural queries, and qmd's documented reversals. Any claim that names a specific version, commit, or bug is pulled from primary source.

---

## TL;DR — three sentences

1. **Don't replace us with gbrain.** Gbrain is a *personal* knowledge brain with a rigid two-layer page structure that has actively lost data in production (see v0.12.2 hotfix below) and a MECE single-home type system that directly contradicts v12's "all pages are just pages." Their worst bugs would hit us harder than they hit them, because our corpus is 60× larger and multi-author.
2. **Do borrow qmd as our retrieval substrate** — MCP-native, local, zero API cost, hybrid BM25+vector+rerank. It fixes the exact `resolve_page` regression we've already instrumented in Langfuse (`is_alphabetical_candidate_list`, 58% of misses). Deploy it as an in-process daemon, wire it behind `resolve_page` with a feature flag.
3. **Do borrow two specific gbrain patterns**: (a) the **fail-improve loop** (regex-first, LLM fallback, log, generate new regex from successful LLM outputs) as the implementation strategy for typed-edge extraction, and (b) the **"Pages that link here" backlink section** as the concrete V12-U5 deliverable. Skip everything else — compiled-truth/timeline split, minions, skills system, intent classifier, 3-tier chunking, data-research recipes — each with a specific reason stated below.

---

## 1. What gbrain actually is, calibrated

GBrain (Garry Tan's "Opinionated OpenClaw/Hermes Agent Brain") is a personal knowledge management system. TypeScript. First release v0.1.0 on **2026-04-05**; currently at v0.12.2 on **2026-04-19** — **~2 weeks of intense iteration**, averaging a release every ~1.3 days. Production deployment: Garry's own brain, reportedly **17,888 pages / 4,383 people / 723 companies / 14,700 pages in another deployment** (README). Positioned as "the mod on GStack" — GStack handles the coding surface, GBrain handles memory/ops/enrichment.

### Component inventory (from deepwiki structural scan)

- **Engine**: `BrainEngine` interface with two implementations. `PGLiteEngine` (embedded Postgres 17.5 via WASM, zero-config, default). `PostgresEngine` (Supabase/direct Postgres).
- **Page schema**: `pages` table with `compiled_truth` + `timeline` + typed `page_type` (9 values: person, company, deal, yc, civic, project, concept, source, media). Frontmatter as JSONB. Versioning table.
- **Chunking**: 3-tier (recursive for timelines, semantic/Savitzky-Golay for compiled_truth, opt-in LLM-guided for high-value). 300-word chunks with 50-word overlap for recursive; 128-word candidate windows for LLM.
- **Embedding**: OpenAI embeddings by default; sliding-worker-pool parallelization (20 workers). Stale-NULL'd out on text change with embed failure. 20k-chunk corpus: 8 min wall time.
- **Search**: Hybrid pipeline. Intent classifier (pattern-match, zero-latency) → multi-query expansion (Claude Haiku) → BM25 + vector → Reciprocal Rank Fusion → cosine re-scoring → 4-layer dedup → compiled-truth guarantee.
- **Graph**: `links` table with `link_type`. Auto-extraction on `put_page` via regex cascade (v0.12.0 / PR #188, the typed-edge shipment). `graph-query` CLI with recursive CTEs, depth cap 10.
- **Minions**: Postgres-backed job queue. Depth cap, per-parent child cap, wall-clock timeout, cascade cancel via recursive CTE, idempotency keys, `child_done` inbox, attachment manifest. Migration v7 — additive.
- **Skills**: 24 skills (was 8 until v0.10.0 added 16) as fat markdown "playbooks" under `skills/`. `RESOLVER.md` is a routing table. `checkResolvable()` validates MECE + reachability + DRY violations.
- **Operations / CLI**: 37+ commands. Self-diagnostic `gbrain doctor` (8 health checks + 0-100 composite score), `gbrain features --auto-fix`, `gbrain autopilot --install` (launchd/cron daemon), `gbrain eval --qrels` retrieval evaluation.
- **MCP**: stdio server via `gbrain serve` + 30+ MCP tools. HTTP transport was initially via Supabase Edge Function, **abandoned in v0.8.0 as unreliable**; they switched to self-hosted + ngrok. `gbrain serve --http` still on the roadmap in `TODOS.md`.
- **Security**: Three "waves" of security hardening (v0.9.1 baseline, v0.9.3 storage, v0.10.2 SSRF+recipe-trust). Path traversal, slug validation, symlink refusal, DoS caps at 100 results, `statement_timeout: 8s`, SSRF blocking, prompt-injection hardening.

### Scale constraints (from CHANGELOG + docs)

- **PGLite → Supabase** recommended at 1000+ files (we're at 529 + 6,770 emails).
- **Embedding** is the dominant cost. 20k chunks = 8min with 20 workers (was 2.5hr). TODOS lists "Batch embedding queue across files" as P1 — they still haven't fully solved it.
- **Hybrid search latency**: 1-3s; keyword-only 100-300ms.
- **Graph traversal depth hard-capped at 10** for remote MCP callers (DoS prevention).
- **Search result hard-capped at 100** (DoS prevention).

---

## 2. Gbrain's evolution in chapters (what they tried, what they learned)

### v0.1.0 – v0.3.0 (Apr 5–10, 2026): **search infrastructure first**
Markdown + Postgres + pgvector + hybrid BM25 + vector. The "compiled truth + timeline" page model was there from v0.2.0.1 (2026-04-07) — *not* a later refinement. Recursive chunking, RRF fusion, initial MCP stdio surface.

### v0.4.0 – v0.6.0 (Apr ~10–12): **operational maturity**
MCP tool consolidation (see **Mistake #3** below: they initially had `search` + `vector_search` + `deep_search` as separate tools, later unified under `query`). Schema migrations, Supabase Edge Function for remote MCP (**Mistake #4**: abandoned), YAML-over-SQLite for recipe configs (conscious reversal from earlier SQLite-for-everything), file-storage tiering.

### v0.7.0 – v0.9.0 (Apr ~12–13): **security + performance waves**
Security Wave 1 + 2: path traversal, symlink refusal, slug authority from path (not frontmatter), DoS caps, statement_timeout, advisory locks for concurrent PGLite processes. Performance: 30× speedup on `embed --all` via sliding worker pool. **12 data-integrity fixes in one release** (v0.9.1) covering orphan chunks, silent-noop writes, zero-always health metrics, stale embeddings, silent embed failures, O(n²) lookup.

### v0.9.3 (Apr 12): **intent classifier arrives**
Zero-latency pattern-match intent classifier → +21% page coverage, +29% signal, 100% source accuracy. `gbrain eval --qrels` with P@k, R@k, MRR, nDCG@k. This is the first search-quality-measured release.

### v0.10.0 (Apr 14): **the skills explosion + fail-improve loop**
Skills expand 8 → 24 (!). `signal-detector` fires on every message. `brain-ops`, `media-ingest`, `meeting-ingestion`, `citation-fixer`, `data-research` (MRR/ARR/runway extraction with regex recipes), `skill-creator`, etc. RESOLVER.md is the dispatcher. **`checkResolvable()`** validates MECE + reachability + DRY. Minions — Postgres-backed job queue. Fail-improve loop — regex first, LLM fallback, log, generate regex from LLM successes. Intent classifier 40% → 87% deterministic.

### v0.10.1 – v0.10.2 (Apr 15–17): **fix the skills, fix the security**
Three bugs in `sync --watch`: loop existed but never called, first sync never saved checkpoint, sync didn't auto-embed. Security Wave 3: nine CVE-class fixes including arbitrary file read via `file_upload` (MCP callers could read `/etc/passwd`), fake `isEmbedded=true` trust boundary, SSRF defense against AWS metadata endpoint, prompt-injection hardening on query expansion.

### v0.11.0 – v0.11.1 (Apr 18): **Minions goes agent-orchestration**
Minions v2: depth cap, per-parent child cap, wall-clock timeout, cascade cancel, idempotency keys, `child_done` parent inbox, `removeOnComplete/removeOnFail`, attachment manifest. Migration v7. **v0.11.0 shipped broken** (the migration orchestrator clobbered Postgres configs with PGLite defaults) — v0.11.1 fixed it within hours with 34 new unit tests and a "migration is canonical, not advisory" principle pinned to `CLAUDE.md`.

### v0.12.0 – v0.12.2 (Apr 18–19): **typed edges + data-correctness hotfix**
v0.12.0 shipped the auto-linking typed-edge graph (PR #188). v0.12.1 fixed extraction N+1 and migration timeouts. **v0.12.2, TWO DAYS later**, was a data-correctness hotfix with THREE silent-data-loss bugs the entire test suite missed — more on this below.

### Note on cadence
14 releases in 14 days. Heavy contributor involvement (@garagon, @Hybirdss, @4shut0sh, @YIING99, @knee5, @leonardsellem). Fast iteration, open-source pressure revealing bugs. Our cadence is slower; we catch issues via Langfuse nightly audits and trace-scorecard deltas, not via external contributors.

---

## 3. Mistakes gbrain made that we must NOT repeat

Each item here is a named incident from their own CHANGELOG plus the lesson for us.

### Mistake #1 — The `---` separator bug (v0.12.2, 2026-04-19)

> `splitBody` treated any standalone `---` line in body content as a timeline separator. @knee5 migrating a 1,991-article wiki landed a 23,887-byte article in the DB as 593 bytes — **4,856 of 6,680 wikilinks lost**. (v0.12.2 CHANGELOG)

**What this tells us:** Their core page model — `---` separating compiled_truth from timeline — is a **structural foot-gun**. Any markdown horizontal rule inside body content silently detonates the page. The bug existed undetected for ~11 releases. It was only caught when a user migrated a real 1,991-article wiki.

**Lesson for us:** **Do not adopt gbrain's hard-separated page model.** V12 already decided on `## Summary` + `## Recent changes` + collapsible `<details>` archive — those are all H2/HTML-tag delimited, not `---` delimited. A `---` in a page body is a horizontal rule, not a metadata boundary. We already do this right. Don't regress to gbrain's shape.

### Mistake #2 — The JSONB double-encode bug (v0.12.2)

> `${JSON.stringify(value)}::jsonb` interpolated into postgres.js queries was double-encoded on the wire. Frontmatter columns held `"\"{\\\"author\\\":\\\"garry\\\"}\""` instead of `{"author":"garry"}`. Every `frontmatter->>'key'` query returned NULL. GIN indexes inert. Same bug on `raw_data.data`, `ingest_log.pages_updated`, `files.metadata`, `page_versions.frontmatter`. **PGLite hid this entirely (different driver path)** — which is exactly why it slipped past the existing test suite. (v0.12.2 CHANGELOG)

**What this tells us:** PGLite (embedded WASM Postgres) is NOT Postgres. Testing against PGLite while deploying to Supabase/postgres.js is the wrong test matrix. Their entire unit suite + CI passed. Users lost data in prod.

**Lesson for us:** We already use real Postgres in tests (via `voice-eval-stack` docker-compose, same driver path as prod). Good. But we should **add a round-trip JSONB test** for every frontmatter field we read via `->>` to guard against this pattern regression. Their fix (`scripts/check-jsonb-pattern.sh` CI grep guard + `test/e2e/postgres-jsonb.test.ts`) is the shape we should copy if we ever add JSONB fields. Currently our `messages` + `wiki_pages` tables don't depend on JSONB `->>` queries, but if we add a `wiki_edges.metadata` JSONB column for Priority 2, this is a landmine.

### Mistake #3 — MCP tool proliferation (v1.1.0 tool renames; gbrain consolidation pattern)

qmd's equivalent: `search` + `vector_search` + `deep_search` → unified `query` (v1.1.0, backwards-incompatible). Gbrain: `gbrain ask` is an alias for `gbrain query` in v0.9.1. Both projects found they had too many tools that did "search-ish" things, and had to consolidate.

**Lesson for us:** When we expose MCP (Priority 5, deferred), start with ONE `resolve_page`/`search_wiki` tool, not three. The agent picks wrong when the options overlap. Our current `resolve_page` / `list_wiki_pages` / `get_page_summary` / `grep` is already three-ish; be careful before adding a fourth.

### Mistake #4 — Supabase Edge Function for remote MCP (abandoned v0.8.0)

> The Edge Function never worked reliably. Self-hosted + ngrok is the path. `scripts/deploy-remote.sh`, `supabase/functions/gbrain-mcp/`, `src/edge-entry.ts`, `.env.production.example`, and `docs/mcp/CHATGPT.md` were all removed in version 0.8.0. (CHANGELOG)

**What this tells us:** Betting on an ephemeral serverless runtime for a persistent-connection protocol is wrong. Edge functions have cold starts, run-time limits, and deployment surface that doesn't match MCP's stdio/SSE/HTTP-stream shape.

**Lesson for us:** When we do Priority 5 (MCP serve), run it as a long-lived process. Not GCP Cloud Run cold-start, not a Vercel function. FastAPI container with a health probe, or stdio-over-subprocess for local dev.

### Mistake #5 — The `isEmbedded=true` fake trust boundary (v0.10.2)

> `loadAllRecipes()` previously marked every recipe as `embedded=true`, including ones from `./recipes/` in your cwd. Anyone who could drop a recipe in cwd could bypass every health-check gate. (v0.10.2 Security Wave 3 CHANGELOG)

**What this tells us:** Trust boundaries that are set by a BOOLEAN are trust boundaries that will accidentally evaluate to `true` in every code path. The fix was: only package-bundled recipes (source install + global install) are trusted.

**Lesson for us:** Our analogue is `OperationContext` — we don't have one formally. When we wire MCP, any "is this caller trusted?" flag should be derived from a structural property (caller identity, transport type), not a boolean that defaults to `true` or can be set by untrusted input.

### Mistake #6 — Health metrics that were always zero (v0.9.1)

> Health metrics (`stale_pages`, `dead_links`, `orphan_pages`) now measure real problems instead of always returning 0. (v0.9.1 CHANGELOG)

**What this tells us:** If a metric is always zero, nobody notices it's broken until the day someone asks "why is this always zero?" This is particularly dangerous because green dashboards lie.

**Lesson for us:** V12-U0 scorer must emit a **test harness** that verifies each metric actually fires. Fixture page with a known orphan → scorer should report 1 orphan. Fixture with a broken wikilink → should report 1 dead link. Our `wiki_quality_metrics.py` has the ingredients but we should add this test.

### Mistake #7 — `performFullSync` not saving checkpoint (v0.10.1)

> First sync no longer repeats forever. `performFullSync` wasn't saving its checkpoint. Fixed: sync state persists after full import so the next sync is incremental. (v0.10.1 CHANGELOG)

**What this tells us:** Any long-running batch that CAN resume must save checkpoint BEFORE declaring the work complete. "Complete" without a checkpoint means "replay from zero on next run."

**Lesson for us:** `scripts/compile_all.py` already uses the `messages.compile_state` state machine, which persists per-message progress correctly. Good. But any new batch operation (Priority 2 edge rebuild, Priority 3 doctor, etc.) should follow the same "checkpoint-before-complete" discipline. One specific risk: our `_rebuild_edge_graph` hook (Priority 2) must set a `wiki_edges.last_full_rebuild_at` sentinel so a crash during rebuild doesn't leave inconsistent state.

### Mistake #8 — Silent empty-catch blocks swallowing embed failures (v0.9.1)

> Embedding failures are no longer silent. The `catch { /* non-fatal */ }` is gone. (v0.9.1 CHANGELOG)

**Lesson for us:** Our `CLAUDE.md` already bans bare `except Exception` — use specific types. Keep that rule religious. One specific place to audit: `scripts/compile_parallel.py` async error handling (not yet reviewed here).

### Mistake #9 — DoS via unbounded `limit` (v0.9.1)

> `limit` is clamped to 100 across all search paths. Requesting `limit: 10000000` now gets you 100 results and a warning. `statement_timeout: 8s` on the Postgres connection as defense-in-depth.

**Lesson for us:** `src/db/wiki_pages.py:search_pages` has `limit: int = 5` default with no hard cap. We don't have a DoS surface today (agent-only), but if we ever expose a public MCP endpoint (Priority 5), we need the same clamp pattern. Add `statement_timeout: 8s` to our Postgres connection config now — cheap defense-in-depth.

### Mistake #10 — Slug authority from frontmatter (v0.9.1)

> A file at `notes/random.md` can't declare `slug: people/admin` and silently overwrite someone else's page. Slug authority is path-derived. (v0.9.1 CHANGELOG)

**What this tells us:** Any input-derived identifier (slug, filename, ID) that the LLM or user controls must be validated against the structural source of truth.

**Lesson for us:** We already do this via `create_entities` — email-canonical hash → slug. `entity_write_autoheal` middleware nudges `write_file("/wiki/people/...")` into `create_entities(...)`. We learned this the hard way with `vishakha-indiamart`, `akash-singh6`. Keep the rule religious.

### Mistake #11 — Transaction mode Supabase pooler silently skipping pages

> Transaction mode pooler causes `.begin() is not a function` errors and silently skips pages. Always use Session mode (port 6543). (gbrain docs)

**Lesson for us:** We're on direct Postgres, not PgBouncer. We're safe today. If we ever go through a pooler, this lesson applies.

### Mistake #12 — Health check as shell string = RCE (v0.9.3)

> Health checks speak a typed language now. Recipe `health_checks` use a typed DSL (`http`, `env_exists`, `command`, `any_of`) instead of raw shell strings. No more `execSync(untrustedYAML)`. (v0.9.3 CHANGELOG)

**Lesson for us:** We don't have shell-string-driven config today. If we ever add "recipe" support (Priority 6 / Data Research), any user-supplied config field that runs code must be a typed DSL, not a shell string. Our linter should refuse `execSync(var)` patterns.

### Mistake #13 — Skills proliferation created its own problem

Skills went 8 → 24 in v0.10.0. They then had to build `checkResolvable()` to detect MECE overlaps, DRY violations, gap detection. **The tool to validate the skills system IS the confession that the skills system didn't scale cleanly.**

**Lesson for us:** We don't have a skills system; we have a unified compiler + middleware hooks. Don't "skill-ify" our agent just because gbrain did. Our middleware hook chain (chronological_scope, same_thread_guard, path_autoheal, entity_write_autoheal, sibling_draft_check) is the right shape for our problem — deterministic pre/post-invariants around a single agent, not 24 specialized sub-agents with a resolver. If we ever DO split into multiple skills, build a `check-resolvable` equivalent FROM DAY ONE, not after 16 of them land.

### Mistake #14 — Migration orchestrator clobbered config (v0.11.0 → v0.11.1 hotfix)

> Running bare `gbrain init` with no flags defaulted to PGLite and called `saveConfig` — silently clobbering any existing Postgres config. (v0.11.1 CHANGELOG)

**Lesson for us:** Any "smart defaults" command that can overwrite user state must refuse to run if user state exists. Our `scripts/init_db.py` already respects existing schema; keep it that way. Never add an `--overwrite-existing` default.

### Mistake #15 — CJK queries silently skipped query expansion (v0.9.3)

> CJK queries expand correctly. Chinese, Japanese, and Korean text was silently skipping query expansion because word count used space-delimited splitting. Now counts characters for CJK.

**Lesson for us:** Our corpus is English + some Hindi (Devanagari) + some Roman-Hindi. When we add qmd (Priority 1), verify its query-expansion handles Devanagari OR disable expansion for non-Latin queries. The resolve_page normaliser currently doesn't touch Devanagari; likely fine, but add a unit test.

---

## 4. What we have that gbrain doesn't (and why our shape is right for us)

Before listing what to borrow, it's worth naming what gbrain is missing that's load-bearing for us. These decisions don't need revisiting — they're the shape of our problem.

- **Chronological scope**: the agent processes email N of a thread "as a writer at that point in time" and must NOT see later replies. Gbrain has no analogue — it's a single-user event stream, no "processing history out of order" problem. Our `src/compile/middleware/chronological_scope.py` is a domain-specific invariant.
- **Multi-email synthesis into a concept page**: our compiler reads email N, resolves the concept, merges new evidence into the existing page, updates `## Recent changes`, detects supersession. Gbrain's event-stream ingestion is per-event, not cross-thread.
- **Multi-author attribution**: 6,770 emails from hundreds of authors. Our `messages.from_address` + `users` + `message_participants` tables track who, and V12-U3 inline footnotes will trace claims to messages. Gbrain's "who said this" is just "me" — single user.
- **Supersession detection from email language**: "deprecating X", "rolling back Y", "replaces Z" → set `status: superseded` + `superseded_by`. Gbrain flags contradictions but doesn't have this domain-specific verb detection.
- **Thread-level guards**: `same_thread_topic_guard` stops two emails from the same thread forking two topic pages in one batch. Gbrain has no "thread" concept.
- **`claim_next_message` state machine with `FOR UPDATE SKIP LOCKED`**: 30-min stale-claim recovery, retries without double-writing. Equivalent in Minions, but ours is simpler and sufficient.
- **`messages.compile_state` as the single source of truth for "have we processed this?"** — we've iterated through two wrong answers here (`mark_as_compiled` tool: 68% unreliable; reconcile-by-any-citation: 715/748 false positives) to arrive at "cited in a content-type page" as the truth. This lesson (`CLAUDE.md` Guardrail principles) is already captured. Gbrain has nothing equivalent because their event-ingest model doesn't have the re-processing problem.
- **Langfuse trace-scoring + nightly audit**: scripts/trace_scorecard.py + scripts/nightly_trace_audit.py continuously grade agent behavior. Gbrain has `gbrain eval` on a 29-page fixture; we run a nightly sample over every compile trace.

**These don't translate to gbrain — they're the value we've built.** Replacing the system means losing every one of them.

---

## 5. Should we replace with gbrain? — expanded argument

The first draft of this doc concluded "no, don't replace." The deeper research sharpens the argument:

### 5.1. Their v0.12.2 data-loss episode is a direct warning shot

83% article truncation silently shipped for 11 releases. JSONB double-encoding silently shipped for longer. Both hit real users with real data. **Neither was caught by their test suite** because they tested on PGLite, not Postgres. If we migrated to gbrain, we'd inherit these exact bugs' test coverage gap at the moment we most need robust tests.

### 5.2. Their core page model is structurally fragile

The `---` separator is the page model's spine. A horizontal rule anywhere in body truncates the page. They've now fixed it, but the fix is conservative — they still use `---` as the separator, just with better parsing. **V12 explicitly rejected this model** (see `docs/audits/v12-north-star-2026-04-19.md` §"What does a page's `## Summary` reflect?"). Our `## Summary` + `## Recent changes` + `<details>` collapsible pattern is H2-delimited and HTML-tag-delimited — strictly more robust.

### 5.3. Their type system is MORE rigid than ours

Gbrain's `PageType` enum (person / company / deal / yc / civic / project / concept / source / media) + MECE directory enforcement ("every fact has exactly one primary home") is the opposite of V12's "all pages are pages." Amit's Wikipedia-model instinct is fundamentally incompatible with gbrain's MECE.

### 5.4. Their scale story is smaller than ours in one dimension

Their reported production brain: 17,888 pages with 45,000 in the big deployment. We have 1,015 pages — on that axis, we're smaller. But our raw input is **6,770 emails with 962 threads, multi-author**. Gbrain's compiler is event-per-page; ours is many-events-per-page with thread awareness. Our harder dimension is thread synthesis, not page count.

### 5.5. Their skills system has its own failure mode (Mistake #13)

We've seen our unified-compiler-with-middleware architecture validated across 11 "cycles" of iteration. Switching to a 24-skill resolver-dispatched architecture means rebuilding 11 cycles of domain knowledge as skills + resolver entries. Net loss.

### 5.6. What gbrain has that's genuinely better

- **Hybrid retrieval with LLM rerank** — our weak point, qmd fixes this.
- **Typed-edge extraction with a fail-improve loop** — the implementation pattern, not the code.
- **Retrieval quality benchmark harness** (`gbrain eval`) — we have eval_harness.py with structural metrics but no P@k/MRR; V12-U0 partially addresses this.
- **`gbrain doctor` composite health score** — clean DX, our scripts have the ingredients but not the packaging.
- **Schema-versioned skills / conformance tests** — good discipline, overkill for our smaller tool surface but the idea travels.

**Verdict: absorb qmd wholesale + borrow 2 specific patterns + study their mistakes. Do not migrate.**

---

## 6. What to borrow — priority-ranked, concrete

### Priority 1 — qmd behind `resolve_page` via MCP (HIGH urgency)

**Problem being solved.** `resolve_page` is SQL-ILIKE fuzzy match with a normalize-query step. `src/observability/trace_signals.py:169-198` already instruments the regression — **58% of misses returned alphabetical candidates** (now partially mitigated via V9-U11 recency tiebreaker). Agent-visible symptom: 3-4 resolve_page calls per miss, each burning a tool round.

**Solution.** Point qmd at `/wiki`, wire its MCP endpoint behind `resolve_page`. Feature-flag. qmd's smart chunking respects our H2 boundaries (Overview / Background / Implementation / Impact — the organic archetype).

**Deployment pattern (learned from qmd's own reversals).**
- qmd removed its Ollama HTTP dependency in v0.6.0 in favor of in-process node-llama-cpp with GGUF models. **Run qmd in-process where possible**, not as a separate daemon, to avoid the HTTP-server-that-never-worked anti-pattern gbrain hit with Supabase edge functions.
- qmd's MCP tool renames (search/vector_search/deep_search → `query` in v1.1.0) are backwards-incompatible. **Pin a qmd version** in our dependency manifest; update on a deliberate cadence, not automatically.
- qmd's GGUF models (embedding 300MB + rerank 640MB + query-expansion 1.1GB ≈ 2GB disk, ~2-3GB RAM when warm) are a one-time cost. Cache directory `~/.cache/qmd/`.
- qmd's `CLAUDE.md` warns "Do NOT run automatically" `qmd collection add`, `qmd embed`, `qmd update`. **Our coordinator, not the agent, triggers these**, post-batch.
- qmd's daemon insists on one instance per port. Add supervision (pm2, systemd, or simple `pgrep`) if we run it persistently.
- qmd warm-query latency ~10s, cold ~16s. **This is slower than our current ILIKE at 50ms** — but the hit-rate improvement pays for the latency if calls-per-miss drops.

**Implementation steps.**
- Week 1 day 1-2: `brew install qmd` / `bun install -g @tobilu/qmd`. `qmd collection add wiki /Users/amtagrwl/git/email-knowledge-base/wiki --mask '**/*.md'`. `qmd embed --all`. Manually run `qmd query "seller ISQ"` vs our `resolve_page("seller ISQ")` on the 9 new-joiner-fixture questions.
- Day 3-4: Write `src/compile/tools/qmd_client.py` — async HTTP client to `http://localhost:8181/mcp/query`, same return envelope as existing `resolve_page`. Retain `_normalize_query` (URL scheme / dotted-host / underscore rewrites) as pre-qmd; qmd doesn't know our domain-specific query leaks.
- Day 5: Feature flag `USE_QMD_RESOLVE`. Default off. Ship behind flag.
- Week 2: Smoke `--limit 30`. Compare `is_alphabetical_candidate_list` signal, `calls_per_miss`, `cost_per_email`. If flag-on wins, flip default.

**Gotchas learned from gbrain.**
- **Don't run `qmd embed --all` during a compile batch.** Queue it as a post-batch coordinator step. Gbrain's v0.10.1 learned this: sync auto-embeds CHANGED pages, large syncs defer to `qmd embed --stale`.
- **Test against the real qmd daemon, not a mock.** Gbrain's v0.12.2 PGLite bug is a direct warning — the embedded-vs-real gap eats test coverage.
- **JSONB-style pitfall on our side**: if we persist qmd results in Postgres (we might, for a backlinks table), use proper JSONB encoding and add a grep-guard in CI against `JSON.stringify(x)::jsonb` patterns.

**Cost.** 1 dev-week. ~2-3GB disk + ~2-3GB RAM. Zero cloud spend. Node.js dependency added.

**What we gain.**
- `is_alphabetical_candidate_list` signal → near-zero.
- Calls-per-miss drops 3-4 → ~1.
- Heading-aware chunking lets the agent hit page-section matches (useful for sub-initiative queries).
- Zero API-cost retrieval.
- MCP-native — Phase 3 chatbot slots in naturally.

**Risks.**
- qmd daemon crash mid-batch → feature flag + fallback mitigates.
- Warm-up latency on first query after cold start → acceptable; amortized across batch.
- Node.js in the stack → Docker option available if it bothers.

### Priority 2 — Typed-edge extraction with fail-improve loop (MEDIUM urgency; powers V12-U5)

**Problem being solved.** V12's success metric is wiki-as-graph navigation. V12-U5 ships "Pages that link here." Our current `related:` list is untyped. Gbrain's PR #188 proves typed edges work and adds real search ranking lift.

**Solution, with the fail-improve pattern.**
1. **Schema**: `wiki_edges(from_slug, to_slug, link_type, source_page_id, extracted_at, extracted_by)` with `UNIQUE(from_slug, to_slug, link_type)`. Migration adds the table via `scripts/init_db.py`'s existing schema-apply path. `extracted_by` column tracks `regex | llm | manual` to measure the fail-improve loop.
2. **Regex cascade** in `src/compile/edges.py`. Initial patterns tailored to IndiaMART:
   - `mentions_person` — any `[[people/<slug>]]` in body
   - `supersedes` / `superseded_by` — frontmatter fields + prose ("replaces", "deprecates", "sunsets")
   - `owns_system` — prose near `[[people/...]]` mentions with owner verbs ("X team owns Y", "maintained by Z")
   - `experiment_for` — "A/B test for Y", "experiment on Y"
   - `rolled_out_to` — prose with percentage ("scaled to 50%", "rolled out to 10%")
   - `decided_by` — only when a decisions/ page wikilinks to a person
   - `depends_on` — "blocked by", "requires Y"
   - `related_to` — fallback for existing untyped `related:` entries
3. **LLM fallback**. When the coordinator detects a page with a surprisingly low edge count (p5 of distribution or lower), enqueue an LLM pass that extracts typed edges. Log every LLM output: `edges_log(page_id, extracted_by='llm', patterns_matched, proposed_type, accepted)`.
4. **Regex auto-generation**. Weekly cron reads `edges_log WHERE extracted_by='llm'` and clusters successful LLM edges by `(proposed_type, surrounding_tokens)`. Human reviews clusters; accepted clusters become new regex patterns. Gbrain reported intent classifier 40% → 87% deterministic over time via this loop.
5. **Coordinator hook** `_rebuild_edge_graph(touched_pages)` in `scripts/compile_all.py`, after `_sync_wiki_catalog` and before `_regenerate_landing_surfaces`. Idempotent via UNIQUE; safe to re-run.
6. **V12-U5 rendering**: `## Linked from` section generated by a query on `wiki_edges WHERE to_slug = ?`. Part of `_regenerate_landing_surfaces`.

**Gotchas learned from gbrain.**
- **v0.12.1 "extract N+1" bug** — `gbrain extract` ran one query per page, not batched. If we loop over touched pages calling regex fn per page, that's fine. But if we also write edges one-at-a-time, that's the N+1. **Bulk INSERT with ON CONFLICT DO NOTHING**.
- **v0.9.1 "zero-always-returning health metric"** — make sure our edge-rebuild actually produces edges. Fixture page → rebuild → count > 0. Regression test.
- **Regex false positives** ("works at home") — gbrain uses 240-char context windows AND co-occurrence with known-person-email / known-system-slug spans. Borrow this.
- **Don't silently drop pages on regex failure.** Log it, count it, put in `edges_log`. v0.9.1's "embed failures no longer silent" lesson applies.

**Cost.** 1.5 dev-weeks for regex+coordinator+schema+V12-U5 rendering. Zero LLM cost for the regex path. LLM fallback cost bounded by p5-triggered pages; <$10/month at our cadence.

**What we gain.**
- Directly ships V12-U5 as typed, not untyped.
- Powers wiki-as-graph success metric.
- Unblocks backlink-boost in resolve_page (Priority 4).
- Over time, the fail-improve loop raises deterministic hit rate — we need less LLM over the edge extraction, driving cost down further.

**Risks.**
- Regex over-matching → false-positive edges. Mitigated by co-occurrence gating.
- LLM fallback abuse if we trigger on every page → triggering gate must be strict (p5 lowest edge count).

### Priority 3 — Unified `scripts/doctor.py --fix` CLI (MEDIUM urgency)

**Problem being solved.** Our diagnostic scripts are scattered: `audit.py`, `lint_wiki.py`, `validate_wiki.py`, `wiki_quality_metrics.py`, `check_file_sizes.py`, `check_duplicate_fixtures.py`, `check_one_shot_expiry.py`, `stats.py`, `size_stats.py`. No consolidated "is the repo healthy?" entry point. No CI gate.

**Solution.** Wrap as `scripts/doctor.py` subcommands. Exit codes 0/1/2. Composite health score 0-100 (copy gbrain's `gbrain doctor`). JSON output for CI.

**Gotchas learned from gbrain.**
- **Metrics that always return zero are invisible bugs.** Add a fixture-based test for each metric (Mistake #6 lesson).
- **`--fix` must never delete.** Limit auto-fix to reversible normalisations (wikilink slug case-fold, trailing-space trim). Never remove content.
- **Exit codes must be stable.** Gbrain's doctor returns structured `issues` with `action` strings. Machine-readable. Ours should too so CI workflow can parse.

**Cost.** 1 dev-week of glue + tests.

**What we gain.**
- Single `make health` command.
- PR-blocking CI gate catches drift.
- JSON output for dashboards.

**Risks.** Low. Existing scripts work; this is packaging.

### Priority 4 — Backlink-boost in `resolve_page` ranking (BLOCKED on #2)

**Problem being solved.** Even with qmd, when two pages score equally on hybrid retrieval, which wins? Gbrain's answer: higher inbound-edge count wins.

**Solution.** `src/db/wiki_pages.py:search_pages` gets a `LEFT JOIN (SELECT to_slug, COUNT(*) FROM wiki_edges GROUP BY to_slug)` and folds `inbound_count DESC NULLS LAST` into ORDER BY after `status` and before `last_touched`.

**Cost.** 1 day post-Priority-2.

**Gotcha.** Gbrain's v0.9.1 "stale embeddings can lie" lesson: stale edges can lie too. If a page gets deleted but edges linger, the dead page ranks high. Cascade-delete edges when a page is archived.

### Priority 5 — MCP serve surface (DEFERRED until Phase 3 chatbot)

**What.** Wrap `resolve_page`, `create_entities`, `get_page_summary`, `get_thread_context`, `list_wiki_pages` as MCP tools. FastAPI process in `src/api/`.

**When.** After v12-tight ships and page quality passes new-joiner test.

**Cost.** 1 dev-week.

**Gotchas learned from gbrain's MCP evolution.**
- Start with ONE search-ish tool, not three (Mistake #3).
- Don't run as a cold-start serverless function (Mistake #4).
- Add DoS caps at 100 results, statement_timeout 8s (Mistake #9).
- Path/slug validation for external callers (Mistake #10).
- If a caller is untrusted, flag it structurally, not via a boolean default (Mistake #5).

### Priority 6 — Data-research recipes (DEFERRED until a concrete candidate)

**What.** Gbrain's `data-research` skill extracts MRR/ARR/runway/headcount from investor-update emails into tracked pages. YAML recipes with battle-tested regex.

**When.** When we identify a concrete recurring email pattern — likely Lens usage, ISQ scores, or BuyLeads metrics in standup emails.

**Cost.** 1-2 weeks per recipe.

**Gotcha.** Their `data-research` uses regex with an "extraction integrity rule" (save first, report second) + dedup with configurable tolerance + canonical tracker pages with running totals. Copy this shape when we start.

### Priority 7 — Compiled-truth + timeline STRUCTURAL split — **SKIP**

See Mistake #1. Gbrain's `---`-separator page model caused silent 83% data truncation in production. Their fix is conservative (still `---`-based, just with better parsing). V12 already chose the better model — H2-delimited sections + HTML-tag-delimited collapsibles. **Do not adopt.**

### Priority 8 — Minions durable job queue — **SKIP**

Our batch sizes (30-100 emails) fit `FOR UPDATE SKIP LOCKED` + stale-claim recovery. Minions solves parent-child DAGs with `child_done` inbox — we don't have parent-child work. Gbrain v0.11.0 shipped broken; v0.11.1 fixed it in hours. We don't want to inherit that blast radius.

**Revisit when** batch sizes cross 1,000 emails AND we have truly independent skills running concurrently.

### Priority 9 — Skillify / 24-skill resolver architecture — **SKIP**

Gbrain's own `checkResolvable` is the confession that a 24-skill resolver is fragile (Mistake #13). Our unified compiler + middleware hooks is a structurally different answer to a similar problem. Don't port.

### Priority 10 — 3-tier chunking (recursive/semantic/LLM) — **SKIP**

qmd's single-strategy smart chunking (heading-aware with score-based break point selection) is already good enough for our topic pages' organic design-doc shape. Gbrain's 3-tier dispatch is optimized for compiled_truth vs timeline separation — a structural distinction we don't make.

### Priority 11 — Intent classifier for retrieval (DEFER until Phase 3)

Their zero-latency pattern-match classifier (+21% page coverage, 40%→87% deterministic over time) is great for a chatbot use case where user queries vary in shape. Our compile agent has a *known* query shape — "find the page for this email's concept." No classifier needed. Revisit when we build Phase 3.

### Priority 12 — Signal detector / cheap-parallel-model on ingest — **PROBABLY SKIP**

Gbrain fires Haiku on every user message to capture entity mentions in parallel. Useful if you're a single user typing into an agent. For us, the equivalent would be firing a Haiku pass on every ingested email to pre-populate entity stubs. Our `scripts/backfill_trivial.py` is offline; doing it on ingest saves compile-time entity creation (~3-5s per 10 entities).

**Cost/benefit is marginal.** Compile-time entity creation isn't our bottleneck (LLM tool rounds are). Revisit if cycle-11 traces show entity creation as a hotspot.

---

## 7. What to check in Langfuse to guide this work

All signals scoped to trace observations over the last 30 days unless noted.

### 7.1. Retrieval quality — priority-1 qmd adoption evidence

1. **`resolve_page` hit rate** — fraction with `exists: True`. Segmented by page_type hit. Expected lift post-qmd: +15-20 pts.
2. **`resolve_page` miss → duplicate-create rate** — fraction of misses where the agent then created a page that the dedupe agent later flagged as a merge candidate. Direct causal signal for "retrieval failed."
3. **Calls-per-miss distribution** — p50 / p95 of resolve_page calls before terminal outcome. Current suspicion: 2-3 median, 5-6 tail. Target post-qmd: 1 median, 2 tail.
4. **`is_alphabetical_candidate_list` signal** — already instrumented at `src/observability/trace_signals.py:169`. Trend over 30 days; target near-zero post-qmd.
5. **`auto_corrected_from` adoption rate** — fraction of calls where normaliser rewrote query. Non-zero ⇒ agent is leaking URL / dotted-host slugs. Per-prompt-version trend.
6. **Latency p50/p95 of resolve_page.** Before qmd: SQL-bound, expect <50ms. After: qmd-bound, expect <500ms p95.
7. **Calls-per-email-compiled.** Total tool rounds / emails compiled. Should drop post-qmd.

### 7.2. Concept-vs-thread — V12 framing signals (priority 2 context)

8. **Thread-subject H2 rate** — fraction of pages written in 30d with any H2 matching `^(Launch Announcement|Vote of thanks|Bug report|Re:|FW:)$`. V12-U0 scorer should report per-page. Currently 14 topics contain these per the archetype audit.
9. **Multi-email concept rate** — fraction of topic pages with `source_threads:` cardinality ≥2 after their second compile. Tracks concept vs one-shot. Should rise post-V12-U1.
10. **5W coverage** — proxy: fraction of topic pages mentioning customer segment + metric + surface + stakeholder. Calibration signal, not grading.
11. **Footnote density** (post V12-U3) — `[^msg-` markers per 100 words.

### 7.3. Wiki-as-graph health — priority-2 evidence

12. **Orphan rate** — topic pages with 0 inbound `mentions_person` / `mentions_system` / `related_to` edges. Reflects V12 success metric directly.
13. **Wikilink-per-page distribution** — p50, p95. V12-U0 scorer wants ≥3 wikilinks per page as quality signal.
14. **Broken-wikilink count** — already countable via `scripts/fix_broken_wikilinks.py`. Publish as per-run signal.
15. **Person-page activation rate** — fraction of 589 person pages with ≥1 inbound `mentions_person`. Candidates for prune.
16. **Reverse-reachability from `home.md`** — BFS fraction of topic pages reachable ≤3 hops. `tests/test_new_joiner_3domains.py` does this for 9 questions; generalise.

### 7.4. Supersession / lifecycle

17. **Supersession-language detection rate** — fraction of batches where agent set `status: superseded` despite email containing deprecation language (heuristic: "replace", "deprecat", "rolled back", "reverted"). False-negative rate.
18. **Superseded-page count trend** — currently 2 topics + 1 system + 2 people; expect growth.
19. **Stale-active pages** — `status: active` but no touch >60 days. Auto-archive candidates.

### 7.5. Compile economics

20. **Cost per email compiled.** Current: $0.156 (cycle-10 smoke). Target post-v12: <$0.10.
21. **Tool rounds per email.** Validates "fewer resolve_page calls" hypothesis.
22. **Trivial-skip vs already-captured vs compiled distribution.** V12 expects trivial_skip 50% → 20%.

### 7.6. Instrumented signals (use as-is)

All already in `src/observability/trace_signals.py`:
- `is_alphabetical_candidate_list`
- `is_glob_timeout`
- `extract_reviewer_merge_count`
- `GATE_REJECT_PAT`
- `REVIEWER_VERDICT_PAT`
- `AUTO_CORRECT_PAT`

---

## 8. What to check in the wiki (pure-file queries, no Langfuse)

Run once before v12-tight, once after.

### 8.1. Corpus shape

1. **Archetype distribution** — rerun the `topic-page-structure-archetypes-2026-04-18.md` classifier. Track design_doc / canonical_v9u1 / thread_narrative / bug_incident shares. Watch design_doc% rise post-V12-U1 while thread_narrative% drops.
2. **H2 frequency top-30** — watch thread-subject leaks drop from 14 pages.
3. **Page-length distribution** — stubs (<200 words) are bottom of V12-U0 scorer.
4. **Lead-opener distribution** — current: Overview 111 / Business Objective 52 / TL;DR 21 / Summary 1. Watch Summary rise post V12-U1.
5. **`source_threads:` cardinality distribution** — one-shot (=1) vs multi-email concept (≥2). Mean should rise post V12-U1.

### 8.2. Graph health (post Priority 2)

6. **Edge-type cardinality** — if `related_to` dominates 10:1 over everything else, typed extraction isn't adding value.
7. **Inbound-edge distribution per page** — p50, p95. Zero-inbound = orphan.
8. **Typed-edge coverage by page type** — systems should have `owns_system`; topics should have `related_to` + occasional `supersedes`.

### 8.3. Person-page hygiene

9. **Person pages with 0 outbound wikilinks** — stub-only candidates for prune.
10. **Person pages with 0 inbound `mentions_person`** — dead-weight, hidden from nav but still in catalog.
11. **`canonical_user_email` missing** — pre-V9-U5 migration residue. `migrate_entities_to_people.py` already handles; verify clean.

### 8.4. Frontmatter drift

12. **Pages with no `last_compiled`** — never touched by compile flow.
13. **Pages with stale `updated_by:` model** — pre-model-pool residue.
14. **Slug-prefix / domain mismatch** — V11-U8 issue: `bl-`-prefixed slugs with `domain: marketplace-discovery`. Publish as report.

### 8.5. Wikilink integrity

15. **Broken wikilinks** — publish count.
16. **Self-referential wikilinks** — defect class.
17. **Reciprocity violations** — A's `related:` lists B; B's doesn't list A.

### 8.6. V12-U0 scorer inputs

18. **`## Why it matters` coverage** — fraction of topics with ≥2-sentence section (currently 0 per archetype).
19. **Recent-changes age** — date of newest bullet per page. Freshness signal.
20. **Sources cardinality distribution** — concepts (>5) vs one-shots (1).

---

## 9. Sequencing — what to do first

### Week 0 — V12-U0 scorer (BLOCKING)
Per `docs/audits/v12-north-star-2026-04-19.md:306-327`. Do not reorder.

### Week 1-2 — qmd parallel track (Priority 1)
Independent of V12-U1-U4 prompt work. Feature-flagged. Smoke at week 2.

### Week 2-3 — V12-U1-U4 (per v12 doc)
Prompt reframe + 5W + footnotes + revision style. Uses V12-U0 anchors.

### Week 4 — smoke V12-tight `--limit 30`
Measure: scorer delta, retrieval signal deltas, cost per email.

### Week 5 — Priority 2 (typed edges + V12-U5 backlinks)
Schema + coordinator hook + regex cascade + `## Linked from` rendering. Fail-improve loop landing: regex-first, LLM fallback logged, weekly regex auto-generation review.

### Week 6 — Priority 3 (doctor.py) + Priority 4 (backlink-boost)
Glue + single ORDER BY change.

### Week 7+ — evaluate Phase 3 readiness
Chatbot scoping if quality passes. Priority 5 (MCP serve) becomes relevant.

### Deferred indefinitely
Priorities 6 (recipes), 11 (intent classifier), 12 (signal detector), 7-10 (hard skips).

---

## 10. Open questions for Amit

1. **qmd daemon placement.** Happy to run `qmd mcp --daemon` as a launchd/systemd service alongside our Python stack? Alternative: Docker. Native is simpler for dev; Docker is cleaner for CI.
2. **Edge type seed list.** Proposed: `mentions_person / supersedes / owns_system / experiment_for / rolled_out_to / decided_by / depends_on / related_to`. Any domain-specific types I'm missing — particularly growth-monetization (`revenue_attributed_to`?) and trust-safety (`threat_vector`? `regulated_by`?).
3. **Backlink-boost vs recency.** Current: recency-first tiebreak. Preference stays recency-first with backlinks as secondary tiebreak? Or flip?
4. **Data-research recipe candidate.** Lens usage over time / ISQ scores / BuyLeads metrics — if one of these is a RECURRING email pattern (not one-off), I can scope Priority 6 around it. Which is most "every-week email with numbers"?
5. **Phase 3 chatbot timing.** V12 says ~6 weeks. If v12-tight smokes cleanly at week 4, are we moving the timeline up? Affects Priority 5 urgency.
6. **Node.js in our stack.** qmd is TypeScript. Do we want it as a supervised background service on our dev boxes + CI, or do we wrap in Docker from day 1 to keep the Python surface clean?
7. **Statement-timeout on Postgres.** Gbrain defaults to 8s as DoS defense-in-depth. Worth adding to our connection config now? Our longest legitimate query (from `scripts/stats.py`) — does it exceed 8s anywhere?

---

## 11. The one-liner, revised

> Gbrain is a fast-iterating personal-KB reference that shipped a data-loss bug 11 releases deep in the exact page model we rejected for v12. Take their typed-edge extraction pattern and their fail-improve loop. Take qmd for retrieval. Study their mistakes — especially the `---`-separator bug, the PGLite-hides-Postgres-bugs gap, the trust-boundary-as-boolean gap, and the skills-system-needed-its-own-validator confession. Keep everything else we have.

---

## 12. References

### Primary sources
- Gbrain CHANGELOG v0.1.0 → v0.12.2: [raw.githubusercontent.com/garrytan/gbrain/master/CHANGELOG.md](https://raw.githubusercontent.com/garrytan/gbrain/master/CHANGELOG.md)
- Gbrain README: [github.com/garrytan/gbrain](https://github.com/garrytan/gbrain)
- Gbrain DeepWiki: [deepwiki.com/garrytan/gbrain](https://deepwiki.com/garrytan/gbrain)
- Gbrain PR #188 (typed-edge graph): [github.com/garrytan/gbrain/pull/188](https://github.com/garrytan/gbrain/pull/188)
- qmd README: [github.com/tobi/qmd](https://github.com/tobi/qmd)
- qmd DeepWiki: [deepwiki.com/tobi/qmd](https://deepwiki.com/tobi/qmd)

### Our source docs referenced
- [`docs/NORTH-STAR.md`](../NORTH-STAR.md)
- [`docs/audits/v12-north-star-2026-04-19.md`](../audits/v12-north-star-2026-04-19.md)
- [`docs/audits/topic-page-structure-archetypes-2026-04-18.md`](../audits/topic-page-structure-archetypes-2026-04-18.md)
- [`docs/audits/cycle-10-smoke-30-2026-04-18.md`](../audits/cycle-10-smoke-30-2026-04-18.md)
- `src/observability/trace_signals.py` — existing instrumented signals
- `src/db/wiki_pages.py:search_pages` — current resolve_page ranking
- `src/compile/tools/raw_access.py:resolve_page` — current resolve_page tool
- `scripts/compile_all.py` — coordinator + post-batch hooks
