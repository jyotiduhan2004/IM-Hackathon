# Backlog

> Work items in pursuit of [`docs/NORTH-STAR.md`](./NORTH-STAR.md). The North Star describes what the wiki should look like; this file describes the tooling and infrastructure to get there.

The full 2,359-line pre-proposal BACKLOG is preserved at [`docs/archive/2026-04-15-pre-proposal/BACKLOG.md`](./archive/2026-04-15-pre-proposal/BACKLOG.md). This file keeps only what's still live after the 2026-04-15 consolidation.

---

## Phase 1 — Ship the concept-page wiki (in flight)

Delivers the North Star's structure, 8 domain hubs, page templates, status badges, and progressive disclosure. Broken into 5 PRs (A–E) that can land in any order once the underlying tooling lands.

### PR-A: Doc consolidation (~landed as part of proposal merge)
- ✓ Archive superseded strategy docs (`docs/archive/2026-04-15-pre-proposal/`)
- ✓ Introduce `docs/NORTH-STAR.md`
- ✓ Rewrite `CLAUDE.md` for new 4+2 page taxonomy
- ✓ Rewrite `README.md` to match direction
- ✓ Ratify `docs/proposal/NORTH-STAR-DRAFT.md` — merging this PR locks the 21 decisions; `NORTH-STAR-DRAFT.md` remains as the decisions log, `docs/NORTH-STAR.md` is the canonical spec

### PR-B: Structural compiler changes
Deliver "concept pages, not filing cabinet".
- **Trivial-message filter** at ingest — cheap pre-pass; messages <50 substantive words, pure acks, calendar invites → `compile_state=trivial`, skipped (~30-50% of mailing-list traffic). This is the single biggest quality lever.
- **Topic-as-concept prompt rule** — compiler MUST call `resolve_page` before writing; default is "grow existing page", not "create new". Uses `find_new_sources` + `resolve_page` together.
- **Decision extraction during topic compile (lazy + active)** — while updating a topic page, the agent scans the email for decision-shaped content. Creates a `decisions/...` page only when a meaningful change is found (e.g. "5% → 50%", "deprecating X"). Quality bar rules in prompt. Lazy: no proactive decision-creation.
- **People pages lazy + demoted** — only create when a topic wikilinks to a person. Hidden from primary nav. Existing 462 entity pages: keep files, remove from nav.
- **Status + supersession detection** — compiler sets `status: superseded` + `superseded_by: <slug>` when it detects deprecation language in emails.
- **Drop `timelines/` + `conflicts/`** — remove from schema/validator/prompts. Existing (zero) pages archived.

### PR-C: Post-compile agents + tooling gaps
Deliver the multi-stage pipeline.
- **`wiki_merge_pages` tool** — the merge primitive the dedupe agent uses.
- **Dedupe agent** — separate agent run after each compile batch. Uses embedding + slug/tag similarity; proposes merges; executes via `wiki_merge_pages`.
- **Domain rollup agent** — regenerates all 8 `wiki/domains/*.md` pages on every pipeline run. Rollup contents: definition, most-active topics, key systems, recent decisions, navigable index.
- **Glossary refresh agent** — scans corpus for acronyms + jargon, writes 1-2 sentence definitions, regenerates `wiki/glossary.md`.
- **Status sweep agent** — detects supersession the main pass missed; validates `superseded_by` wikilinks; updates `wiki/changes.md`.
- **Synthesis self-review pass** — extend PR #73 (`check_my_work` format pass) with a synthesis critique: "does this page synthesize or just list emails?" Agent rewrites if it fails.
- **Agent tooling gaps** (highest-leverage, from the tool-audit):
  - `get_page_summary(path) -> {type, status, sources_count, last_compiled, h2_headings, snippet}` — replaces N `read_file` calls during discovery
  - `get_thread_context(thread_id) -> {message_count, date_range, subjects, participants, already_cited_pages}` — single SQL replaces N `read_file` calls
  - `find_related_pages(slug_or_email) -> list[path]` — reverse-index lookup via `message_touched_pages`
  - `propose_page(frontmatter, body) -> {validated, errors, preview}` — dry-run validation
  - `list_entity_by_email(email)` — lookup without side-effect (current `create_entity` always creates)
  - **Remove `list_uncompiled_emails` from agent tool list** (5 min) — coordinator owns it; agent returns a 1.3M-token footgun.
  - **Paginate/summarize `list_wiki_pages`** — default returns counts by category; explicit arg for full list.

### PR-D: Viewer + landing pages
Deliver the home page layout from the North Star.
- **`wiki/home.md`** as real landing page — 2-sentence intro + 8 domain cards (4x2 grid) + recent activity ribbon + search bar + glossary card
- **`wiki/topics/index.md`** — domain-grouped TOC
- **`wiki/systems/index.md`** — real systems list (post-prune)
- **Status badge rendering** — Material theme admonition for `active/superseded/archived` at the top of each page
- **Prune thin systems** — one-shot cleanup: systems with body <500B AND no inbound wikilinks → archive (27 candidates per wiki audit)
- **Domain card styling** in MkDocs Material

### PR-E: Unified pipeline command
- `make pipeline` → `ingest → compile → dedupe → domain rollup → glossary refresh → status sweep`
- Flags: `--skip-dedupe`, `--skip-rollup`, `--only compile` for partial runs
- Clean logs per stage

### Agent scaffolding (independent track, folds into PR-B or PR-C)
Investigation from the 2026-04-13 glm-4.6-failure-mode analysis. Relevant to compile quality (root cause of the filing-cabinet pattern: agent loops, context bloat).
- **Step-count reminder middleware** — `pre_model_hook` injects "you've used N of expected M tool calls" every N steps. Direction: LangGraph `pre_model_hook`.
- **Tool-call de-duplication hooks** — detect N identical calls in a batch; inject "you already did this, move on".
- **Context pruning** — after N steps, drop raw file bodies from the running history.
- **Stronger exit discipline in prompts** — the agent needs clearer "when to stop" signals.

### Governance debt
- **CI workflow** (`.github/workflows/ci.yml`) — `uv run ruff check` + `uv run pytest` on every PR. No CI today; regressions land silently.
- **CHANGELOG discipline** — enforce via PR guardrails going forward. Stop backfilling.

---

## Phase 2 — Live + multi-user + richer discovery

Delivers the Phase-2 end-state from the North Star (always-up-to-date, multiple lists, sub-page hierarchy, reports/impact sections).

- **Live ingestion** — Gmail watch + Pub/Sub + FastAPI webhook → wiki updates within 1 hour of an email landing. Formerly `docs/issues/08-phase1-live-ingestion.md` (archived; promote back when picked up).
- **Multiple mailing lists** — one cursor per list; shared catalog; per-list ingest config. Design-time question: does each list get its own domain tagging, or do lists just feed the same 8 domains?
- **Domain meta-agent** — runs weekly (or per-N-new-emails). Reviews corpus; proposes hub splits / merges / adds. Outputs suggestions for owner approval; no auto-changes.
- **Multi-level topic hierarchy** — parent page + sub-pages for topics that outgrow single-file (e.g. Seller ISQ with 10+ active initiatives).
- **Reports / impact sections** — topics grow a `## Impact` section with quantitative follow-up (metrics, adoption rates, outcomes).
- **QMD semantic search** (https://github.com/tobi/qmd) — local-first three-stage retrieval (BM25 → vector → local LLM rerank). Two use cases:
  1. Agent tool during compile — replaces slug-guess loop; agent calls `qmd search "voice eval"` and gets top-3 existing pages ranked by semantic relevance. Directly fixes duplicate-page problem.
  2. Replaces MkDocs built-in lunr search — better on a ~700-page wiki with real prose. Ship after synthesis self-review is in place (otherwise QMD indexes 95% frontmatter).
- **AgentMiddleware migration** — replace hand-rolled coordinator hooks in `scripts/compile_all.py` (`_mark_batch_compiled`, `_stamp_recently_modified_pages`, `_append_batch_log`, etc.) with LangChain v1's native `AgentMiddleware` pattern. Colocates verification with execution; opens the door to coordinator-driven re-invocation with evidence feedback. See [LangChain guardrails docs](https://docs.langchain.com/oss/python/langchain/guardrails).
- **Parallel compile** — `scripts/compile_parallel.py` exists as a half-finished draft (`b6368d6`). Pick up after Phase-1 quality stabilizes. Race surface is narrow under coordinator-owned state: only `write_file` on wiki pages. Thread-grouping already serializes same-thread work; the remaining risk is cross-thread writes to the same topic page — needs measurement + a lock per page-slug.
- **Most-cited topics / entities panel** on `home.md` — "Top 10 topics by recent activity" block, driven by `message_touched_pages` join. Ship after corpus is de-noised.
- **Eval suite** — blind evaluation of compile quality: recall of facts from sources, wikilink correctness, supersession accuracy, synthesis quality. Reference: [Anthropic — Evals for agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

---

## Phase 3 — Askable + writable wiki

Delivers the Phase-3 end-state (queryable interface, human edits with conflict resolution, MCP-exposed tools).

- **"Ask this wiki" interface** — Postgres + LLM query layer returning cited answers. Probably FastAPI endpoint + a small frontend. Pairs with QMD for retrieval.
- **Manual edit workflow** — markdown PRs against `wiki/` with edit-vs-regen conflict resolution. Until this ships, compiler is read-only.
- **Inline citations for sensitive claims** — `[^1]` pattern or similar, linked to source email excerpts.
- **Trust signals per page** — freshness (last compiled), source reliability (N sources, age), human-verified flag.
- **MCP server** exposing wiki query + raw grep + catalog lookup as tools for downstream consumers. Reference: [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp).
- **Chat-with-wiki** — mobile UX. "What's the latest on iOS fix?" → cited answer.

---

## Research / reading list (digest, pull out concrete work)

Referenced from multiple places; treat as context, not ship items.

- [Anthropic — Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic — Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- [Anthropic — Effective context engineering for agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic — Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Karpathy — LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [LangChain — Agent guardrails](https://docs.langchain.com/oss/python/langchain/guardrails)
- [QMD by Tobi Lütke](https://github.com/tobi/qmd)
- [OpenRouter — prompt caching best practices](https://openrouter.ai/docs/guides/best-practices/prompt-caching#provider-sticky-routing)

---

## Already shipped (for context — don't re-promote)

- Postgres queue/catalog (messages, wiki_pages, message_touched_pages, compile_runs, ingest_cursors, compile_attempts)
- Coordinator-owned compile state (PR #31 and downstream)
- Thread-aware compilation
- Entity identity by email (deterministic slug)
- Per-batch model A/B pool + auto-exclusion guard (PR #74)
- Langfuse integration (bounded timeouts, opt-in)
- Prompt-caching verification + stats
- Per-batch compile stall detection (`--batch-timeout`, PR #50)
- Per-tool-call telemetry to Postgres + JSONL fallback (PR #62)
- `log_insight` tool (PR #61)
- `resolve_page` tool (PR #57)
- `find_new_sources` + paginated DB query (PR #56)
- `write_draft_page` tool + `_drafts/**` exclude (PR #63)
- Auto-stub cleanup (PR #68)
- Entity evidence gate (PR #67)
- Compact metadata banner (PR #49)
- Collapsed `<details>` Sources + entity cap (PR #60)
- Attachment placeholder in viewer (PR #48)
- Section-template validator (PR #58)
- Explicit MkDocs nav (PR #59)
- Wiki quality metrics (PR #54)
- Stuck-at-compiled-count cleanup (auto-stub)
- Miscategorized humans in systems/ relocated (PR #52)
- Post-batch auto-format + validate (PR #71)
- Pre-commit hooks (check-merge-conflict + check-ast) (PR #72)
- `--deploy` flag on compile_all (PR #70)
- `check_my_work` format self-review (PR #73, in flight)
- Tool/coordinator split doc section in CLAUDE.md (PR #75, in flight)

---

## Open tactical items (housekeeping)

- Legacy entity pages under non-canonical slugs (`-clean`, `-v2`, numeric-suffix duplicates) — manual merge batch after dedupe agent ships.
- `compile_attempts` row orphans from `not_cited` messages — addressed in PR #74; verify by watching the `not_cited`-but-no-attempt-row metric over 1 week.
- `Langfuse` server-side OTLP hang (issue #17) — blocker for default-on Langfuse. `LANGFUSE_ENABLED=false` by default until resolved.
- Broken wikilinks between renamed pages (legacy from before `resolve_page` shipped) — one-shot fix script.
- Policy pages at 1 count — either promote known policies or accept that policies are rare.
