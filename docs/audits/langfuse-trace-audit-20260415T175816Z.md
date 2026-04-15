# Langfuse trace audit — most recent 50 traces

Generated: `2026-04-15T17:58:16Z`

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
