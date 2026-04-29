---
title: "Axis-2 review — PR #260 (prompts PR2 content + behavior), 2026-04-29"
inputs:
  - /Users/amtagrwl/git/email-knowledge-base/docs/audits/prompt-review-decisions-2026-04-28.md
  - /Users/amtagrwl/git/email-knowledge-base/docs/audits/prompt-review-synthesis-2026-04-29.md
  - /Users/amtagrwl/git/email-knowledge-base/docs/audits/prompt-pr-coverage-matrix-2026-04-29.md
  - /Users/amtagrwl/git/email-knowledge-base/docs/audits/intent-review-2026-04-29.md
  - PR diff (2b5c9ff, 88c5405, fd0bc38)
status: review-complete
---

# Axis-2 review — PR #260, 2026-04-29

## VERDICT

**minor-followups.** Every "future 5b" coverage-matrix row landed and the spirit holds. CC sweep is clean. Length budget is over the Q0.1 target by ~600 lines (1354 vs ~750), driven mostly by `<few_shots>` doubling — defensible, but two examples (Example 14 at 83 lines, Example 13 at 63 lines) are bigger than the brief implied. Three small gaps worth a follow-up before merge; none block the core intent.

## Per-commit-group review

### A. New sections — `<content_floor>`, `<voice>`, `<related_links>`

**Intended (Q1.1 / Q14.1 / Q9.1)**: 5-item verifiable floor right after `<background>`; voice section between `<editorial_notes>` and `<few_shots>` carrying anti-patterns + 6-month test + ≥30% blockquote (synthesis A2); related-links rule.

**Shipped**:
- `<content_floor>` at L17-43, all 5 items present (lead, owner, current state, open questions, references resolve). Sits immediately after `</background>` per the synthesis. Each item is concrete + verifiable, so the MUSTs are well-grounded (synthesis self-contradiction #1 mitigation respected).
- `<voice>` at L804-834, **between `<editorial_notes>` and `<few_shots>`** as specified. Includes "Vote of thanks" / "As of <date>" / first-person / thread-reference anti-patterns, the 6-month test, AND the ≥30% blockquote anti-pattern (synthesis A2 picked up cleanly before validate_page_draft deletion).
- `<related_links>` at L836-855: frontmatter canonical, body `## Related` only when prose adds value, people NEVER in `## Related`. Faithful.

**Wrong-shape**: none. **Missing**: none. **Scope creep**: none. Verdict: **clean.**

### B. Content rewrites — chronological_scope, Why-it-matters, expert_questions per-archetype, 5W to reviewer, owner DRI

**Intended**: Q1.2 symmetric forward-update without internal jargon; Q3.1 promote `## Why it matters` without "current-truth Summary" jargon; Q3.2/CC-C per-archetype reframe; Q16.6 move 5W block to reviewer (~58 lines saved); Q3.4/CC-B owner DRI in frontmatter.

**Shipped**:
- `<chronological_scope>` (L45-64): symmetric "two rules", clean prose, no `summary-stale-date` term anywhere. Reads as plain language.
- `<expert_questions>` (L553-592) reframed: archetypes (launch / bug / policy / decision / system overview), no per-domain flavor. **`## Why it matters` load-bearing rule retained** at L582-586 with the 5-line anchor pointer ("operational constraint — customer pain, SBU boundary, historical incident, revenue") — synthesis item #2 (depth scaffolding) honored.
- 5W block move: 34 lines added to `src/compile/reviewer.py` (commit 88c5405); the compiler-side block is gone from `<expert_questions>`. Net compiler savings ~32 lines (smaller than the brief's "58 lines" because the 5-line pointer + archetype framing were retained — correct call given the depth-gap concern).
- Owner DRI: `owner:` in frontmatter template (L1345), in content_floor item 2, in self_review item 6, in every example (1, 2, 7, 9, 13). Email-canonical kebab slug throughout (`ravi-menon-indiamart-com`, `aa-indiamart-com`, etc.). CC-B applied consistently.

**Wrong-shape**: none. **Missing**: none. **Scope creep**: none. Verdict: **clean.**

### C. Procedure rewrite — CC-G hybrid, multi-page consolidation, reviewer-call rule, fs tools auth

**Intended (Q6-meta, Q6.4, Q10.3)**: replace 9-step recipe with lean ~30-line outcome+example procedure, keep terminal-decision contract + 4 outcomes + Q-delta + A-delta exceptions; multi-page consolidation rule; `## Reviewer call rule` in `<tool_guidance>`; ls/glob/grep authorized; drop "deepagents".

**Shipped**:
- `<workflow>` is 184 lines (vs 170 on main). The terminal-decision contract is the entire top half (L157-295) — 4 outcomes + Q-delta + A-delta + investigatory-vs-terminal distinction + proactive meta-insight teaching. **"Pick the terminal outcome before typing"** anchor explicit at L159 (synthesis A1 honored).
- `### Procedure` subsection (L297-335) is ~38 lines: outcome goal + tools-that-help list + preamble rule + boy-scout/multi-page consolidation + 5-bullet pre-return checklist. The 9-step recipe is gone. CC-G hybrid landed.
- **Multi-page consolidation rule** present at L320-323: "If you traversed multiple pages or links to find a fact, consolidate it: inline the answer or add a cross-link so the next agent doesn't redo the traversal." Faithful to user's boy-scout instruction.
- `## Reviewer call rule` at L129-139 inside `<tool_guidance>`. Lands in PR2 as required (Worker 3's flagged gap; `task()` not docstring-editable from this repo).
- `## Inherited filesystem tools` at L141-149: ls/glob/grep authorized, wiki-specific tools preferred first. **No "deepagents" mention** (CC-A clean).

**Wrong-shape**:
- The 4-bullet "Tools that help" list at L302-313 lightly duplicates the Discovery section in `<tool_guidance>` (L76-94). Defensible — the workflow needs to name the tools at the point of decision — but it adds ~12 lines.

**Missing**: none. **Scope creep**: none. Verdict: **clean.**

### D. Examples — replace Example 7, add Q-delta / UPDATE / chronological-DON'T / bug / meta-insight

**Intended (Q12.1-12.4, Q7.3, Q17)**: Example 7 selective wikilinking with email-canonical slugs; Example 10 Q-delta `patch_page("Open questions")`; Example 11 UPDATE flow re-reading Summary + edit_file the stale claim; Example 12 chronological-scope DON'T; Example 13 bug shape (Symptoms / Root cause / Fix / Verification); Example 14 meta-insight + terminal outcome.

**Shipped**:
- Example 7 (L1011-1057, 53 lines): rollout DRI + approver wikilinked, three context-only contributors plain-prose. Email-canonical slugs everywhere. `## Related` people-exclusion called out in the trailing note. Faithful — closes F-070.
- Example 10 (L1126-1153, 29 lines): Q-delta extension via `patch_page("Open questions", ...)`; explicit "this is NOT already_captured" note. Faithful.
- Example 11 (L1155-1180, 27 lines): forward-update — `read_file` then `edit_file` the stale lead-paragraph claim, then `patch_page("Recent changes", ...)`, then `check_my_work`. Hits Q5.1 + Q12.3 cleanly.
- Example 12 (L1182-1209, 29 lines): today-is-older DON'T — appends only to Recent changes, leaves lead alone. Closing line "Don't rewrite history from the future, even when the future is already on the page" is a clean restatement of Q1.2.
- Example 13 (L1211-1272, **63 lines**): bug page shape — Symptoms / Root cause / Fix / Verification; explicit note that bug pages omit Why-it-matters + Current state. Faithful.
- Example 14 (L1274-1307, **83 lines**): patch + log_insight("prompt_ambiguity") in parallel. Faithful to Q17 active-meta-insight teaching.

**Wrong-shape**: Example 14 is the longest example (83 lines). It carries weight (it's also the proof-by-demonstration of Q17's active-meta-insight reframe), but ~30 lines of it are commentary rather than tool-call. Could plausibly trim to ~50 lines without losing the lesson.

**Missing**: none. **Scope creep**: none. Verdict: **minor — Example 14 over-prosed.**

### E. Hard rules + page_types + frontmatter + self_review

**Intended (Q7.1, Q7.2, Q4.2, Q8.1, Q11.2, Q13.2, Q13.3, Q16.5, Q17)**: Universal H2 floor; drop `## TL;DR` + `## Summary` + `## References` template + `## Sources` warning; owner-only frontmatter; lead-paragraph + owner self-review checks; `NEVER ## Decision: <X>` H2 + `NEVER strikethrough`; ISO 8601; triple-redundancy on `source_threads:`; active log_insight teaching.

**Shipped**:
- Universal H2 floor at `<page_types>` L406-422: topic / system / policy each get a floor, framed as "direction, not a law". H3 LLM-owned. Bug-page deviation called out with cross-ref to Examples 7 + 13. Q7.1 spirit preserved.
- `## TL;DR` and `## Summary` H2s dropped — only references are explanatory ("lead paragraph IS the summary"). `## References` mentioned only as runtime-rendered (L412, L603, L917). `## Sources` warning gone. Q4.2 / Q7.2 unblocked-and-shipped cleanly.
- Owner-only frontmatter (Q8.1) at L1339-1352. `# NOT sources:` triple-redundancy comment at L1346 — Q16.5 honored. CC-B email-canonical slug example in the comment.
- Self-review (L758-780): item 5 (lead paragraph) + item 6 (owner) added. Item 1 (≥30% blockquote) duplicates the `<voice>` rule but is short — defensible. **Devin one-liner** at L775-776 ("Take one beat to verify each email reached a terminal outcome..."). Synthesis A4 honored.
- New Hard rules at L1328-1335: `NEVER ## Decision: <X>` + `NEVER strikethrough` both present.
- ISO 8601 (Q13.3) at L645-647 in `<revision_style>`: "Use ISO 8601 (YYYY-MM-DD) for dates everywhere — frontmatter fields, `## Recent changes` bullets, body prose. Never 'Apr 15' or '15-04-2026'; always `2026-04-15`."
- Active log_insight teaching at L273-289: "Suggest meta-insights proactively" + 4 categories (`question_for_human`, `tool_gap`, `prompt_ambiguity`, `structure_suggestion`). Bullet-format. Q17 honored.

**Wrong-shape**: none. **Missing**: none. **Scope creep**: none. Verdict: **clean.**

### F. Real-world patterns — preamble, pre-return checklist, Devin one-liner

**Intended (Q16.1, Q16.4, A4)**: 1-2 sentence preamble before tool batches (not single calls); concrete + verifiable pre-return checklist (no MUST overload); Devin `<think>` one-liner.

**Shipped**:
- Preamble rule at L315-318: "Before any batch of tool calls, emit a 1-2 sentence preamble describing your intent (8-12 words). 'Resolving the page and reading the thread for X.' Don't preamble single tool calls; don't narrate each call." Q16.1 honored.
- Pre-return checklist at L325-334: 5 concrete + verifiable bullets (terminal outcome / lead paragraph / owner / voice / wikilinks). No MUST piling. Q16.4 honored.
- Devin one-liner: L775-776 in `<self_review>`. Synthesis A4 honored.

Verdict: **clean.**

### Length-budget audit

Section sizes (PR branch):

| Section | Lines | Δ vs main | Note |
|---|---:|---:|---|
| `<background>` | 16 | 0 | unchanged |
| `<content_floor>` | 27 | +27 | NEW (Q1.1) — defensible, each item is a verifiable check |
| `<chronological_scope>` | 20 | +13 | symmetric rewrite (Q1.2) — well-spent |
| `<tool_guidance>` | 85 | +38 | absorbs Reviewer-call rule + fs-tools rule (Q6.4 + Q10.3) — well-spent |
| `<workflow>` | 184 | +14 | terminal-decision contract expanded with active meta-insight teaching (Q17); Procedure section trimmed; net +14 — hybrid balance correct |
| `<page_types>` | 64 | -1 | universal floor reframed; comparable size |
| `<expert_questions>` | 40 | +3 | archetype reframe; 5W block gone (~32 lines saved); Why-it-matters anchor added — hybrid net |
| `<inline_citations>` | 47 | -15 | trim |
| `<revision_style>` | 93 | +4 | ISO 8601 line + minor edits |
| `<self_review>` | 23 | +9 | items 5/6 added + Devin one-liner |
| `<voice>` | 31 | +31 | NEW (Q14.1) — well-spent |
| `<related_links>` | 20 | +20 | NEW (Q9.1) — well-spent |
| `<few_shots>` | **453** | **+213** | 5 new examples (10-14) + Example 7 replacement — see below |

`<few_shots>` is the dominant driver of the +367 expansion. Per-example sizes (PR branch):

- Example 1: 61 (was 67) — shrunk
- Example 2: 27 (was 23) — owner-frontmatter add
- Example 7 (replacement): **53** (was 22 on main as "Fully investigated, no new delta")
- Example 9: 35 (was 71 on main) — actually shrunk significantly
- Example 10 (NEW): 29
- Example 11 (NEW): 27
- Example 12 (NEW): 29
- Example 13 (NEW): **63**
- Example 14 (NEW): **83**

**Examples 13 + 14 together account for 146 lines (~40% of the few_shots delta).** Example 13 is a full bug-page write_file with realistic prose — defensible because the brief explicitly asked for "different shape" demonstration. Example 14 (83 lines) is heavier than its peers; ~30 lines are commentary/notes around the tool calls. **Trim candidate**: ~25-30 lines of Example 14 prose without losing the meta-insight lesson.

**Verdict on length budget**: 1354 lines is over the Q0.1 750-line target by ~600 lines, but the target was aspirational. The bulk of the +367 is non-discretionary (every "future 5b" row required new prose). The legitimate question is whether `<few_shots>` at 453 lines is the right shape — it's now ~33% of the prompt. If the agent is pattern-matching to examples (which is the brief's premise), this is correct; if the prompt is being skimmed, the example weight may dilute the rules section. Watch trace data after smoke.

## CC sweep result

`grep -nE "(current-truth|summary-stale-date|message_touched_pages|V12-U3|check_my_work_gate|sibling-draft-check|terminal_decision middleware|deepagents|coordinator|middleware|ContextVar)" /tmp/prompts_pr260.py` → **zero hits**.

CC-A jargon sweep is **clean**.

CC-B email-canonical slug form: `ravi-menon-indiamart-com`, `priya-bansal-indiamart-com`, `anjali-shankar-indiamart-com`, `aa-indiamart-com`, `swati-jain-indiamart-com`, `ankur-raj-indiamart-com`, `priyanka-rao-indiamart-com`, `fraud-team-lead-indiamart-com`, `platform-reliability-leads-indiamart-com`, `amit-agarwal-indiamart-com`. Consistent across all examples. **Clean.**

CC-C per-archetype reframe in `<expert_questions>` — present (L555-580). **Clean.**

CC-G hybrid procedure — terminal anchor at L159, lean Procedure at L297-335. **Clean.**

## Cross-PR consistency

- TL;DR: NS (post-#254) says lead-IS-summary; runtime (post-#255) parses lead paragraph; validator (this PR's fd0bc38) drops `Summary` from suggested floor; prompt (this PR) drops `## Summary` and `## TL;DR` template + adds explanatory "lead paragraph IS the summary". **All four surfaces aligned.**
- References: NS (post-#254) renamed Sources→References; runtime (post-#258) auto-renders `## References` from inline footnotes; validator (this PR) drops `References` from floor; prompt (this PR) only mentions `## References` as runtime-rendered. **All four aligned.**
- Slug form: NS (post-#254) explicit slug-form sentence with `aa-indiamart-com`; tools (post-#257) accept bare slugs; prompt (this PR) examples all use email-canonical kebab. **Aligned.**

No drift detected.

## Worker 5b's noted quirks

- **`_workflow_prompt_under_budget` ceiling raised to 60k** (PR body). Worth a sanity check that no token-budget alarm needs raising elsewhere; PR body says 1354 lines / 57.7k chars from 41.7k — a 38% chars increase. Not a blocker.
- **4 pre-existing `FakeAgent` test failures** flagged as "same as main". Not introduced by PR. Acceptable.
- **Smoke compile not run in worktree** (no LLM access). The PR ships untested by the runtime. Synthesis required smoke-pass before declaring done — needs to happen post-merge.
- **`section_shapes.py` policy floor** kept legacy 5-section list (no `Related`). Validator-side asymmetry vs topic/system: OK because policy archetype IS different shape (Current policy / Who it affects / Effective date / Supersedes / History). Intentional.

## Recommended actions before merge

Listed by severity (lowest first — none are blockers):

1. **Optional**: Trim Example 14 by ~25 lines. The lesson is the parallel `patch_page` + `log_insight("prompt_ambiguity", ...)` shape; the rest is commentary. Would bring the example in line with peers and shave ~25 lines off the +367. Defer if the user wants the meta-insight teaching maximally explicit; this is the example whose proactive-suggestion lesson is hardest to compress.

2. **Optional**: Item 1 of `<self_review>` (≥30% blockquote) and the matching rule in `<voice>` are duplicates. Two surfaces is intentional reinforcement (per Q16.5 triple-redundancy thinking) but the user may want to pick one. Low-risk either way.

3. **Smoke required post-merge**: synthesis explicitly required `make compile` smoke before declaring PR2 done. Worker couldn't run it in the worktree. Track as a merge-gate item, not a review-blocker.

Otherwise, every "future 5b" coverage-matrix row is shipped with the right intent and the cross-cutting principles (CC-A through CC-J as in scope) are honored.

Report saved to /Users/amtagrwl/git/email-knowledge-base/docs/audits/intent-review-pr260-2026-04-29.md
