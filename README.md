# Email Knowledge Base

A topic-first knowledge base that ingests email from a Gmail/Google Workspace
mailing list and compiles it into an interlinked markdown wiki.

Raw emails are immutable evidence. Wiki pages are compiled knowledge.

Based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
extended for email: thread awareness, supersession detection, and incremental
compilation.

## Current status

The repo already works for:

- backlog ingest from Gmail into `raw/`
- manual compile into `wiki/`
- local/read-only browsing via MkDocs
- coordinator-owned compile state in Postgres

The repo is **not** yet a polished team wiki. Live ingestion, strong search,
and chatbot-style querying are still future phases, and entity pages remain
noisy compared to topic pages.

Relevant design docs:

- [Internal wiki structure](docs/issues/09-internal-wiki-structure.md)
- [Phase 1 implementation plan](docs/issues/10-phase1-implementation-plan.md)
- [User personas and knowledge flows](docs/issues/11-user-personas-and-knowledge-flows.md)

## What this project is optimizing for

Near-term, this project should optimize for four things:

1. **Trustworthy topic pages**: project and system pages should preserve the
   concrete decisions, metrics, tables, and changes that matter.
2. **Provenance without clutter**: readers should be able to trust a page
   without scrolling through hundreds of lines of frontmatter.
3. **Topic-first navigation**: the wiki should feel browsable via hubs,
   rollups, glossary pages, and timelines, not just by scanning filenames.
4. **People pages as support structure**: entity pages should help navigation
   and attribution, not dominate the wiki or become the primary product.

## What it does

```
Gmail mailing list → ingest → raw/ (immutable emails) → compile → wiki/ (knowledge base)
```

1. **Ingests** emails from a Gmail mailing list (OAuth; backlog flow shipped,
   live flow designed but not yet shipped)
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
make setup  # installs uv deps, copies .env, and wires pre-commit hooks
```

`make setup` runs `pre-commit install` so the merge-conflict and syntax
hooks in `.pre-commit-config.yaml` execute on every `git commit`, matching
the checks enforced in CI.

Linked git worktrees automatically fall back to the main checkout's `.env` if
the worktree does not have its own copy.

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

# 5. Smoke-test Langfuse tracing
make langfuse-smoke
```

## Architecture

```
Gmail mailing list
  -> ingest (`scripts/ingest_backlog.py`, `src/ingest/*`)
  -> immutable raw emails in `raw/`
  -> Postgres queue/catalog (`src/db/*`)
  -> compiler agent (`src/compile/compiler.py` + `prompts.py`)
  -> wiki pages in `wiki/`
  -> MkDocs viewer (`mkdocs.yml`, `mkdocs_hooks.py`)
```

### Runtime responsibilities

- `raw/` is immutable source material. The agent never edits it.
- Postgres owns compile state and run bookkeeping.
- The compiler agent writes wiki content only.
- The coordinator script verifies outcomes, stamps modified pages,
  appends to `wiki/log.md`, and rebuilds `wiki/index.md`.

### Post-batch auto-format and validate

After every batch of emails is marked compiled, the coordinator runs the
idempotent formatter (`scripts/format_wiki.py`) and the per-page validator
(`scripts/validate_wiki.py::validate_page`) in-process over every wiki page
whose mtime advanced during the batch. The formatter strips agent-written
nav sections and normalises `## Related`; the validator surfaces format
drift the formatter can't auto-fix (malformed frontmatter, broken
wikilinks, duplicate H2s). Errors are logged and attached to the batch's
notes in `wiki/log.md` but do **not** fail the batch — the emails are
already compiled, and recompiling them rarely helps. Operators re-compile
manually via `scripts/reconcile_compile_state.py` when the validator
flags something actionable.

### Current information architecture

- `wiki/topics/` is the most important surface. This is where project and
  decision knowledge should concentrate.
- `wiki/systems/` is for products, services, tools, URLs, and mailing lists.
- `wiki/entities/` is for humans only.
- `wiki/policies/`, `wiki/timelines/`, and `wiki/conflicts/` exist, but are
  still underused compared to topics and systems.
- Query APIs and chatbot-style retrieval are not shipped yet.

## Tech stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Team standard |
| Package manager | uv | Fast, workspace-aware |
| Agent framework | [Deep Agents](https://github.com/langchain-ai/deepagents) | Batteries-included agent on LangGraph — built-in file ops, sub-agents, planning, model-agnostic |
| LLM access | [LiteLLM](https://github.com/BerriAI/litellm) | Model-agnostic — OpenAI, Anthropic, Gemini, open models |
| Email access | Gmail API (google-api-python-client) | Google Workspace, supports watch + history for live mode |
| Email parsing | Gmail API payload parsing + MarkItDown fallback | Matches current code path in `src/ingest/parser.py` |
| Observability | [Langfuse](https://github.com/langfuse/langfuse) (optional) | Wired in, but disabled unless env keys are set |
| Config | pydantic-settings + .env | Type-safe config |
| Logging | structlog | Structured logging |

## Project layout

```
email-knowledge-base/
├── README.md
├── CLAUDE.md                      # Agent rules / operating contract
├── Makefile
├── pyproject.toml
├── .env.example
├── src/
│   ├── config.py
│   ├── budget.py
│   ├── ingest/                    # Gmail -> raw markdown
│   ├── compile/                   # Compiler agent, prompts, cache stats, entity identity
│   └── db/                        # Postgres-backed queue/catalog state
├── scripts/
│   ├── ingest_backlog.py
│   ├── compile_all.py
│   ├── compile_parallel.py
│   ├── watch_and_compile.py
│   ├── lint_wiki.py
│   ├── validate_wiki.py
│   ├── snapshot_wiki.py
│   ├── backfill_*.py
│   └── audit.py
├── raw/                           # Immutable email storage
├── wiki/                          # Compiled knowledge base
├── docs/
│   ├── BACKLOG.md
│   ├── issues/
│   ├── reviews/
│   └── runs/
├── mkdocs.yml
├── mkdocs_hooks.py
├── CHANGELOG.md
└── .snapshots/                    # Pre-compile safety backups
```

## Delivery plan

### Phase 0 — working pipeline ✅
- Langfuse tracing (`make langfuse-smoke` to verify end-to-end export)

Done today:

- backlog ingest from Gmail
- raw email serialization
- compile queue in Postgres
- wiki compiler + coordinator guardrails
- MkDocs viewer
- validator, lint, snapshots, audits

### Phase 1 — trustworthy and approachable wiki

Current priority:

- move provenance out of bloated markdown frontmatter and into the catalog/render layer
- make the wiki topic-first and easier to browse
- de-noise entity pages so they support the wiki instead of overwhelming it
- add better hubs, glossary, rollups, and visible freshness/status metadata
- keep docs, backlog, and milestones honest about what is actually shipped

### Phase 2 — live ingestion

After the wiki is trustworthy enough to deserve automation:

- Gmail watch + Pub/Sub
- webhook endpoint
- `historyId` tracking
- quiet-period thread compilation
- default attachment/image handling

### Phase 3 — queryable knowledge base

- local search that works at wiki scale
- better navigation across related topics and timelines
- question-answering over compiled knowledge with citations

### Phase 4 — team-scale system

- multi-user/team workflow
- stronger review/eval loops
- production-grade deployment and multi-mailing-list support

## What makes it better as a wiki

If the output should feel like a real wiki rather than a file dump, the next
improvements are mostly structural:

- **Topic-first homepages and hubs**: readers should land on projects, systems,
  and cross-cutting themes before they land on people pages.
- **Rollups over filenames**: generate pages like "all WhatsApp work" or
  "all buyer-chat work" so browsing does not require guessing slugs.
- **Glossary and metadata**: define acronyms, show freshness/status/owner-like
  context, and make page state legible at a glance.
- **Less provenance noise**: preserve trust, but render sources in a way that
  supports the prose instead of swallowing it.
- **Cleaner category boundaries**: entities are humans, systems are products and
  tools, timelines/policies/conflicts appear when they add navigational value.

The detailed target structure is captured in
[`docs/issues/09-internal-wiki-structure.md`](docs/issues/09-internal-wiki-structure.md).
The execution plan for getting there lives in
[`docs/issues/10-phase1-implementation-plan.md`](docs/issues/10-phase1-implementation-plan.md).

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

To combine compile + publish in one step, pass `--deploy` to `compile_all.py` —
the coordinator will invoke `make publish` automatically after a successful run
(and skip it on a killed/failed run so you never ship a partially-compiled
wiki). Use `--deploy-force` to skip the `validate_wiki` gate, equivalent to
`make publish-force`.

```bash
uv run python scripts/compile_all.py --deploy          # compile + publish
uv run python scripts/compile_all.py --deploy-force    # skip validator gate
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
