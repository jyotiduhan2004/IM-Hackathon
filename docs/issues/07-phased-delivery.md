# Issue: Phased Delivery Plan with Acceptance Criteria

**Labels**: `documentation`, `planning`

---

## Phase 0 — "It works" ✅ COMPLETE (2026-04-13)

**Goal**: Run two commands, get a browsable wiki compiled from last month of email.

| # | Task | Status |
|---|---|---|
| 1 | Project scaffolding (pyproject.toml, Makefile, .gitignore, config) | ✅ |
| 2 | Gmail OAuth + email fetcher | ✅ |
| 3 | Email parser (→ raw/ markdown with frontmatter) | ✅ |
| 4 | Attachment handler (code shipped; `--skip-attachments` default for now) | ✅ |
| 5 | Wiki compiler (Deep Agents + LiteLLM) | ✅ |
| 6 | Supersession rules in compiler prompt | ✅ |
| 7 | Wiki lint + validator + auto-fix | ✅ |
| 8 | CLAUDE.md agent schema | ✅ |
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
ls wiki/topics/     # has pages
cat wiki/index.md   # has catalog
uv run python scripts/lint_wiki.py  # reports clean
```

---

## Phase 1 — "It's live" (Week 1–2)

**Goal**: New emails auto-ingested and compiled within minutes of arrival.

| # | Task | Status |
|---|---|---|
| 1 | Gmail watch + Pub/Sub setup | ⬜ |
| 2 | Watch auto-renewal (every 6 days) | ⬜ |
| 3 | FastAPI webhook `/webhook/gmail` | ⬜ |
| 4 | Incremental ingestion (historyId tracking) | ⬜ |
| 5 | Thread-aware processing | ⬜ |
| 6 | Quiet period batching (30 min since last activity) | ⬜ |
| 7 | Auto-commit to git after compilation | ⬜ |
| 8 | Image/attachment captioning invoked by default | ⬜ |

### Definition of Done
- Send a test email to the mailing list
- Within 5 minutes, it's in `raw/` and affected wiki pages are updated
- No manual intervention

---

## Phase 2 — "It's smart" (Week 3–4)

**Goal**: Typed relations, confidence scoring, hybrid search, wiki UI.

| # | Task | Status |
|---|---|---|
| 1 | Typed relations (supersedes, amends, clarifies, retracts, conflicts_with) | ⬜ |
| 2 | Confidence scoring per wiki claim | ⬜ |
| 3 | LLM-powered lint (semantic contradiction detection) | ⬜ |
| 4 | Lint auto-fix for high-confidence issues | ⬜ |
| 5 | Full-text search over wiki (grep-based or ripgrep) | ⬜ |
| 6 | MkDocs Material site generation | ⬜ |
| 7 | Human review queue for low-confidence changes | ⬜ |

### Definition of Done
- Wiki is browsable via local MkDocs web UI
- Search returns relevant pages with ranking
- Supersession chain navigable (current → what it replaced → original)
- Review queue has actionable items

---

## Phase 3 — "It talks back" (Month 2)

**Goal**: Chatbot agent answering questions with cited sources.

| # | Task | Status |
|---|---|---|
| 1 | Query agent with hybrid retrieval | ⬜ |
| 2 | Source citations + recency labels | ⬜ |
| 3 | Knowledge accumulation (Q&A → wiki pages) | ⬜ |
| 4 | Conflict surfacing in answers | ⬜ |

### Definition of Done
- Ask "What's the current reimbursement policy?" → correct answer with citations
- Ask "Has the travel allowance changed?" → timeline of changes
- Ask about contested topic → both positions + link to conflict page

---

## Phase 4 — "It scales" (Month 3+)

**Goal**: Multi-user, deployed, production-grade.

| # | Task |
|---|---|
| 1 | Multi-user access, team wiki |
| 2 | Postgres + PGroonga for structured storage + search |
| 3 | GCP Cloud Run deployment |
| 4 | Multiple mailing lists support |
| 5 | Embedding-based semantic search (1000+ pages) |
| 6 | Eval suite with golden Q&A test set |

---

## Milestone Summary

```
Phase 0 (Day 1)      → "I have a wiki from my emails"             ← CURRENT
Phase 1 (Week 1-2)   → "It updates itself when new emails arrive"
Phase 2 (Week 3-4)   → "It's searchable and has a web UI"
Phase 3 (Month 2)    → "I can ask it questions and get cited answers"
Phase 4 (Month 3+)   → "The whole team uses it in production"
```
