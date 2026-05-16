# CLAUDE.md — Email Knowledge Base Agent Schema

## What this is

**A compiled, curated Wikipedia for IndiaMART.** Pages are about *things* (products, systems, initiatives, decisions), not *events* (emails, threads). Raw emails are immutable evidence; wiki pages are LLM-compiled knowledge.

Canonical direction: [`docs/NORTH-STAR.md`](docs/NORTH-STAR.md). Active design detail: [`docs/proposal/NORTH-STAR-DRAFT.md`](docs/proposal/NORTH-STAR-DRAFT.md). You (the agent) compile emails into the wiki.

Based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
extended for email: concept-page deduplication, supersession detection, and multi-stage pipeline.

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
raw/                        # IMMUTABLE. One .md per email. Never modify after creation.
  attachments/              # Attachments by message-id hash
wiki/                       # LLM-MAINTAINED. Compiler writes; no human edits.
  home.md                   # Curated front door
  glossary.md               # Auto-generated IndiaMART terms
  domains/                  # 8 compiler-generated hub pages
  topics/                   # Concept pages (not per-thread)
  systems/                  # Products + platforms
  decisions/                # Lazy-created; linked from topics
  policies/                 # Org-wide rules
  people/                   # Reference-only; hidden from primary nav
  changes.md                # Auto-generated recent activity
src/                        # Application code
  ingest/                   # Email → raw/ + Postgres
  compile/                  # raw/ → wiki/ (Deep Agents)
  db/                       # Postgres catalog
scripts/                    # CLI entry points
tests/
docs/NORTH-STAR.md          # Canonical direction
docs/proposal/              # Active design drafts
docs/archive/               # Superseded strategy docs
```

## Commands

```bash
# Setup
make setup                    # install uv, sync deps, create .env

# Full pipeline (compile → dedupe → rollup → glossary → status sweep)
make pipeline                 # one unified command — the production path

# Individual stages (for debugging)
make ingest                   # Gmail → raw/ + messages table
make compile                  # raw/ → wiki/ (Deep Agents)
make lint-wiki                # wiki health checks

# Code quality
make check                    # format-check + lint + type-check + test
uv run ruff check path/to/file.py
uv run mypy path/to/file.py
uv run pytest tests/test_file.py -x
```

## Page types (4 visible + 2 reference)

| Type | Directory | Visible in nav? | When to create |
|---|---|---|---|
| topic | `wiki/topics/` | Yes | A concept worth its own page (a project, initiative, a thing in the world). Multiple emails about the same concept → one page that grows. |
| system | `wiki/systems/` | Yes | A product, platform, service, or tool (Lens, ISQ, BuyLeads, MCAT). |
| policy | `wiki/policies/` | Yes | An org-wide rule/procedure/guideline. Rare. |
| glossary | `wiki/glossary.md` | Yes (single page) | Auto-generated. Acronyms + terms. Don't create by hand. |
| decision | `wiki/decisions/` | Indirect (linked from topics) | Lazy — only when a topic page wikilinks to one. Meaningful changes (e.g. "scaled 5%→50%", "deprecated X"), not trivial acks. |
| people | `wiki/people/` | No (hidden from primary nav) | Lazy — only when a topic page wikilinks to a person. Reference-only. |

Dropped in the 2026-04-15 consolidation: `timelines/`, `conflicts/` (zero pages after 2 weeks, no real use case).

## Status values

| Status | Meaning |
|---|---|
| active | Active, up-to-date version |
| superseded | A newer page/email has replaced this |
| archived | Deliberately retired; kept for lineage |

## Operations

### INGEST
- Pull emails from Gmail mailing list via Gmail API
- Parse into `raw/` markdown with YAML frontmatter
- Store attachments, caption images via vision model
- Writes a row to the `messages` table

### COMPILE (primary agent job)
1. Use `find_new_sources` to find a batch (filter by date, sender, subject, or thread).
2. **Trivial filter** — skip emails with <50 substantive words, pure acks, calendar invites.
3. For each surviving email, determine its concept (topic), existing system, or new category.
4. **Resolve existing pages first** via `resolve_page` — grow existing concept pages instead of creating duplicates.
5. Update the topic/system page: add a `Recent changes` entry, update `Current state` if warranted, append sources.
6. **Extract decisions** during topic compile — if the email contains a meaningful change ("we're scaling X to 50%", "deprecating Y"), create a `decisions/...` page and wikilink from the topic.
7. **People pages** only when a topic wikilinks to someone — lazy, reference-only.
8. **Status updates** when you see supersession — set `status: superseded` + `superseded_by`.
9. **Self-review** — after writing, re-read the page. Does it synthesize or just list emails? If the latter, rewrite.
10. Return. The coordinator (`scripts/compile_all.py`) handles the rest
    deterministically: it flips `compile_state` in Postgres (only for
    emails whose raw_path is actually cited in a content-type wiki
    page — people-only citation doesn't count); it stamps
    `last_compiled` on modified wiki pages via mtime diff; it appends
    a structured batch row to `wiki/log.md`; and it regenerates
    `wiki/home.md`.

### DEDUPE / ROLLUP / GLOSSARY / STATUS (post-compile agents, not part of the main compile loop)
- Dedupe: merge near-duplicate topics via `wiki_merge_pages`
- Domain rollup: regenerate all 8 `wiki/domains/*.md` pages from tagged topics
- Glossary: scan corpus, generate `wiki/glossary.md`
- Status sweep: detect supersession the main pass missed; update `wiki/changes.md`

### QUERY
- Search wiki pages for the user's question
- Synthesize answer citing wiki pages and raw source emails
- Indicate recency status: active / superseded / archived
- Good answers can become new wiki pages

### LINT
- Stale pages (old sources, newer emails exist)
- Orphan pages (no incoming wikilinks)
- Missing cross-references (mentions entity without linking)
- Broken wikilinks (point to non-existent page)
- Frontmatter issues (missing required fields)

## Wiki page format

Every wiki page MUST have YAML frontmatter:

```yaml
---
title: "Seller ISQ"
slug: seller-isq
page_type: topic            # topic | system | policy | decision | people
status: active              # active | superseded | archived
tags: [seller, marketplace] # multi-tag; drives domain rollups
domain: seller              # primary domain for navigation
last_compiled: "2026-04-15T07:00:00Z"
superseded_by: null         # wikilink slug when status=superseded
source_threads:
  - "19b92d9b270daa57"
  - "19b59cdc863ac109"
related:
  - "[[topic/seller-isq]]"
  - "[[decision/scale-buyer-trust-50pct]]"
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
4. If unsure → leave `status: active` and let the dedupe agent surface the conflict later. Don't guess.

## Cross-referencing

- Use `[[page-type/slug]]` wikilinks (e.g. `[[topic/seller-isq]]`, `[[system/lens]]`, `[[decision/cap-notif-frequency]]`)
- Wikilink people by slug (e.g. `[[people/anjali-shankar]]`) — the page creation is lazy
- Every content page should have a `Related` section (wikilinks to 3-8 adjacent pages)

## What you MUST NOT do

- NEVER modify files in `raw/` — not the body, not the frontmatter.
  The Postgres `messages` table owns compile state, not raw YAML.
- NEVER invent entity slugs. Call `create_entities(entities=[{email,
  display_name}])` — it returns a deterministic email-canonical slug
  + creates the stub page. Identity is email; display names collide.
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
- NEVER create `timelines/` or `conflicts/` pages — those categories were dropped
- NEVER create people pages proactively — only when a topic page wikilinks to a person
- NEVER create decision pages proactively — only when a topic wikilinks to one (quality bar: meaningful change, not trivial ack)

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
  decision), not "cited anywhere".
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
- Anything that enumerates or counts (rebuilding `wiki/home.md`,
  weekly email counts, compile-progress).
- Any state-flag transition derivable from evidence the agent already
  wrote (flipping `messages.compile_state` based on which raw files
  are cited in a content-type page).
- Append-only audit logging of batch outcomes (`wiki/log.md`).
- Slug generation where identity is stable (email → entity slug,
  message_id → raw path).
- Post-batch quality gates (critique, auto-format, validator).

**The agent does (and only the agent can):**

- Read raw email and classify which topics/systems/policies
  it affects.
- Decide whether a new email supersedes or contradicts an existing
  page.
- Write prose — paragraphs, summaries, history entries.
- Merge new facts into an existing page without losing prior
  information.
- Call `create_entities`, `write_draft_page`, `resolve_page`,
  `log_insight` — judgement-call tools that mint or look up
  artefacts. Not to be confused with the banned `append_to_log`
  from the MUST NOT section.

**Tool-design rules the agent can rely on:**

1. Every tool name is a **verb matching the agent's intent** —
   namespacing by name offloads decision cost into the name itself.
   (This is why `create_entities` subsumed the earlier
   `find_entity_by_email` + `write_file` dance.)
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
   `docs/BACKLOG.md` (Phase 2).)
5. **High-leverage tools, not API wrappers.** One `create_entity`
   beats three of `list_entities`/`check_slug`/`write_file`.
6. **Dual-format responses** for any listing tool: support a
   `response_format` enum (`"concise"` vs `"detailed"`) so agents
   can pay tokens only when they need downstream IDs. Anthropic's
   worked example: 206 tokens → 72 tokens by defaulting to concise.
7. **Truncation messages must guide, not just fail.** When a tool
   caps output, return "Showing first N of M. Refine with `filter=`
   or `slug_prefix=` to narrow." — never a bare `"truncated"`.
8. **Idempotency by default.** Tools that mutate state (`create_entities`,
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
  mention is a strong steering signal.

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
