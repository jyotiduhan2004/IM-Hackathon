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
| topic | wiki/topics/ | Email discusses a project, initiative, feature, or theme |
| entity | wiki/entities/ | A HUMAN PERSON referenced in From/To/CC/body. One page per person, never for products. |
| system | wiki/systems/ | A product, platform, service, URL, tool, or mailing list (NOT a human) |
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
1. List uncompiled emails via `list_uncompiled_emails` (reads the
   Postgres `messages` table — NOT raw frontmatter; that field is
   legacy dead state)
2. Process chronologically, grouped by `thread_id`
3. For each email, determine affected topics/entities/policies
4. Create or update wiki pages with proper frontmatter + cross-references
5. Detect supersession and conflicts
6. Return. The coordinator (`scripts/compile_all.py`) handles the rest
   deterministically: it flips `compile_state` in Postgres (only for
   emails whose raw_path is actually cited in a content-type wiki
   page — entity-only citation doesn't count); it stamps
   `last_compiled` on modified wiki pages via mtime diff; it appends
   a structured batch row to `wiki/log.md`; and it regenerates
   `wiki/index.md`.

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

- NEVER modify files in `raw/` — not the body, not the frontmatter.
  The Postgres `messages` table owns compile state, not raw YAML.
- NEVER invent entity slugs. Call `create_entity(email, display_name)`
  — it returns a deterministic email-canonical slug + creates the
  stub page. Identity is email; display names collide.
- NEVER call `mark_as_compiled`, `stamp_page_compiled_at`,
  `append_to_log`, or `update_wiki_index` (these are no longer agent
  tools as of 2026-04-13). The coordinator flips compile state,
  stamps pages, logs structured batch rows, and rebuilds the index
  deterministically after you return. Do your wiki work, then return.
- NEVER invent information not in source emails
- NEVER delete a wiki page — supersede it instead
- NEVER remove history — only add to it
- NEVER silently overwrite — always preserve old versions
- NEVER catch bare `Exception` — use specific types
- NEVER use pip — use `uv add` / `uv remove` for dependencies
- NEVER put secrets in code — use environment variables

## Guardrail principles (learned 2026-04-13)

**Coordinators verify, LLMs propose.** Every LLM-claimed state
transition needs independent external evidence showing the agent did
the WORK, not just that it called a tool or set a flag. Three
incidents produced this rule in one session:

1. `mark_as_compiled` was 68% unreliable — agent forgot to call it
   on 19/28 batch emails.
2. Entity slug invention produced `vishakha-indiamart`,
   `arjun-gaur-clean`, `akash-singh6` (garbage / duplicates / numeric
   noise).
3. Naïve reconcile-by-citation would have false-flipped 715 of 748
   candidates because the agent name-drops emails in entity
   `sources:` lists without writing a topic page.

**Practical rules**:

- If a tool writes state the coordinator could compute, move it to
  the coordinator.
- If identity is stable (email address, message_id, thread_id, file
  mtime), compute it deterministically — never ask the LLM.
- Citation alone is not evidence of content extraction. Tighten the
  check to "cited in a content-type page" (topic/system/policy/
  timeline/conflict), not "cited anywhere".
- Migration is iterative, not big-bang. Ship the deterministic rule
  for NEW data; let the migration script clean legacy data in
  `--limit N` batches. Legacy that hasn't been migrated keeps
  working via compatibility-shim lookups (e.g.
  `find_entity_by_email` scans legacy display-name slugs).
- When in doubt, leave state `pending`. The compile queue's
  claim/finish loop re-processes it automatically — self-healing.

Design rationale + migration plan in `docs/BACKLOG.md`
("Coordinators verify, LLMs propose" + "Migrate hand-rolled
coordinator hooks → LangChain AgentMiddleware").

## Tool/coordinator split (the "intelligent human" frame)

The LLM agent is treated as a capable, disciplined, forgetful colleague.
Deterministic code handles what a computer does perfectly; the agent
handles what needs judgement. The split is explicit — every time the
coordinator makes the LLM do a computer's job, the agent gets slower,
more expensive, and less reliable. Every time we ask the LLM to *choose*
a UUID, compute a timestamp, or flip a flag, the model spends its
reasoning budget on something `datetime.now(UTC)` does perfectly.

**The coordinator does (never an agent tool):**

- Anything driven by the clock (`last_compiled`, `updated_by`,
  `update_count`).
- Anything that enumerates or counts (rebuilding `wiki/index.md`,
  weekly email counts, compile-progress).
- Any state-flag transition derivable from evidence the agent already
  wrote (flipping `messages.compile_state` based on which raw files
  are cited in a content-type page).
- Append-only audit logging of batch outcomes (`wiki/log.md`).
- Slug generation where identity is stable (email → entity slug,
  message_id → raw path).
- Post-batch quality gates (critique, auto-format, validator).

**The agent does (and only the agent can):**

- Read raw email and classify which topics/entities/systems/policies
  it affects.
- Decide whether a new email supersedes or contradicts an existing
  page.
- Write prose — paragraphs, summaries, history entries.
- Merge new facts into an existing page without losing prior
  information.
- Call `create_entity`, `write_draft_page`, `resolve_page`,
  `log_insight` (and aspirational variants like `log_doubt`) —
  judgement-call tools that mint or look up artefacts. Not to be
  confused with the banned `append_to_log` from the MUST NOT section.

**Tool-design rules the agent can rely on:**

1. Every tool name is a **verb matching the agent's intent**
   (`log_doubt`, not `log_insight(category="question")`) —
   namespacing by name offloads decision cost into the name itself.
2. Every tool docstring opens with a **"WHEN to use / WHEN NOT to
   use"** block before listing args — Claude Code's TodoWrite
   pattern. The LLM optimizes for triggering conditions, not
   argument types.
3. Every tool returns an **actionable `error` string on misuse**,
   never a stack trace. On success, returns the minimum context
   the agent needs to proceed (slug, path, remaining count — not
   full objects).
4. If a tool's state is something the coordinator could compute
   deterministically, it is **NOT an agent tool**. It is a
   middleware hook the coordinator runs between batches.
   (The current implementation uses hand-rolled coordinator hooks;
   migration to LangChain `AgentMiddleware` is deferred — see
   `docs/proposal/NORTH-STAR-DRAFT.md §5` (Phase 1 exclusions) and
   `docs/BACKLOG.md`.)
5. **High-leverage tools, not API wrappers.** One `create_entity`
   beats three of `list_entities`/`check_slug`/`write_file`.
6. **Dual-format responses** for any listing tool: support a
   `response_format` enum (`"concise"` vs `"detailed"`) so agents
   can pay tokens only when they need downstream IDs. Anthropic's
   worked example: 206 tokens → 72 tokens by defaulting to concise.
7. **Truncation messages must guide, not just fail.** When a tool
   caps output, return "Showing first N of M. Refine with `filter=`
   or `slug_prefix=` to narrow." — never a bare `"truncated"`.
8. **Idempotency by default.** Tools that mutate state (`create_entity`,
   `write_draft_page`) must be safe to call twice.
   Either (a) return the existing resource on repeat call, or (b) use
   a stable key so the coordinator can dedupe. Timeouts + retries will
   happen; the tool must survive them.
9. **Unambiguous parameter names.** `email` not `user`;
   `canonical_user_email` not `key`; `message_id` not `id`. The agent
   binds semantics from the name — overloaded names cost retries.
10. **Evaluate tools against real transcripts.** Every new tool's
    usefulness is proven by `compile_tool_calls` showing it was
    called when the trigger condition held. Pipe failed transcripts
    back to Claude (or Codex) and ask "why didn't the agent call
    `X` here?" — refactor based on the answer. A tool that ships
    and goes unused is a broken prompt, not a broken tool.

**Prompt / docstring division of labour:**

- **System prompt**: names every tool once, in the workflow section
  where it applies, with a one-sentence trigger ("when you're
  uncertain a concept deserves its own page, call
  `write_draft_page`"). Strategic guidance only.
- **Docstring**: mechanics — args, return shape, `WHEN/WHEN NOT`,
  one concrete example of good use.
- A tool with zero prompt mentions will get zero calls, regardless
  of how good its docstring is. The prompt is the index; the
  docstring is the page. Prompt real estate is scarce, so every
  mention is a strong steering signal. `create_entity` at 111 calls
  vs `log_insight` at 0 is the existence proof on this codebase.

**When writing a new tool, use this checklist:**

- [ ] Can a deterministic function compute this? If yes, it's a
      coordinator hook, not a tool.
- [ ] Does the tool name match an *intent verb* the agent would
      recognize from a natural reading of the prompt?
- [ ] Is there a `## Workflow` section in the prompt where this
      tool is named as the right move for a specific trigger?
- [ ] Does the docstring lead with WHEN/WHEN-NOT, then args?
- [ ] Does the return payload fit in <200 tokens for typical use?
- [ ] Is the error path actionable ("pass at least one of X, Y, Z"),
      not opaque ("KeyError")?
- [ ] After shipping, does `ToolCallLogHandler` show it's actually
      called when the trigger condition holds? If not, the prompt
      or name is broken — fix that before adding more tools.

Sources: Anthropic's ["Writing effective tools for AI agents"](https://www.anthropic.com/engineering/writing-tools-for-agents),
Anthropic's ["Effective context engineering"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
LangChain's [Agent Middleware post](https://blog.langchain.com/agent-middleware/),
Garry Tan's ["Thin Harness, Fat Skills"](https://x.com/garrytan/status/2042925773300908103),
and the reverse-engineered Claude Code TodoWrite description at
[Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-todowrite.md).
(A fuller patterns review lives outside this branch — the four links
above are sufficient to follow the reasoning.)

## Code conventions

- Use `async def` for all IO operations
- Use Pydantic models for data schemas
- Use structlog for logging: `logger.info("message", key=value)`
- All public functions require type hints (mypy strict)
- Tests in `tests/` — pattern: `test_*.py`
- MUST use `uv run` to execute any Python tool
