---
title: "Prompt PR coverage matrix — what landed in which PR, 2026-04-29"
inputs:
  - docs/audits/prompt-review-decisions-2026-04-28.md
  - docs/audits/prompt-review-synthesis-2026-04-29.md
status: in-flight
---

# PR coverage matrix

Single-page map from each decisions-log question to the PR that implements it. Use this with the final verification agent to confirm nothing was lost.

## Open PRs

| PR | Worker | Branch | Scope |
|---|---|---|---|
| #254 | Unit 4 | `docs/north-star-ratify-conventions` | NORTH-STAR.md ratification (8 edits) |
| #255 | Unit 1 | `feat/get-page-summary-tldr-fallback` | `_extract_tldr` falls back to lead paragraph |
| #258 | Unit 2 | `feat/mkdocs-references-auto-builder` | mkdocs hook auto-renders `## References` |
| #257 | Unit 3 | `worktree-agent-ad6f3c7ba96bf6516` | Tool docstring updates (cmw, resolve_page, get_thread_context cite_key, log_insight, create_entities, write_draft_page deprecate, validate_page_draft deprecate) |
| (pending) | Unit 5a | `worktree-agent-af474b802e4e0f230` (local) | prompts.py PR1 — boy-scout + dedupe + reorg |

## Decisions → PR mapping

| Q | Decision | PR | Notes |
|---|---|---|---|
| **Meta (Part 0)** | | | |
| Q0.1 | 750-line target | 5a → 5b | Unit 5a + future Unit 5b |
| Q0.2 | Mechanics-first reorg | 5a (commit 3) | Section reorder still pending |
| Q0.3 | Phase 2 prompts/ split | (deferred) | Tracked in synthesis |
| Q0.4 | Two-step PR | 5a + future 5b | |
| **Part 1 — opening** | | | |
| Q1.1 | `<content_floor>` block | future 5b | Content edit |
| Q1.2 | Symmetric forward-update | future 5b | |
| **Part 2 — concept_vs_thread** | | | |
| Q2.1 | New `<voice>` section | future 5b | |
| Q2.2 | Trim L41-49 batch enum | 5a (commit 2) | Dedupe |
| **Part 3 — expert_questions** | | | |
| Q3.1 | Promote `## Why it matters` | future 5b | |
| Q3.2 | Per-archetype reframe (CC-C) | future 5b | |
| Q3.3 (X1) | Email-canonical slug | #254 | Ratified in NS |
| Q3.4 | Owner DRI in frontmatter | future 5b | |
| **Part 4 — citations** | | | |
| Q4.1 | `cite_key` from get_thread_context | **#257** | Worker 3 added field |
| Q4.2 | Auto-build References | **#258** | Worker 2 mkdocs hook |
| **Part 5 — revision_style** | | | |
| Q5.1 | Update flow Example 11 + rule | future 5b | |
| **Part 6 — workflow (THE BIG ONE)** | | | |
| Q6.1 | Split into terminal_decision + procedure | 5a (commit 3) + 5b | Reorg in 5a; lean procedure in 5b |
| Q6.2 | Keep email-shaped | (no-op) | Tabled per user |
| Q6.3 | cmw post-write rule | **#257** | Worker 3 docstring |
| Q6.4 | Reviewer-call rule | **#257** (partial) | task() not editable from this repo; needs `<tool_guidance>` rule in 5b |
| Q6.5 | Sibling-phrasing check | (skipped) | Per user |
| Q6.6 | Cut bookkeeping | 5a (commit 2) | |
| **Part 7 — page_types** | | | |
| Q7.1 | Universal H2 floor | future 5b + #254 | NS ratified; prompt edit pending |
| Q7.2 (X2) | Lead-paragraph IS summary | **#255** + #254 + future 5b | Runtime ✓; NS ✓; prompt drop pending |
| Q7.3 | Bug-page Example 13 | future 5b | |
| Q7.4 | Current-state contract | future 5b | |
| **Part 8 — frontmatter** | | | |
| Q8.1 | owner only | future 5b | |
| **Part 9 — related_links** | | | |
| Q9.1 | New section | future 5b | |
| **Part 10 — tool_guidance** | | | |
| Q10.1 | Move tool_guidance up | 5a (commit 3) | Section reorder |
| Q10.2 | resolve_page bare-slug | **#257** | |
| Q10.3 | fs tools authorization | future 5b | |
| Q10.4 | Cut dead tools | **#257** (deprecation flag) + Phase 2 (code deletion) | |
| Q10.5 | WHEN/WHEN-NOT all tools | **#257** | Worker 3 covered |
| **Part 11 — self_review** | | | |
| Q11.1 | Cut reviewer dup | future 5b | |
| Q11.2 | Lead + owner checks | future 5b | |
| **Part 12 — few_shots** | | | |
| Q12.1 | Replace Example 7 | future 5b | |
| Q12.2 | Add Q-delta example | future 5b | |
| Q12.3 | Add Example 11 (covered by Q5.1) | future 5b | |
| Q12.4 | Add Example 12 chronological | future 5b | |
| Q12.5 | Skip trim | (no-op) | Per user |
| **Part 13 — hard_rules** | | | |
| Q13.1 | Trim per CC-A/J | 5a (commit 2) | |
| Q13.2 | Decision-H2 + strikethrough bans | future 5b | |
| Q13.3 | ISO 8601 everywhere | future 5b + #254 | NS done |
| **Part 14 — voice** | | | |
| Q14.1 | New section | future 5b | |
| **Part 15 — NORTH-STAR** | | | |
| Q15.1 | Bundled ratification | **#254** | Worker 4 done |
| **Part 16 — real-world** | | | |
| Q16.1 | Preamble before tool batches | future 5b | |
| Q16.2 | Reversibility framing | (skipped) | |
| Q16.3 | Ambition vs precision | (skipped) | |
| Q16.4 | Verifiable pre-return checks | future 5b | |
| Q16.5 | Triple-redundancy on source_threads | future 5b | |
| Q16.6 | Move 5W to reviewer | future 5b | |
| Q16.7 | Final-message format | (skipped) | |
| Q16.8 | Cite glossary | (skipped) | |
| **Part 17 — log_insight** | | | |
| Q17 | Active proactive teaching + Example 14 | future 5b | |
| **Part 18 — tool parity** | | | |
| Q-NEW.1 | Cut validate_page_draft | **#257** (deprecation) + Phase 2 (code deletion) | |

## Cross-cutting principles

| CC | Principle | Where applied |
|---|---|---|
| CC-A | No internal jargon in prompt | 5a (commit 1: current-truth, message_touched_pages, V12-U3 sweeps) |
| CC-B | Email-canonical kebab slug | #254 (NS) + future 5b (prompt examples) |
| CC-C | Per-archetype, not per-domain flavor | future 5b |
| CC-D | Pattern-think (fix the class) | (applied throughout walk-through) |
| CC-E | Question format with context | (conversation-level only) |
| CC-F | Runtime computes what it can | #255 (`_extract_tldr` fallback) + #258 (auto-build References) + #257 (cite_key) |
| CC-G | Outcome-based, less prescriptive | future 5b (lean `<procedure>`) + 5a (commit 2 hoist) |
| CC-H | Boy-scout / leave better than found | 5a (commit 1: phantom + dead refs) + #257 (deprecation flags) |
| CC-J | Don't ban non-capabilities | 5a (commit 1: dropped "NEVER modify /raw/", redundant bookkeeping rules) |

## Open gaps to verify before declaring done

1. **`task()` reviewer-dispatch rule**: Worker 3 flagged it's not editable from this repo. Needs to land as a `## Reviewer call rule` in `<tool_guidance>` in PR2 (Unit 5b) — explicitly tracked.

2. **Q7.2 dependency chain**: `## TL;DR` drop (prompt PR2) gates on:
   - #255 (runtime fallback) ✓ open
   - #254 (NS doc updated) ✓ open
   - PR2 (prompt edits to drop the H2 template + `## TL;DR` references)

3. **Q4.2 dependency chain**: `## References` template removal gates on:
   - #258 (mkdocs auto-render) ✓ open
   - PR2 (prompt edits to drop `## References` template + `## Sources` warning)

4. **Phase 2 deferred** (intentional, NOT a gap):
   - DELETE `validate_page_draft` def + `write_draft_page` def + `src/compile/draft.py`
   - DELETE `reviewer.py` `draft_recommended` field
   - `prompts/` directory split

5. **Things I should verify with a final agent**:
   - Worker 5a's commit 3 (section reorder) preserves all content
   - All cross-cutting principles applied consistently
   - No inconsistency between PRs (e.g. NS says lead-IS-summary while prompt still teaches `## TL;DR`)
   - PR2 plan is complete: every "future 5b" row above is in scope

## What "done" looks like

- 5 PRs landed (4 + Worker 5a final).
- PR2 (Unit 5b) opens against post-merge main, ships content edits per the rows marked "future 5b".
- 30-email compile smoke + persona-deep audit re-run shows no regression.
- NORTH-STAR.md and prompt are mutually consistent; tools' return shapes match prompt teachings.
