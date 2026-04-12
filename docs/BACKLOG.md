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

**P0 (fix before next full compile)** — ALL SHIPPED (see CHANGELOG for details):

1. ~~Wikilink casing~~ → **DONE** (235dc74, b95f7da): prompt + lint normalizer
2. ~~Date hallucination~~ → **DONE** (ddd0c5a): `stamp_page_compiled_at` tool
3. ~~Non-person "entities"~~ → **DONE** (ae5f0e1): `wiki/systems/` category added
4. Orphan entity back-links → still open: not blocking compile, low value
5. ~~Index.md stale~~ → **DONE** (compile_all.py post-batch regen)

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

## Performance: parallelize compilation (DRAFTED — `scripts/compile_parallel.py`)

**Status**: Script shipped (commit b6368d6). Not yet benchmarked at scale.
Overnight plan (`docs/reviews/overnight-plan-*.md`) recommends `--concurrency 4`
for the main backlog compile. Still TBD: per-batch retry/timeout, Makefile
wire-up.

---

## [original content below]

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

## Schema versioning, migration, and changelog for future agents

**Why**: As we iterate on page structure, relations, and frontmatter fields, older
pages will fall out of spec. We need a way to:
1. Version the schema (e.g., `schema_version: 2` on every page)
2. Migrate pages between versions without losing data
3. Leave a changelog that explains WHY a decision was made (so a future LLM agent
   compiling against v5 understands why a v2 page did something differently)

**Pieces to build (Phase 2+)**:
- `docs/SCHEMA.md` with versioned spec (v1, v2, ...) and changelog entries
- `scripts/migrate_wiki.py migrate --to v2` that rewrites pages to match new
  schema; must be idempotent and resumable
- Every wiki page gets `schema_version` in frontmatter
- `docs/DECISIONS.md` or ADRs for "why we did X" (e.g., "switched entity IDs
  from name-slug to email-slug on 2026-05-01 because...")
- Snapshot before migrating so you can roll back

**Notes**:
- Even v0→v1 will be a migration (our current pages have inconsistent `last_compiled`
  hallucinations — a migration could stamp them all to a fresh known-bad marker
  like `"unknown"`)
- This also helps when comparing compiler prompt changes: snapshot v1, iterate
  prompt, run on same raw emails, diff outputs

**When**: Before Phase 3 chatbot. Essential for a multi-month evolving system.

---

## Thread-aware compilation — not yet implemented

**Current state**:
- Gmail API gives us `thread_id` (string like `19d431cd45e0b512`) on every
  message — fully authoritative, no piecing together needed
- We DO capture it in `raw/*.md` frontmatter
- `list_uncompiled_emails` tool returns `thread_id`
- Compiler prompt says "Group by thread_id when possible"

**What's missing**:
- No `list_uncompiled_threads` tool to let agent batch a whole thread
- No thread state model (open / decision_pending / decided / amended / closed /
  reopened)
- Agent might compile the reply first (because it's chronologically later) and
  miss context from the original
- Multi-email threads show up as separate compile steps

**Why it matters**:
- A reply can reverse a decision ("Actually, let's go with option B instead")
- Discussion threads need to compile as a unit for proper synthesis
- Supersession detection is harder without thread context

**Proposed design**:
1. Add `list_uncompiled_threads` tool: groups uncompiled emails by `thread_id`,
   returns `[{thread_id, emails: [{path, date, ...}], participants, subject}]`.
2. Compile-all CLI batches by thread, not by email, so one agent invocation
   sees all emails in one thread at once.
3. Add `thread_state` field on relevant wiki pages: open / decided / etc.
4. For live mode (Phase 1): "quiet period" — wait 30 min after last thread
   activity before compiling. Prevents mid-conversation compilation.

**When**: Phase 1 or Phase 2. Critical before full backlog (30 days, 3000+
emails).

---

## Ordering guarantees — current behavior and known gaps

**Sequential `compile_all.py`**:
- `list_uncompiled_emails` sorts by filename (filenames start with `YYYY-MM-DD`)
- Batches handed to agent strictly oldest → newest
- Within a batch (3 emails): also chronological
- **Supersession works naturally**: agent sees old policy first, then the email
  that supersedes it

**Parallel `compile_parallel.py`**:
- Groups by `thread_id`, sorts WITHIN thread chronologically
- Threads themselves processed CONCURRENTLY (no order between threads)
- Supersession within a thread: fine
- Cross-thread supersession (rare but real: "that reimbursement policy we
  discussed in [thread A]? we're changing it, see [new thread B]"): agent
  won't see thread A context while processing thread B if they're processing
  concurrently

**Mitigation for 30-day default run**:
- Stick with sequential `compile_all.py` for the first full pass
- Use parallel only for incremental updates (newly-arrived emails) where
  cross-thread supersession is less common

**Long-term fix** (Phase 2+):
- Two-pass compile: pass 1 creates pages per email (parallel, fast); pass 2
  runs a linter-agent that reads the whole wiki and detects cross-thread
  supersession + conflicts
- Or: serialize the "supersession detection" sub-task even while other steps
  parallelize

---

## Multiple mailing lists

**The dedup story is already fine** — we key off `Message-ID` (global, set by
mail server), not Gmail's `thread_id`. Same email delivered to two lists
produces one file because the hash collides to the same filename.

**Changes needed to support `list A + list B`**:

1. `.env`: `MAILING_LIST_ADDRESSES=list-a@company.com,list-b@company.com`
   (current key `MAILING_LIST_ADDRESS` stays for single-list mode).
2. `src/config.py`: expose `mailing_list_addresses: list[str]`.
3. `scripts/ingest_backlog.py`: either loop the list, or build one Gmail query
   `list:A OR list:B after:...`.
4. `src/ingest/parser.py`: add `mailing_lists: [A, B]` to raw frontmatter when
   we can detect which list delivered it (look at `Delivered-To` or
   `List-Id` header).
5. Compiler prompt: unchanged. It processes raw files regardless of list. The
   `list:` provenance field is available if the compiler wants to label pages.

**Edge cases**:
- Same email sent to both lists → one raw file (dedup wins), `mailing_lists` has both.
- Separate threads on same topic in two lists → two separate thread_ids, two
  timelines. Compiler may create separate pages; cross-reference via the topic.
- Budget: ~2× emails = ~2× compile cost. Use `compile_all --limit N` to
  stagger.

**Authentication**:
- Current OAuth pulls `me` mailbox. If you're on all the lists, no changes.
- If not: need domain-wide delegation (service account impersonating users) —
  memory note already captures this.

**When to build**: after live mode is proven on one list. Probably Phase 2.

---

## Agent meta-commentary (lessons-learnt from each compile batch)

**Idea**: Each compile batch is a test. The LLM already forms judgments
about what was easy/hard/ambiguous. Capture that structured commentary to
compound improvement over runs.

**Design**:
- New tool `log_insight(category, message, suggested_action="")` — action
  is optional. User explicitly said: it's fine to emit raw doubts like
  "this is conflicting and confusing, not sure which is right" without
  proposing any fix. Just the signal is valuable.
- Categories: `missing_page` | `prompt_ambiguity` | `tool_gap` |
  `supersession_doubt` | `conflict_candidate` | `pattern_noticed` |
  `improvement_suggestion`
- Writes a structured entry to `docs/insights/YYYY-MM-DD.md` with:
  - ISO timestamp
  - batch_id / thread_id (if available)
  - category, severity (low/medium/high), message, suggested_action
- Prompt: "After completing a batch, if anything was genuinely ambiguous
  or would have benefited from a missing tool/page, call log_insight
  once. Otherwise skip."

**Value**:
- Missing-page flags → auto-seed stubs in a nightly job
- Ambiguity patterns → tighten prompt
- Tool gaps → build the tools they ask for
- Weekly human review of `docs/insights/*.md` in <10 min

**Cost**: a few tokens per batch at most. Compounding benefit.

**When**: After this thread-batching test validates. Probably before the
next prompt iteration.

---

## Langfuse integration (self-hosted)

**Instance**: `https://langfuse.intermesh.net`

**Code status**: hooks already exist. `src/compile/compiler.py::
get_langfuse_handler()` reads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_HOST`, `LANGFUSE_ENABLED` from `.env` and attaches as a callback
to every compile run. Today it's disabled (`LANGFUSE_ENABLED=false`).

**To turn on**:
1. Get credentials from Langfuse admin UI at langfuse.intermesh.net
2. Set .env:
```
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://langfuse.intermesh.net
LANGFUSE_ENABLED=true
```
3. Subsequent compile runs will stream traces to the Langfuse instance

**What it adds beyond LiteLLM UI**:
- Trace graphs — every tool call visible per batch (not just LLM cost)
- Prompt A/B testing — version prompts, compare on same inputs
- LLM-as-judge eval — automated scoring of compile quality
- Sessions view — journey across batches
- Free tier is self-hosted; no per-request fee

**When**: After Phase 0 stabilizes (probably after thread-batching proves out
over a full overnight run) and when we want to start tuning prompts
systematically.

---

## Trivial-message filter (skip "+1", "thanks", "lgtm" replies)

**Idea**: 40-60% of replies in corporate mailing lists are acknowledgement
noise that pays full compile cost but adds zero to the wiki.

**Two levels**:

**Level 1 — deterministic** (no LLM, cheap):
- Body < 20 words AND has `in_reply_to` (i.e., not thread-starter)
- No URLs, no numbers, no code blocks, no attachments
- Body matches regex blocklist: `^(thanks|thank you|\+1|👍|great|amazing|
  lgtm|ship it|congrats|nice|sweet|awesome|\w+\+\+)\.?\s*$`
- Mark with `skip_compile: true` at ingest time

**Level 2 — cheap classifier** (optional, ~$0.001/call):
- For messages 20-60 words, gpt-4.1-nano classifies: "substantive" or "ack"
- Runs during ingest, zero impact on compile

**Compile step**:
- Skips any email with `skip_compile: true` — marks it compiled
  immediately (bookkeeping) without LLM call
- Summary of acknowledgements per thread added to thread's wiki page:
  "Additional +1s from: [list of names]"

**Expected savings**: 40-60% fewer LLM calls on mailing-list corpora.
Probably bigger than thread-batching alone.

**When**: After thread-batching proves out. Can be layered on top.

---

## Storage tier: local → GCS → Cloud SQL

**Current (Phase 0)**: local disk only. raw/, wiki/, .snapshots/ all
gitignored. GitHub holds code + docs + audit reports only.

**Why not GitHub for content**:
- Compliance risk — internal emails contain names, customer data, ticket IDs,
  roadmaps. Even private GitHub has AI-training caches.
- 100MB per-file limit; attachments will break this.
- Git remembers forever — awkward for retention policies.
- Can't do per-folder IAM.

**Phase 1 target (when pipeline stable)**: GCS in voice-eval-stack-im.

### Bucket layout

```
gs://voice-eval-stack-im-email-kb/
  raw/                    # immutable .md, one per email (source of truth)
  attachments/{msg_id}/   # blobs (PDFs, images) keyed by message_id short hash
  wiki/                   # compiled markdown pages (regenerable)
  snapshots/{label}/      # pre-compile backups (cleanup after 30d)
  site/                   # optional: prebuilt static wiki HTML for hosting
```

### Sync mechanism

- `scripts/sync_to_gcs.py` uses `gsutil rsync -d -r <local> gs://...`
- Trigger: after every successful compile OR on a cron (10-min interval)
- Can also pull: `gsutil rsync gs://... <local>` on a second machine
- State: keep `.gcs_last_sync` with ISO timestamp

### Cost (projected for 1 year at current rate)

| Tier | Size | Monthly |
|---|---|---|
| Raw (hot, 6 months) | ~500MB × 2 = 1GB | $0.02 |
| Raw (cold, 6-12 months) | 1GB | $0.004 |
| Attachments (when enabled) | 5-50GB | $0.10-1.00 |
| Wiki | 100-500MB | $0.01 |
| Snapshots (rolling 7d) | ~500MB | $0.01 |
| **Total** | — | **<$2/month** at full scale |

Egress (if we serve publicly): $0.12/GB. If 10 viewers × 10MB/visit/day =
100MB/day = 3GB/month = $0.36. Inconsequential.

### Serving the wiki

Three options, cheapest first:

1. **Local MkDocs dev server + phone on LAN** (current): free
2. **Static build → GCS static website hosting**: $0.01/month plus egress.
   Not HTTPS by default (need Cloud CDN or custom domain+LB).
3. **Static build → Cloud Run**: $free under free tier, HTTPS automatic,
   can add Google IAP (Identity-Aware Proxy) for org-only access. **This
   is what we'd pick for team access.**

### Speed

- Build time: MkDocs Material builds 10k pages in ~30s. Not a concern.
- Read time: served as static HTML from Cloud Run or GCS — <100ms cold, <10ms warm.
- GCS-to-build: rsync every 10 min is ~5s for incremental changes.
- No per-request GCS reads = no per-request cost.

### Rollout sequence (when we promote)

1. Create bucket with uniform bucket-level IAM
2. Grant voice-eval-stack-im service account read/write
3. Initial `gsutil rsync` uploads current raw/ + wiki/
4. Modify `scripts/watch_and_compile.py` to rsync at end of each tick
5. Deploy static site to Cloud Run with IAP: `gcloud run deploy
   --source site/ --allow-unauthenticated=false` + IAP binding
6. Add `.env`: `GCS_BUCKET`, `WIKI_URL` for the deployed site
7. README section: "Accessing the wiki"

**Do this when**: (a) compile pipeline is stable for a week, OR (b) you
want to access the wiki from another machine, OR (c) another team member
wants read-only access.

**Phase 3 target (search at scale, >5k pages)**: Cloud SQL Postgres (already
running in the GCP project). PGroonga for full-text, pgvector for semantic.

**Phase 4 target (team access to wiki)**: MkDocs static build → GCS static
website → Cloud Run + IAP. Google sign-in gated to the org.

**When to promote Phase 0 → 1**: second machine needs access, or we add
automated ingestion (watch_and_compile.py running 24/7).

---

## Future: multi-list ingestion via Google Groups

See memory: `email_kb_multi_list.md`. Instead of per-user OAuth, use Google
Workspace domain-wide delegation + Admin SDK to enumerate groups and watch each.
Would let us ingest all authorized mailing lists without new credential flows.
