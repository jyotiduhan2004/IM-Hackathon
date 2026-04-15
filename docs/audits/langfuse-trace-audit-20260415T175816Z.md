# Langfuse trace audit — most recent 50 traces

Generated: `2026-04-15T17:58:16Z`

Updated: `2026-04-16` with a second expert review focused on Anthropic tool
design and context-engineering guidance.

## Scope

This pass audits the most recent 50 Langfuse traces visible on 2026-04-15.
For each trace, I used the first retrievable observation among the trace's first
12 observation IDs that still carried `input.messages`. When no such observation
was retrievable, I fell back to trace metadata only.

The directional assessment below compares observed behavior against the current
compiler prompt in `src/compile/prompts.py` and the proposal-draft north-star
docs at `/Users/amtagrwl/git/email-knowledge-base/.claude/worktrees/proposal-draft/docs/NORTH-STAR.md`
and `/Users/amtagrwl/git/email-knowledge-base/.claude/worktrees/proposal-draft/docs/proposal/NORTH-STAR-DRAFT.md`.

Langfuse itself was unstable during collection:

- 22 of 50 traces needed one or more retries because observation fetches hit HTTP `524`.
- The `observations list --trace-id ...` CLI path was returning server errors, so this pass used the SDK.

## Verdict

We are not moving in the right direction yet.

The intended direction in the prompt and proposal docs is clear: thin coordinator,
fat judgment; concept pages over filing-cabinet behavior; lazy/reference-only
people pages; scoped tools; and explicit reflection when the agent hits ambiguity
or tool friction. The traces still show the opposite failure modes recurring:
the agent is being asked to restate coordinator-known context, can still wander a
filesystem namespace that is much wider than the active batch, gets weak lookup
help from `resolve_page`, and almost never uses `log_insight` even when the run
is visibly off the rails.

This is mostly a harness and tool-contract problem, not a "rewrite one more
paragraph of prompt prose" problem.

## Aggregate findings

| Finding | Count |
|---|---:|
| No `log_insight` call despite visible tool friction | 20 |
| Agent used absolute or virtual-absolute file paths | 11 |
| `resolve_page` returned misses without enough recall | 8 |
| `create_entities` forced the model to restate `raw_paths` context | 7 |
| `resolve_page` returned absolute wiki paths | 5 |
| Agent bypassed entity tooling and wrote entity pages directly | 3 |
| Blank trace name | 2 |
| Agent traversed a foreign worktree/repo path | 1 |
| `create_entities/raw_paths` were not repo-relative `raw/*` paths | 1 |

Trace family split:

- `compile:*`: 31
- `LangGraph`: 17
- `<blank>`: 2

This split is itself a smell. A quarter of the sample is still surfacing as
generic `LangGraph` traces or blank names instead of a consistent compile trace
taxonomy.

## Directional drift against prompt and north star

- Coordinator-owned context is still leaking into the model contract. The clearest example is `create_entities(raw_paths, entities)`, which makes the model serialize batch evidence the coordinator already knows.
- Filesystem scoping is still too loose. In 11 traces the agent used absolute or virtual-absolute paths, and one trace crossed into a foreign worktree namespace.
- `resolve_page` is not doing enough work for the agent. It both under-recalls on fuzzy/domain-like queries and returns absolute wiki paths on hits, which reinforces the wrong path habit.
- Reflection is mostly absent. The prompt names `log_insight` as the channel for tool gaps, prompt ambiguity, and structure suggestions, yet 20 traces showed visible friction with no corresponding insight log.
- Entity discipline is still porous. Three traces wrote entity pages directly instead of treating `create_entity` as the only slug-minting boundary.

## Second review synthesis

A second expert review reached the same directional conclusion but added a more
explicit roadmap:

- Prompt alignment to the ratified north star is only about 50%.
- Tooling is closer, around 60%, but still has overlap and stale contracts.
- The coordinator is furthest ahead, around 80%, because it already owns most
  deterministic state transitions.

That diagnosis matches the trace sample and the DB telemetry:

- `compile_insights` still has 0 rows, so the reflection channel exists but is
  not being activated.
- `create_entity` has 271 calls versus `write_draft_page` at 1, which is the
  opposite of the intended "people are lazy/reference-only" posture.
- `resolve_page` has 143 calls with 113 misses, so the lookup surface is still
  too weak to anchor the model.
- 290 of 1120 file-operation calls in `compile_tool_calls` were absolute-like,
  which confirms the path-shape problem is systemic rather than anecdotal.

## Anthropic rubric applied here

The second review grounded the roadmap in two Anthropic essays:

- "Writing effective tools for AI agents"
- "Effective context engineering for AI agents"

The highest-value rules for this codebase are:

- High-impact workflows over thin API wrappers.
- Fewer tools with less overlap.
- Unambiguous parameters and strict schemas.
- Tool docstrings written as if for a new teammate.
- Just-in-time context loading instead of preloading large catalogs.
- Structured prompts with clear sections and only the minimum rules that still
  explain the job.

## Prompt audit additions

### Keep

- The hard-rule block is strong and specific.
- The wikilink rules are concrete and testable.
- The light topic-page guidance is good: lead paragraph first, sentence 1 is a
  definition, no hand-written `## Related`.
- The "preserve technical depth" and "preserve structured tables verbatim"
  sections are directionally right.
- The `log_insight` taxonomy is well-scoped once the model actually uses it.

### Rewrite

- The prompt still teaches the old six-page-type world. It references
  `timelines/` and `conflicts/`, while the north star explicitly drops both and
  replaces the center of gravity with topics, systems, domain hubs, glossary,
  and lazy decisions/people.
- The workflow is still "process each email, decide what pages it touches" when
  the north star is "maintain concept pages that happen to grow from email."
- The prompt is still entity-first. It says to always call `create_entity` for
  people, which conflicts with the new "people are reference-only and hidden
  from primary nav" model.
- There is no synthesis self-review. `check_my_work` currently asks "is the
  markdown clean?" but not "does this read like an encyclopedia entry instead of
  a filing cabinet?"
- There is no trivial-message filter. Acks and short reply-only emails are not
  being explicitly skipped even though the north star expects that filter.
- There is no domain/tag guidance, no lazy decision-page guidance, and no
  canonical examples of a good topic page in the new shape.
- At 400+ lines, the prompt has grown by patching rather than by a deliberate
  rewrite. The next change should be a Phase-1 rewrite, not another local edit.

## Tool audit additions

### Strong tools

- `find_new_sources`: good bounded shape and actionable errors.
- `resolve_page`: good intent, but current behavior under-delivers.
- `write_draft_page`: good concept, underused.
- `log_insight`: good concept, unused.

### Tools that need contract changes

- `list_wiki_pages`: too thin. It returns bare names, forcing the agent into
  unnecessary `read_file` calls just to preview state. It should grow a
  `response_format` and expose page type, status, last compiled, section
  headings, and source count.
- `check_my_work`: the docstring needs a hard "call this after writing, never
  before" anchor. It also needs to evolve from lint-only critique into
  synthesis critique.
- `create_entity` and `create_entities`: the implementations are better than the
  surrounding workflow, but they are still fighting the north star. People
  should no longer be the compiler's default expansion surface.
- `create_entities` specifically should lose `raw_paths` from the model-facing
  contract. That is coordinator-owned context and should be injected, not
  restated.

### Coordinator-only cleanup

The second review also reinforced an earlier point: several old `@tool`
functions should stay out of the model-facing toolbox entirely because they are
coordinator work or dead-state helpers.

- `list_uncompiled_emails`
- `stamp_page_compiled_at`
- `mark_as_compiled`
- `update_wiki_index`
- `append_to_log`

### Missing tools

The highest-value additions named in the second review are:

- `get_page_summary(slug)`: structured preview without a full page read.
- `get_thread_context(thread_id)`: one call instead of multiple raw-file reads.
- `patch_page(slug, section, new_content)`: a higher-level write tool than
  repeated `edit_file`.
- `propose_page(frontmatter, body)`: validate new-page structure before writing.
- `list_domain_topics(domain)`: needed once domain hubs are generated.
- `merge_topics(slug_a, slug_b)`: explicitly deferred to a later dedupe phase.

## Ordered roadmap

### Phase A - stop the bleeding

- Remove coordinator-only tools from the agent-facing surface and convert the
  dead `@tool` wrappers to plain functions.
- Upgrade `list_wiki_pages` with a concise/detailed response shape.
- Tighten the `check_my_work` and `resolve_page` docstrings so they explicitly
  steer away from the two most common trace failures:
  `check_my_work` at the start, and `resolve_page` loops on the same concept.
- Trim the model pool to known-good options rather than keeping obviously weak
  or noisy choices around.

### Phase B - rewrite the compiler prompt to the north-star shape

- Replace the current six-page-type/event-archive prompt with a shorter
  structured prompt organized around concept pages, lazy people pages, domain
  tagging, lazy decisions, and synthesis self-review.
- Drop `timelines/` and `conflicts/` from the main workflow.
- Add the trivial-message filter.
- Add domain and tag guidance.
- Move `create_entity` out of the default workflow and into an exceptional path.

### Phase C - add the missing high-leverage tools

- Ship `get_page_summary`.
- Ship `get_thread_context`.
- Ship a higher-level page patch/update tool so the model stops doing repeated
  low-level markdown surgery.

### Phase D - land north-star structures

- Compiler-generated domain hubs.
- Lazy decision pages.
- Auto-generated glossary.
- Viewer/nav changes that hide people from primary navigation and show the new
  status model.

### Phase E - formalize evaluation

- Keep a small golden set of representative compile tasks.
- Add a trace-driven optimization loop so prompt and tool changes are judged on
  real transcripts, not just local intuition.

## Explicit non-goals from the second review

- Do not mix viewer work into the same PR as the prompt rewrite.
- Do not add sub-agents yet.
- Do not pull `wiki_merge_pages` forward into Phase 1 just to paper over
  duplicate-generation problems.
- Do not keep incrementally patching the current prompt forever; the next big
  step should be a clean rewrite.

## Per-trace notes

- `44da64f44d04bfdf9987db0c19b8d652` | `compile:x-ai/grok-4.1-fast:19b934e448c1` | `2026-04-15T16:57:24.263000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `b7ca08ce3a1a8056d999abe960931d7b` | `compile:x-ai/grok-4.1-fast:19b8de76b2d8` | `2026-04-15T16:55:00.904000+00:00`
  `create_entities` forced the model to restate `raw_paths` context. `resolve_page` returned misses without enough recall. `resolve_page` returned absolute wiki paths.
- `9788800aad6155afe2cce7187503ec2d` | `compile:minimax/minimax-m2.7:19bb28170236` | `2026-04-15T16:49:37.643000+00:00`
  Agent used absolute or virtual-absolute file paths. Agent traversed a foreign worktree/repo path. `create_entities` forced the model to restate `raw_paths` context.
- `812477efc521acc6f84ad799168d00a4` | `compile:x-ai/grok-4.1-fast:19b9d0eca1be` | `2026-04-15T16:49:05.257000+00:00`
  `create_entities` forced the model to restate `raw_paths` context. `resolve_page` returned misses without enough recall. No `log_insight` call despite visible tool friction.
- `e780aa514381fc5515109056e3c14303` | `<blank>` | `2026-04-15T16:46:11.265000+00:00`
  Blank trace name.
- `408e8cb75642ebdf1deb6bc6c0e8b8d9` | `compile:z-ai/glm-5:19bb1d1492b3` | `2026-04-15T16:44:55.824000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `d04acb7320e7ab8a5b0bd6eeae833287` | `compile:z-ai/glm-5:19b4f4a7e8fb` | `2026-04-15T16:41:17.796000+00:00`
  `resolve_page` returned absolute wiki paths.
- `9f8b3faeea969fc0d87541285048a41f` | `compile:z-ai/glm-5:19b9dc5ba940` | `2026-04-15T16:39:54.909000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `1bc2ba44d6446b0fb1732718085b3366` | `compile:minimax/minimax-m2.7:19b9e1215323` | `2026-04-15T16:36:23.944000+00:00`
  Agent used absolute or virtual-absolute file paths. `resolve_page` returned absolute wiki paths. No `log_insight` call despite visible tool friction.
- `c4c1852a59c280c8481d19f1301631b1` | `compile:x-ai/grok-4.1-fast:198f15ed296b` | `2026-04-15T16:35:46.375000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `ccaa3b83a54a51371264771006a99be3` | `compile:z-ai/glm-5:19b735bb25b2` | `2026-04-15T16:34:15.618000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `66ef4b7768132f34481c9aab58811301` | `compile:z-ai/glm-5:19bb1ec15f6e` | `2026-04-15T16:32:29.871000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `3ee5e81036a3a698b1be9c4982982766` | `compile:z-ai/glm-5:19b9dc5eba08` | `2026-04-15T16:30:08.518000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `a02d85900e9a3c958ca7eb4e7359c131` | `compile:minimax/minimax-m2.7:19b9d0eca1be` | `2026-04-15T16:28:19.523000+00:00`
  Agent used absolute or virtual-absolute file paths. `create_entities` forced the model to restate `raw_paths` context. No `log_insight` call despite visible tool friction.
- `0fd95178dc9648e8c5ee1fa5a210b4c7` | `compile:z-ai/glm-5:19ba2d6fefe1` | `2026-04-15T16:25:26.679000+00:00`
  `resolve_page` returned absolute wiki paths.
- `62e68b81b058c9899629faa69b05ee0f` | `compile:z-ai/glm-5:19bb1990173b` | `2026-04-15T16:21:30.164000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `24fdfce8b63ddfbe19804d95b432232d` | `compile:minimax/minimax-m2.7:19bb1990173b` | `2026-04-15T16:20:54.387000+00:00`
  Agent used absolute or virtual-absolute file paths. `create_entities` forced the model to restate `raw_paths` context. No `log_insight` call despite visible tool friction.
- `a009839a24be72efcd43ed1552fb5707` | `compile:minimax/minimax-m2.7:19bb17f63440` | `2026-04-15T16:18:48.251000+00:00`
  Agent used absolute or virtual-absolute file paths. No `log_insight` call despite visible tool friction.
- `8300416773b43975609a001f09d59b50` | `compile:z-ai/glm-5:19ba21050082` | `2026-04-15T16:16:24.286000+00:00`
  Agent bypassed entity tooling and wrote entity pages directly. No `log_insight` call despite visible tool friction.
- `9d0ca8aa5c6ca1accd3f125dbd8168b4` | `compile:x-ai/grok-4.1-fast:19b9e1972f3f` | `2026-04-15T16:15:31.694000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `504e0b8f67818045099d084e16d2cf50` | `<blank>` | `2026-04-15T16:14:20.371000+00:00`
  Blank trace name.
- `73547030e9509069eb9b5302544dbcf1` | `compile:z-ai/glm-5:19b49539ba25` | `2026-04-15T16:07:41.050000+00:00`
  Agent bypassed entity tooling and wrote entity pages directly. No `log_insight` call despite visible tool friction.
- `44bd186deb5ef255d320edec4b80b259` | `compile:minimax/minimax-m2.7:19bb17119d23` | `2026-04-15T16:06:29.432000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `07ff975b44b19b3b19a6baf41402babb` | `compile:x-ai/grok-4.1-fast:19b735bb25b2` | `2026-04-15T16:05:30.656000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `8a66d99beabd33038dcdf0de4d070744` | `compile:x-ai/grok-4.1-fast:19ba21050082` | `2026-04-15T16:04:40.506000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `1ebdf3bba7d4fd417ff9de4e70f1fec1` | `compile:minimax/minimax-m2.7:19ba21050082` | `2026-04-15T16:04:09.656000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `5d930447ab364e0f0acfa14c1b0d0c77` | `compile:z-ai/glm-5:19b8dba90db4` | `2026-04-15T15:59:16.250000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `e3a218dd97d191d0d474216129a1289c` | `compile:minimax/minimax-m2.7:19b9e1972f3f` | `2026-04-15T15:58:14.408000+00:00`
  Agent used absolute or virtual-absolute file paths. `create_entities` forced the model to restate `raw_paths` context. `resolve_page` returned misses without enough recall.
- `5241e5094d49970dc7f7352aa2a2fc26` | `compile:minimax/minimax-m2.7:19ba81e386d2` | `2026-04-15T15:56:49.773000+00:00`
  `create_entities` forced the model to restate `raw_paths` context. No `log_insight` call despite visible tool friction.
- `8aab72c69ee6566aee8a130f2ca9425f` | `compile:x-ai/grok-4.1-fast:19b9e1972f3f` | `2026-04-15T15:03:11.482000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `b3f03786d23f51960ce1ee12939ff417` | `compile:minimax/minimax-m2.7:19ba81e386d2` | `2026-04-15T15:02:44.304000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `149b54d4108edd3cc01c24c431a173f1` | `compile:x-ai/grok-4.1-fast:19b9e1972f3f` | `2026-04-15T14:46:01.739000+00:00`
  Agent used absolute or virtual-absolute file paths. No `log_insight` call despite visible tool friction.
- `a164bdc82f9f116b8fab0f59bf556d06` | `compile:x-ai/grok-4.1-fast:19ba81e386d2` | `2026-04-15T14:45:19.111000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `c54db0c78b4a6028082bc82f65954f62` | `LangGraph` | `2026-04-15T14:15:56.624000+00:00`
  Agent used absolute or virtual-absolute file paths. No `log_insight` call despite visible tool friction.
- `9727e301f03ba2a4fb72b6102f72f14c` | `LangGraph` | `2026-04-15T14:14:54.011000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `f8831dd73fe174b9495ebc1064d98179` | `LangGraph` | `2026-04-15T14:14:09.869000+00:00`
  Agent used absolute or virtual-absolute file paths. No `log_insight` call despite visible tool friction.
- `c72d807a57fbc0e01f3eb7d3ecae03c6` | `LangGraph` | `2026-04-15T14:13:31.925000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `e449408bf7287b7df38aa0eafd7502d5` | `LangGraph` | `2026-04-15T14:02:57.144000+00:00`
  Agent used absolute or virtual-absolute file paths. `resolve_page` returned misses without enough recall. No `log_insight` call despite visible tool friction.
- `096de2f6be44ab10d3013d8ebc35bc77` | `LangGraph` | `2026-04-15T14:02:56.895000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `d090a465ecfc8b858655364ed2446f75` | `LangGraph` | `2026-04-15T13:58:56.235000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `e3ecc72a3e58da21f32162d16214a23a` | `LangGraph` | `2026-04-15T13:57:57.532000+00:00`
  Agent used absolute or virtual-absolute file paths. `resolve_page` returned misses without enough recall. No `log_insight` call despite visible tool friction.
- `9028f706b55c1af13ec63f2021f38965` | `LangGraph` | `2026-04-15T13:56:12.707000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `7fa2e2f2556d38dd24ebdd0a6cf61810` | `LangGraph` | `2026-04-15T13:45:51.053000+00:00`
  `resolve_page` returned misses without enough recall. No `log_insight` call despite visible tool friction.
- `fa6311d64393bac39f212df9d0b57c10` | `LangGraph` | `2026-04-15T13:38:28.385000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `618f8580cc0bab87643f2dffdebf7fb3` | `LangGraph` | `2026-04-15T13:35:33.680000+00:00`
  Agent bypassed entity tooling and wrote entity pages directly. No `log_insight` call despite visible tool friction.
- `34d1fb194c24b83144f32c0354069557` | `LangGraph` | `2026-04-15T13:28:37.057000+00:00`
  `resolve_page` returned misses without enough recall. No `log_insight` call despite visible tool friction.
- `877ddb7c30f8614a7b2c45ca03d4a56d` | `LangGraph` | `2026-04-15T13:26:53.209000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `8ab5f8d957bebb9c17719c7c7446cc64` | `LangGraph` | `2026-04-15T13:22:43.735000+00:00`
  `resolve_page` returned misses without enough recall. No `log_insight` call despite visible tool friction.
- `1a34aacb62d7e9fbebaadd5845222d3b` | `LangGraph` | `2026-04-15T13:18:47.077000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
- `75812900ea5d13bd27f26533708885b8` | `LangGraph` | `2026-04-15T13:09:18.349000+00:00`
  Mostly clean in the latest observation snapshot; no obvious tool-contract violation surfaced.
