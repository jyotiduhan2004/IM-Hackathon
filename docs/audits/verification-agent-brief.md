---
title: "Verification agent brief — final prompt-PR sanity check"
intent: "Spawn after Worker 5a opens its PR. Goal: catch contradictions or coverage gaps before merging."
status: ready-to-spawn
---

# Verification agent brief

Spawn a verification agent with this self-contained prompt. The agent should NOT make changes — only read + report.

## Inputs the agent needs

1. **Decisions log**: `docs/audits/prompt-review-decisions-2026-04-28.md` — single source of truth for all 51 question-decisions + cross-cutting principles (CC-A through CC-J).
2. **Re-review synthesis**: `docs/audits/prompt-review-synthesis-2026-04-29.md` — captures what 5 reviewers flagged + auto-applied tweaks.
3. **PR coverage matrix**: `docs/audits/prompt-pr-coverage-matrix-2026-04-29.md` — per-decision PR mapping.
4. **The PRs themselves** (open at verification time): #254, #255, #256, #257, plus Worker 5a's PR (number TBD).
5. **Original prompt** (pre-changes baseline): commit b6fa25b at `src/compile/prompts.py`.

## Verification questions the agent must answer

### A. Does each PR's diff match its declared scope?
Diff each open PR against main. Confirm:
- PR #254 only touches `docs/NORTH-STAR.md` (8 ratification edits per Q15.1).
- PR #255 only touches `src/compile/compiler.py` (`_extract_tldr` + tests).
- PR #256 only touches `mkdocs_hooks.py` + its tests.
- PR #257 only touches tool docstrings + `cite_key` field in `get_thread_context` + deprecation flags.
- PR for Unit 5a only touches `src/compile/prompts.py` (cleanup + dedupe + reorg, no content additions).

Flag any out-of-scope changes.

### B. Coverage check — does every decision land somewhere?
Walk the coverage matrix doc. For each row:
- Confirm "future 5b" rows are intentionally deferred (PR2 is planned, not missed).
- Confirm "this PR" rows are actually in the named PR's diff.
- Flag any mapping discrepancies.

### C. Cross-PR consistency
Test that the 5 PRs don't contradict each other:
- PR #254 (NS) drops `## TL;DR` from template. PR #255 (runtime) supports lead-paragraph fallback. Worker 5a's prompt PR1 does NOT yet drop `## TL;DR` H2 references (that's Unit 5b). Confirm the prompt + NS + runtime are CONSISTENT in their stage of evolution: prompt still teaches `## TL;DR` (will change in 5b); NS drops it now (ahead of prompt).
- PR #256 (mkdocs) auto-renders `## References`. Worker 5a's prompt PR1 does NOT yet drop the `## References` template (Unit 5b). Confirm intentional staging.
- PR #257 (tool docstrings) flips `validate_page_draft` + `write_draft_page` to deprecation messages. Worker 5a's prompt PR1 already cuts `validate_page_draft` calls from few-shot examples + `write_draft_page` Example 4. Confirm consistency.

### D. Cross-cutting principle audit
For each CC (A through J in the decisions log):
- Confirm at least one PR applies it where applicable.
- Specifically check CC-A (jargon sweep): grep Worker 5a's prompt branch for `current-truth`, `summary-stale-date`, `message_touched_pages`, `V12-U3`, `check_my_work_gate`, `sibling-draft-check`, `terminal_decision middleware`, `deepagents`. Should be ZERO hits except in clearly user-facing tool names.

### E. Prompt-quality sanity check
Read Worker 5a's branch state of `src/compile/prompts.py` end-to-end. Specifically:
- Does the prompt still parse (importable as `COMPILER_SYSTEM_PROMPT`)?
- Are there orphaned references to removed sections / examples?
- Does `## Hard rules` still have coherent rules (not internally contradicting)?
- Is the section reorder commit truly behavior-preserving (compare diff: every `+` should mirror a `-` in a different location)?

### F. Worker 3's flagged gap
Worker 3 reported the DeepAgents `task` reviewer-dispatch tool isn't editable from this repo. The reviewer-call rule (Q6.4) needs to land as a `## Reviewer call rule` section in `<tool_guidance>` in PR2 (Unit 5b). Confirm:
- This is captured in the synthesis doc as a known gap.
- The Unit 5b commit-list in the plan / synthesis includes it.

### G. Things that should NOT be in any of these PRs
- Code deletion of `src/compile/draft.py`, `validate_page_draft` def, `write_draft_page` def, `reviewer.py`'s `draft_recommended` field — these are Phase 2 (post-PR2 smoke). Confirm none of the open PRs prematurely delete them.
- New behavior in PR1 (Worker 5a) — this PR is mechanical only.

## Output format

Save to `/Users/amtagrwl/git/email-knowledge-base/docs/audits/verification-2026-04-29.md`.

```markdown
# Verification — prompt-PR set, 2026-04-29

## VERDICT
ship / ship-with-fixes / hold-for-rework

## Per-PR scope check
| PR | In-scope? | Notes |

## Coverage check
| Decision | Expected PR | Found | Status |

## Cross-PR consistency
- (per the 3 staging tests in section C)

## CC sweep
- CC-A: <hits / clean>
- CC-B through CC-J: <each>

## Prompt-quality findings (Worker 5a branch)
- (any orphans, contradictions, parse breaks)

## Worker 3 gap (Q6.4 task() rule)
- (status of the tracked deferred item)

## Things that shouldn't be there
- (any premature deletions or out-of-scope changes)

## Recommended actions before merge
- (concrete fixes if any)
```

## What the agent must NOT do

- DO NOT push commits or open new PRs.
- DO NOT comment on the existing PRs (avoid noise; let the human merge).
- DO NOT generate a long opinion piece — stick to the verification checks above.
- DO NOT spawn sub-agents.

## How long should this take

Aim for under 600 words in the report. Read-only work. Should complete within 5-10 minutes.
