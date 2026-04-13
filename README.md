# Email Knowledge Base

A living knowledge base that ingests emails from a Gmail/Google Workspace mailing list
and compiles them into an interlinked markdown wiki using LLM-powered compilation.

Based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
(April 2026), extended for email: thread awareness, supersession detection, and
incremental compilation triggered by new mail events.

## What it does

```
Gmail mailing list → ingest → raw/ (immutable emails) → compile → wiki/ (knowledge base)
```

1. **Ingests** emails from a Gmail mailing list (OAuth, supports backlog + live)
2. **Parses** each email into structured markdown with YAML frontmatter
3. **Compiles** raw emails into interlinked wiki pages using an LLM agent (Deep Agents + LiteLLM)
4. **Maintains** the wiki incrementally — new emails update only affected pages
5. **Detects** when newer emails supersede older guidance
6. **Lints** the wiki for contradictions, stale claims, and orphan pages

The result is a browsable folder of interlinked markdown files — your team's email
knowledge, compiled and cross-referenced. Open in VS Code, Obsidian, MkDocs, or any
markdown viewer.

## Why not just RAG?

> "Traditional RAG has the LLM rediscovering knowledge from scratch on every question.
> There's no accumulation." — [Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

Classic RAG embeds email chunks and retrieves at query time. For ~100 emails/day over
months, this breaks down:
- **No synthesis**: 5 emails about the same topic produce 5 separate chunks, never combined
- **No supersession**: Old policy and new policy both rank equally in vector search
- **No accumulation**: Every question re-derives the same knowledge

This project **compiles** knowledge at ingest time. The wiki is the accumulated, cross-referenced,
supersession-aware state of everything the emails contain. Queries search pre-compiled
knowledge — not raw fragments.

## Quick start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Gmail API credentials ([setup guide](#gmail-api-setup))
- LLM API access via LiteLLM (OpenAI, Anthropic, or any supported provider)

### Install

```bash
git clone https://github.com/indiamart-ai/email-knowledge-base.git
cd email-knowledge-base
uv sync
cp .env.example .env  # edit with your API keys
```

### Gmail API setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use existing)
3. Enable the Gmail API
4. Create OAuth 2.0 credentials (Desktop application type)
5. Download as `credentials.json` to the project root
6. First run will open a browser for OAuth consent → saves `token.json`

### Usage

```bash
# 1. Pull last 30 days of mailing list emails into raw/
uv run python scripts/ingest_backlog.py --days 30

# 2. Compile all unprocessed raw emails into wiki pages
uv run python scripts/compile_all.py

# 3. Browse the result
ls wiki/topics/     # project/product pages
ls wiki/entities/   # people and team pages
ls wiki/policies/   # current policies with history
cat wiki/index.md   # master catalog

# 4. Lint the wiki for issues
uv run python scripts/lint_wiki.py
```

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Gmail Mailing List                         │
└──────────┬───────────────────────────────────────────────────┘
           │  Gmail API (OAuth)
           ▼
┌──────────────────────────────────────────────────────────────┐
│                   INGEST PIPELINE                             │
│                                                               │
│  gmail.py ─────► parser.py ─────► attachments.py              │
│  Fetch emails    Convert to       Extract & store             │
│  from Gmail      raw markdown     attachments + images        │
│  (backlog or     with YAML                                    │
│   live watch)    frontmatter                                  │
└──────────┬───────────────────────────────────────────────────┘
           │  Writes to raw/
           ▼
┌──────────────────────────────────────────────────────────────┐
│                   RAW STORAGE (raw/)                          │
│                                                               │
│  Immutable. One .md file per email.                           │
│  YAML frontmatter: from, to, date, thread_id, subject,       │
│    message_id, in_reply_to, labels, has_attachments           │
│  Attachments in raw/attachments/{message_id}/                 │
│  Images captioned at ingest time via vision model             │
│                                                               │
│  Naming: YYYY-MM-DD_{subject-slug}_{msg-id-short}.md         │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│              COMPILE (Deep Agents + LiteLLM)                  │
│                                                               │
│  compiler.py ──► prompts.py ──► relations.py                  │
│  Deep Agents     Compilation     Supersession &               │
│  workflow:       prompts for     conflict detection            │
│                  each page type                                │
│  1. Read new raw emails                                       │
│  2. Classify: what topics/entities/policies are mentioned?    │
│  3. For each affected wiki page:                              │
│     a. Read existing page (if any)                            │
│     b. Merge new information                                  │
│     c. Detect supersession (new overrides old)                │
│     d. Write updated page                                     │
│  4. Update index.md and log.md                                │
│  5. Flag contradictions → wiki/conflicts/                     │
│                                                               │
│  All LLM calls traced via Langfuse                            │
└──────────┬───────────────────────────────────────────────────┘
           │  Writes to wiki/
           ▼
┌──────────────────────────────────────────────────────────────┐
│                   WIKI (wiki/)                                │
│                                                               │
│  wiki/index.md        Master catalog of all pages             │
│  wiki/log.md          Chronological ingest/compile log        │
│  wiki/topics/         One page per project/product/initiative │
│  wiki/entities/       People, teams, products, systems        │
│  wiki/policies/       Current policies with history           │
│  wiki/timelines/      Chronological event tracking            │
│  wiki/conflicts/      Unresolved contradictions               │
│                                                               │
│  Every page has YAML frontmatter:                             │
│    title, sources (raw file refs), last_compiled,             │
│    status (current | superseded | contested)                  │
│  Pages interlink via [[wikilinks]]                            │
│  Git tracks all changes (free version history)                │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                  QUERY + LINT                                 │
│                                                               │
│  QUERY: Search wiki pages, synthesize answers                 │
│         Good answers become new wiki pages                    │
│  LINT:  Health check for stale claims, orphan pages,          │
│         missing cross-refs, contradictions                    │
└──────────────────────────────────────────────────────────────┘
```

## Tech stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Team standard |
| Package manager | uv | Fast, workspace-aware |
| Agent framework | [Deep Agents](https://github.com/langchain-ai/deepagents) | Batteries-included agent on LangGraph — built-in file ops, sub-agents, planning, model-agnostic |
| LLM access | [LiteLLM](https://github.com/BerriAI/litellm) | Model-agnostic — OpenAI, Anthropic, Gemini, open models |
| Email access | Gmail API (google-api-python-client) | Google Workspace, supports watch + history for live mode |
| Email parsing | mail-parser | Robust .eml parsing with charset detection |
| Observability | [Langfuse](https://github.com/langfuse/langfuse) | LLM tracing from day one |
| Config | pydantic-settings + .env | Type-safe config |
| Logging | structlog | Structured logging |

## Project layout

```
email-knowledge-base/
├── README.md                      # This file
├── CLAUDE.md                      # Agent schema — rules for the LLM compiler
├── pyproject.toml                 # Dependencies (uv)
├── Makefile                       # Common commands
├── .env.example                   # Environment template
├── .gitignore
│
├── src/
│   ├── __init__.py
│   ├── config.py                  # Settings via pydantic-settings
│   ├── budget.py                  # LiteLLM proxy budget check
│   │
│   ├── ingest/                    # Email → raw/ pipeline
│   │   ├── __init__.py
│   │   ├── gmail.py               # Gmail API client (OAuth, fetch, list)
│   │   ├── parser.py              # Email → raw markdown with YAML frontmatter
│   │   └── attachments.py         # Attachments + image captioning (LiteLLM vision)
│   │
│   ├── compile/                   # raw/ → wiki/ compilation
│   │   ├── __init__.py
│   │   ├── compiler.py            # Deep Agents workflow + custom tools
│   │   └── prompts.py             # Karpathy-pattern compilation prompts
│   │
│   ├── wiki/__init__.py           # reserved for Phase 2 search/index modules
│   └── api/__init__.py            # reserved for Phase 1+ FastAPI endpoints
│
├── raw/                           # Immutable email storage (content gitignored)
│   ├── .gitkeep
│   └── attachments/               # Email attachments by message_id hash
│
├── wiki/                          # LLM-compiled knowledge base (content gitignored)
│   ├── index.md                   # Master catalog (auto-generated)
│   ├── log.md                     # Append-only compile log
│   ├── topics/                    # Projects, initiatives, features
│   ├── entities/                  # People (humans only)
│   ├── systems/                   # Products, platforms, services, mailing lists
│   ├── policies/                  # Current policies with version history
│   ├── timelines/                 # Long-running chronologies
│   └── conflicts/                 # Unresolved contradictions
│
├── scripts/
│   ├── ingest_backlog.py          # Pull last N days of mailing-list email
│   ├── compile_all.py             # Sequential compile (chronological, oldest-first)
│   ├── compile_parallel.py        # Thread-aware parallel compile
│   ├── lint_wiki.py               # Advisory checks + auto-fix (wikilinks, stubs)
│   ├── validate_wiki.py           # Hard integrity check (exits non-zero on corruption)
│   ├── snapshot_wiki.py           # Save/restore wiki state for safe iteration
│   └── watch_and_compile.py       # Live mode: poll Gmail + compile (candidate for Phase 1)
│
├── mkdocs.yml + mkdocs_hooks.py   # Material theme + roamlinks + Sources-section hook
│
├── docs/
│   ├── BACKLOG.md                 # "For later" items
│   ├── issues/                    # Issue docs (optional GitHub promotion)
│   └── reviews/                   # Audit reports (coherence, quality, plans)
│
├── CHANGELOG.md                   # Living record of issues + fixes + rationale
├── CLAUDE.md                      # Agent schema (symlinked as AGENTS.md)
└── .snapshots/                    # Pre-compile backups (gitignored, local-only)
```

## Phased delivery

### Phase 0 — "It works" (Day 1)
- Gmail OAuth + email fetcher (backlog pull)
- Email parser → raw/ markdown with frontmatter
- LLM compiler → wiki/ pages
- CLI scripts: ingest, compile, lint
- Langfuse tracing

### Phase 1 — "It's live" (Week 1–2)
- Gmail watch + Pub/Sub for real-time notifications
- Incremental compilation (only new emails trigger updates)
- Thread-aware ingestion (reply chains as units)
- Image/attachment captioning via vision model
- FastAPI webhook for Pub/Sub push

### Phase 2 — "It's smart" (Week 3–4)
- Typed supersession relations
- Confidence scoring per wiki claim
- Lint agent with auto-fix for high-confidence issues
- Hybrid search: full-text + metadata filters
- Wiki UI (BookStack or MkDocs Material)

### Phase 3 — "It talks back" (Month 2)
- Chatbot agent over wiki + raw layers
- Agentic retrieval: metadata → full-text → semantic → rerank
- Every answer cites sources, declares recency status
- Query answers compiled back into wiki

## Deploying to GCP

The wiki is served read-only on Cloud Run behind Identity-Aware Proxy, accessible
to the Workspace org. See `docs/gcp-migration.md` for the full phased plan.

One-time setup (Owner/Editor on the target GCP project required, OAuth consent
screen must already be configured Internal):

```bash
make bootstrap        # creates GCS bucket w/ versioning, enables APIs
```

Publish the current local `wiki/` to the deployed viewer:

```bash
make publish          # mkdocs build → rsync to GCS → redeploy Cloud Run
```

Defaults target project `voice-eval-stack-im`, region `asia-south1`, bucket
`indiamart-email-kb`, service `email-kb-viewer`, and IAP domain `indiamart.com`.
Override via env vars (`GCP_PROJECT`, `GCP_REGION`, `GCP_BUCKET`, `GCP_SERVICE`,
`GCP_IAP_DOMAIN`) passed to the scripts in `scripts/gcp/`.

## References

- [Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — Core pattern
- [LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2) — Supersession, confidence scoring, typed relations
- [lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki) — OSS implementation (document-focused)
- [Deep Agents](https://github.com/langchain-ai/deepagents) — Agent framework
- [Realtime-Gmail-Listener](https://github.com/sangnandar/Realtime-Gmail-Listener) — Gmail Pub/Sub reference

## License

MIT
