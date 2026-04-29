---
title: "Prompt review — re-review synthesis, 2026-04-29"
inputs:
  - docs/audits/prompt-review-decisions-2026-04-28.md
  - /tmp/rereview-best-practices-2026-04-29.md
  - /tmp/rereview-audit-findings-2026-04-29.md
  - /tmp/rereview-langfuse-2026-04-29.md
  - /tmp/rereview-northstar-2026-04-29.md
  - /tmp/rereview-realworld-2026-04-29.md
status: synthesis-complete
---

# Re-review synthesis — what to adjust before PR1

Five reviewers + tool-parity audit + my own contradiction pass against the 51-question decisions log.

## TL;DR

**Ship with tweaks.** No fundamental contradictions. Roughly a dozen specific adjustments, of which 8 auto-apply and 4 need user input.

| Reviewer | Verdict | High-priority items |
|---|---|---|
| Best-practices | ship-with-tweaks | 3 concerns, 3 missing items |
| Audit-findings | needs gating | 12 closed, 4 uncovered, 5 at-risk, 6 new risks |
| Langfuse | data-decayed-but-supported | 4 headline decisions self-closed (good), 3 new patterns to add |
| NORTH-STAR | partially aligned | 3 wins, 3 drift items, 3 PR3 gaps |
| Real-world | at-mid-distribution | 4 well-adopted, 3 rejections to reconsider |

## Contradictions found in the original decisions log

These are mine, double-checking before reviewer findings:

1. **Q1.1 vs best-practices on MUST overload** — `<content_floor>` block uses 5 MUSTs at the top. Best-practices flags MUST overload as damaging Opus reasoning when applied to soft rules. Mitigation: each item in the floor is concrete and verifiable (TL;DR present, owner set, etc.) so MUSTs are well-grounded — keep, but watch trace data after PR2.

2. **Q3.1 (promote ## Why it matters) vs Q16.6 (move 5W to reviewer)** — Q3.1 makes ## Why-it-matters load-bearing for hidden-curriculum, but Q16.6 moves the 5W frame (which TEACHES depth) to the reviewer. Gap: the compiler is asked to write depth-y Why-sections without the depth scaffolding. Mitigation: keep a 5-line pointer in compiler ("Why-it-matters: anchor on the operational constraint — the customer pain, the SBU boundary, the historical incident") even after the full 5W block moves.

3. **Q7.2 (drop ## TL;DR) vs `get_page_summary` runtime contract** — current `get_page_summary` parses `## TL;DR` H2 → returns `tldr` field. Coordinator change is mandatory before the prompt change ships. Already noted in the decisions log; tightening below in Q-NEW.2.

4. **Q-NEW.1 (cut validate_page_draft) vs Q7.2 (drop ## TL;DR)** — `validate_page_draft.check_missing_tldr` is one of its 4 checks. If we cut the tool, the over_quoting (>30% blockquote) check ALSO disappears as a guardrail. Best-practices reviewer flagged this. Mitigation: move ≥30% blockquote check into `<voice>` section + `check_my_work`.

5. **CC-G lean `<procedure>` vs F-034 terminal-decision** — Cutting the 9-step procedure could erode the "every email gets a terminal outcome" discipline. L425 "Pick the terminal outcome before typing" is load-bearing — best-practices, langfuse, and audit-findings all flag this independently. Mitigation: hoist L425 sentence into `<terminal_decision>` section.

## Reviewer-flagged adjustments — AUTO-APPLY

These have clear evidence and don't need user input.

### A1. Hoist L425 "Pick the terminal outcome before typing" into `<terminal_decision>`

Three reviewers flag this. Lift the sentence (and any related commit-discipline phrasing) into `<terminal_decision>` so cutting the 9-step procedure doesn't drop it.

### A2. `<voice>` section adds ≥30% blockquote anti-pattern

Real-world + best-practices. Picks up the over_quoting check from `validate_page_draft.check_over_quoting` before we delete the tool. Keep as a one-liner in `<voice>`: *"If more than ~30% of body lines are blockquotes (`> ` prefix), the page is filing email paste-in, not synthesis. Rewrite."*

### A3. Defer code deletion of `validate_page_draft` + `write_draft_page` until PR2 traces confirm

Best-practices. Cut prompt mentions in PR1 (already planned). DELETE actual code only after PR2 ships and 100+ traces show neither tool is called and no regression follows. Update PR1 to delete prompt mentions but leave the registrations.

### A4. Add Devin's named-transition `<think>` rule (one line) to `<self_review>` or `<procedure>`

Real-world. *"Before returning, take one beat to verify each email reached a terminal outcome and the lead paragraph defines what it IS."* Just one sentence.

### A5. PR3 NS-ratification scope — three additions

NORTH-STAR + audit-findings. Add to PR3:
- `## Sources` → `## References` rename in NS:95-99 (also touched in NS body).
- Explicit slug-form sentence: "Slugs are kebab-case identifiers; for people, they are kebab-case email addresses (e.g. `aa-indiamart-com`)."
- Wikilink density target: "Each topic page links 3-8 adjacent concepts in `related:` frontmatter."

### A6. Sweep "think" → "consider" / "evaluate" for Opus 4.7 prose

Best-practices, citing Anthropic Opus 4.7 prompting docs. Audit current prompt for "think" / "thinking" used as instructions; replace with "consider" / "evaluate" / "verify" where appropriate. Quick grep before PR1.

### A7. Phase 2 `prompts/` split — calendar not list

Real-world. Add a concrete date/trigger to the Phase 2 follow-up: "Phase 2 `prompts/` directory split — schedule for first week post-PR2 smoke-pass."

### A8. Verify renderer supports `[[slug|Display]]` before CC-B ships

Audit-findings + slug-form pragmatic. One-line check on the MkDocs config / wikilink renderer. If it doesn't, ship CC-B with bare-slug-only and add display-name resolution as a follow-up.

## Reviewer-flagged adjustments — NEEDS USER INPUT

These are non-trivial and need a tie-break. Asking via AskUserQuestion next.

### B1. Reconsider Q6.5 (sibling-near-dups, F-031)

Audit-findings escalates: F-031 was Tier-1 in V12, "explicitly skipped Q6.5 despite being V12 Tier-1." But langfuse re-review found 0 evidence of the failure pattern in current 7-day window. **Skip-because-no-evidence vs add-because-Tier-1-might-resurface.**

### B2. Reconsider Q16.7 (final-message format)

Real-world: same trace-greppability justification as the preamble (Q16.1) we adopted. If we adopted preamble for trace-readability, why reject final-message format which is even more diff-able? Originally rejected as "scope creep".

### B3. Add new langfuse-grounded patterns to PR2

Langfuse re-review found 3 new patterns NOT in the walk-through:
- `glob` timeout storm: 44/70 calls (63%) error on `**/...` patterns.
- `edit_file` "string not found": 9/75 calls (12%) — agent reads stale state after a successful prior edit.
- `duplicate-h2` non-recovery loop: 7 consecutive blocked cmws on one trace; same SHAPE as summary-stale-date but on a different check. Example 11 only teaches Summary stale.

Three sub-decisions to make.

### B4. Q7.2 / Q4.2 / Q-NEW.1 gating — block PR2 on coordinator changes, or ship prompt with shims?

Q7.2 (drop ## TL;DR) requires `get_page_summary` to parse the lead paragraph instead. Q4.2 (auto-build References) requires runtime to render `## References` from inline footnotes. Both are coordinator-side changes. **Ship sequencing**: (a) coordinator changes first → prompt PR2, or (b) ship in parallel with feature flags, or (c) defer Q7.2 + Q4.2 to a later prompt PR after coordinator changes.

## Reviewer-flagged adjustments — DEFERRED for now

### Q6.2 calcification tripwire (NORTH-STAR)

Tabled in walk-through. NS reviewer warns it could calcify. Add to Phase 2 follow-up: "Re-evaluate concept-shaped terminal-decision after PR2 smoke; concrete trigger = `>X` traces showing one-email-touches-N-pages compile pattern."

### Q16.6 + CC-G depth gap (NORTH-STAR + audit-findings)

`<voice>` (Q14.1) and reviewer-side 5W must actually carry the depth that the compiler-side 5W block was carrying structurally. Mitigation handled by self-contradiction item #2 above (keep 5-line Why-it-matters pointer in compiler).

## Decisions confirmed by re-review (high confidence)

These survived all 5 angles unchanged:

- CC-A jargon sweep, CC-J capability sweep, CC-F runtime delegation — all "textbook GPT-5/Anthropic moves" per best-practices.
- CC-B email-canonical slug form — 348 instances backed by data; clean migration shape; only the renderer pipe-syntax check is a residual risk.
- Q3.4 + Q8.1 owner DRI in frontmatter — closes F-066 unambiguously.
- Q14.1 `<voice>` section + 6-month test.
- Q13.2 inline-Decision-H2 ban + strikethrough ban — strikethrough still has 3 hits / 3 traces in latest data.
- Q12.1 selective wikilinking Example 7 — F-070 tied for highest-impact behavior shift.
- Q17 active log_insight encouragement + Example 14 — real-world novel, but matches the user's explicit request.
- CC-G lean `<procedure>` — supported by langfuse data; agent will find its way.

## Decisions still backed by data (langfuse re-confirmed)

| Decision | Latest evidence |
|---|---|
| CC-B email-canonical slugs | 348 instances + 8 wrong-prefix `people/` |
| D9 strikethrough ban | 3 hits in 3 traces |
| Terminal-outcome contract | kimi-k2.6 model: 2/49 traces miss; keep contract explicit |
| CC-J inherited fs tools | 118 ls/glob/grep calls |

## Decisions whose evidence has decayed (closed by middleware)

| Decision | Old evidence | Now | What this means |
|---|---|---|---|
| U1 cmw pre-write | 64% | 0% | Middleware fixed it; rule moves to docstring as ratification, not as fix. |
| D3 reviewer task pre-write | 58% | 0% | Same. |
| U2 resolve_page flail | 70% | 14-29% | qmd closed bulk of it; remaining is residual. |
| U3 summary-stale-date | 0/11 | 0 instances | Closed; rule retained as ratification. |

The frame for these decisions shifts from "this fixes a current bug" to "this ratifies a behavior the runtime now enforces." Same edits, different motivation. Worth being honest about.

## Updated ship plan deltas

### PR1 — reorg + dedupe (BEHAVIOR-PRESERVING + smoke-test)

Adds vs original plan:
- A1 (hoist L425), A6 ("think"→"consider" sweep) before declaring no-change.
- Smoke-test required before merge (audit-findings: not truly behavior-preserving without it).
- A3: cut PROMPT mentions of `validate_page_draft` and `write_draft_page` only; leave code registrations.

### PR2 — content + behavior

Adds vs original plan:
- A2 (≥30% blockquote in voice).
- A4 (Devin `<think>` one-liner).
- B3 sub-decisions (glob/edit_file-stale/duplicate-H2 loop) per user input.
- B4 sequencing (Q7.2 + Q4.2 gating) per user input.
- Self-contradiction #2: keep 5-line Why-it-matters pointer in compiler.

### PR3 — NORTH-STAR ratification

Adds vs original plan:
- A5 three additions (Sources rename, slug form sentence, wikilink density).
- Q6.2 tripwire definition.

### Phase 2 calendar

- A7: prompts/ split scheduled for first week post-PR2 smoke.
- Code deletion of `validate_page_draft` + `write_draft_page` after PR2 + 100-trace confirmation.
- Q6.2 re-evaluate trigger.

## Final question-stack — RESOLVED

All B-series tie-breaks now answered.

| Q | User pick | Note |
|---|---|---|
| B1 — F-031 sibling check | **Skip** | Trust the data; revisit if pattern reappears post-PR2. |
| B2 — Final-message format spec | **Skip** | User: "This should just be generated by the coordinator based on what the agent has done. If we need something like this, we should have a tool for it." Applies CC-F + CC-J — coordinator owns the artifact, not the prompt. |
| B3 — 3 new langfuse patterns | **Skip for now** | User: "not sure how much this is needed." Defer to post-PR2; if patterns persist, address then. Each is a small-volume tactical bug, not a structural issue. |
| B4 — Q7.2 + Q4.2 sequencing | **Coordinator changes first, then prompt PR2** | Safe sequencing. PR-runtime-A (get_page_summary parses lead) + PR-runtime-B (References auto-builder) before prompt PR2 ships. |

## Final ship sequence

1. **PR-runtime-A** — `get_page_summary` parses lead paragraph; returns lead-as-tldr.
2. **PR-runtime-B** — `## References` auto-builder from inline footnotes; abort on `## Sources` H2.
3. **PR1 (prompt)** — reorg + dedupe + jargon/capability sweep + boy-scout cleanups (prompt-side only). BEHAVIOR-PRESERVING — smoke-test before merge.
4. **PR2 (prompt)** — content + behavior edits (CC-G hybrid procedure, content_floor, voice section, examples, etc.).
5. **PR3 (NS ratification)** — NORTH-STAR.md edits (Sources rename, slug-form sentence, wikilink density, terminal-decision conventions).
6. **Phase 2 (calendared, post-PR2 smoke)** — `prompts/` directory split, code deletion of `validate_page_draft` + `write_draft_page`, Q6.2 tripwire re-evaluation, B3 patterns if still present.

PR1 starts now.
