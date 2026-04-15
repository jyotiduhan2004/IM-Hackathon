# CLAUDE.md — Email Knowledge Base Agent Schema

## What this is

**A compiled, curated Wikipedia for IndiaMART.** Pages are about *things* (products, systems, initiatives, decisions), not *events* (emails, threads). Raw emails are immutable evidence; wiki pages are LLM-compiled knowledge.

Canonical direction: [`docs/NORTH-STAR.md`](docs/NORTH-STAR.md). Active design detail: [`docs/proposal/NORTH-STAR-DRAFT.md`](docs/proposal/NORTH-STAR-DRAFT.md). You (the agent) compile emails into the wiki.

Based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), extended for email: concept-page deduplication, supersession detection, multi-stage pipeline.

## Tech stack

- Python 3.12+ (strict typing, mypy strict)
- `uv` for packaging (NOT pip, NOT poetry)
- Deep Agents (LangGraph-based) + LiteLLM for the compile loop
- Gmail API for ingest
- Postgres for catalog + queue state
- Langfuse for observability
- MkDocs-Material for the viewer

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

## Frontmatter schema

Every wiki page has YAML frontmatter:

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
sources:
  - "raw/2026-04-12_....md"
  - "raw/2026-04-05_....md"
related:
  - "[[system/isq]]"
  - "[[decision/scale-buyer-trust-50pct]]"
---
```

## Default page template (loose guidance, validator warns but doesn't block)

```markdown
## TL;DR
One paragraph. What is this, current state in 2-3 sentences.

## Background
Synthesis across emails. How we got here.

## Current state
What's happening now. Sub-initiatives as `### H3` sections if needed.

## Recent changes
- 2026-04-12: scaled buyer-trust pilot 5% → 50% — see [[decision/...]]
- 2026-04-05: launched photo-similarity in 3 categories
(last 3-5 entries; older collapsed)

## Decisions
- [[decision/deprecate-old-bidding]] (2026-03-20)

## Sources
<details><summary>Sources (23)</summary>
(collapsed, rendered by mkdocs_hooks.py)
</details>
```

Compiler aims for this template but may deviate when the content warrants it. Validator warns on missing required sections (TL;DR, Current state, Sources), does not block.

## Supersession rules

1. Explicit supersession language in email ("this replaces", "supersedes", "deprecate X in favor of Y") → set old page `status: superseded`, add `superseded_by: <new-slug>`, create/update the new page.
2. Changed numbers/dates/rules → update `Current state` section AND add an entry to `Recent changes`.
3. NEVER silently delete old information — preserve lineage.
4. If unsure → leave `status: active` and let the dedupe agent surface the conflict later. Don't guess.

## Operations

### INGEST
- Pull emails from Gmail mailing list via Gmail API
- Parse into `raw/` markdown with YAML frontmatter
- Store attachments; caption images via vision model
- Writes a row to the `messages` table

### COMPILE (primary agent job)
1. Use `find_new_sources` or `list_uncompiled_emails` to find a batch.
2. **Trivial filter** — skip emails with <50 substantive words, pure acks, calendar invites.
3. For each surviving email, determine its concept (topic), existing system, or new category.
4. **Resolve existing pages first** via `resolve_page` — grow existing concept pages instead of creating duplicates.
5. Update the topic/system page: add a `Recent changes` entry, update `Current state` if warranted, append sources.
6. **Extract decisions** during topic compile — if the email contains a meaningful change ("we're scaling X to 50%", "deprecating Y"), create a `decisions/...` page and wikilink from the topic.
7. **People pages** only when a topic wikilinks to someone — lazy, reference-only.
8. **Status updates** when you see supersession — set `status: superseded` + `superseded_by`.
9. **Self-review** — after writing, re-read the page. Does it synthesize or just list emails? If the latter, rewrite.
10. Return. The coordinator (`scripts/compile_all.py`) flips `compile_state` deterministically based on actual citations.

### DEDUPE / ROLLUP / GLOSSARY / STATUS (post-compile agents, not part of the main compile loop)
- Dedupe: merge near-duplicate topics via `wiki_merge_pages`
- Domain rollup: regenerate all 8 `wiki/domains/*.md` pages from tagged topics
- Glossary: scan corpus, generate `wiki/glossary.md`
- Status sweep: detect supersession the main pass missed; update `changes.md`

## Cross-referencing

- Use `[[page-type/slug]]` wikilinks (e.g. `[[topic/seller-isq]]`, `[[system/lens]]`, `[[decision/cap-notif-frequency]]`)
- Wikilink people by slug (e.g. `[[people/anjali-shankar]]`) — the page creation is lazy
- Every content page should have a `Related` section (wikilinks to 3-8 adjacent pages)

## What you MUST NOT do

- **NEVER modify files in `raw/`** — the Postgres `messages` table owns compile state, not raw YAML.
- **NEVER invent entity slugs**. Call `create_entity(email, display_name)` — it returns a deterministic email-canonical slug.
- **NEVER create `timelines/` or `conflicts/` pages**. Those categories were dropped.
- **NEVER create entity pages proactively**. Only when a topic page wikilinks to a person. Hidden from primary nav.
- **NEVER create decision pages proactively**. Only when a topic page wikilinks to one. Quality bar: meaningful change, not trivial ack.
- **NEVER invent information** not in source emails.
- **NEVER delete a wiki page** — supersede it instead.
- **NEVER remove history** — only add to it.
- **NEVER catch bare `Exception`** — use specific types.
- **NEVER use pip** — use `uv add` / `uv remove`.
- **NEVER put secrets in code** — use environment variables.

## Guardrail principles

**Coordinators verify, LLMs propose.** Every LLM-claimed state transition needs independent external evidence showing the agent did the WORK, not just that it called a tool.

Rules:

- If a tool writes state the coordinator could compute, move it to the coordinator.
- If identity is stable (email, message_id, thread_id, file mtime), compute it deterministically — never ask the LLM.
- Citation alone is not evidence of content extraction. Tighten the check to "cited in a content-type page" (topic/system/policy/decision), not "cited anywhere" (which trivially passes on entity-only mentions).
- When in doubt, leave state `pending`. The claim/finish loop re-processes it automatically.
- Don't silent-strip stubs. Explicit one-shot cleanup is fine; blanket post-batch deletion erases failure signal.

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

- `async def` for all IO operations
- Pydantic models for data schemas
- structlog for logging: `logger.info("message", key=value)`
- All public functions require type hints (mypy strict)
- Tests in `tests/` — pattern: `test_*.py`
- MUST use `uv run` for any Python tool
