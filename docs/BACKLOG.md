# Backlog — "For later" ideas and open questions

Not scheduled, just captured so we don't forget. Promoted to issues when we pick
them up.

---

## Most-cited topics / entities panel on the index page (2026-04-13)

User request during the index-progress work: surface the top-N most
cited topics / entities / systems on `wiki/index.md` — e.g. a
"Top 10 people by mention count" + "Top 10 topics by source count"
block. Gives visitors a fast way into the hot subjects without
drilling per-category.

Implementation path: the `message_touched_pages` table (PR3 /
PR #31) already stores which messages touched which wiki pages. A
query like `SELECT page_id, count(*) FROM message_touched_pages
GROUP BY page_id ORDER BY 2 DESC LIMIT 10` joined to `wiki_pages`
gives the answer cheaply. Until that PR merges, a fallback is "count
`sources:` list length per wiki page" via a filesystem scan — same
signal, slower.

Ship after PR #31 merges. Don't ship before the 500-email milestone
— the current `Compile progress` + `Emails per week` blocks are
already more surface than the site had.

---

## Migrate hand-rolled coordinator hooks → LangChain AgentMiddleware (2026-04-13)

**Priority: high.** Found during today's guardrails work. LangChain v1 /
DeepAgents ships a native middleware system that does exactly what
we've been hand-rolling in `scripts/compile_all.py`. Migrating cuts
code, colocates verification with execution, and opens the door to
proper "active self-correction" (coordinator re-invokes the agent with
evidence-based feedback, not just leaves it pending for the next run).

### What we have today (hand-rolled, scattered across compile_all.py)

| Today | What it does |
|---|---|
| `_mark_batch_compiled(batch, wiki_dir)` | post-agent: verify wiki citation, flip messages.compile_state |
| `_mark_batch_failed(batch, error)` | post-agent exception handler |
| `_collect_cited_raw_paths(wiki_dir)` | the citation-evidence scan |
| `_stamp_recently_modified_pages(wiki_dir, since, model)` | post-agent: stamp `last_compiled` on mtime-new wiki pages |
| `_append_batch_log(batch_idx, batch, result, wiki_dir)` | post-agent: write structured log row |
| `start_run() / finish_run()` via try/finally wrap | per-run observability |
| `scripts/reconcile_compile_state.py` (strict mode) | retro-active citation check |
| Prompt reminders to "not invent entity slugs, call `create_entity`" | pre-model reminder encoded as text |

### What LangChain v1 ships natively

Docs: https://docs.langchain.com/oss/python/langchain/guardrails (the
whole "After agent guardrails" page is this exact pattern).

- **`AgentMiddleware`**: the idiomatic wrapper. Subclass it, pass to
  `create_deep_agent(middleware=[...])`.
- **`wrap_model_call(request, handler)`**: called on every LLM
  invocation. Can mutate the request (inject system reminders), run
  the handler, then post-process the response. Perfect for
  step-count-reminder (the Claude-Code pattern from the other
  backlog entry), context pruning, enforced exit signalling.
- **`wrap_tool_call(request, handler)`**: called on every tool
  invocation. Pre-validate args, run the tool, verify the result,
  optionally rewrite the `ToolMessage` the agent sees.
- **`interrupt_on`**: pauses the agent at specified tool calls.
  Coordinator inspects live state, can send feedback back to the
  paused run. This is the "active self-correction" primitive.
- **`response_format` (Pydantic `BaseModel`)**: schema-validated
  final output. Replaces our prose `append_to_log` instruction with
  a typed structured summary the coordinator reads directly.
- **Node-style hooks** (`before_agent`, `after_agent`, etc.): the
  simplest form when you don't need to intercept individual tool
  calls.

### Migration sketch

One new module, one class:

```python
# src/compile/middleware.py
class CompileCoordinatorMiddleware(AgentMiddleware):
    def __init__(self, *, batch, wiki_dir, run_id):
        self.batch = batch
        self.wiki_dir = wiki_dir
        self.run_id = run_id
        self.batch_start = datetime.now(UTC)
        self.step_count = 0

    def wrap_model_call(self, request, handler):
        self.step_count += 1
        if self.step_count % 30 == 0:
            request.messages.append(SystemMessage(
                f"{self.step_count} tool calls so far. Wrap up and return."
            ))
        return handler(request)

    def wrap_tool_call(self, request, handler):
        return handler(request)

    def after_agent(self, state):
        # The current _mark_batch_compiled + _stamp_recently_modified_pages
        # + _append_batch_log all collapse here. Citation scan scoped to
        # this batch's wiki deltas (mtime >= self.batch_start).
        ...
```

### Why it's worth doing

1. **Active self-correction** — today the 714 entity-only-cited emails
   just stay pending. With `interrupt_on` + a post-tool hook, the
   coordinator can re-invoke the agent inline: *"you wrote an entity
   mention for `raw/foo.md` but no topic page cites it yet. Write
   the topic page before you return."*
2. **Single source of truth** for the compile loop's verification
   rules. Today they're scattered across `compile_all.py` +
   `reconcile_compile_state.py` + the tests the W4-W6 PRs added.
3. **Testability** — middleware is testable with fake agent state,
   no real deep_agent needed.
4. **The step-count-reminder** backlog entry collapses into a 10-line
   `wrap_model_call` instead of its own separate design.

### Scope + ordering

- `src/compile/middleware.py` (~200 lines) — one class, 3-4 methods
- `src/compile/compiler.py` — inject middleware into `create_deep_agent`
- `scripts/compile_all.py` — drop `_mark_batch_compiled`,
  `_mark_batch_failed`, `_stamp_recently_modified_pages`,
  `_append_batch_log` (~150 lines deleted). Keep `_group_by_thread`
  + the runner shell.
- Tests move from `tests/test_compile_all_*.py` →
  `tests/test_compile_middleware.py` (fake agent state, no DB).

Don't ship before PR #26 (`create_entity`) merges — that gives
`wrap_tool_call` a concrete target. Don't ship before the 500-email
milestone — current scaffolding works, migrating now trades forward
motion for cleanup.

---

## Design principle: coordinators verify, LLMs propose (2026-04-13)

**Rule**: every LLM-claimed state transition must be backed by an
independent external-evidence check. The evidence has to show the agent
did the WORK, not just that it called the tool or set a flag.

**Why it exists**: we discovered this the hard way today. Over three
separate incidents in one compile session:

1. `mark_as_compiled` — agent called it on 9/28 batch emails, forgot on
   19/28. Script reported "28/30 processed", DB disagreed. Coordinator
   now flips state based on wiki-citation check, not agent tool calls.
2. Entity slug generation — agent invented `vishakha-indiamart`,
   `arjun-gaur-clean`, `akash-singh6` when it struggled with display
   names. Deterministic `email_to_slug` in `src/compile/entities.py`
   replaced LLM judgment.
3. Reconcile-by-citation — a naïve "email is cited somewhere in wiki"
   rule would have falsely flipped 715 of 748 candidates. The agent
   had name-dropped them into entity `sources:` lists but never wrote
   topic pages. Strict rule: citation in a CONTENT page (topic, system,
   policy, timeline, conflict) is required. Entity-only citation gets
   left pending so the next compile batch re-claims the email —
   self-healing loop.

**Two patterns to apply going forward**:

- **Passive self-healing**: when the coordinator detects failed
  verification (e.g., email cited only in entity), leave the state
  pending so the queue re-claims it. The LLM gets another shot.
- **Active self-correction (unbuilt)**: after each batch, inspect what
  the LLM claimed vs. the evidence. If mismatched, inject a
  system-reminder-style message into the next turn: *"You touched
  these 3 emails but no topic page cites 2 of them. Go back and
  finalize them before returning."* This is the Claude-Code-style
  step-count-reminder pattern from the separate agent-scaffolding
  investigation backlog entry.

**Rule of thumb for reviewers**: whenever you see a tool that writes
state the coordinator could compute itself, ask "why isn't the
coordinator doing this?" If the answer is "because the agent is the
one with the context," verify the agent's claim externally before
trusting it.

**Examples already shipped this session**:
- `src/db/messages.py::find_by_raw_path` + coordinator-owned
  `finish_message_compile` in `scripts/compile_all.py`
- `src/compile/entities.py::email_to_slug` (pure function)
- `scripts/compile_all.py::_collect_cited_raw_paths` (citation check)
- `scripts/compile_all.py::_stamp_recently_modified_pages` (mtime-based)
- `scripts/compile_all.py::_append_batch_log` (structured, not
  LLM-prose)
- `scripts/reconcile_compile_state.py` with strict-mode default (only
  content-page citations count as evidence)

---

## Priority index (as of 2026-04-13T11:30Z)

Catalog migration has landed (`messages` table is source of truth; users/threads/wiki_pages
in-flight via PRs #22–#35). Compile pipeline runs but still produces stub-heavy entity
pages. This index orders the sections below by ship-value so we don't re-scan the whole
file each time.

### 🔥 NOW — small, high-leverage papercuts
1. **Verify prompt caching** — cheap ($2–5 spike), directly affects per-batch cost.
   Key has the budget. See § Verify prompt caching.
2. **Trivial-message filter (skip `+1`, `thanks`, `lgtm`)** — deterministic regex at
   ingest. Every batch re-processes these today. See § Trivial-message filter.
3. **Compile stall detection** — overnight wrapper has `timeout 900`; inner
   `compile_all.py` doesn't. See § Compile stall detection.
4. **De-noise entity pages: drop CC-only sources** — entity bloat partly fixed via
   `dedupe_sources`, CC-only still pulls entities into every announcement thread.
   See § De-noise entity pages.
5. **`log_insight` tool / agent meta-commentary** — captures "this structure is weird" /
   "I'm uncertain about X" during compile so humans can act. See § Agent meta-commentary.

### 🟡 SOON — medium effort, set up once, keeps paying
6. **Agent scaffolding: step-count reminders, pre-model hooks, context pruning** —
   directly attacks recursion-limit-hits and 8-min hangs at the agent level. See
   § Agent scaffolding investigation.
7. **Per-batch random model A/B with stats** — key allows `minimax-m2.7`, `-m2.5`,
   `glm-5`, `glm-5.1` but proxy `/v1/models` doesn't surface them; separate ticket to
   ask proxy admin to provision routes. See § Per-batch random model A/B.
8. **Inline citations (replace long Sources)** — depends on catalog PR3 (#31) landing.
   See § Inline citations.
9. **Entity page content compaction** — dedupe_sources trimmed sources only; page
   bodies still accumulate narrative across batches. See § Entity page bloat.

### 🟢 LATER — bigger surfaces; wait for catalog cascade
10. **Phase 1 live ingestion (Gmail watch + Pub/Sub)** — depends on ingest_cursors (#27).
11. **Phase 2 wiki UI** — post-catalog, post-search.
12. **QMD (Tobi Lütke) local semantic search** — needs real prose content first
    (post source-strip per catalog PR3).
13. **Multiple mailing lists** — architectural change after current list proves clean.
14. **Agent skills + MCP server** — downstream consumers; wait until output is worth
    consuming.
15. **Storage tier local→GCS→Cloud SQL** — migration prep, not blocking today.

### ⚪️ Research / reading (not ship)
16. Reading list — Anthropic engineering posts.

### ✅ Already shipped — historical record, don't re-promote
- **Quality: date hallucination** — DONE via `stamp_page_compiled_at` tool
- **Quality: wikilink casing** — DONE via prompt + lint normalizer
- **Architecture: real datastore** — DONE via Postgres `messages` (commit ecbd4ad);
  users/threads/wiki_pages in-flight
- **Langfuse self-hosted integration** — DONE via #15 (pin + bounded timeouts);
  server-side hang tracked in issue #17
- **Langfuse callback stalls** — mitigation DONE via #15; root cause in issue #17
- **Schema: entity identity name→email** — IN PROGRESS (#24, #26)
- **Performance: parallelize compilation** — DRAFTED (`scripts/compile_parallel.py`),
  not benchmarked
- **Thread-aware compilation** — DONE
- **Phase 0 review: first full compile observations** — archived; all P0 shipped
- **Review tools vs Anthropic tool-writing guide** — partial (issue #4 open)

### 🧹 Governance debt (visible right now)
- **CHANGELOG discipline has slipped** — 12 open PRs (#22–#35) + 4 recently merged
  (#16, #19, #20, #21) did not update `CHANGELOG.md`. Enforce going forward via a
  PR guardrail workflow. Not retroactively fixing.
- **No CI workflow for tests/lint** — `.github/workflows/` only has Claude action
  files. A simple `ci.yml` running `uv run ruff check` + `uv run pytest` would catch
  regressions quickly. Separate papercut.

---

## Per-batch random model A/B with stats tracking (2026-04-13)

**Why**: `z-ai/glm-4.6` is the current default, but the proxy exposes several
cheaper / faster / smarter candidates. We want to learn which model actually
performs best on OUR compile workload (not benchmarks), measured by wiki
quality, not just cost per batch.

### Proxy inventory — what's actually available

Key `email-kb-wiki` explicitly lists these in `/key/info`:
- `minimax/minimax-m2.7` — **callable ✓** (proxy responds 200 on POST
  /v1/chat/completions, even though `/v1/models` doesn't advertise it)
- `minimax/minimax-m2.5` — **callable ✓**
- `z-ai/glm-5` — **callable ✓**
- `z-ai/glm-5.1` — **callable ✓**
- `all-team-models` — wildcard; callable below are provisioned on the
  team: `z-ai/glm-4.6` (default), `minimax/minimax-m2`, `minimax-m2.1`,
  `z-ai/glm-4.5-air`, `deepseek/*`, `qwen/*`, etc.

**Note on `/v1/models`**: it returns a filtered list that excludes the
four above, so any tool enumerating the catalog misses them. Tests rely
on a direct call (verified 2026-04-13 11:30Z). If a future tool
auto-discovers models, whitelist these four explicitly.

### Design — random-per-batch with stats

1. **Model pool config** — list of candidate model IDs + weights (maybe
   uniform to start) in `src/config.py` or a YAML file. Operator flag:
   `--model-pool random` vs current `--model <id>`.
2. **Seed per batch** — `compile_all.py::run_batch()` picks a random model
   from the pool at the start of each batch. Sticky for the whole batch
   (all emails in a thread-group use the same model) so results are
   comparable.
3. **Stamp the model on every compiled row** — already partly there:
   `scripts/compile_all.py` passes `model_name` to `create_compiler`;
   `stamp_page_compiled_at` writes `updated_by` on the wiki page. Extend
   `messages.compile_model` column (needs migration) so we can join model
   → outcome in SQL.
4. **Stats rollup** — new `scripts/model_stats.py` (or extend
   `scripts/stats.py`):
   - cost per batch by model (from LiteLLM `usage.cost` or `key/info` delta)
   - avg time per email
   - recursion-limit hits / failures per model
   - wiki-quality proxy metrics: avg `update_count`, avg page size, stub
     rate on pages touched by each model
   - source-dedup ratio, wikilink breakage rate (advisory validator output)
5. **Backstop**: don't randomize in production until we've burned a small
   exploration budget (~$10) in shadow mode on a subset of uncompiled
   emails. First pass: 50-email batch each from the candidate set, same
   thread grouping, then manual inspection + stats diff.

### What this does NOT need to be
- Not a full multi-armed-bandit scheduler. Uniform random is enough for v0.
- Not model routing by content signal. Pick-per-batch is simpler and
  comparable.
- Not an eval harness — stats-driven, not rubric-scored (that's a separate
  BACKLOG item: reading list → "demystifying evals for ai agents").

### Prerequisites / cross-deps
- Issue #17 (Langfuse server hang) — useful for per-model latency traces,
  not blocking. Can start without tracing.
- Catalog PR cascade (#21/#22/#23/#24) finishing — `messages.compile_model`
  lives naturally next to `compile_state`, `compile_attempts`.

### Smallest-shippable slice
Add `--model <id>` to `compile_all.py` (probably already there), plus a
`--model-pool a,b,c` that picks random per batch. Stamp model to a new
`messages.compile_model` column in a tiny migration. Leave stats rollup
for a second PR.

---

## Agent scaffolding investigation — step-count reminders, hooks, context pruning (2026-04-13)

**Problem observed today**: 1-email compile batches take 8-14 minutes at
z-ai/glm-4.6. Pathological threads (e.g. 16-email Tender Audit Automation,
`thread_id=19b10ae9d236af98`) hit the default 150-step LangGraph
recursion limit and crash after ~14 min burning ~$0.20 per failure. The
workaround landed — `--recursion-limit 60` (see `scripts/compile_all.py`) —
which at least fails fast instead of bleeding budget, but it does NOT
attack the root cause: the agent loops because it has no "you've done
enough" signal beyond `mark_as_compiled`, context bloat confuses the
model about what it already did, and the agent can't self-notice
repetition on the same entity page.

**The right fix is agent-level scaffolding, not a harder recursion cap.**
This is a discrete research task and should be picked up by a separate
agent (don't interleave with the forward compile push).

What to deeply investigate:

1. **Step-count reminder middleware** (the Claude Code pattern).
   Wrap `create_deep_agent` with a pre-model hook that counts turns and
   injects a `system` message every N steps:
   _"You have used N of your ~40 expected tool calls for this email. If
   the core pages are updated, call `mark_as_compiled` and stop."_
   Likely surface: `src/compile/compiler.py:create_compiler`. LangGraph
   supports this via `pre_model_hook` or a compiled-graph wrapper.
   References: https://langchain-ai.github.io/langgraph/,
   https://github.com/langchain-ai/deepagents.

2. **Hooks for tool-call de-duplication.** If the agent has called
   `edit_file` on `entities/ruchi-gupta.md` three times in one batch,
   that's thrash. A hook can detect it and reply "you already edited
   that file twice, move on."

3. **Context pruning.** After N steps, summarize the earlier turns or
   drop the raw file bodies from the running message history. The agent
   doesn't need the raw markdown re-shown every turn.

4. **Stronger exit discipline in the prompt** (`src/compile/prompts.py`).
   Tool-audit review at `docs/reviews/tool-audit-20260413T050000Z.md`
   already noted exit conditions are vague. Codex's priority review
   (`docs/reviews/codex-priority-review-20260413T090000Z.md`) flagged
   the prompt as actively fighting the Postgres migration.

5. **Parallel compile with safety.** `scripts/compile_parallel.py`
   already exists but is marked experimental. Shared-entity-page races
   are the blocker. Once per-page locking is on the catalog
   (Codex priority review PR3), parallel becomes safe.

6. **Measure before optimizing.** Instrument the agent to emit per-step
   metrics: tool name, duration, cumulative context size, cumulative
   cost. Persist to `compile_runs` (Codex priority review PR5). Then we
   can tell which of the four root causes above actually dominates for
   a real batch — right now we're guessing.

Success criteria for this investigation:
- Median per-email compile time under 3 minutes at z-ai/glm-4.6
- <10% batch failure rate on backlog of 6,500+ emails
- No repeated `edit_file` on the same page within a single batch

Do NOT implement before running a measurement pass. A wrong hook
design wastes more time than the loops it replaces.

---

## Langfuse callback stalls compile when proxy is slow (2026-04-13)

**Symptom**: `compile_all.py` hangs after the first "running compilation"
log line, no further output for 8+ minutes. Log shows:
`Failed to export span batch code: None, reason: HTTPSConnectionPool(host='langfuse.intermesh.net', port=443): Read timed out.`
Killing the process and re-running with `LANGFUSE_ENABLED=false` lets the
exact same batch finish in ~30s per email.

**Root cause hypothesis**: the langchain `CallbackHandler` from langfuse
v3 emits per-step events to a Langfuse client whose default flush
interval (0.5s) and httpx timeout (5s) compound when the langfuse proxy
is slow. Each agent tool call blocks waiting on a span flush.

**Workaround in use**: `LANGFUSE_ENABLED=false` for compile runs.
**Real fix**: in `src/compile/compiler.py:get_langfuse_handler`, pass
`flush_at=100` and a short `httpx_client` timeout (~2s) to `Langfuse(...)`
so flushes batch and fail fast. Confirm against
https://langfuse.com/docs/sdk/python/sdk-v3 once we have time.

---

## Entity pages compile to stubs (2026-04-13)

**Observation** (audit `docs/audits/audit-20260413T081547Z.md`): 72% of
entity pages are <500B (221/307). Compare topic pages: 0 stubs, avg 2.7KB.
Spot-checks (`csd-tester.md` at 124B with 1 source; `ruchi-gupta.md` at
152B with 10 sources) show the agent extracts an `Email:` line and a
`Related` list but no role context.

**Why it matters**: Codex's priority review flagged "team-ready browse"
as a non-goal for week 1 partially because of this. A 152B Ruchi Gupta
page is a hyperlink target, not knowledge.

**Probable fix surface**: prompt instruction. The agent is told to
create entity pages but not to enrich them on subsequent mentions. Add
a "for each entity touched, append a one-line role-in-this-thread note
to their page" rule in `src/compile/prompts.py`. Run a small batch and
re-audit. Trade-off: longer agent steps per email = higher cost.

---

## QMD (Tobi Lütke) — local semantic search for the wiki (2026-04-13)

https://github.com/tobi/qmd — TypeScript CLI, local-first, three-stage
retrieval: BM25 → vector embeddings → local LLM rerank. ~2 GB of GGUF
models, no cloud APIs.

Why it's useful for this project (complementary to #8, not overlapping):

1. **Agent tool during compile** — replaces the `list_wiki_pages` +
   slug-guess loop. Agent calls `qmd search "voice eval automated"` and
   gets top-3 existing pages ranked by semantic relevance. This is the
   right fix for the duplicate-page problem (compiler keeps making
   `foo-new.md` because it can't tell there's already a `foo.md`).
2. **Replaces mkdocs built-in search** — lunr is weak on a 463-page wiki,
   QMD rerank is substantially better. Ship once the wiki is prose-heavy
   (after issue #8 Phase 4 strips the source bloat).
3. **Chat-with-wiki** — future mobile UX, "what's the latest on iOS fix?"
   QMD is the retrieval layer.

**Tactical ordering**: don't adopt before #8 Phase 2 ships — indexing 95%
frontmatter is wasted. After strip-sources, the wiki is real knowledge
and QMD's rerank has signal to work with.

Pair with the Anthropic "writing tools for agents" reading below — QMD
exposed as a compile-time tool is the first candidate for our next tool
improvement.

---

## Verify prompt caching is actually working (2026-04-13)

https://openrouter.ai/docs/guides/best-practices/prompt-caching#provider-sticky-routing

**Why it matters**: every compile batch sends the same ~6 KB system prompt
+ accumulating tool-call history. If prompt caching isn't hitting, we're
paying for hot tokens on every turn. Overnight we burned $15.69 on 187
messages — plausibly ~half of that is uncached repeat context.

**Our stack**: LiteLLM proxy → OpenRouter → `z-ai/glm-4.6`. Two open
questions:

1. Does `z-ai/glm-4.6` even support prompt caching on any of its OpenRouter
   providers? (Not all do; Anthropic/OpenAI models do by default, Chinese
   models often don't.)
2. Does LiteLLM pass through the OpenRouter provider-sticky-routing hint
   needed for cache hits? The hint usually goes via
   `extra_body={"provider": {"order": [...], "allow_fallbacks": false}}` or
   a request header like `X-Title`.

**How to test cheaply** (~$2–5):
- Run a compile batch with `litellm --detailed_debug`; capture the raw
  OpenRouter response for each turn. Look for
  `usage.prompt_tokens_details.cached_tokens > 0`.
- Compare the same batch with and without provider-sticky-routing
  configured via `extra_body`.
- If cached_tokens is 0 either way, the model doesn't support caching
  through this route.

**If caching doesn't work**: move to a Claude model (Haiku 4.5 or Sonnet
4.6) — both cache natively via LiteLLM, 90% discount on hot context. Cost
comparison on our workload: a 4× token savings on cached reads should pay
for Claude's higher per-token price at our mix.

---

## Reading list — Anthropic engineering posts (2026-04-13)

User flagged these to digest "eventually, not right away." Once read, pull out
concrete improvements to our agent loop and drop them in this file as their
own items:

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://www.anthropic.com/engineering/advanced-tool-use
- https://www.anthropic.com/engineering/code-execution-with-mcp
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://www.anthropic.com/engineering/writing-tools-for-agents

Likely relevance to us:
- **Evals for agents** → our compile quality is eyeballed, not measured. A real
  eval suite (recall of facts from source, wikilink correctness, supersession
  accuracy) would let us A/B model changes.
- **Advanced tool use** + **Writing tools for agents** → our current tools (read_file,
  write_file, edit_file, grep, list_wiki_pages, stamp_page_compiled_at) are thin.
  Probably missing `execute_shell` / subagents / better filter primitives.
- **Code execution with MCP** → whether our compiler should be an MCP server
  exposing tools (wiki query, raw grep, catalog lookup once it exists) so any
  Claude/Codex/Cursor session can drive it.
- **Context engineering** → entity page bloat, thread grouping, what goes into
  the LLM's prompt window at each compile step. Directly tied to the
  knowledge-vs-index work already planned.

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

## Thread quiet-period — avoid incremental thread recompiles (user insight)

User's observation: "if I've processed message 1 and then replies 2, 3, 4
arrive, I shouldn't send [1,2], then [1,2,3], then [1,2,3,4]. Just send
[1,2,3,4] once."

**For backfill**: already handled by `_group_by_thread` in `compile_all.py`.
One LLM call per thread, full context. No redundant re-reads within a run.

**Across separate backfill runs**: mild overhead. When run N+1 adds replies
to a thread already compiled in run N, the agent re-reads the existing
wiki page (which is a compressed summary of the earlier messages) before
merging the new replies. Cost is lower than re-reading the original raws
but non-zero. Mitigation: batch similar-date compile runs so most threads
finish in one run.

**Live mode (Phase 1)**: this is where the user's insight is critical.
Naive live compile-on-every-arrival would recompile the same thread page 4
times for a 4-reply conversation. Solution: **thread quiet period**
(already in `docs/issues/08-phase1-live-ingestion.md`):
- New message arrives → note thread_id, start 30-min timer
- Additional message to same thread → reset timer
- Timer fires → compile the whole thread as one batch
- Saves ~3× on active threads

**Edge**: hot threads that never go quiet — need a max-wait-time fallback
(e.g., "compile anyway after 2h even if replies keep coming") to avoid
starving important long-running discussions.

**When**: implement with Phase 1 live ingestion.

---

## Topic hierarchy + tags (user observation from wiki UX)

**Problem**: Currently `wiki/topics/*.md` is flat. `whatsapp-messaging-
enhancement`, `whatsapp-smarter-seller-recommendations`, `whatsapp-context-
aware-pricing-framework`, `whatsapp-launch-audit-as-skill`, `whatsapp9696`
are all siblings at the same level. User asks: "WhatsApp has multiple
different topics here. Should they be like tags?"

**Proposed**:

1. **Tags** (cheap, immediate): Add `tags:` frontmatter field populated by
   the compiler. MkDocs Material's `tags` plugin renders a tag cloud +
   per-tag page automatically. E.g., `tags: [whatsapp, buyer-side,
   launch-2026-03]`. No file reorg needed.

2. **Nested topics** (medium effort): split `wiki/topics/` into
   `wiki/topics/whatsapp/{messaging,pricing,recommendations}.md` etc.
   MkDocs nav auto-picks up the hierarchy. Requires:
   - Compiler prompt update: "When creating a topic page that's clearly a
     subtopic of an existing parent, put it under `wiki/topics/{parent}/`"
   - Lint: parent page should list subtopics
   - Index regeneration: show tree, not flat list

3. **Cross-reference parent-child via `parent_topic:` frontmatter**:
   `wiki/topics/whatsapp-messaging.md` has `parent_topic: whatsapp`. Hook
   renders a "Part of [[whatsapp]]" banner. Less restructuring needed.

**Recommendation**: ship tags first (zero migration cost, immediate
benefit), revisit nested topics after compiling more of the backlog when
we can see the shape of the hierarchy. Tag examples the compiler could
emit: product (whatsapp, buyermy, msite), team (launch, tech, qa), phase
(launch-audit, rollout, post-launch), entity-type (launch, bug-fix,
infra).

**When**: next iteration after current Tier 1 (stub-filler, tables).

---

## Wiki page metadata visible to users

**Shipped in this session (mkdocs_hooks.py)**:
- Metadata banner under h1: `**Last updated:** YYYY-MM-DD · **Sources:** N ·
  **Status:** current`
- Fix: blank line inserted before any list whose predecessor is a
  paragraph (MkDocs was rendering `text\n- item` as a single paragraph)

**Still missing** (user requested):
- Number of updates (count of commits / recompiles per page) — needs
  tracking in frontmatter or git log
- Inline reference links to raw emails per section (currently all at
  bottom in collapsible blocks)
- Per-section update dates when a page's history spans weeks/months
- Tags rendered in banner (blocked on tag implementation above)

---

## Advanced tools: filtering, sub-agents, RLM optimization

Extensions once the basic KB and MCP are stable:

1. **Complex filtering tools** for consumers: search by
   `sender+date+entity+keyword` combo, Gmail-style operators (`from:X
   before:Y has:table`). The MCP server surfaces these as structured
   query tools rather than requiring the consumer agent to do multi-hop
   filtering itself.

2. **Sub-agent tools**: expose Deep Agents' subagent pattern to MCP
   consumers so Claude Code / other clients can spawn bounded sub-agents
   for specific KB tasks — e.g., "summarize everything related to X in
   the last 30 days", "find all unresolved conflicts for this team".
   The KB runs the sub-agent on behalf of the consumer, returns just
   the distilled answer.

3. **RLM-style optimization**: continually improve the compilation
   prompt based on signal from Langfuse/Arize traces + user feedback on
   wiki pages (a simple "was this helpful?" button). Use Langfuse's
   prompt management / dataset features, or lean on something like
   DSPy's COPRO/MIPRO to auto-tune the system prompt against a held-out
   set of compilations.
   - Phase: after 500+ compiled pages and at least two weeks of trace
     data
   - Requires: eval set of known-good compilations, a metric (fidelity?
     concision? citation density?), a held-out corpus

**When**: Phase 3+ — after MCP ships and we have real consumer usage.

---

## Agent skills + MCP server + tools for downstream consumers

Once the KB is stable, expose it to other agents and tools:

1. **Claude Agent Skill** — package the KB as a skill bundle so Claude Code
   users can ask questions like "what's the current reimbursement policy"
   and get cited answers from their org's compiled wiki without manual
   setup.

2. **MCP server** — standardized read interface. Tools:
   - `list_topics(category?)` / `list_entities()` / `list_systems()`
   - `read_page(slug)` with structured metadata
   - `search(query, filters?)` — full-text + metadata filters
   - `find_sources(person_email)` — return raw mails referencing a person
   - `timeline(topic)` — chronological events on a topic
   - `conflicts()` — surfaces unresolved contradictions
   - `summarize_thread(thread_id)` — on-demand rollup
   MCP makes the KB usable from any MCP-speaking client (Claude, Gemini,
   Cursor, Windsurf, etc.).

3. **Python library** — `pip install email-kb` exposing the same surface
   programmatically for scripts and notebooks.

4. **REST API** — thin FastAPI wrapper around the library for non-MCP
   consumers (Slack bot, internal tools).

**When**: after the wiki has enough content (500+ pages) and quality is
stable. The MCP + skill bundle are each ~1-2 days. The library + API come
free once the data model is versioned (schema_version frontmatter
already in BACKLOG).

**Why this matters**: the KB isn't just for browsing — it should be the
authoritative source for any agent answering org-specific questions. A
Slack bot, a "new hire onboarding" agent, a project-status dashboard —
all should query this KB rather than re-deriving.

---

## Compile stall detection

Overnight run hit a stuck batch — compile_all stayed in "running
compilation" for 28+ min with no TCP activity, no budget movement, no
file writes. I killed it manually and the loop resumed.

For unattended runs, the overnight script should:
1. Timeout each compile_all invocation (e.g., 15 min max per batch of 20)
2. When hung, kill and resume next iteration
3. Log the stall as a data point in .logs/ so we can see frequency

Implementation: `timeout 900 uv run python scripts/compile_all.py ...`
in scripts/compile_overnight.sh. Or `gtimeout` via coreutils on macOS.

Also: per-batch `asyncio.wait_for` inside compile_parallel (already
queued in the Tier 2 plan from overnight-plan).

**When**: before next multi-hour run.

---

## Entity page bloat + context management

**Cause diagnosed**: hot entity pages accumulate 100+ sources and 20-26KB
of body text. When compiler reads them to merge new info, context fills
up, LLM call hangs silently.

**Fixes (Phase 2)**:
1. **Size cap on entity pages**: summary of role + recent 20 sources +
   link to "older sources" list. No unbounded growth.
2. **Summarize tool**: `summarize_entity(slug) → ≤500 tokens` so the
   compiler never loads full bloated pages. Only `read_file` the full
   page if the summary isn't sufficient.
3. **Auto-compact pass**: scheduled job that walks entity pages, merges
   overlapping sources, rewrites into canonical summary. Ideally
   triggered by RLM / DSPy prompt optimization (see below).

**RLM-based quality loop** (extending earlier BACKLOG entry):
User asked: "later we should set up RLM or something to solve these
kinds of issues" — meaning auto-detect and auto-fix recurring failure
modes via reinforcement learning on compile trajectories.

Concrete shape:
- Log every compile as a trace (Langfuse or Arize Phoenix)
- Label traces that stalled, hit recursion limit, created duplicates, or
  produced broken wikilinks
- Use DSPy COPRO/MIPRO to tune the system prompt against these negative
  examples
- Iterative loop: bad behavior → label → retrain prompt → redeploy
- Cost: the eval compute (probably 10× a normal compile run once)
- Payoff: each prompt iteration fixes a class of failure (e.g., never
  again create `foo-new.md` when `foo.md` exists)

Prereqs: persistent trace storage, golden eval set of ~30 hand-compiled
reference pages, a metric (fidelity / dupe rate / broken-link rate /
context usage).

**When**: after Phase 3 chatbot — need real user feedback to label
failures that aren't just structural.

---

## Inline citations instead of long Sources section

User: "Endless list of email threads at the bottom doesn't seem the best.
Move to citation format."

**Current** (messy):
```
## Main content
The API launched on April 11 with 87% uptime and improved latency.

## Sources
📧 Email 1 subject (collapsible with full body)
📧 Email 2 subject (collapsible)
... (50 more)
```

**Proposed** (clean, academic-style):
```
## Main content
The API launched on April 11 [^api-launch] with 87% uptime [^perf-audit].
Metrics aligned with the Q1 target [^q1-review].

## References
[^api-launch]: [API Launch] (2026-04-11, from Amit) — launch@im thread
[^perf-audit]: [Performance Audit] (2026-04-12, from QA) — perf-test thread
[^q1-review]: [Q1 Review] (2026-03-28, from Yashwant)
```

MkDocs Material already renders pymdownx footnotes nicely — clickable,
pops up on hover, can have back-refs.

**Compiler prompt change**:
- For every factual claim, emit `[^short-slug]` citation
- Define footnotes at the bottom, each pointing to the right raw email
- Keep `sources:` in frontmatter for machine use, but don't render raw
  email bodies in the page anymore

**What to preserve vs drop**:
- Keep: source lineage, click-through to raw email
- Drop: 50 collapsible email body blocks taking up 10KB of HTML per page
- Add: inline citation markers at the exact claim they support

**Migration path**:
1. Compile new pages with new citation prompt going forward
2. Old pages stay old until touched
3. Per-page migration script that runs a compile pass in "reformat mode"
   — re-read the sources, rewrite body with inline citations, don't change
   facts

**Expected visual improvement**:
- No 20KB of collapsible `<details>` at the bottom of every page
- Reader sees claim+citation together, not bolted-on-bottom
- Aligns with how the MkDocs Material community publishes docs

**When**: after source-dedup + CC-filter land; this is the "polish"
layer. Probably after 500+ pages compiled.

---

## De-noise entity pages: drop CC-only sources

**User insight**: "Does CC'd on this email really matter?" — no, usually
not. CC-only means informational, not active. Including these on an
entity page creates 3× noise vs real "work this person did."

**Proposed filter rules** (multi-level — not just header position):

Tier 1 — STRONG signal, always include:
- Person is From **AND** email body from them is >30 words / has numbers /
  URLs / code / decisions

Tier 2 — MEDIUM signal, include with caveat:
- Person is To (expected to act)
- Person is From but message is short (<30 words) AND not an ack phrase

Tier 3 — WEAK signal, exclude from main sources but keep searchable:
- CC-only
- From but message is just acknowledgement ("thanks", "+1", "lgtm",
  "adding X to thread", emoji-only)
- Body-mention only without header presence

Classification:
- Deterministic for CC-only and word-count heuristic (free)
- Cheap classifier (gpt-4.1-nano ~\$0.001) for borderline cases:
  ack vs substantive

**Expected impact**: Entity pages drop from 50 sources to 10-15 sources
of MEANINGFUL contribution. Page becomes "what did this person actually
do" instead of "every thread they were CC'd on."

**Expected impact**: Bharat Agarwal 48 sources → ~18. Himanshu-Jain 47 →
maybe ~25. Page size and render noise both drop significantly.

**Where to implement**:
1. **Compile prompt**: tell the agent "Do not include an email as a source
   on an entity page if the person is only in CC. From or To only."
2. **backfill_stubs.py**: exclude CC-only matches from the hits list
3. **One-off migration script**: walk existing entity pages, re-classify
   each source as From/To/CC/body, drop the CC-only ones, update
   frontmatter

**Risk**: we lose some context. "Bharat was informed of X" is gone.
Mitigation: keep a low-priority `also_mentioned_in:` frontmatter list
with CC-only raws, not rendered by default but searchable.

**When**: after current backfill stabilizes. One prompt change + migration.

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
  `improvement_suggestion` | `question_for_human` | `structure_suggestion`
- **`structure_suggestion`**: LLM can propose directory/schema/naming
  changes. E.g., "All these 12 WhatsApp-* topics should probably live
  under `wiki/topics/whatsapp/` with a parent page" or "We should add a
  `wiki/bugs/` category separate from `topics/`". These get reviewed
  weekly; good ideas become migrations.
- **`question_for_human`**: LLM can ask outright questions it can't answer
  from context — e.g., "What does BL mean here? Is it 'buyer lead' or
  'back log' or something else?" or "Why does this policy differ from
  the Feb 2026 one?" — queued for async human answer. Answers get fed
  back into compiler context (via CLAUDE.md or a glossary file) so the
  LLM doesn't have to ask again. Compounds system knowledge over time.
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
