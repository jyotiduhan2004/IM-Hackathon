# Issue: Phased Delivery Plan with Acceptance Criteria

**Labels**: `documentation`, `planning`

---

## North star

Build a polished internal wiki that:

- preserves references and provenance
- keeps up with a fast-moving company
- reads like curated knowledge, not like one-email-one-page output
- is organized around topics and systems first, with people pages as support

This changes the milestone order. Compile quality and wiki information
architecture come before live-ingest automation.

---

## Phase 0 — Working Pipeline ✅ COMPLETE (2026-04-13)

**Goal**: Ingest backlog mail, compile it into a wiki, and browse the result locally.

| # | Task | Status |
|---|---|---|
| 1 | Project scaffolding (pyproject.toml, Makefile, .gitignore, config) | ✅ |
| 2 | Gmail OAuth + email fetcher | ✅ |
| 3 | Email parser (→ raw/ markdown with frontmatter) | ✅ |
| 4 | Attachment handler (code shipped; `--skip-attachments` default for now) | ✅ |
| 5 | Wiki compiler (Deep Agents + LiteLLM) | ✅ |
| 6 | Supersession rules in compiler prompt | ✅ |
| 7 | Wiki lint + validator + auto-fix baseline | ✅ |
| 8 | CLAUDE.md / AGENTS.md agent schema | ✅ |
| 9 | Langfuse tracing integration (opt-in; disabled by default) | ✅ |
| 10 | CLI scripts (ingest, compile, compile_parallel, lint, validate, snapshot) | ✅ |
| 11 | MkDocs Material + roamlinks wiki viewer | ✅ |
| 12 | LiteLLM budget check integrated into compile | ✅ |
| 13 | Pre-compile auto-snapshot for safe iteration | ✅ |
| 14 | Hard validator (exits non-zero on corruption) | ✅ |
| 15 | CHANGELOG.md living record of fixes | ✅ |

### Definition of Done

```bash
cp .env.example .env && vim .env  # add API keys, mailing list
uv sync
uv run python scripts/ingest_backlog.py --days 30
uv run python scripts/compile_all.py
cat wiki/index.md   # has catalog
uv run python scripts/lint_wiki.py
```

---

## Phase 1 — Make It A Real Wiki ← CURRENT

**Goal**: The output should feel like a trustworthy internal wiki, not a dump of generated pages.

| # | Task | Status |
|---|---|---|
| 1 | Move rendered provenance off markdown frontmatter and into the catalog/render layer | ⬜ |
| 2 | Make the wiki topic-first: hubs, rollups, glossary, and stronger index navigation | ⬜ |
| 3 | De-noise entity pages: drop CC-only noise, reduce stub proliferation, treat people as support pages | ⬜ |
| 4 | Strengthen compile guardrails: timeout, corruption checks, repair/self-healing loop | ⬜ |
| 5 | Fix duplicate pages, miscategorization, and dead-end categories | ⬜ |
| 6 | Surface freshness/status/ownership-style metadata where it helps readers | ⬜ |
| 7 | Keep docs, backlog, and roadmap coherent with what is actually shipped | ⬜ |

### Definition of Done

- A reader can answer "what is going on with X?" by browsing topics, systems,
  and rollups without guessing filenames.
- Topic pages are denser and more trustworthy than entity pages.
- References are preserved, but no important page is dominated by frontmatter noise.
- The wiki feels curated and navigable even before search or chat exists.

---

## Phase 2 — Keep Up With Company Speed

**Goal**: Once compile quality is good enough, keep the wiki current automatically.

| # | Task | Status |
|---|---|---|
| 1 | Gmail watch + Pub/Sub setup | ⬜ |
| 2 | Watch auto-renewal (every 6 days) | ⬜ |
| 3 | FastAPI webhook `/webhook/gmail` | ⬜ |
| 4 | Incremental ingestion (`historyId` tracking) | ⬜ |
| 5 | Quiet-period thread compilation | ⬜ |
| 6 | Attachment/image handling on by default | ⬜ |
| 7 | Automatic compile/reporting loop that does not regress wiki quality | ⬜ |

### Definition of Done

- New mail lands in `raw/` and updates the right wiki surfaces without manual intervention.
- The automation does not flood the wiki with low-value pages or noisy updates.
- The system can keep up with new mail while preserving curation quality.

---

## Phase 3 — Searchable And Askable

**Goal**: Add search and QA on top of a wiki that is already worth searching.

| # | Task | Status |
|---|---|---|
| 1 | Full-text / hybrid search over real wiki knowledge | ⬜ |
| 2 | Typed relations, confidence, and contradiction-aware retrieval | ⬜ |
| 3 | Query agent with citations + recency labels | ⬜ |
| 4 | Knowledge accumulation from good Q&A back into the wiki | ⬜ |

### Definition of Done

- Search surfaces the right topic pages and rollups, not just filename matches.
- Ask "what changed?" or "what is current?" and get a cited answer rooted in the wiki.
- Superseded and contested knowledge is explicit.

---

## Phase 4 — Team-Scale System

**Goal**: Make the polished wiki operational for a wider team.

| # | Task |
|---|---|
| 1 | Multi-user/team workflow |
| 2 | Deeper review/eval loops for quality control |
| 3 | Production-grade deployment and ops |
| 4 | Multiple mailing lists support |
| 5 | Richer semantic/graph navigation at larger scale |

---

## Milestone Summary

```
Phase 0           → "I can compile email into a browsable wiki"         ✅
Phase 1 (current) → "It feels like a real internal wiki"
Phase 2           → "It stays current as the company moves"
Phase 3           → "I can search and ask questions over it"
Phase 4           → "The team can rely on it"
```
