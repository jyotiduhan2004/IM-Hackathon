# Backlog — "For later" ideas and open questions

Not scheduled, just captured so we don't forget. Promoted to issues when we pick
them up.

---

## Phase 0 review — first full compile observations (2026-04-13)

**Test input**: 22 emails, 1 day of `marketplacelaunch@indiamart.com`
**Model**: `z-ai/glm-4.6` via Intermesh LiteLLM proxy
**Result**: 15/22 emails fully compiled, 72 wiki pages (14 topic + 56 entity + index + log),
elapsed ~12 min before process stalled. Compile was killed before finishing the last 7.

### What works beautifully

- **Multi-email topic merge**: `wiki/topics/dynamic-smart-rfq-form.md` correctly
  merged 2 emails (April 11 + April 12), adding a "Version 2 Requirements" section
  from the newer email while preserving V1 context. This is the hardest thing the
  system does and it's genuinely good.
- **Concrete data extraction**: iOS page extracted `326.1 → 165.7 sec/hour, 49%
  improvement`, `30% adoption`, `Apple target 1 sec/hour p95`. Not hallucinated,
  all from the source email.
- **Role identification**: Compiler correctly labels "Lead Engineer — iOS", "Product
  Owner", "Stakeholder", "Testing and reporting issues" — useful context.
- **Cross-batch continuity**: The same topic getting a V2 update in a later batch
  properly appends, not overwrites.
- **Structured sections**: Overview, Metrics, Issues, Team, Related — the LLM
  follows the Karpathy prompt well.

### What's broken — quality issues

**P0 (fix before next full compile)**:

1. **Wikilink casing is 50/50 mixed** — `grep -hoE "\[\[.+?\]\]" wiki/` shows
   both `[[abhishek-bhartia]]` (kebab-case, works) and `[[Abhishek Sharma]]`
   (Title Case, broken). 210 broken link warnings. Every Title Case wikilink is
   dead because files live at `wiki/entities/lucky-agarwal.md`.
   - **Fix**: two layers — (a) harden the prompt to say "ALWAYS use the exact
     filename stem from list_wiki_pages in wikilinks; never Title Case"; (b) add
     a lint auto-fixer that normalizes `[[Title Case]]` → `[[title-case]]` via
     a case-insensitive lookup against existing files.

2. **Date hallucination** — some pages get `last_compiled: "2025-01-10T00:00:00Z"`
   (model's training cutoff), others get real times. Inconsistent.
   - **Status**: Partially fixed this session — added `stamp_page_compiled_at`
     tool + `list_wiki_pages` tool, and told the compiler NOT to write
     `last_compiled` itself. Need to re-run to verify.

3. **Non-person "entities"** — compiler wikilinks products/platforms/lists as if
   they're people: `[[BuyerMY]]`, `[[WhatsApp]]`, `[[M-Site]]`,
   `[[Marketplace Launch]]`, `[[Launch IndiaMART]]`, `[[ai.intermesh.net]]`.
   These have no pages and clutter the graph.
   - **Fix**: either add a `wiki/systems/` category for products/platforms, or
     tighten the prompt to not wikilink these (just mention in prose). Prefer
     the former since products ARE useful to have pages for.

4. **Orphan entity pages (17)** — e.g., `satya-nand.md`, `sandeep-garg.md`.
   These were created in later batches as new entities but the topic pages in
   earlier batches don't link back. Cross-batch state problem.
   - **Fix**: either pass "existing wiki pages" in initial prompt of each batch
     (partial) or post-compile pass that adds reverse links.

5. **Index.md stale** — 17 entities not in index.md because the final
   `update_wiki_index` call happens once per batch, not after all batches.
   - **Fix**: run `update_wiki_index` once at the end of the CLI script, not
     inside the agent.

**P1 (do next)**:

6. **Entity identity should be email, not name** — 56 entity pages have
   `title: "Lucky Agarwal"` + body line `Email: agarwal.lucky@indiamart.com` but
   no `email:` frontmatter field. Name-based filenames risk collisions (two
   "Amit Sharma"s). Email is always present in headers, globally unique,
   stable. See separate section below.

7. **Batch stall at 15/22** — compile process hung on batch 6+ with 0% CPU but
   live TCP connection. Seems LLM call was slow/stuck. No retry logic. Output
   file also had 0 bytes (buffering issue with `2>&1 | tail`).
   - **Fix**: add per-batch timeout, retry with backoff, don't pipe to tail for
     background runs.

8. **Topic count (14) > email count for V2** — some emails generated 2+ topics.
   Correct for multi-topic emails ("API Knowledge Agent AND Data Lineage
   Agent") but worth watching — could lead to too many thin pages.

9. **No policies / timelines / conflicts created** — expected for
   feature-announcement emails, but verify the compiler wouldn't miss these
   when they exist. Needs policy-email test case.

### What's genuinely fine and needs no change

- Raw markdown format, YAML frontmatter
- Gmail OAuth + fetch pipeline
- Filesystem backend wiring
- LiteLLM proxy routing
- Git-ignored content, committed structure

---

## Performance: parallelize compilation

**Why**: Sequentially compiling 22 emails via `z-ai/glm-4.6` on LiteLLM takes ~4
min in our first test (batches of 3, ~30-90s each). For 30-day backlog (~3000
emails) or multiple mailing lists, this gets slow.

**Options, easiest first**:
1. **Async batch parallelism** — use `agent.ainvoke()` + `asyncio.gather()` to
   run N batches concurrently. Safe because each batch processes distinct raw
   files. Expected 3-5× speedup.
2. **Deep Agents sub-agents** — use `create_deep_agent(subagents=[...])` to
   spawn per-topic or per-thread sub-agents that run in parallel.
3. **Async tool calls within a batch** — Deep Agents already batches internally,
   low-value.

**Gotchas to handle**:
- Race condition on shared entity pages (two batches both writing
  `wiki/entities/amit-agarwal.md`)
- Cross-reference staleness (topic A links to entity B that's still being written
  by another batch)
- Index regeneration MUST wait for all batches to finish
- Partial-batch failure recovery (one batch crashes, others commit — how to
  resume?)

**When**: After we're confident compilation quality is good. Phase 1 candidate.

---

## Schema: switch entity identity from name to email

**Why**: Using human names as entity IDs (`wiki/entities/amit-agarwal.md`) is
fragile:
- Two "Amit Sharma"s at the same org collapse into one page
- Name casing/spelling variations create broken wikilinks (we already see
  `[[Amit Agarwal]]` vs `[[amit-agarwal]]` inconsistency in our first run)
- Missing or mangled `From` name fields produce bad slugs

Emails are globally unique, stable, and always present in headers.

**Proposed scheme**:

```yaml
# wiki/entities/agarwal-lucky-at-indiamart-com.md
---
title: "Lucky Agarwal"                  # human display
email: agarwal.lucky@indiamart.com      # canonical ID
aliases:
  - "Lucky Agarwal"
  - "lucky@indiamart.com"
---
```

- Filename: slugify email → `agarwal-lucky-at-indiamart-com.md`
- Page title: human display name
- Cross-references: always use email-slug `[[agarwal-lucky-at-indiamart-com]]`
- Index.md: renders display name with email-slug as link target

**Side benefit**: fixes the wikilink casing inconsistency bug we already see.

**When**: Before running the 30-day backlog. Need to update CLAUDE.md prompt
+ compiler prompt + lint checker.

---

## LLM API: Chat Completions vs Responses API vs OpenResponses

**Question**: Should we switch from OpenAI-compatible Chat Completions API to
the newer Responses API (or the open-source OpenResponses spec)?

**Potential benefits**:
- Stateful server-side conversations → less token overhead per turn
- Richer built-in tools (file search, web search, computer use)
- OpenAI says Chat Completions won't be extended much going forward
- Better structured output handling

**Blockers to investigate**:
1. Does our LiteLLM proxy (`imllm.intermesh.net`) support Responses API yet?
2. Does `langchain-openai` / `ChatOpenAI` support Responses API? (As of Feb 2026
   there's a separate `ChatOpenAI` Responses variant — confirm)
3. Does Deep Agents work on top of Responses? (It uses LangChain chat models
   underneath, so should work if the chat model supports it)
4. Are all models we care about (`z-ai/glm-4.6`, `anthropic/claude-sonnet-4`,
   `google/gemini-2.5-pro`) addressable via Responses, or only OpenAI models?

**When**: Evaluate after Phase 1 (live ingestion) is stable. Don't churn APIs
while iterating on prompts.

**Reference**:
- OpenAI Responses API docs
- OpenResponses (community spec for provider-agnostic Responses) — need to verify
  if this actually exists as a shipped thing

---

## Quality: date hallucination in wiki pages

**Observed**: Agent writes `last_compiled: "2025-01-10T00:00:00Z"` instead of
real time. Training cutoff bleed.

**Fix options**:
1. Have the compiler prompt explicitly pass today's date in the instruction
2. Stop having the LLM write `last_compiled` at all — set it in post-processing
   via a hook or a wrapper
3. Use a `set_compiled_timestamp()` tool the agent must call (forces it through
   the real clock)

**When**: Next prompt iteration.

---

## Quality: wikilink casing inconsistency

**Observed**: Index has `[[api-knowledge-agent]]` but topic pages have
`[[API Knowledge Agent]]`. Lots of broken links.

**Fix**: Either enforce kebab-case everywhere (update prompt), or post-process
via lint auto-fix to normalize. Email-based entity IDs (see above) partially
solves this.

**When**: Next prompt iteration — easy win.

---

## Quality: batch-boundary duplication / fragmentation

**Risk**: Each batch invocation spawns a fresh agent that doesn't see what
previous batches did in its context window. It may:
- Create `topic-x.md` in batch 1, then in batch 4 create `topic-X.md` (different
  case) because it didn't find the earlier file
- Miss cross-references to entities created in earlier batches

**Fix options**:
1. Before each batch, `ls wiki/topics/ wiki/entities/` and include the list in
   the agent's initial message so it knows what already exists
2. Use Deep Agents `subagents=` with a parent agent that tracks state across
   batches
3. Smaller chunks + better context re-injection

**When**: Watch for this in the full 30-day backlog run. May not be an issue at
small scale.

---

## Architecture: move to a real datastore

**Current**: Raw emails + wiki pages are markdown files on disk. Works great
locally, but:
- Hard to query at scale (grep is fine for <1000 pages; gets slow beyond)
- No history browsing except via git
- Attachments pile up (Phase 0 skips them)
- Multi-user access requires shared filesystem

**Options for later**:
- Postgres + PGroonga for structured + full-text search
- Supabase (what `lucasastorian/llmwiki` uses) — similar, with auth baked in
- GCS/S3 for attachments, Postgres for metadata
- Keep markdown as the "render layer" but back it with a DB

**When**: Phase 4 or when grep/read file ops become the bottleneck.

---

## Phase 1: live ingestion (Gmail watch + Pub/Sub)

See `docs/issues/08-phase1-live-ingestion.md` for full design.

---

## Phase 2: wiki UI

BookStack or MkDocs Material serving the compiled `wiki/`. Enables sharing with
team, nice browsing UX.

---

## Phase 3: chatbot over the knowledge base

The original goal. Much easier to build well once the wiki is solid.

---

## Quality: review all tools against Anthropic's tool-writing guide

**Source**: https://www.anthropic.com/engineering/writing-tools-for-agents

Review every tool (`list_uncompiled_emails`, `mark_as_compiled`,
`update_wiki_index`, `append_to_log`, plus Deep Agents built-ins we rely on)
against the rubric in that post. Key checks:

- **Docstring quality**: agent-facing description, not human-facing. Is it clear
  when the agent should call vs skip?
- **Parameter clarity**: names self-documenting? Types constrained enough?
- **Return values**: structured + stable + useful for the agent's next step?
- **Error signaling**: agent can tell "retry", "skip", "human-help" apart?
- **Namespace**: tools grouped logically, no ambiguous "do X" helpers?
- **Side effects documented**: agent knows what the tool actually modifies?

Specific suspects in our code:
- `mark_as_compiled` returns `"marked compiled: {path}"` — low info density, could
  return `{"ok": true, "remaining_uncompiled": N}`
- `list_uncompiled_emails` returns a flat list — should include date, thread_id,
  subject so the agent can plan without re-reading every file
- `update_wiki_index` has no arguments but silently scans all of `wiki/` — agent
  might not realize it's expensive

**When**: After Phase 0 stabilizes. Good low-risk quality pass.

---

## Attachments and inline images — currently disabled

**Status**: Code exists in `src/ingest/attachments.py`:
- `save_attachments()` downloads every attachment to `raw/attachments/{msg-id-short}/`
- `caption_image()` generates a caption via LiteLLM vision model (gpt-4o default)

**But**: Phase 0 runs with `--skip-attachments`, so attachments/images are NOT
being pulled. The raw .md files end up with `has_attachments: true`,
`attachment_files: []`, `inline_images: []`.

**Gaps to close**:
1. Run ingestion WITHOUT `--skip-attachments` on a small batch, verify downloads
   + captions work end-to-end
2. Populate `inline_images[].caption` with vision model output so the compiler
   can use image content (right now images exist as filename references only,
   no content)
3. PDFs / DOCX / XLSX attachments: markitdown already supports these
   (`markitdown[all]` is installed). Pipeline would need a step to convert each
   attachment to markdown and inject/reference from the raw email's body
4. Decide how compiler uses attachment content — summarize into source email's
   body? Treat each attachment as an additional raw source?
5. Storage: attachments can be huge. Currently `raw/attachments/` is gitignored
   (good). For 30-day backlog, measure size.

**When**: After Phase 0 quality fixes. Then run a batch WITH attachments and
see how compilation changes.

---

## Observability: evaluate LiteLLM UI logs vs Langfuse

**Source**: https://docs.litellm.ai/docs/proxy/ui_logs_sessions

We already saw the LiteLLM UI logs when the user shared them — per-call cost,
token counts, model, latency, user. That's actually a lot of the observability
we'd want.

**Tradeoff**:
| Feature | LiteLLM UI | Langfuse |
|---|---|---|
| Per-call cost/tokens | yes | yes |
| Session/trace grouping | sessions (newer) | traces |
| Agent step visualization | no | yes (native for LangChain/LangGraph) |
| LLM-as-judge eval | no | yes |
| Prompt versioning | no | yes |
| Already running | yes (imllm.intermesh.net) | no |

**Recommendation for later**:
- LiteLLM UI is sufficient for cost/latency/error monitoring. Zero extra infra.
- Add Langfuse ONLY when we need trace visualization, eval suite, or prompt
  A/B testing.
- For Phase 0-1: LiteLLM UI is enough. Revisit for Phase 2 quality work.

**When**: Before Phase 3 chatbot (evals matter more there).

---

## Future: multi-list ingestion via Google Groups

See memory: `email_kb_multi_list.md`. Instead of per-user OAuth, use Google
Workspace domain-wide delegation + Admin SDK to enumerate groups and watch each.
Would let us ingest all authorized mailing lists without new credential flows.
