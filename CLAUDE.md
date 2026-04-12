# CLAUDE.md — Email Knowledge Base Agent Schema

## What this is

A living knowledge base compiled from email. Raw emails are immutable source documents.
Wiki pages are LLM-compiled knowledge. You (the LLM agent) maintain the wiki.

Based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
extended for email: thread awareness, supersession detection, and incremental compilation.

## Tech stack

- **Language**: Python 3.12+ (strict typing)
- **Package manager**: `uv` (NOT pip, NOT poetry)
- **Agent framework**: Deep Agents (LangGraph-based, model-agnostic)
- **LLM**: LiteLLM (any model)
- **Email**: Gmail API (Google Workspace)
- **Observability**: Langfuse
- **Linter/Formatter**: Ruff (line length 100, double quotes)
- **Type checker**: mypy (strict mode)

## Directory structure

```
raw/                        # IMMUTABLE. One .md per email. NEVER modify after creation.
  attachments/              # Email attachments by message ID hash
wiki/                       # LLM-MAINTAINED. The agent creates, updates, cross-references.
  index.md                  # Master catalog of all pages by category
  log.md                    # Append-only chronological log
  topics/                   # One page per project/product/initiative
  entities/                 # People, teams, products, systems
  policies/                 # Current policies with version history
  timelines/                # Chronological event tracking
  conflicts/                # Unresolved contradictions
src/                        # Application code
  ingest/                   # Email → raw/ pipeline
  compile/                  # raw/ → wiki/ compilation (Deep Agents)
  wiki/                     # Wiki management utilities
  api/                      # FastAPI endpoints
scripts/                    # CLI entry points
tests/                      # Test suite
```

## Commands

```bash
# Setup
make setup                    # install uv, sync deps, create .env

# Email operations
make ingest                   # pull last 30 days of mailing list email
make compile                  # compile unprocessed raw → wiki
make lint-wiki                # check wiki health
make pipeline                 # ingest → compile → lint (full pipeline)

# Code quality
make check                    # format-check + lint + type-check + test
uv run ruff check path/to/file.py
uv run mypy path/to/file.py
uv run pytest tests/test_file.py -x
```

## Page types

| Type | Directory | When to create |
|---|---|---|
| topic | wiki/topics/ | Email discusses a project, product, initiative, or theme |
| entity | wiki/entities/ | Email introduces or significantly references a person, team, product, or system |
| policy | wiki/policies/ | Email announces, updates, or clarifies a rule/procedure/guideline |
| timeline | wiki/timelines/ | A topic has enough chronological events for a timeline view |
| conflict | wiki/conflicts/ | Two+ emails disagree on a factual claim, policy, or decision |

## Status values

| Status | Meaning |
|---|---|
| current | Active, up-to-date version |
| superseded | A newer page/email has replaced this |
| contested | Conflicting information — needs human review |

## Operations

### INGEST
- Pull emails from Gmail mailing list via Gmail API
- Parse into raw/ markdown with YAML frontmatter
- Store attachments, caption images via vision model
- Handled by `scripts/ingest_backlog.py`

### COMPILE (primary agent job)
1. List uncompiled emails (`compiled: false` in frontmatter)
2. Process chronologically, grouped by thread_id
3. For each email, determine affected topics/entities/policies
4. Create or update wiki pages with proper frontmatter and cross-references
5. Detect supersession and conflicts
6. Update wiki/index.md and append to wiki/log.md
7. Mark processed emails as `compiled: true`

### QUERY
- Search wiki pages for the user's question
- Synthesize answer citing wiki pages and raw source emails
- Indicate recency status: current / superseded / contested
- Good answers can become new wiki pages

### LINT
- Stale pages (old sources, newer emails exist)
- Orphan pages (no incoming wikilinks)
- Missing cross-references (mentions entity without linking)
- Broken wikilinks (point to non-existent page)
- Frontmatter issues (missing required fields)
- Unresolved conflicts (open > 7 days)

## Wiki page format

Every wiki page MUST have YAML frontmatter:

```yaml
---
title: "Page Title"
page_type: topic | entity | policy | timeline | conflict
status: current | superseded | contested
sources:
  - "raw/YYYY-MM-DD_subject_msgid.md"
related:
  - "[[other-page]]"
last_compiled: "ISO-timestamp"
---
```

Policy pages additionally require:
- `supersedes` / `superseded_by` fields when applicable
- "Current Policy" section with latest state
- "History" table: date, event, source link

## Supersession rules

1. Explicit supersession language ("this replaces", "supersedes", "please disregard") →
   set old page `status: superseded`, add `superseded_by`, create/update current page
2. Changed numbers/dates/rules → update current page AND add to History section
3. NEVER silently delete old information — preserve lineage always
4. If unsure → create a conflict page, do NOT guess at supersession

## Conflict rules

1. Disagreement with no clear supersession → create conflict page in wiki/conflicts/
2. List both positions with source links
3. Mark affected pages as `status: contested`
4. Analyze: is this a contradiction, an exception, or a clarification?

## Cross-referencing

- Use `[[page-name]]` wikilinks between wiki pages
- Link entity pages when mentioning people/teams/products
- Link policy pages when referencing rules/guidelines
- Every page should have a "Related" section

## What you MUST NOT do

- NEVER modify files in raw/ (except setting `compiled: true`)
- NEVER invent information not in source emails
- NEVER delete a wiki page — supersede it instead
- NEVER remove history — only add to it
- NEVER silently overwrite — always preserve old versions
- NEVER catch bare `Exception` — use specific types
- NEVER use pip — use `uv add` / `uv remove` for dependencies
- NEVER put secrets in code — use environment variables

## Code conventions

- Use `async def` for all IO operations
- Use Pydantic models for data schemas
- Use structlog for logging: `logger.info("message", key=value)`
- All public functions require type hints (mypy strict)
- Tests in `tests/` — pattern: `test_*.py`
- MUST use `uv run` to execute any Python tool
