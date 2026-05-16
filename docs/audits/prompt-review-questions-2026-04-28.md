---
title: "Compile prompt review — decisions to make, 2026-04-28"
companion: docs/audits/prompt-review-collated-2026-04-28.md
target: src/compile/prompts.py (1057 lines)
reviewers: best-practices · audit-findings · langfuse · north-star · structural · real-world-agents
---

# Prompt review — decisions to make

Walk-through driver. The collation lives at [`prompt-review-collated-2026-04-28.md`](./prompt-review-collated-2026-04-28.md). 6 reviews fed this doc:

1. **Best-practices** (Anthropic/OpenAI/LangChain engineering blogs + ReAct paper)
2. **Audit-findings** (cross-ref against 94 findings)
3. **Langfuse traces** (59 trace bodies, 4 models)
4. **North-Star alignment** (NORTH-STAR.md + V12 audit)
5. **Structural / first-impressions** (orchestrator)
6. **Real-world agent prompts** (Claude Code, Cursor, Aider, Devin, Codex CLI, Replit, Windsurf, v0 — verified line counts and patterns from the Piebald + x1xhlol leaked-prompts repos)

Format per question:
- **Q** — the call to make
- **Options** — A / B / C with reviewer positions
- **Impact** — findings closed, behavior moved, length delta
- **My take** — ⭐ recommended pick
- **Visual** (when useful) — before/after comparison

User flow: I'll fire `AskUserQuestion` for each Q in order. You answer A/B/C (or your own). Skip anything you want; we revisit. After Q-block answered, I produce diffs, you eyeball, we ship.

---

## Reference: prompt-length data (from review #6)

Concrete numbers from production agent prompts (verified `curl` to source files):

| Agent | Prose lines |
|---|---:|
| Codex CLI (older) | 46 |
| Replit | 137 |
| Augment Code (Claude) | 159 |
| Aider EditBlock | 172 |
| Augment Code (GPT-5) | 241 |
| Cursor 2.0 prose | ~260 |
| Claude Code 2.0 prose | ~300 |
| Codex CLI 2025 | 342 |
| Devin | ~400 |
| v0 | ~600 |
| **Our compile prompt** | **~1000** |

**Median: ~250-300.** **Max in production: ~600 (v0).** **Ours is 3× median, 1.5× the largest.**

The only architectural device that scales past ~300 lines without losing reliability is **prompt composition** (Piebald-style file split). Claude Code in production loads ~60 small files; the runtime picks which to include per turn.

---

## Part 0 — Meta questions (length, structure, sequencing)

These shape every section below.

### Q0.1 — Total length target

Real-world data (median 250-300 prose lines; max ~600 at v0; ours is ~1000).

- **A — Aggressive dedupe to ~750 lines** (~30% cut). Cut duplicate bookkeeping rule (3×), duplicate reviewer-invocation (2×), duplicate domain flavor list, the rsplit instruction, most of the Hard rules block.
- **B — Aggressive cut to ~500 lines** (~50% cut). Plus: remove `<expert_questions>` 5W block (move to reviewer prompt or glossary), trim `<concept_vs_thread>` from 49 → 25 lines, trim `<editorial_notes>` from 20 → 5 lines.
- **C — Composable refactor to ~500 lines split across ~10 files** (Piebald pattern). Same line count as B but split into `prompts/` directory; runtime composes. Higher engineering cost but reviewable + per-feature ownership.
- **D — Keep current 1057 lines.** Don't risk weakening load-bearing bits.

**Impact**: A saves cost without behavior risk. B saves more but loses some teaching. C is the only architectural pattern proven to scale past 300 lines (Claude Code does this). D status quo.

**My take**: ⭐ **C** for Phase 2 (composable refactor as a separate larger PR). ⭐ **A** for Phase 1 (this PR — dedupe + reorg without splitting files).

### Q0.2 — Section ordering: mechanics-first or philosophy-first?

Real-world data: Codex CLI, Devin, Cursor 2.0, Claude Code all order **mechanics → output spec → philosophy → examples → rules**. Ours is **background → philosophy (concept_vs_thread, expert_questions, citations, revision_style) → workflow → page_types → tools → rules**. Our first ~500 lines are philosophy; mechanics are buried.

- **A — Reorganize to mechanics-first.** Move `<workflow>` (currently L289) and `<tool_guidance>` (currently L604) above the philosophy blocks. Insert `<content_floor>` at L16.
- **B — Edit in place.** Tighten current sections; keep numbering stable. Less surgical, lower diff risk.

**Impact**: A fixes the biggest structural flaw (tools at L604 used on turn 1). B preserves git history but keeps the buried-tools problem.

**My take**: ⭐ **A**.

**Visual** — proposed order:

```
Current order             →    Proposed order
─────────────────              ─────────────────
1. background                  1. background
2. chronological_scope         2. content_floor (NEW)
3. concept_vs_thread           3. terminal_decision (split from workflow)
4. expert_questions            4. chronological_scope
5. inline_citations            5. tool_guidance (moved from L604)
6. revision_style              6. procedure (split from workflow)
7. workflow                    7. recovering_from_blockers
8. page_types                  8. page_types
9. domain_frontmatter          9. frontmatter (renamed)
10. section_titles             10. section_titles + related_links
11. tool_guidance              11. concept_vs_thread (philosophy)
12. sources_management         12. expert_questions
13. todo_rule                  13. inline_citations
14. self_review                14. revision_style
15. recovering_from_blockers   15. sources_management
16. editorial_notes            16. todo_rule
17. few_shots                  17. self_review
18. hard_rules                 18. editorial_notes
19. frontmatter_template       19. voice (NEW)
                               20. few_shots
                               21. hard_rules (consolidated)
                               22. frontmatter_template
```

### Q0.3 — Split into `prompts/` directory? (Piebald pattern)

Real-world: only proven scalable pattern past ~300 lines.

- **A — Yes, this PR.** Refactor `prompts.py` → `prompts/` directory now, while we're touching the file. ~10 files of 30-100 lines each. `__init__.py` exports `COMPILER_SYSTEM_PROMPT` so callers don't change.
- **B — Yes, but as Phase 2 PR.** Ship dedupe + reorg + content edits first; split into directory in a separate PR after smoke confirms.
- **C — No, keep monolith.** Single file is easier to grep / read in one shot.

**Impact**: A is highest leverage but riskiest (PR has both structural reorg and file split). B is safer (isolate one risk per PR). C foregoes the architectural pattern.

**My take**: ⭐ **B**.

**Visual** — proposed file split:

```
src/compile/prompts/
  __init__.py             # composes + exports COMPILER_SYSTEM_PROMPT
  identity.md             # 5 lines: who you are
  content_floor.md        # 12 lines: 5-item content floor
  terminal_decision.md    # 4-outcome contract + Q-delta + A-delta
  chronological_scope.md  # don't time-travel + symmetric forward-update
  tool_guidance.md        # WHEN/WHEN-NOT per tool
  procedure.md            # numbered Steps + tool ordering rule
  recovery.md             # blocker recovery
  page_types.md           # universal H2 shape + bug-page alt
  frontmatter.md          # 8 domains + owner + template
  section_titles.md       # H2 naming + related_links
  concept_vs_thread.md    # philosophy + GOOD/BAD pair
  expert_questions.md     # 5W frame (or move to reviewer)
  inline_citations.md     # footnote syntax + References
  revision_style.md       # current truth, history, supersession
  sources_management.md   # source_threads:
  voice.md                # NEW: avoid event-log voice
  self_review.md          # pre-return checks
  editorial_notes.md      # filter reviewer feedback
  hard_rules.md           # consolidated NEVER list
  few_shots/
    01_new_topic.md
    02_question_delta.md  # NEW
    03_answer_delta.md    # NEW
    04_update_summary.md  # NEW (closes U3)
    05_chronological.md   # NEW (closes worst-case anti-pattern)
    ...
```

### Q0.4 — PR sequencing

- **A — One big PR** with the structural reorg + top-5 leverage edits + content fixes. Verify on single 30-page smoke. Single revert if regression.
- **B — Two-step PR**: PR1 ships dedupe + reorg only (no behavior changes; LLM-diff verifiable). PR2 ships content/behavior edits.
- **C — Per-finding PRs**: F-046, F-066, F-067, F-068, F-070 each their own PR.

**My take**: ⭐ **B**. Reorg is mechanical, easy to verify nothing changed. Behavior PR2 then has clean signal.

---

## Part 1 — Prompt opening (lines 1-23)

### Q1.1 — Add `<content_floor>` block at line 16?

The audit-findings review proposes a hard 5-item floor:

```
<content_floor>
A page MUST clear this floor before you return:

1. ## TL;DR — ≤3 sentences, at least one number.
2. owner: frontmatter pointing to a single DRI.
3. ## Current state with stage + rollout + last-verified-date markers.
4. Open questions (if any) carry a target date.
5. References footnotes resolve; no orphans.

Structure without these is filing-cabinet polish. The reviewer will pass
a structurally-clean page that fails the floor; the floor is YOUR job.
</content_floor>
```

- **🅰 Add it as proposed.** Closes F-065 META by chaining F-046 + F-066 + F-067 + F-068 enforcement at the top. Primacy effect: rules read first stick.
- **🅱 Add a *softer* version** — bullets without "MUST"; let middleware enforce. Best-practices review warns MUST-language overuse damages reasoning.
- **🅲 Skip; teach in each section's home.** TL;DR rule lives in `<page_types>`, owner rule lives in `<frontmatter>`, etc. Don't centralize.

**Impact**: 🅰 closes 5 findings. 🅱 closes them but slightly weaker. 🅲 keeps current behavior (silent failures).

**My take**: ⭐ 🅰. The 5-item floor IS the V12 depth gap. The MUST is OK here because each item has clear failure criteria — agent can verify. Anthropic's MUST guidance is about over-use across many soft rules; one well-defined floor is fine.

### Q1.2 — Expand `<chronological_scope>` (L17-23) with the symmetric forward-update rule?

Currently: "Don't rewrite history from the future."

Proposed addition (closes U7 / F-016 / U3 — the 100% non-recovery on summary-stale-date):

> *"Conversely: if today's email IS the newest evidence on `source_threads:`, you MUST update the Summary's current-state sentence. Leaving the Summary stale is the failure mode the `summary-stale-date` blocker fires for."*

- **🅰 Add the symmetric rule here.** Conflict with `<revision_style>` resolved in same place agents read first.
- **🅱 Add it to `<revision_style>` instead** (line 199). Keeps `<chronological_scope>` short.
- **🅲 Add to both** — primacy + topical home.

**Impact**: closes U3 (langfuse: 11/11 traces never recovered from `summary-stale-date`). 🅰 has highest primacy. 🅲 most thorough but adds duplication best-practices flagged.

**My take**: ⭐ 🅰. The conflict between "leave it alone" (L19) and "rewrite Summary" (L199) is what creates U3. Resolve in the section the agent reads first.

---

## Part 2 — `<concept_vs_thread>` (lines 25-74)

All 5 reviewers say PRESERVE. Open questions are about additions only.

### Q2.1 — Add a second BAD example for event-log voice (closes F-045)?

Currently the BAD section shows thread-narrative Summaries. F-045 ("Vote of thanks to X", "As of 2026-01-07: [[X]] approved") is body voice, not Summary voice — different failure mode.

Proposed addition at L73:

> *"Equally bad — event-log voice in the body:*
> > "As of 2026-01-07: [[rehan-atiqulla-indiamart-com]] approved.
> > [[amit-indiamart-com]] acknowledged with approval (👍).
> > Vote of thanks to the QA team."
>
> *Synthesise instead: 'The phase-1 cutover was approved on 2026-01-07 by Rehan Atiqulla; the QA pass found 4 regressions (3 fixed same day) [^msg-xxx].' Names + dates belong INSIDE prose, never as standalone log lines."*

- **🅰 Add here** in `<concept_vs_thread>`.
- **🅱 Add to a new `<voice>` section** (closes F-045 in its own home).

**Impact**: closes F-045 either way. 🅱 cleaner separation; 🅰 reinforces concept-vs-thread frame.

**My take**: ⭐ 🅱 — `<voice>` deserves its own section between `<editorial_notes>` and `<few_shots>`. Concept-vs-thread covers structure; voice covers prose. Different concern.

### Q2.2 — Trim `<concept_vs_thread>` from 49 → ~25 lines?

Best-practices review: "the BAD/GOOD example pair carries 90% of the load. Drop the explanatory prose at L26-50; lead with the example."

- **🅰 Trim** — keep the lede (L26 "page is a CONCEPT, emails are EVIDENCE") + the BAD/GOOD pair. Drop the 6-step "When you're compiling a batch" enumeration (L41-49), since `<workflow>` covers compile steps.
- **🅱 Keep current length.**

**Impact**: 🅰 saves ~25 lines; tighter prompt. 🅱 keeps current (some redundancy with `<workflow>`).

**My take**: ⭐ 🅰. Steps 1-6 at L41-49 duplicate `<workflow>` steps. The lede + GOOD/BAD pair is what teaches.

---

## Part 3 — `<expert_questions>` (lines 76-133)

### Q3.1 — Promote `## Why it matters` to load-bearing for hidden-curriculum?

North-Star review (Edit #5): the 5W block teaches the questions, but no example shows a senior IndiaMart employee *answering* them with operational intuition.

Proposed addition before `<self_review>`:

> *"The `## Why it matters` section is the hidden-curriculum lever. Two sentences minimum. Anchor on the operational constraint that explains the work — the customer pain, the SBU boundary, the rate limit, the historical incident — not just the metric. A new joiner reading only your Summary + Why-it-matters should know enough to ask a smart follow-up."*

- **🅰 Add it.** Closes the "structure adopted, content floor flat" finding from V12 (F-065).
- **🅱 Skip.** Add when there's a worked example to anchor the rule.

**Impact**: 🅰 raises the depth bar substantively. 🅱 keeps current depth.

**My take**: ⭐ 🅰. Without this, every other depth fix is structural polish.

### Q3.2 — Cut "Flavor varies by domain" sub-list (L106-122)?

Best-practices review: 17 lines of per-domain question flavors that duplicate `<domain_frontmatter>` (L532-571). Pick one home.

- **🅰 Cut here**, keep in `<domain_frontmatter>`.
- **🅱 Cut from `<domain_frontmatter>`**, keep here.
- **🅲 Cut both, accept some loss.**

**My take**: ⭐ 🅰. 5W list belongs here; domain flavors belong in domain frontmatter.

### Q3.3 — Wikilink slug form (the X1 conflict)

The big one. Current line 97-98: *"Link people by canonical slug (`[[amit-agarwal]]`, not `[[aa-indiamart-com]]`)."*

But langfuse shows 361 instances of `[[<x>-indiamart-com]]` form across recent traces. **Why? Because `create_entities` returns email-canonical slugs as canonical.** The tool contract contradicts the prompt teaching. 

- **🅰 Migrate the entity store** to produce display-name slugs (`amit-agarwal`). Audit + north-star reviewers prefer this. Cleaner end-state. **Engineering cost: ~1-day migration script (similar to PR #243's bulk-slug rename) + invalidation of 361 existing wikilinks across pages.**
- **🅱 Accept email-canonical slugs.** Update prompt example to match what `create_entities` actually returns. Lower engineering cost. Add a one-liner: "the slug is opaque; readability comes from the page title, not the slug." Langfuse review prefers this.
- **🅲 Alias / redirect layer.** Wire `resolve_page` to accept both forms; render canonical-name slugs from email-canonical store. Keeps existing wikilinks working without rewrite.

**Impact**:
- 🅰 closes F-070 (304+ instances → 0); cleanest readable wikilinks; ~1 week of infra work
- 🅱 closes F-070 differently (re-defines what canonical means); ships immediately; loses readability
- 🅲 expensive; postpones the decision

**My take**: ⭐ 🅰 if you have engineering budget; ⭐ 🅱 if you want to ship the prompt PR this week. 🅲 isn't worth the complexity.

### Q3.4 — Extend WHO bullet (L95-98) to mandate `owner:` frontmatter (closes F-066)?

- **🅰 Add**: *"Put the single accountable owner (DRI) in `owner:` frontmatter as a `[[<dri-slug>]]` wikilink. Link other people inline."*
- **🅱 Skip** — leave the rule in `<frontmatter>` template only.

**Impact**: 🅰 closes F-066 (single biggest persona-audit finding). 🅱 leaves teaching scattered.

**My take**: ⭐ 🅰. Persona P2 explicitly hits F-066 ("PM wanting 'who do I ping?' has to read multiple sub-sections"). Mandatory frontmatter is the fix.

---

## Part 4 — `<inline_citations>` (lines 135-197)

### Q4.1 — Cut the rsplit instruction (L141-146)?

Currently teaches the agent to compute footnote target via `raw_path.stem.rsplit("_", 1)[-1]`. Best-practices review: Karpathy-grade misuse — LLM doing deterministic string ops.

- **🅰 Cut + have `get_thread_context` return the cite key.** Coordinator-side change required (one-liner in `src/compile/tools/raw_access.py` plus tool docstring update).
- **🅱 Keep** — the explicit syntax helps; coordinator change is over-engineering.

**Impact**: 🅰 saves 6 lines + reduces a token-cost-per-call. 🅱 keeps current (works but wasteful).

**My take**: ⭐ 🅰 if we're touching the tools layer anyway. ⭐ 🅱 if scope creep matters.

### Q4.2 — Compress the `## Sources` warning (L167-172)?

Currently 5 lines explaining the MkDocs hook. Best-practices: leak of build-system internals.

- **🅰 Compress to 1 line**: *"Use `## References`. Never `## Sources` (disabled by the viewer)."* The agent doesn't need to know WHY.
- **🅱 Keep verbatim** — the WHY anchors the rule. Audit-findings explicitly says NEVER CHANGE.

**My take**: ⭐ 🅱. Conflict with audit-findings reviewer; their reason ("real consequence anchor") is strong. Even if it's a minor leak, the agent stickies the rule because it knows the consequence.

---

## Part 5 — `<revision_style>` (lines 199-287)

All 5 reviewers say STRONG SECTION. Questions are about additions.

### Q5.1 — Add a worked UPDATE example (closes U3 — 100% summary-stale-date non-recovery)?

Langfuse review §D4 proposes Example 11 in `<few_shots>`:

```
### Example 11 — Update existing page: rewrite the Summary's stale claim

Context: Page topics/whatsapp-9696-rollout has Summary "live on 12% coverage".
Email reports "now at 25%, p95 at 1.8s".

resolve_page("whatsapp-9696-rollout") → exists: true
read_file("/wiki/topics/whatsapp-9696-rollout.md")
edit_file("/wiki/topics/whatsapp-9696-rollout.md",
  "Live on 12% of verified buyer segments; p95 at 2.1s.",
  "Live on 25% of verified buyer segments since 2026-04-22; p95 at 1.8s.",
)
patch_page("whatsapp-9696-rollout", "Recent changes",
           "- **2026-04-22** — Coverage 12%→25%; p95 2.1s→1.8s. [^msg-xxx]\n")
check_my_work(raw_email_path="raw/2026-04-22_..._xxx.md")
# If summary-stale-date blocker fires, re-read and edit_file again.
```

- **🅰 Add Example 11** as proposed (in `<few_shots>` block).
- **🅱 Promote into `<workflow>` step 5** prose — *"For UPDATE flows: re-read Summary first; if any fact is stale, rewrite the sentence."*
- **🅲 Both.** Audit-findings recommends.

**Impact**: closes U3 (single most impactful behavioral bug per langfuse). 🅰 teaches by example; 🅱 teaches by rule; 🅲 belt-and-braces.

**My take**: ⭐ 🅲. U3 is the worst-recovery failure in the trace data. Worth both surfaces.

---

## Part 6 — `<workflow>` (lines 289-464) — THE BIG ONE

This is where most disagreement lives. 175 lines doing two things.

### Q6.1 — Split into `<terminal_decision>` + `<procedure>`?

Current: one big `<workflow>` mixing the 4-outcome contract (L294-413) with the numbered Steps (L416-463).

- **🅰 Split into two sections** (best-practices Issue #1, structural Issue #8). Move terminal-decision near the top; procedure stays near tools.
- **🅱 Keep merged** but add subsection headers and tighten prose.

**Impact**: 🅰 lifts contract visibility (closes part of U1 + U7). 🅱 less invasive.

**My take**: ⭐ 🅰. The contract being buried 290 lines deep is the prompt's biggest structural flaw.

### Q6.2 — Reframe terminal-outcome from email-shaped to concept-shaped (north-star Edit #4)?

Currently: *"Every email ends with EXACTLY ONE of these four..."*

Proposed: *"Every email is evidence for AT LEAST ONE concept page (or a logged skip). For each batch, identify the set of concepts the emails are evidence for, then for each concept commit to one of: edit/create the page; merge into a sibling (`already_captured`); skip as non-substantive (`trivial_skip`); flag for triage (`insufficient_decision`)."*

- **🅰 Reframe** — concept-shaped matches NORTH-STAR.md.
- **🅱 Keep email-shaped** — terminal_decision middleware enforces per-email; reframe risks confusion with what the runtime actually does.

**Impact**: 🅰 better aligned with NORTH-STAR but creates rule-vs-runtime confusion. 🅱 keeps current alignment with middleware.

**My take**: ⭐ 🅱. The middleware enforces per-email. Aligning the prompt with the runtime is more important than aligning with NORTH-STAR philosophy when they conflict. Note this for a future "should middleware change?" decision.

### Q6.3 — Tool-ordering rule callout at top of `<procedure>` (closes U1: cmw pre-write 64%)?

Langfuse review §D1 proposes:

> *"Tool ordering rule (read this first). `check_my_work` and `task(reviewer)` are POST-WRITE validators. Never call either before you have called `write_file` / `edit_file` / `patch_page` in the current batch. If the email leads to a `log_insight` (no-write outcome), skip both validators entirely."*

- **🅰 Add as proposed.**
- **🅱 Skip** — middleware (`check_my_work_gate`) already rejects premature calls; redundant.

**Impact**: 🅰 closes U1 (the single biggest behavior bug in 59 traces). 🅱 relies on middleware, which apparently isn't enough — the agent retries the same shape.

**My take**: ⭐ 🅰. 64% miss rate says the prompt teaching is needed. Middleware is the floor; prompt teaching is the ceiling.

### Q6.4 — Tighten step 7 reviewer rule (closes F-043)?

Current L446-452: *"For any page where you wrote ≥4 lines of new prose (or a new page), call `task(...)`. Skip ONLY for trivial edits. Default to calling it."*

Conflict in the same paragraph: "Default to" + "ONLY for trivial."

- **🅰 Replace with**: *"For any new page (`write_file`) or any page edit producing ≥4 lines of new prose, you MUST call `task(subagent_type='reviewer', ...)` AFTER the write tool returns successfully. Skip ONLY for one-line frontmatter / typo fixes."*
- **🅱 Soften MUST** per best-practices: *"Call `task(reviewer)` after every new page or substantive edit."*

**Impact**: 🅰 closes F-043 (V12 audit Tier 1 #1 — newly-created V12-shape pages getting 0 reviewer cycles). 🅱 may not be strong enough; adoption already low.

**My take**: ⭐ 🅰. F-043 is forward-looking critical. The MUST is OK because it has a clear threshold.

### Q6.5 — Tighten step 2 sibling-near-dup detection (F-031)?

Current L421: *"`resolve_page(<concept>)` for existing pages; `get_page_summary(slug)` is usually enough to decide merge vs. new."*

Proposed: *"`resolve_page(<concept>)` AND a second `resolve_page` on a sibling phrasing (e.g., `m-site-pdp`, `mpdp`, `pdp-mobile`) before any `write_file` of a new topic. If either returns a candidate, edit the existing page; do NOT create a sibling."*

- **🅰 Add the sibling-phrasing check.**
- **🅱 Skip** — overly procedural; sibling-draft-check middleware exists.

**Impact**: 🅰 closes F-031; risks adding a slow step. 🅱 relies on middleware (V12 audit confirms it's too loose).

**My take**: ⭐ 🅰. Cheap step (one extra resolve call ~ 200ms). Middleware was supposed to handle it; V12 confirmed it doesn't.

### Q6.6 — Cut the bookkeeping restatement at L461-463 (it's at L654 + L1027)?

- **🅰 Cut here** — keep at the canonical home in `<background>` only.
- **🅱 Keep** — repeating in workflow context catches the agent at the moment it might try.

**Impact**: 🅰 saves 3 lines + reduces contradiction-shape risk. 🅱 keeps the reinforcement.

**My take**: ⭐ 🅰. Best-practices review is right: restating reduces effective reasoning. Move it to a single canonical home in `<background>`.

---

## Part 7 — `<page_types>` (lines 466-530)

### Q7.1 — Collapse per-type H2 templates into one universal shape (north-star Edit #1)?

Current: 3 different H2 sequences for topic / system / policy (L504-512).

Proposed: ONE shape — `## TL;DR` → `## Why it matters` → `## Current state` (or `## Current policy` for policy) → `## Recent changes` → `## Open questions` → `## Related` → `## References`. Type-specific sections become *optional flavor*.

- **🅰 Collapse to universal shape.** Closes Axis 5 GAP + Axis 4 residue (`## Key decisions`).
- **🅱 Keep per-type templates.** Different page types ARE different things; one shape forces a fit.

**Impact**: 🅰 aligns with NORTH-STAR "all pages just pages"; reduces hierarchy bias. Risk: bug pages (F-058) and policy pages might lose useful structure.
🅱 preserves type-specific structure but contradicts NORTH-STAR.

**My take**: ⭐ 🅰 with careful preservation of bug-page alternative shape (Q7.3). The "type-specific is optional flavor" framing handles policy's `## Effective date / ## Supersedes` cleanly.

### Q7.2 — Flip TL;DR from "Optional" → "Required" (closes F-046, the X2 conflict)?

Current L528: *"Optional: a `## TL;DR` H2..."*

- **🅰 Flip to Required** for topic and system pages. Audit-findings position. NORTH-STAR.md already says TL;DR.
- **🅱 Drop the redundancy** — the lead paragraph IS the TL;DR. Update NORTH-STAR.md to drop the explicit `## TL;DR` heading.
- **🅲 Both** — lead paragraph defines what it IS; TL;DR states current state and ownership. (audit-findings F-046 framing — they're complementary)

**Impact**: 🅰 closes F-046 simply. 🅱 simpler page shape but contradicts existing tooling (`get_page_summary` returns `tldr`). 🅲 most teaching but adds a constraint.

**My take**: ⭐ 🅲. Per audit-findings F-046: "The lead paragraph defines what the thing IS; the TL;DR states the current state and ownership." Two-sentence overlap, but they serve different functions. Without TL;DR, `get_page_summary` returns blank.

### Q7.3 — Add explicit bug-page alternative shape (closes F-058)?

Proposed (audit-findings F-058):

> *"**Bug/incident pages are an explicit alternative shape.** When the email is a bug report or post-mortem, use: `## Bug details` → `## Impact` → `## Fix` → `## Verification` → `## Related`. Do NOT pad with `## Why it matters` — bugs have a different shape."*

- **🅰 Add.**
- **🅱 Skip** — let the "template not a law" escape hatch handle it.

**My take**: ⭐ 🅰. F-058 is real (5 bug pages got design-doc filler). The alternative shape is a small addition.

### Q7.4 — `## Current state` from slot to contract (closes F-067)?

Current: `## Current state` listed as a section name. Never defined.

Proposed: *"`## Current state` is REQUIRED on every topic. Use markers: `Stage: poc | alpha | beta | ga | sunset` · `Rollout: <N>% of <segment>` · `Last verified: <date>`. If evidence doesn't say, write `Stage: unknown — last evidence <date>`."*

- **🅰 Add as proposed.**
- **🅱 Soften** — make it required only for "launch-class" topics; allow other shapes.

**Impact**: 🅰 closes F-067 (judge-flagged on 12/16 V12 pages). 🅱 less prescriptive.

**My take**: ⭐ 🅰. The structured markers are what makes the section useful for downstream `get_page_summary`.

---

## Part 8 — `<frontmatter>` template (lines 1040-1056)

### Q8.1 — Add `owner:`, `stage:`, `target_date:` to template (closes F-066, F-067, F-068)?

Proposed:

```yaml
---
title: "Human Readable Title"
page_type: topic | system | policy | person | decision
status: active | superseded | archived
owner: "[[<dri-slug>]]"             # required for topic/system: single DRI
stage: poc | alpha | beta | ga | sunset   # topic only; required when applicable
target_date: 2026-06-30             # next rollout milestone; omit if none
source_threads:
  - 19b59cdc863ac109
related:
  - "[[other-slug]]"
---
```

- **🅰 Add all three** (owner, stage, target_date).
- **🅱 Add owner only.** Stage and target_date can stay in `## Current state` body markers (Q7.4).
- **🅲 Add nothing** — keep template minimal.

**Impact**: 🅰 closes 3 findings. 🅱 closes 1 (F-066). 🅲 closes 0.

**My take**: ⭐ 🅱. Owner is universally relevant. Stage / target_date are structured data that already lives in Q7.4's `## Current state` markers. Don't double-record.

### Q8.2 — Add `## Decision Source` (X1 conflict) — pick wikilink form?

This is the X1 decision from Q3.3. Whichever you picked there, the frontmatter template's `related:` examples should match.

(Decided in Q3.3.)

---

## Part 9 — `<related_links>` (NEW — closes F-069 + persona finding)

### Q9.1 — Add a new `<related_links>` section?

Proposed insert after L571:

> ```
> <related_links>
> Related neighbours render in TWO disjoint places:
> - Frontmatter `related:` — short list (≤8) of CONCEPT pages this page
>   directly references in body prose. Drives graph navigation; rendered
>   into the page's Related footer by the build, not by you.
> - Body `## Related` H2 — write ONLY when the related list deserves
>   prose framing (e.g. "These three topics together describe the
>   photosearch funnel"). For most pages, leave the body H2 OFF and
>   let the build render the frontmatter list.
>
> Never duplicate: if you write a body `## Related`, don't ALSO list
> the same slugs in frontmatter `related:`. Pick one.
>
> People belong in frontmatter `owner:` and inline body wikilinks —
> NEVER in `## Related`.
> </related_links>
> ```

- **🅰 Add as proposed.**
- **🅱 Add a shorter version** — just "Don't duplicate body Related and frontmatter related:".
- **🅲 Skip** — `<section_titles>` already covers H2s.

**Impact**: 🅰 closes F-069 (11/16 V12 pages had both) + persona P4 finding (Related-was-people-only). 🅲 leaves both open.

**My take**: ⭐ 🅰. Persona audit explicitly hit this. Worth a 12-line section.

---

## Part 10 — `<tool_guidance>` (lines 604-657) — promote up

### Q10.1 — Move section to position 3 (after `<background>` + `<terminal_decision>`)?

Best-practices Issue #2: tool list buried at L604; agent uses tools on turn 1.

- **🅰 Move up.**
- **🅱 Keep at L604.**

**Impact**: 🅰 raises tool-teaching primacy. 🅱 keeps current.

**My take**: ⭐ 🅰. Same argument as `<workflow>`.

### Q10.2 — Add `resolve_page` idempotency teaching (closes U2 — 70% flail)?

Langfuse §D2 proposed addition:

> *"`resolve_page` is idempotent: calling it twice with the same query returns the same answer. If `exists: false`, the page does not exist. To find a page: try the bare slug; scan the `candidates: [...]` array; fall back to `list_wiki_pages` ONCE. Never prefix the slug with `system/`, `topic/` — those are wikilink targets, not resolver inputs."*

- **🅰 Add.**
- **🅱 Skip** — verbose; the agent should figure out idempotency.

**Impact**: 🅰 closes U2 (worst case: 28 calls in one trace; cumulative 407 calls in 59 traces). 🅱 keeps wasteful behavior.

**My take**: ⭐ 🅰. U2 is concrete and frequent. The cost-saving alone justifies it.

### Q10.3 — Acknowledge inherited tools (`glob` / `ls` / `grep`) — 129 calls in 59 traces (U6)?

- **🅰 Authorize with guidance**: *"The runtime exposes `ls`, `glob`, `grep` from DeepAgents. Prefer wiki-specific tools above; use `glob`/`ls` only when you need to confirm a path exists."*
- **🅱 Forbid**: *"Do not use `ls`, `glob`, `grep` — use `resolve_page`, `list_wiki_pages`, `read_file` instead."*
- **🅲 Skip** — leave silent.

**Impact**: 🅰/🅱 both close U6. 🅰 is realistic (the tools are useful for debugging). 🅱 is cleaner but may surprise the agent. 🅲 keeps the gap.

**My take**: ⭐ 🅰. Practical; tools are inherited and have legitimate uses. Just steer toward the wiki-specific layer.

### Q10.4 — Decide on dead tools `write_draft_page` and `wiki_merge_pages` (U4)?

0 calls in 59 traces.

- **🅰 Add triggering examples** in `<few_shots>` for each. Estimated added length: ~30 lines.
- **🅱 Delete** from `<tool_guidance>` and prompt mentions. Save tokens.
- **🅲 Keep, no action.**

**Impact**: 🅰 might activate them (good if they're meant to live). 🅱 cuts dead weight (saves ~10 lines but loses the option). 🅲 status quo.

**My take**: ⭐ 🅱 for `wiki_merge_pages` (the audit pipeline's `apply_merge_candidate.py` covers the case). ⭐ 🅰 for `write_draft_page` (legit need: borderline pages should land in `_drafts/` not become content). Different tools, different verdicts.

### Q10.5 — Add WHEN/WHEN-NOT to each tool (closes C8 / best-practices #6)?

Currently 3 of 13 tools have triggering language. Proposed: add one line each.

- **🅰 Add to all.** Estimated +10 lines.
- **🅱 Add only to high-cost tools** (`write_file`, `edit_file`, `task`).

**My take**: ⭐ 🅰. WHEN/WHEN-NOT is the highest-leverage prompt-engineering pattern per Anthropic + Claude Code's TodoWrite reverse-engineered prompt.

---

## Part 11 — `<self_review>` (lines 681-700)

### Q11.1 — Cut the duplicate reviewer-invocation teaching (L691-696)?

Same rule lives in `<workflow>` step 7. Drift across the two.

- **🅰 Cut here.**
- **🅱 Keep both.**

**My take**: ⭐ 🅰. One canonical home (workflow); self-review is for OTHER checks (TL;DR present, owner set, no event-log voice, etc.).

### Q11.2 — Add TL;DR / owner / voice checks to self-review?

Proposed additions:
- *"5. Does the page have a `## TL;DR` with at least one number? Required for topic/system."*
- *"6. Is `owner:` set in frontmatter? If unclear, leave blank and add an Open question — don't guess."*
- *"7. Any "As of <date>: <name> approved" or "Vote of thanks" event-log voice? Rewrite as concept-level prose with footnotes."*

- **🅰 All three.**
- **🅱 TL;DR + owner only.** Voice check sits in `<voice>` section.

**My take**: ⭐ 🅱. Self-review should be a checklist. Voice gets its own section anyway.

---

## Part 12 — `<few_shots>` (lines 762-1018)

The 295-line examples block. 28% of total prompt.

### Q12.1 — Replace Example 7 (closes F-070 + north-star Edit #3)?

Currently shows `create_entities` for everyone in from/to. North-star says: decision-makers + experiment-owners + approvers only.

- **🅰 Replace with selective example** showing the rollout owner + approver, with 4 other contributors mentioned without wikilinks.
- **🅱 Keep current** — Example 7 demonstrates the create_entities call mechanics.

**My take**: ⭐ 🅰. Current Example 7 actively teaches the F-070 failure mode. North-star Edit #3 has the proposed text.

### Q12.2 — Replace Example 8 (largely redundant with Example 7) with a Question-delta `patch_page` example?

Best-practices Issue #3: no canonical worked example for Question-delta despite 70 lines of prose teaching it.

- **🅰 Replace.**
- **🅱 Keep** — Example 8 has different shape from Example 7.

**My take**: ⭐ 🅰. The Question-delta exception is one of the prompt's strongest features and currently has zero worked example.

### Q12.3 — Add Example 11 (UPDATE flow Summary rewrite, closes U3)?

Q5.1 already covered. Skip if Q5.1 = 🅰 or 🅲.

### Q12.4 — Add Example 12 (chronological-scope DON'T-rewrite-future)?

Best-practices Issue #3: the highest-stakes anti-pattern in the prompt has zero worked example.

- **🅰 Add.**
- **🅱 Skip** — the rule is short, no example needed.

**My take**: ⭐ 🅰. Concrete anti-example beats prose.

### Q12.5 — Trim other examples for length?

Currently 10 examples. After Q12.1 + Q12.2 + Q12.3 + Q12.4: would be 12 (replacing 2 + adding 2). Examples 4 (draft) and 5-8 (skip variants) could be condensed.

- **🅰 Condense skip examples** (5, 6, 7) into one shorter combined example. Cuts ~30 lines.
- **🅱 Keep all.**

**My take**: ⭐ 🅰 (modest cut). Skip-shape examples can share infrastructure.

---

## Part 13 — `## Hard rules` (lines 1020-1038)

### Q13.1 — Cut duplications + convert to positive form (best-practices #8)?

Each NEVER duplicates earlier prose:
- L1024 invent slugs ↔ L429-430, L640
- L1027 last_compiled ↔ L461, L654
- L1029 sources: ↔ L660-667
- L1032 broken wikilinks ↔ L703-727, L686-687

- **🅰 Cut to 4-6 unique items, positive form.**
- **🅱 Keep all** — last-line emphasis catches the agent at exit.

**My take**: ⭐ 🅰. Replace with: ① Always go through `create_entities` for people. ② Replace stale claims via `edit_file`; archive in `<details>`. ③ Never write `## Decision: <X>` as inline H2 — use Recent changes bullet. ④ Never use `[[<x>-indiamart-com]]` form (or whichever X1 form is rejected).

### Q13.2 — Add new positive rules from findings?

- Strikethrough rule (closes D9 — 4/59 traces use `~~`).
- F-063 inline `## Decision:` ban.
- F-070 wikilink form (depends on Q3.3).

- **🅰 Add all three.**
- **🅱 Add strikethrough + Decision-H2; defer F-070 to Q3.3 outcome.**

**My take**: ⭐ 🅱. F-070 form depends on the X1 decision.

---

## Part 14 — `<voice>` (NEW section, closes F-045)

Already covered in Q2.1 / Q11.2. Confirmation only:

### Q14.1 — Add `<voice>` section between `<editorial_notes>` and `<few_shots>`?

Proposed:

> *"Pages are reference docs, not narratives. Avoid: 'We did X' / 'The team launched Y' (say what the thing IS). 'Vote of thanks to <name>' (celebratory belongs in chat). 'As of <date>: <name> approved <thing>' (put decision in `## Recent changes` with footnote). 'In this thread we discussed X' (page is about X, not the thread). Test: would this sentence still make sense if rewritten 6 months from now? If not, rewrite."*

- **🅰 Add.**
- **🅱 Keep voice teaching in `<concept_vs_thread>` only.**

**My take**: ⭐ 🅰. Voice is a separate concern. Section gets its own home.

---

## Part 16 — Real-world agent prompt patterns (review #6)

Concrete patterns from production agent prompts (Claude Code, Cursor, Aider, Devin, Codex CLI, Replit, v0). Each is a known technique used by a shipping high-quality agent.

### Q16.1 — Add a "preamble between tool calls" rule? (Codex CLI pattern)

Codex CLI: *"Before making tool calls, send a brief preamble to the user explaining what you're about to do. Keep it concise: be no more than 1-2 sentences, focused on immediate, tangible next steps. (8-12 words for quick updates)."*

Currently the agent goes silent for stretches of dozens of tool calls per email. A 1-line preamble per logical group would make Langfuse traces and `wiki/log.md` an order of magnitude more readable, at near-zero quality cost.

- **A — Add as proposed.** ~5 lines added to `<workflow>`.
- **B — Skip** — agent narrative isn't a real-time UX problem for our compile (batch run, no human watching).

**My take**: ⭐ **A**. Even without a watching human, traces become diff-able and patterns easier to spot in audits.

### Q16.2 — Add reversibility / blast-radius framing? (Piebald pattern, 16 lines)

Piebald `system-prompt-executing-actions-with-care.md`: *"Carefully consider the reversibility and blast radius of actions. Generally you can freely take local, reversible actions like editing files or running tests. But for actions that are hard to reverse, affect shared systems beyond your local environment, or could otherwise be risky or destructive, check with the user before proceeding."*

Currently we have hard rules ("NEVER modify /raw/") but no framework for novel risks. When something unexpected happens (e.g. wiki rename propagated through catalog but not page), the agent has no mental model.

- **A — Add adapted to wiki context** as a `<reversibility>` block: "Wiki edits are reversible (mtime + git). Frontmatter mutation, slug changes, and `_drafts/` promotions are higher-blast: prefer `wiki_merge_pages` over manual rewrite when consolidating; prefer `write_draft_page` when uncertain."
- **B — Skip.** Our risks are well-enumerated; framework over-builds.

**My take**: ⭐ **A**. Cheap (16 lines), useful for novel-edge-case reasoning.

### Q16.3 — Add "ambition vs precision" paragraph? (Codex CLI pattern)

Codex CLI: *"For tasks that have no prior context (i.e. the user is starting something brand new), you should feel free to be ambitious and demonstrate creativity. If you're operating in an existing codebase, you should make sure you do exactly what the user asks with surgical precision."*

Adapted: for a concept the wiki already has 5+ pages on, defer to existing structure (extend in place, mention in `Related`). For a fresh concept (no resolve_page hit, no close candidates), be willing to introduce a system page + topic page in the same batch — under-extracting on a green-field concept leaves the next agent to rediscover what you already saw.

- **A — Add 6-line block** in `<workflow>`.
- **B — Skip** — current "be conservative everywhere" is correct posture for a knowledge base.

**My take**: ⭐ **A**. The Cycle-8 `turn_ended_mid_thought` pattern in MEMORY.md is exactly the failure mode "ambition for green-field" prevents.

### Q16.4 — Add Devin-style mandatory `<think>` transitions? (Devin pattern)

Devin: *"You should ask yourself whether you have actually gathered all the necessary context … (3) Before reporting completion to the user. You must critically examine your work so far and ensure that you completely fulfilled the user's request and intent."*

Currently `<self_review>` is advisory ("Before marking a page done:"). Devin's pattern is enforceable ("You MUST critically examine your work").

- **A — Promote `<self_review>` to a "scratchpad before return" block** with a numbered checklist the agent must answer before the return statement.
- **B — Keep advisory.** Anthropic warns about MUST-language overuse.

**My take**: ⭐ **A** with clear, verifiable checks (TL;DR present? owner set? terminal-decision committed for every email?). The MUST is OK because checks are concrete.

### Q16.5 — Triple-redundancy on `source_threads:` rule? (Aider/v0 pattern)

Aider's edit format (>99% well-formed) repeats "ONLY EVER RETURN CODE IN A SEARCH/REPLACE BLOCK!" twice. v0's LaTeX rule is repeated 3 times in one block.

Our `source_threads:` (not `sources:`) rule appears once in `<sources_management>`, once in Hard rules. Compliance is ~85%.

- **A — Add 3rd repetition** at the bottom of the frontmatter template: "If you wrote `sources:` in frontmatter, you broke the format — only `source_threads:`."
- **B — Skip** — best-practices review explicitly warns against duplication.

**My take**: ⭐ **A** with the caveat that this is the ONE rule that gets triple-redundancy. Format-level invariants (Aider/v0 case) are the right scope for repetition; soft guidance is not.

### Q16.6 — Move `<expert_questions>` (5W block) out of compiler prompt? (real-world review §8)

Review #6 §8: "no reference prompt has anything analogous to `<expert_questions>`. The 5W frame is genuine product knowledge — but it should live in `wiki/glossary.md` as a *resource the LLM can look up*, not in the system prompt. The agent that needs the 5W frame is the *reviewer*, not the compiler."

- **A — Move to reviewer prompt** (`src/compile/reviewer.py`); cite from compiler in 1 line.
- **B — Move to `wiki/glossary.md`** as a queryable resource.
- **C — Trim only** — keep in compiler but cut the per-domain flavor table (already covered by Q3.2).
- **D — Keep as-is.**

**Impact**: A/B saves ~58 lines from compiler prompt. A puts it where it'll fire (reviewer judges depth). B makes it accessible by both compiler and reviewer. C minor cut. D status quo.

**My take**: ⭐ **A**. The reviewer is the one judging "is this depth-y?". Compiler's job is to write; reviewer's job is to check. Separates concerns cleanly.

**Caveat**: this conflicts with audit-findings reviewer (who promoted `## Why it matters` in Q3.1). The 5W frame can leave; the `## Why it matters` requirement stays in `<page_types>`.

### Q16.7 — Add a final-message format spec? (Codex CLI pattern)

Codex CLI has a "Final answer structure and style guidelines" section telling the agent how to format its return message. Currently our agent returns prose of arbitrary shape; the coordinator + critic then have to parse it.

- **A — Add a 10-line "final-message format" spec.** Greppable, reduces return-message tokens, makes critic verdicts diff-able.
- **B — Skip** — return message is consumed by code, not humans; structure isn't critical.

**My take**: ⭐ **A** if we're touching the prompt anyway. ⭐ **B** if scope creep matters (this is a polish item).

### Q16.8 — Cite `wiki/glossary.md` from compiler instead of duplicating?

If Q16.6 = A/B, the compiler prompt should reference the resource. One-line: *"For unfamiliar IndiaMART terms (MCAT, ISQ, BL, BMC, KYC), `read_file('/wiki/glossary.md')` — don't guess."*

- **A — Add the cite line.**
- **B — Skip** — agent should figure it out.

**My take**: ⭐ **A**. Glossary is currently dead (intentionally removed 2026-04-24); without the cite agents won't know to look.

---

## Part 15 — Things to ratify in NORTH-STAR.md (CC5)

Not prompt edits. Surfaced for completeness.

### Q15.1 — Update NORTH-STAR.md per the prompt's emergent conventions?

- `## Sources` → `## References` (NORTH-STAR.md:95-99)
- TL;DR vs lead paragraph (depends on Q7.2 / X2)
- Wikilink density target (≥3? ≥4? prompt is silent)
- Recent-changes retention (NORTH-STAR.md:90 says 3-5; prompt silent)
- 4 terminal-outcome categories — ratify
- Question/Answer-delta exception — ratify
- "≥4 lines triggers reviewer" threshold — ratify

- **🅰 One bundled NORTH-STAR PR after the prompt PR ships.** Then NORTH-STAR catches up.
- **🅱 Skip** — accept doc-prompt drift.

**My take**: ⭐ 🅰. After the prompt PR. NORTH-STAR is the ratification surface; let the prompt prove conventions first, then ratify.

---

## Summary table — all decisions

49 questions total. **Letter scheme**: A / B / C / D = options. ⭐ = my recommended pick.

| # | Question | My pick | Closes |
|---|---|---|---|
| **Meta** | | | |
| Q0.1 | Total length target | C (compose for Phase 2) + A (this PR) | length |
| Q0.2 | Section ordering | A (mechanics-first) | structure C3 |
| Q0.3 | Split into prompts/ dir? | B (Phase 2 PR) | scalability |
| Q0.4 | PR sequencing | B (two-step) | safety |
| **Top of prompt** | | | |
| Q1.1 | `<content_floor>` block? | A (add) | F-046+F-066+F-067+F-068+F-070 |
| Q1.2 | Symmetric forward-update in `<chronological_scope>`? | A | U3, U7 |
| **Concept vs thread** | | | |
| Q2.1 | Event-log voice example | B (in `<voice>` section) | F-045 |
| Q2.2 | Trim `<concept_vs_thread>` 49→25 lines | A (trim) | length |
| **5W block** | | | |
| Q3.1 | Promote `## Why it matters` to load-bearing? | A | F-065 depth |
| Q3.2 | Cut "Flavor varies by domain" sub-list | A | length |
| **Q3.3** | **Wikilink slug form (X1 — BLOCKER)** | **A or B — your call** | F-070 |
| Q3.4 | Owner in WHO bullet? | A | F-066 |
| **Citations** | | | |
| Q4.1 | Cut rsplit instruction (let tool return cite key) | A | length, quality |
| Q4.2 | Compress `## Sources` warning | B (keep) | preserve |
| **Revision style** | | | |
| Q5.1 | Add UPDATE Example 11? | C (both prose+example) | U3 |
| **Workflow (THE BIG ONE)** | | | |
| Q6.1 | Split into `<terminal_decision>`+`<procedure>`? | A | structure |
| Q6.2 | Concept-shaped terminal-outcome? | B (keep email-shaped) | runtime alignment |
| Q6.3 | Tool-ordering callout (cmw post-write only)? | A | U1 |
| Q6.4 | Tighten step 7 reviewer rule? | A (MUST) | F-043 |
| Q6.5 | Sibling-phrasing check? | A | F-031 |
| Q6.6 | Cut bookkeeping at L461? | A | C5 |
| **Page types** | | | |
| Q7.1 | Universal H2 shape (collapse per-type)? | A | NORTH-STAR Axis 5 |
| **Q7.2** | **TL;DR Required (X2 — BLOCKER)** | **C (both lead+TL;DR)** | F-046 |
| Q7.3 | Bug-page alternative shape? | A | F-058 |
| Q7.4 | `## Current state` contract? | A | F-067 |
| **Frontmatter** | | | |
| Q8.1 | Owner+stage+target_date frontmatter? | B (owner only) | F-066 |
| Q9.1 | `<related_links>` section? | A | F-069 |
| **Tool guidance** | | | |
| Q10.1 | Move tool guidance up? | A | C8 |
| Q10.2 | resolve_page idempotency teaching? | A | U2 |
| Q10.3 | Inherited fs tools (glob/ls/grep)? | A (authorize) | U6 |
| Q10.4 | Dead tools (write_draft / wiki_merge)? | mixed: B for merge / A for draft | U4 |
| Q10.5 | WHEN/WHEN-NOT on every tool? | A | C8 |
| **Self review** | | | |
| Q11.1 | Cut reviewer dup in self-review? | A | C4 |
| Q11.2 | TL;DR + owner checks in self-review? | A (both) | F-046, F-066 |
| **Few shots** | | | |
| Q12.1 | Replace Example 7 (selective wikilinking)? | A | F-070 |
| Q12.2 | Replace Example 8 with Question-delta? | A | gap |
| Q12.3 | Example 11 UPDATE flow? | A (covered by Q5.1) | U3 |
| Q12.4 | Example 12 chronological-scope DON'T? | A | gap |
| Q12.5 | Trim skip examples? | A | length |
| **Hard rules** | | | |
| Q13.1 | Cut Hard-rules duplications? | A | C5 |
| Q13.2 | New positive Hard rules? | A (after Q3.3 settled) | F-063, D9 |
| **Voice** | | | |
| Q14.1 | Add `<voice>` section? | A | F-045 |
| **Real-world patterns** | | | |
| Q16.1 | Preamble between tool calls? | A | trace readability |
| Q16.2 | Reversibility/blast-radius framing? | A | edge cases |
| Q16.3 | Ambition vs precision paragraph? | A | green-field |
| Q16.4 | Devin `<think>` mandatory transitions? | A | enforcement |
| Q16.5 | Triple-redundancy on `source_threads:`? | A | format invariant |
| Q16.6 | Move `<expert_questions>` to reviewer? | A | length, separation |
| Q16.7 | Final-message format spec? | A if no scope creep | greppability |
| Q16.8 | Cite glossary from compiler? | A | discoverability |
| **NORTH-STAR doc** | | | |
| Q15.1 | Bundled NORTH-STAR ratification PR? | A (after prompt PR) | doc lag |

**Two questions block progress, no default**:
- **Q3.3 (X1)** — wikilink slug form. A=`[[amit-agarwal]]` (clean, ~1 week migration). B=`[[aa-indiamart-com]]` (matches `create_entities`, ships now). C=alias layer (deferred).
- **Q7.2 (X2)** — TL;DR canonical form. A=Required. B=lead paragraph IS the TL;DR. C=both serve different functions.

---

## How we walk this together

1. **Pre-walk**: pick X1 (Q3.3) and X2 (Q7.2). Everything else has my recommended pick as default.
2. **Walk**: I'll fire `AskUserQuestion` per Q in order. You pick A/B/C/D (or override). Some questions have visual aids inline above (file tree, before/after templates). Skip anything; we revisit.
3. **Apply**: I produce a unified diff per Q, you eyeball, we commit. Two-step PR (Q0.4=B): PR1 reorg + dedupe (no behavior change), PR2 content/behavior edits.
4. **Smoke**: 30-page compile post-PR2. Re-audit personas. Verify behavior moves.

Ready. After you compact, I'll start with Q0.1 via `AskUserQuestion`.
