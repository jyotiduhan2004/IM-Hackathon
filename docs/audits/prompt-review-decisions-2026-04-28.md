---
title: "Compile prompt review — running decisions log, 2026-04-28"
companion: docs/audits/prompt-review-questions-2026-04-28.md
status: in_progress
---

# Decisions log — keep updated every Q-batch

Single source of truth for what we've decided + cross-cutting principles surfaced during the walk-through. Update after every `AskUserQuestion` round.

## Cross-cutting principles (apply EVERYWHERE, not just where surfaced)

These are not per-question edits — they sweep the whole prompt.

### CC-A — No internal jargon in the prompt

**Rule**: the prompt must not reference names that only the runtime / middleware / coordinator knows. The LLM never sees those names, so a rule that says "the `summary-stale-date` blocker fires" is gibberish to it.

**Sweep targets so far**:
- `current-truth Summary` (3× at L44, L127, L271) → rephrase as "the Summary that reads as if written today" or just "the Summary".
- `summary-stale-date` (only in question prose, not in prompt — confirmed clean) → if used in any future text, rephrase.
- Any blocker name (`check_my_work_gate`, `sibling-draft-check`, etc.) → describe the rule, don't name the gate.

**Action**: when applying any prompt edit, audit the diff for these terms. Add a CI check post-PR that greps the prompt for known internal-jargon terms and fails if found.

### CC-B — Slug form: email-canonical kebab-case

**Decided in Q3.4 user note** (formal Q3.3 confirmation pending):

- `[[aa-indiamart-com]]` is the canonical slug form (matches what `create_entities` returns).
- Display name (e.g. "Amit Agarwal") goes in link text via `[[aa-indiamart-com|Amit Agarwal]]` syntax (or whatever the renderer supports — verify).
- The current prompt teaches the OPPOSITE (`[[amit-agarwal]]` not `[[aa-indiamart-com]]`) — that's wrong; flip it.

**Sweep targets**:
- L97-98 example: invert.
- Any `<few_shots>` example that uses display-name slugs.
- F-070 (304+ instance count) becomes a non-issue: the langfuse-observed form was already correct; the *prompt* was wrong.
- Hard rule about wikilink form: rewrite to "use the email-kebab form, not display-name guesses".

### CC-C — Page archetype, not domain, drives flavor

**From Q3.2 user note**: pages can be cross-cutting; one email can belong to multiple domains; the per-domain flavor list is the wrong shape.

**Action**: replace L106-122's per-domain bullet list with per-archetype framing:
- *Launch / rollout* — stage, % rollout, latency gate, segment.
- *Bug / incident* — symptom, scope, fix, verification, regression test.
- *Policy* — effective date, supersedes, scope, exception path.
- *Decision* — the change, the alternative considered, the rationale.
- *System overview* — surface, owner team, dependencies, runbook.

User explicit: "I am okay with the LLM taking more calls and reading more." So if archetype is ambiguous, the agent reads the page neighbours and picks. No hardcoded mapping.

### CC-D — Pattern-think: when one instance surfaces, fix the class

**User principle, 2026-04-28**: "Don't just fix the one issue that you found... when you find that you understand, 'Oh yeah I need to fix this across every place.'"

Applies to:
- CC-A jargon sweep.
- CC-B slug form sweep.
- Future: any other class of issue surfaced during the walk-through.

### CC-E — Question format with context

Saved as feedback memory. Every `AskUserQuestion` option's `preview` ships CURRENT / PROPOSED / WHY. No abstract questions.

### CC-J — Don't write rules against capabilities the LLM doesn't have

**User principle, Q6.6**: "do these actually need to be there? Shouldn't coordinator handle these deterministic things? Does the LLM even have access to change this stuff?"

**Rule**: if the agent has no tool to do X, don't write a rule saying "don't do X". It wastes prompt budget and confuses the agent about what tools exist. Most "Bookkeeping is NOT your job" lines are paranoid clutter — the LLM literally has no `flip_compile_state` tool.

**Sweep**: enumerate the agent's actual tool surface, then strike any rule that bans a capability the agent lacks.

| Currently in prompt | Verdict |
|---|---|
| "Don't flip compile state" (L461, L654, L1027) | DELETE — no tool exists |
| "Don't stamp last_compiled" (L461, L654, L1027) | KEEP slim — agent CAN edit frontmatter via edit_file. Single canonical home: "Don't manually edit `last_compiled` frontmatter — the coordinator stamps it after you return." |
| "Don't write catalog rows" (L461, L654, L1027) | DELETE — no tool exists |
| "Don't append to wiki/log.md" (L461, L654, L1027) | DELETE — no tool exists |
| "Don't invent slugs" (L429-430) | KEEP — agent CAN write_file with any slug; the rule prevents misuse |

### CC-G — Outcome-based, not step-prescriptive (trust LLM intelligence)

**User principle, Q6.1 walk-through**: "this still looks like very very rule based, how can we make it more outcome based and let the LLM figure out how it wants to get there. We can provide it a few examples of how different types of emails can be handled. so that we are actually using its intelligence otherwise we are just using a workflow disguised as an agentic workflow."

**Rule**: prefer outcome contracts + worked examples over numbered procedures. Do not write "step 1: do X; step 2: do Y" when you can write "the outcome is Z; here are 4 example shapes; the tools that help are A, B, C — pick what fits."

**Sweep targets**:
- `<workflow>` Steps L415-463 — replace 9-step procedure with: outcome contract (already exists at L294-413) + diverse example shapes (extend `<few_shots>`) + tool-usage hints (move to `<tool_guidance>`).
- Anything else that reads as "first you do, then you do, then you do" — convert to "your goal is X; the success criterion is Y; here are example trajectories."

**What stays prescriptive**: hard rules with hard consequences (don't fabricate, always cite, never delete history, terminal-decision contract). These are constraints, not workflows.

**What becomes outcome-based**: tool-call ordering, "when to do X first vs. Y first", procedural sequencing.

**Risk**: Opus 4.7 with reduced scaffolding may make different trajectory choices that surprise the validator. Mitigate with the existing post-write check (`check_my_work`), reviewer middleware, and quality bar (content_floor + voice).

### CC-H — Boy Scout rule: leave the prompt + wiki better than found

**User principle**: "we need to leave the documents and the wiki better than we found it. ... if you find a mistake or issue from earlier, feel free to fix it."

**Rule**: while walking through Question N, if I spot an issue elsewhere (typo in earlier section, inconsistent term, broken example), fix it as part of the same diff. Don't queue it as "later work".

**Action**: each batch of edits gets a final pass for "anything else broken nearby?" — fix in place, note in decisions log.

**Extension to wiki content** (Q6-meta user note): "There must be cases where one email updates a lot of pages or multiple prior emails need to be read and links need to be followed to find some information in its best form and then we might want to simplify so that the next time we don't have to follow such a long process — boy scout thing."

So when the compile agent does multi-page traversal to find info or has to follow links, it should leave breadcrumbs / consolidate / inline / improve the page where information was found, so the next agent's traversal is shorter. This becomes a quality-bar rule for the compile agent itself (separate from the prompt-edit boy-scout for ME).

**Where this lands in the prompt**: a short bullet in the new lean `<procedure>` section: "If you traversed multiple pages or links to find a fact, consolidate / inline / cross-link so the next agent doesn't redo the traversal."

### CC-F — If the runtime can compute it, the prompt shouldn't teach it (Karpathy pattern)

**User principle, Q4.1 + Q4.2 walk-through**: "Yeah this is a good pattern to understand. I think whenever this is available, we should use this." Plus on Q4.2: "if all the footnotes, references are cited correctly, can't we just deterministically build the references or links section easily and maybe then we don't even need to mention it to the prompt?"

**Rule**: every prompt rule that teaches the LLM to do a deterministic computation (string parsing, list dedup, render-a-section-from-data, "make sure X is well-formed") is a candidate for runtime delegation. The agent should write claim-level data; the runtime should render structural artefacts.

**Sweep targets identified so far**:

| Currently in prompt | Move to runtime |
|---|---|
| `raw_path.stem.rsplit("_", 1)[-1]` (Q4.1) | `get_thread_context` returns `cite_key` |
| `## References` block template + ordering (Q4.2 / L165-180) | Runtime builds it from inline `[^msg-*]` references at compile-finalize |
| `## Sources` ban + MkDocs leak (Q4.2 / L167-172) | Irrelevant once runtime owns `## References` |
| Source-threads dedup / sort order (likely L660-667) | Runtime can dedup + sort frontmatter list |

**Action**: when applying any prompt edit, ask "could a function compute this?" If yes: write a coordinator-side hook + delete the prompt teaching. Phase 2 PR (after the prompt walk-through PR1 lands).

**Gating**: prompt edits that depend on coordinator changes are blocked on those coordinator changes shipping first. Don't delete the rsplit instruction or `## References` template until `cite_key` + auto-references-builder are live.

---

## Per-question decisions

Updated after each batch.

### Part 0 — Meta

| Q | Pick | Note |
|---|---|---|
| Q0.1 | A — 750 lines, dedupe only | Phase 1; Phase 2 = composable split |
| Q0.2 | A — Mechanics-first reorg | User: section names are placeholder-shaped, will refine as we dig |
| Q0.3 | B — Phase 2 PR for prompts/ split | After content edits ship |
| Q0.4 | B — Two-step PR | Reorg + dedupe first, then content/behavior |

### Part 1 — Prompt opening

| Q | Pick | Note |
|---|---|---|
| Q1.1 | A — Add `<content_floor>` block | User: ensure role/task/goal first, then mechanics. Floor = goal/definition-of-done, lands AFTER background. |
| Q1.2 | A — Add symmetric forward-update rule in `<chronological_scope>` | Rephrase to remove `summary-stale-date` jargon (CC-A). |

### Part 2 — `<concept_vs_thread>`

| Q | Pick | Note |
|---|---|---|
| Q2.1 | A — New `<voice>` section | Keep concept-vs-thread for structure; voice for prose. |
| Q2.2 | A — Trim L41-49 (6-step batch enumeration) | Keep lede + GOOD/BAD pair; ~25 lines saved. |

### Part 3 — `<expert_questions>`

| Q | Pick | Note |
|---|---|---|
| Q3.1 | A — Promote `## Why it matters` | User: rephrase "current-truth Summary" (CC-A jargon). |
| Q3.2 | C — Reframe as per-archetype, not per-domain | User input changes the shape; not just "cut here, keep there". See CC-C. |
| Q3.3 | **B — Email-canonical kebab-case slug** (CONFIRMED) | User: "deterministic, easy to find, no collisions because email uniquely identifies." Aesthetic cost in raw markdown is real but rendered display can use `[[slug\|Display Name]]` or pull from page title. Triggers CC-B sweep. |
| Q3.4 | A — Owner DRI in frontmatter, rest inline | Combined with CC-B slug form. |

### Part 4 — `<inline_citations>`

| Q | Pick | Note |
|---|---|---|
| Q4.1 | A — `cite_key` from `get_thread_context` | Triggers CC-F (runtime computes); coordinator change required. |
| Q4.2 | C (new) — Runtime auto-builds `## References` from inline footnotes | User-proposed extension of CC-F. Drops L165-180 entirely from prompt once runtime owns it. Gated on coordinator hook. |

### Part 5 — `<revision_style>`

| Q | Pick | Note |
|---|---|---|
| Q5.1 | C — Rule in <workflow> step 5 + Example 11 in <few_shots> | U3 langfuse: 0/11 recovery on stale-summary. Two-surface landing. Rephrase to drop "blocker" jargon (CC-A). |

### Part 11 — `<self_review>`

| Q | Pick | Note |
|---|---|---|
| Q11.1 | A — Cut reviewer-invocation rule (item 5) | Lives in task() docstring per Q6.4. Keep items 1-4. |
| Q11.2 | A — Add lead-paragraph + owner checks | Voice check goes in `<voice>` section per Q2.1. |

### Part 12 — `<few_shots>`

| Q | Pick | Note |
|---|---|---|
| Q12.1 | A — Replace Example 7 | Selective wikilinking + email-canonical slug. Closes F-070. |
| Q12.2 | A — ADD Q-delta example, KEEP current Example 8 | User: "what is example 8?" Both shapes valuable; symmetric pair. |
| Q12.3 | A — Add Example 11 UPDATE flow | Already covered by Q5.1=C. |
| Q12.4 | A — Add Example 12 chronological-scope DON'T | Per Q1.2; closes worst-case anti-pattern gap. |
| Q12.5 | Skip | User: "Just token savings are not needed. If it actually has a meaningful impact on quality, sure; otherwise skip." No quality impact → skip the consolidation. |

### Part 13 — `## Hard rules`

| Q | Pick | Note |
|---|---|---|
| Q13.1 | A — Trim per CC-A/J | Cut "NEVER modify /raw/" (sandbox enforces; CC-J). Strip `message_touched_pages` jargon (CC-A). Keep 7 essential rules. |
| Q13.2 | A — Add inline-Decision-H2 ban + strikethrough ban | Closes F-063 + D9. |
| Q13.3 (new, surfaced by user) | A — ISO 8601 everywhere | One-line rule in `<revision_style>`. Frontmatter + bullets + body prose all use YYYY-MM-DD. Closes 34% drift (177/524 files). |

### Part 14 — `<voice>` (NEW section)

| Q | Pick | Note |
|---|---|---|
| Q14.1 | A — Add as proposed | 4 anti-examples + 6-month test. Section sits between `<editorial_notes>` and `<few_shots>`. Closes F-045. |

### Part 15 — NORTH-STAR ratification

| Q | Pick | Note |
|---|---|---|
| Q15.1 | A — Follow-up PR3 after prompt PRs ship | Prompt proves conventions; NS catches up. List of NS edits in PR3 scope. |

### Part 16 — Real-world agent prompt patterns (in progress)

| Q | Pick | Note |
|---|---|---|
| Q16.1 | A — Add minimal preamble rule | 1-2 sentences before tool batches. Trace audit signal. |

### Part 10 — `<tool_guidance>` (in progress)

| Q | Pick | Note |
|---|---|---|
| Q10.1 | A — Move up | Already implied by Q0.2 mechanics-first reorg. |
| Q10.2 | **Skip** | Background agent: flail dropped 70% → 14% post-qmd (Apr 23-24 integration). qmd + PR #182 closed it. Old "teach idempotency" item is now docstring polish only. Optional: add tiny "pass bare slug, not prefix" line to resolve_page docstring (residual 1.8% prefixed calls). |
| Q10.3 | A — Authorize with steering, drop "deepagents" mention | User: "you don't need to mention deepagents. that's the coordinator, llm does not need to know about it." Pure CC-A. |
| Q10.4 | **CUT BOTH** (data-driven flip) | Background agent findings: (a) wiki_merge_pages **TOOL DOES NOT EXIST** — only mentioned in `prompts.py:238`; never registered; agent has been hallucinating it. Real merge pipeline is reviewer flag → coordinator queue → human script. (b) write_draft_page is fully implemented but 0 calls in 107+59 traces, `wiki/_drafts/` directory doesn't exist, `draft_recommended` field is never auto-actioned. Both go. |
| Q10.5 | A — WHEN/WHEN-NOT on every tool | Implied by Q6.3 + Q6.4 (CC-F: tools own their usage). |

**Boy-scout cleanup (CC-H, code-side, separate from prompt PR1)**:
- DELETE `prompts.py:238` phantom `wiki_merge_pages` reference; replace with one-line note about the reviewer's `merge_candidates` field.
- DELETE `src/compile/draft.py` entirely.
- DELETE `compiler.py:2432` registration of `write_draft_page`.
- DELETE `prompts.py:454` and `:887` mentions of write_draft_page.
- DELETE `draft_recommended` field in `reviewer.py:254`.
- Coordinator-side: nothing to clean (the field was never consumed).

### Part 16 — Real-world agent prompt patterns (cont'd)

| Q | Pick | Note |
|---|---|---|
| Q16.2 | Skip | User: not enough evidence yet; revisit on more traces. |
| Q16.3 | Skip | User: "let's reevaluate this on more traces and metrics and actual pages — not clear about the benefits yet." |
| Q16.4 | A — Make checks verifiable, advisory tone | Concrete checks; no MUST overload. Best-practices guidance respected. |
| Q16.5 | A — Triple-redundancy on `source_threads:` | Format invariants warrant repetition (Aider/v0 pattern). Add `# NOT sources:` comment in frontmatter template. |
| Q16.6 | A — Move 5W block to reviewer prompt | Compiler writes; reviewer judges depth. 58 lines saved from compiler. Keep 1-line cite. |
| Q16.7 | Skip | Polish item, not load-bearing. |
| Q16.8 | Skip | User: "We are on the way to remove the glossary completely because it's not working right now." Glossary will be deleted; no point citing it. |

---

## Tool ↔ prompt parity audit (2026-04-29)

| Tool | Registered | In prompt | Action |
|---|:---:|:---:|---|
| list_wiki_pages | ✓ | ✓ | keep |
| resolve_page | ✓ | ✓ | keep + Q10.2 docstring polish |
| create_entities | ✓ | ✓ | keep |
| log_insight | ✓ | ✓ | keep + Q17 active framing |
| check_my_work | ✓ | ✓ | keep + Q6.3 docstring update |
| get_page_summary | ✓ | ✓ | keep + Q4.2 cite_key change + Q7.2 lead-paragraph parsing |
| get_thread_context | ✓ | ✓ | keep + Q4.1 cite_key in response |
| patch_page | ✓ | ✓ | keep |
| **write_draft_page** | ✓ | ✓ | **CUT** (0 calls in 100+ traces) |
| **validate_page_draft** | ✓ | ✗ | **CUT** (Q-NEW.1; 0 calls; cmw covers post-write) |
| **wiki_merge_pages** | ✗ | ✓ | **CUT phantom ref** at L238 |
| read_file (DeepAgents) | inherited | ✓ | keep |
| write_file (DeepAgents) | inherited | ✓ | keep |
| edit_file (DeepAgents) | inherited | ✓ | keep |
| task (DeepAgents) | inherited | ✓ | keep + Q6.4 reviewer-call docstring |
| ls / glob / grep (DeepAgents) | inherited | future Q10.3 add | add steering line |
| find_new_sources / list_uncompiled_emails / mark_as_compiled / append_to_log / stamp_page_compiled_at / update_wiki_index | @tool defined but coordinator-only (NOT registered to compile agent) | not in prompt | leave as-is |

**Net code cleanups (Boy-scout PR1, separate from prompt diff)**:
- `prompts.py`: cut L238 (`wiki_merge_pages`), cut L454 + L887 (`write_draft_page` mentions).
- `compiler.py`: cut L2432 (`write_draft_page` registration), cut L2438 (`validate_page_draft` registration), cut L2139-2210 (`validate_page_draft` def), cut patch_page docstring bullet at L412.
- `src/compile/draft.py`: DELETE entire file.
- `reviewer.py`: cut L254 (`draft_recommended` field).

## New questions surfaced during walk-through

### Part 17 — log_insight proactivity

| Q | Pick | Note |
|---|---|---|
| Q17 | A — Strong active teaching + Example 14 | Reframe L403-407 to actively encourage agent to log meta-insights (questions_for_human, tool_gap, prompt_ambiguity, structure_suggestion) alongside terminal outcomes. Add Example 14 showing meta-insight pattern. CC-G compliant. |

### Part 18 — Tool parity findings

| Q | Pick | Note |
|---|---|---|
| Q-NEW.1 | A — CUT validate_page_draft | 0 calls in 100+ traces; cmw covers post-write; CC-G trust LLM. |

## Walk-through complete — 51 questions answered (49 original + Q17 + Q-NEW.1)

Status: **READY FOR DIFF + PR1**.

## Ship plan (per Q0.4 = two-step PR)

### PR1 — reorg + dedupe (mechanical, no behavior change)

1. Section reorder per Q0.2 visual. Mechanics-first: background → content_floor → terminal_decision → chronological_scope → tool_guidance → procedure → recovering_from_blockers → page_types → frontmatter → section_titles → concept_vs_thread → expert_questions (or moved to reviewer per Q16.6) → inline_citations → revision_style → sources_management → self_review → editorial_notes → voice (NEW) → few_shots → hard_rules.
2. Cut duplicates per Q2.2 (concept_vs_thread L41-49), Q3.2 (5W flavor list), Q6.6 (bookkeeping rules), Q11.1 (reviewer dup), Q13.1 (hard rule trims).
3. Apply CC-A jargon sweep: rephrase `current-truth` ×3, drop "deepagents", drop `message_touched_pages`. Audit prompt for any other internal-jargon term.
4. Apply CC-J capability sweep: cut "NEVER modify /raw/", drop "don't flip compile state / write catalog rows / append to log", keep slim "don't manually edit `last_compiled` frontmatter".
5. Boy-scout (CC-H) code cleanups landed in same PR or split:
   - DELETE `prompts.py:238` phantom `wiki_merge_pages` ref.
   - DELETE `src/compile/draft.py` + `compiler.py:2432` registration + `prompts.py:454, :887` mentions + `reviewer.py:254` `draft_recommended` field.

**PR1 is verifiable line-by-line: same content, different shape.**

### PR2 — content + behavior edits

1. Add `<content_floor>` block (Q1.1).
2. Add symmetric forward-update rule in `<chronological_scope>` (Q1.2 — without `summary-stale-date` jargon).
3. Add `<voice>` section (Q14.1).
4. Promote `## Why it matters` to load-bearing (Q3.1 — without `current-truth` jargon).
5. Reframe `<expert_questions>` per-archetype, not per-domain (Q3.2 user note + CC-C). Then move whole block to reviewer per Q16.6.
6. Update WHO bullet → owner DRI in frontmatter (Q3.4 + Q8.1).
7. Replace Example 7 with selective wikilinking + email-canonical slug (Q12.1, CC-B).
8. Add new Q-delta example after Example 10 (Q12.2 retry). Add Example 11 UPDATE flow (Q5.1 + Q12.3). Add Example 12 chronological-scope DON'T (Q12.4). Add Example 13 bug-page shape (Q7.3).
9. Apply CC-G hybrid: replace 9-step procedure with lean `<procedure>` (~30 lines: outcome contract + tool-usage hints + multi-page consolidation rule). Move tool-ordering / reviewer-call rules into respective tool docstrings (Q6.3, Q6.4, Q10.5).
10. Universal H2 floor + LLM owns sections (Q7.1).
11. Lead paragraph IS the summary; drop `## Summary` and `## TL;DR` H2s (Q7.2). **Coordinator change required**: `get_page_summary` parses lead paragraph.
12. `<related_links>` rule (Q9.1).
13. Add inherited fs tools authorization without "deepagents" (Q10.3).
14. Self-review checks: lead-paragraph + owner (Q11.2).
15. ISO 8601 everywhere rule (Q13.3).
16. Hard rule additions: inline-Decision-H2 ban + strikethrough ban (Q13.2).
17. Triple-redundancy comment on `source_threads:` (Q16.5).
18. Preamble before tool-batches (Q16.1).
19. Verifiable pre-return checklist (Q16.4).
20. Apply CC-F coordinator hooks:
    - `cite_key` returned by `get_thread_context` (Q4.1).
    - Auto-build `## References` from inline footnotes (Q4.2).
    - Strip the `<inline_citations>` `## References` template + `## Sources` warning once runtime owns it.

**PR2 is content + behavior; smoke-tested before merge.**

### PR3 — NORTH-STAR.md ratification (post-PR2 smoke)

Per Q15.1. Ratify the conventions PR2 proved: References naming, lead-as-summary, slug form, selective wikilinking, terminal outcomes, Q/A-deltas, universal H2 floor, ISO 8601.

### Phase 2 follow-ups (separate PRs)

- Composable refactor: `prompts.py` → `prompts/` directory (Q0.3).
- Coordinator auto-trigger reviewer when N edits accumulate without a `task()` call (Q6.4 user note).
- Corpus-driven page-archetype research (Q7.1 user note: "scan a few hundred pages, see what 5-10 or 20 types are there").
- Concept-shaped terminal-outcome reframe (Q6.2 — tabled until middleware can change).
- Re-evaluate Q16.2 (reversibility framing) + Q16.3 (ambition) on more traces.

---

## Decisions log status: COMPLETE

49 of 49 questions answered. Diff plan above.

### Part 8 — `<frontmatter>`

| Q | Pick | Note |
|---|---|---|
| Q8.1 | A — owner only | Stage / target_date stay in body prose (consistent with Q7.4). Closes F-066. |

### Part 9 — `<related_links>` (NEW)

| Q | Pick | Note |
|---|---|---|
| Q9.1 | A — Add minimal rule | Frontmatter `related:` canonical; body `## Related` only when prose adds value; people NEVER in `## Related`. Closes F-069. |

### Part 7 — `<page_types>` (in progress)

| Q | Pick | Note |
|---|---|---|
| Q7.1 | A — Universal floor + LLM owns the rest | Top-level H2 floor: lead → Why → Current state → Recent changes → Open questions → Related → References. LLM can ADD sections + owns H3 structure. Frame as direction, not law (CC-G). User: "scan a few hundred pages and see what 5-10 or 20 different types are there and accordingly decide" — research task tracked in Phase 2 backlog. |
| Q7.2 | **A — Lead paragraph IS the summary** (X2 CONFIRMED) | Drop ## Summary and ## TL;DR. Runtime change: `get_page_summary` parses lead paragraph. Closes F-046. |
| Q7.3 | A — Show via worked Example 13 | Bug/incident shape via example, not rule. Closes F-058 without prescription. |
| Q7.4 | B — Show example + end-state expectation | User: "show examples and end state expectation. Let the LLM do the rest." Pure CC-G. |

### Part 6 — `<workflow>` (in progress)

| Q | Pick | Note |
|---|---|---|
| Q6.1 | A — Split into <terminal_decision> + <procedure> | But user note triggers CC-G: workflow shouldn't be step-prescriptive. After split, drop most of the 9-step procedure in favor of outcome + examples. |
| Q6.2 | B — Keep email-shaped | User: "I like both ideas but changing this now will require a lot of other changes" — table the concept-shaped reframe. |
| Q6-meta | Hybrid CC-G | Outcome contract + slim procedural floor; trajectory teaching moves to diverse worked examples. User: "if we see good signs, move to more radical outcome-based prompt later." |
| Q6.3 | A — WHEN-NOT in cmw docstring | Tool-level constraint, not workflow step. Closes U1 (64% premature cmw calls). |
| Q6.4 | A — WHEN in `task()` docstring + Phase 2 coordinator auto-trigger | User: also clarify cmw-vs-reviewer roles in their docstrings; coordinator can nudge agent when N edits accumulate without reviewer call (Phase 2). |
| Q6.5 | Skip | User: "skip unless evidence this is happening very often." F-031 not in langfuse top hits. |
| Q6.6 | DELETE all 3 instances | Triggers CC-J: LLM has no tools for most of these ops. Keep one slim rule: "don't manually edit `last_compiled` frontmatter — coordinator stamps it after return." Cut the rest from L461, L654, L1027. |

---

## Pending sweeps to apply during diffing

- [ ] CC-A: jargon sweep — `current-truth` × 3.
- [ ] CC-B: slug-form flip — L97-98 example + Hard rules + few-shots Example 7 + frontmatter template comments.
- [ ] CC-C: per-archetype reframe of L106-122 + corresponding `<domain_frontmatter>` adjustment.
- [ ] CC-D pattern-think: every batch, ask "where else does this show up?"
- [ ] CC-E format: every Q has CURRENT / PROPOSED / WHY in preview.

---

## Open structural questions surfaced during walk-through

- Section names are placeholder-shaped (Q0.2 user note). After PR1 reorg lands, audit names for clarity.
- Per-archetype framing (CC-C) may bleed into `<page_types>` and `<frontmatter>`. Revisit Q7.1 with this in mind.

