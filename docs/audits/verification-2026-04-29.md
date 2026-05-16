---
title: "Verification — prompt-PR set, 2026-04-29"
inputs:
  - docs/audits/verification-agent-brief.md
  - docs/audits/prompt-review-decisions-2026-04-28.md
  - docs/audits/prompt-review-synthesis-2026-04-29.md
  - docs/audits/prompt-pr-coverage-matrix-2026-04-29.md
status: ship-with-minor-followups
---

# Verification — prompt-PR set, 2026-04-29

## VERDICT

**ship-with-minor-followups.** All five PRs are in scope, behavior-preserving where claimed, mutually consistent at their declared staging point, and free of premature deletions. CC-A jargon sweep is clean (zero hits in PR #259's branch). Two cosmetic notes for PR2 follow-up; nothing blocking.

## Per-PR scope check

| PR | Files | In-scope? | Notes |
|---|---|---|---|
| #254 | `docs/NORTH-STAR.md` (+48 / -14) | ✅ | Doc-only. Matches Q15.1 + A5 synthesis (Sources rename, slug-form sentence, density target, terminal outcomes, H2 floor, ISO 8601, drop `## TL;DR`). |
| #255 | `src/compile/compiler.py` + 2 tests | ✅ | `_extract_tldr` + `get_page_summary` docstring; lead-paragraph fallback + 3 new tests (precedence, empty body, fallback). |
| #257 | `compiler.py`, `draft.py`, `raw_access.py`, 2 tests | ✅ | Docstrings only + new `cite_key` field on `get_thread_context.messages_summary`. `draft.py` change is the deprecation docstring on `write_draft_page` (per PR-body claim). |
| #258 | `mkdocs_hooks.py` + tests (+127 / 0; +122 tests) | ✅ | Auto-References builder, `## Sources` strip+warn, legacy bypass, dedupe, unresolved-hash marker. |
| #259 | `src/compile/prompts.py`, 2 test files | ✅ | Mechanical cleanup + dedupe + reorder. Commit 4 (test alignment) is acknowledged in PR body and necessary given commit 1's structural cuts. |

(Note: brief calls the mkdocs PR `#256`; actual number is **#258** — `#256` was filed under a different number. Coverage matrix doc reflects the old number.)

## Coverage check

Walked every row of `prompt-pr-coverage-matrix-2026-04-29.md`. Each "this PR" row resolves to the named diff; each "future 5b" row is intentionally deferred and tracked. No silent drops. Q3.3 / Q4.1 / Q4.2 / Q6.3 / Q6.4 / Q7.2 / Q10.2 / Q10.4 / Q10.5 / Q15.1 / Q-NEW.1 / CC-A / CC-F / CC-H / CC-J — all confirmed where mapped.

## Cross-PR consistency (synthesis section C, three staging tests)

1. **#254 drops `## TL;DR` from NS template; #255 adds runtime fallback; #259 still teaches `## TL;DR` H2.** Verified — `## TL;DR` appears at `prompts.py` L46 and L342 in #259's branch (intentional; gets cut in PR2). NS is ahead of the prompt; runtime supports both. Consistent staging.
2. **#258 auto-renders `## References`; #259 still teaches `## References` template (L531-L544).** Intentional staging, matches synthesis B4 sequencing.
3. **#257 deprecates `validate_page_draft` + `write_draft_page` docstrings; #259 cuts both from prompt examples + tool listing.** Tool registrations remain in compiler.py (L2273 def, L2564/L2570 register, L30 import) — consistent with synthesis A3 ("cut prompt mentions in PR1, leave registrations until PR2 traces confirm").

## CC sweep (Worker 5a's branch)

- **CC-A — clean.** Grep for `current-truth`, `summary-stale-date`, `message_touched_pages`, `V12-U3`, `check_my_work_gate`, `sibling-draft-check`, `terminal_decision middleware`, `deepagents` against `prompts.py` on PR #259's branch returns **zero hits**. Also grepped `coordinator`, `middleware`, `ContextVar`: zero hits — commit 4's "the coordinator → tracked automatically" sweep landed cleanly.
- **CC-B** (slug form) — applied in #254; prompt examples deferred to PR2.
- **CC-C** (per-archetype) — deferred to PR2 (not in scope of any of these 5 PRs).
- **CC-D / CC-E** — process principles, n/a per-PR.
- **CC-F** — runtime delegation visible in #255 (lead-paragraph fallback), #257 (cite_key), #258 (References auto-builder).
- **CC-G** — A1 hoist (terminal-outcome anchor) landed in PR #259 commit 2; lean procedure deferred to PR2.
- **CC-H** — boy-scout cleanups in PR #259 commit 1 (phantom `wiki_merge_pages`, dead-tool refs).
- **CC-J** — cut "NEVER modify /raw/" + bookkeeping bans confirmed gone in PR #259.

## Prompt-quality findings (PR #259 branch)

- `from src.compile.prompts import COMPILER_SYSTEM_PROMPT` parses cleanly; 41,706 chars / 987 lines (matches PR description).
- Section order matches commit 3 message and Q0.2 target (verified open/close tags, lines 15-950).
- Hard rules (L952-968): 7 rules, internally coherent, no contradictions, no orphan references.
- Examples 1-9 are sequentially numbered (Example 4 was the cut `write_draft_page` demo; subsequent examples renumbered). No dangling "Example N" pointers in body prose.
- Reorder behavior preservation: `sort prompts.py(770a89c) | diff - sort prompts.py(bc7549a)` is **empty** — every line preserved, only relocated. Symmetric +391 / -391 stat in commit message verified.
- Commit 4's deltas are 4 small jargon-sweep tweaks ("the coordinator" → "automatically") plus test alignment — accurate to commit message.

## Worker 3 gap (Q6.4 task() rule)

Tracked correctly. `prompt-pr-coverage-matrix:52` lists Q6.4 as `#257 (partial)` with note "task() not editable from this repo; needs `<tool_guidance>` rule in 5b". Item 1 of "Open gaps to verify before declaring done" (matrix:117) restates it. PR #257's body also calls it out. Nothing further to do here.

## Things that shouldn't be there

Confirmed **none** of the 5 PRs deletes `src/compile/draft.py`, `validate_page_draft` def, `write_draft_page` def, or `reviewer.py:draft_recommended`. All four artifacts present on every branch's tree. Phase 2 deletion remains intentional.

No out-of-scope changes detected.

## Recommended actions before merge

1. **Coverage matrix housekeeping (non-blocking)** — `prompt-pr-coverage-matrix-2026-04-29.md` lists the mkdocs PR as `#256`; actual number is `#258`. Update for accuracy when convenient.
2. **PR #259 follow-up** — `<todo_rule>` is parked next to `<self_review>` since Q0.2 didn't enumerate it. Either add an explicit slot in PR2's reorder pass or note it stays where it is. Currently flagged in PR body; not a blocker.
3. **Smoke before merging PR #259** — PR body acknowledges the smoke run was skipped (no API access in worktree). Since this is the system prompt, run a small `make compile` smoke before merging the prompt PR even though it's mechanically behavior-preserving. Synthesis A3 also explicitly calls for smoke-test on PR1.
4. **PR2 must include**: Q6.4 `## Reviewer call rule` in `<tool_guidance>`; Q7.2 prompt-side drop of `## TL;DR` H2 references; Q4.2 prompt-side drop of `## References` template + `## Sources` warning. All gated correctly today.

Worker 5a's noted quirks (brief's `prompts.py:412` was actually `compiler.py:~412/415`, `<todo_rule>` parking, commit 4 test alignment) are acceptable trade-offs given Q0.2 ("section names placeholder-shaped, will refine") + the test-alignment necessity once commit 1 cut renumbered examples.
