---
title: "Compile prompt review — 5-angle collation, 2026-04-28"
audit_kind: prompt-review-multi-reviewer
target: src/compile/prompts.py (COMPILER_SYSTEM_PROMPT, 1057 lines)
reviewers: best-practices · audit-findings · langfuse-traces · north-star · structural
---

# Prompt review — 5-angle collation (2026-04-28)

Collation of 5 independent reviews of `src/compile/prompts.py` (`COMPILER_SYSTEM_PROMPT`, 1057 lines, 17 XML-tagged sections):

1. **Best-practices** — web research from Anthropic, OpenAI, LangChain engineering blogs + ReAct paper. Source: `/tmp/prompt-review-best-practices.md`.
2. **Audit-findings** — cross-referenced against 94 findings in STATUS.md / findings.jsonl + persona-deep-audit-deployed-2026-04-28 + V12 50-compile audit. Source: `/tmp/prompt-review-audit-findings.md`.
3. **Langfuse-traces** — 59 trace bodies analyzed across 4 active models (z-ai/glm-5, z-ai/glm-5.1, x-ai/grok-4.1-fast, moonshotai/kimi-k2.6). Source: `/tmp/prompt-review-langfuse-traces.md` + `/tmp/lf-audit/`.
4. **North-Star alignment** — judged against `docs/NORTH-STAR.md`, `docs/proposal/NORTH-STAR-DRAFT.md`, and `docs/audits/v12-north-star-2026-04-19.md`. Source: `/tmp/prompt-review-northstar.md`.
5. **Structural / first-impressions** — in-session quick read by orchestrator. Source: `/tmp/prompt-review-structural.md`.

6. **Real-world agent prompts** — comparative review of Claude Code, Cursor 2.0, Aider, Devin, Codex CLI, Replit, Windsurf, Augment, v0, Cline. Source: `/tmp/prompt-review-real-world-agents.md` + `/tmp/agent-prompts/` (verified `curl` to source files).

A 7th review (Codex independent) was attempted via `codex-rescue`; **failed silently** (Codex CLI offline). The structural review covers the second-set-of-eyes angle.

This doc is the **navigation surface** for a line-by-line walk-through. Read top-to-bottom for the executive summary, then walk Section-by-Section starting from "Walk-through by section".

**Headline from review #6**: median agent prompt is ~250-300 prose lines; max in production is v0 at ~600. **Ours at ~1000 is 3× median, 1.5× the largest.** Only architectural device proven to scale past 300 lines is composition (Piebald-style file split). 10 specific patterns to borrow are listed in §6 below.

---

## Executive summary — strong consensus

These show up in ≥3 of 5 reviews. Ship-first targets.

| # | Issue | Reviewers | Severity |
|---|---|---|---|
| C1 | **TL;DR labelled "Optional" (line 528)** — should be MUST. Cycle-10 smoke: 1/7 pages adopt it. Audit-findings, north-star, structural all flag. | audit, north-star, structural | High |
| C2 | **`owner:` / `dri:` frontmatter never requested.** Persona P2 hits this. 319/326 topics lack ownership. Single biggest gap for PM-style lookups. | audit, structural, langfuse | High |
| C3 | **`<workflow>` is buried + too long (175 lines).** Terminal-outcome contract at L294 sits behind 290 lines of editorial. Tools listed at L604 but used on turn 1. | best-practices, structural, north-star | High |
| C4 | **Reviewer-invocation ordering bug.** Langfuse: 30/52 reviewer calls fire BEFORE any write (58%). The prompt teaches the rule twice in different language (`<workflow>` step 7 + `<self_review>` step 5), which the model reads as license to reinterpret. F-043 still Open. | langfuse, audit, structural, best-practices | **Critical** |
| C5 | **Bookkeeping rule duplicated 3× (L461, L654, L1027).** OpenAI's GPT-5 guide explicitly warns that contradiction-shape damages reasoning more than other models. | best-practices, structural | Med |
| C6 | **Per-type H2 templates teach a hierarchy** the North-Star explicitly rejects ("all pages just pages"). `## Key decisions` H2 in topic template (line 505) survives the experiment-not-decision reframe. | north-star, audit | Med-High |
| C7 | **Example 7 (line 920-933) teaches the failure mode the V12 audit tried to fix** — creates entities for everyone in from/to instead of "decision-makers + experiment-owners only". | north-star, audit | High |
| C8 | **Tool list buried at L604.** Agent uses tools on turn 1; tool teaching should be near the top. | best-practices, structural | Med |
| C9 | **Footnote `rsplit("_", 1)[-1]` instruction (L141-146)** asks the LLM to do deterministic string ops — Karpathy-grade waste. Have `get_thread_context` return the cite key. | best-practices, audit | Med |

## Executive summary — high-impact unique findings

These show up in only one review but carry serious weight on their own.

| # | Issue | Reviewer | Severity |
|---|---|---|---|
| U1 | **`check_my_work` called BEFORE any write — 64% of 59 traces.** Agent treats validator as a probe. Single biggest behavioral bug. | langfuse | **Critical** |
| U2 | **`resolve_page` flail — 70% of traces ≥4 calls; 25% repeat same query.** Worst case: 28 calls in one trace. Idempotency teaching missing; agent mutates with `system/` prefixes, case variants, synonyms. | langfuse | High |
| U3 | **`summary-stale-date` blocker → 100% non-recovery (11/11).** Middleware says "rewrite the Summary"; agent appends Recent changes only. No worked UPDATE example in `<revision_style>`. | langfuse | **Critical** |
| U4 | **3 dead tools, 0 calls each**: `write_draft_page`, `wiki_merge_pages`, `log_insight("insufficient_decision")`. Either give them a triggering example or delete. | langfuse | Med |
| U5 | **361 `[[<x>-indiamart-com]]` wikilinks** in 200 page-edits — and `create_entities` returns email-canonical slugs. **Tool contract contradicts prompt teaching.** Pick one. | langfuse | High |
| U6 | **Filesystem tools (glob/ls/grep) used 129×** — never mentioned in prompt. Inherited from DeepAgents. Either authorize or forbid. | langfuse | Med |
| U7 | **`<chronological_scope>` "leave it alone" (L19) vs `<revision_style>` "rewrite Summary" (L199) conflict.** Agent reads "leave it alone" too broadly, never updates Summary on UPDATE flows. Validates U3. | audit | High |
| U8 | **`## Sources` vs `## References` doc lag** — NORTH-STAR.md still says `## Sources` but prompt forbids it (MkDocs hook short-circuits). Update NORTH-STAR. | north-star | Low |
| U9 | **Wikilink incentive is graph-hostile.** Hard rule (L1032-33) makes broken wikilinks a reviewer block; nothing rewards link density. Random-walk navigation is North-Star's success metric. | north-star | High |
| U10 | **Hidden-curriculum voice not modeled in examples.** 5W block (L76-133) teaches the questions; no example shows a senior IndiaMart employee *answering* them with operational intuition. | north-star | Med |

## Executive summary — conflicts to resolve

Where reviewers disagree, the user has to pick. None are blockers — but pick a direction before editing.

| # | Conflict | Reviewer A says | Reviewer B says |
|---|---|---|---|
| X1 | **Wikilink slug form for people** | audit + north-star: agent should use `[[people/<canonical-name>]]`; update entity store to produce them | langfuse: `create_entities` returns email-canonical slugs; prompt's `[[amit-agarwal]]` example contradicts the tool — either update prompt's example to `[[amit-aa-indiamart-com]]` form OR ship a migration first |
| X2 | **TL;DR vs lead paragraph as the lede** | audit-findings: TL;DR Required (one mandatory format) | north-star: NORTH-STAR.md says TL;DR but prompt prefers a 2-sentence lead paragraph (Wikipedia-style). Update NORTH-STAR or the prompt — don't carry both |
| X3 | **MUST language** | structural: flip "should" → "MUST" on critical rules (e.g. F-046, F-043) | best-practices: Anthropic's Opus 4.7 docs say *dial down MUST* — over-use damages reasoning |
| X4 | **`<workflow>` reorganization** | best-practices: split terminal-outcome from procedure; move tools up | audit-findings: keep current order, just tighten the rules in place |

---

## Walk-through by section (in prompt order)

For each section, the reviewers' verdicts and recommended changes. **Lead with the consensus, then the unique findings, then conflicts.** Use this when walking line-by-line.

### `<background>` — Lines 1–15

- **Best-practices**: Keep, very short. Should remain at top.
- **North-star**: PASS implicit. The "don't fight the sandbox" framing (L12-14) is correct trust-the-middleware posture.
- **Audit-findings**: Lines 12-14 (sandbox path autoheal) → **NEVER CHANGE** — load-bearing for F-021 fix.
- **Structural**: Keep. Could host a 1-line concept-vs-thread teaser since `<concept_vs_thread>` is buried.

**Action**: keep as-is. Optional: add a one-sentence "Pages are CONCEPTS, emails are EVIDENCE" lede here so the framing hits before chronological scope.

### `<chronological_scope>` — Lines 17–23

- **Best-practices** (#2): "criminally short for a critical rule. Don't rewrite future facts is the most expensive failure mode in this codebase. It deserves a worked example."
- **Audit-findings** (U7): conflicts with `<revision_style>` "rewrite Summary on UPDATE." Agent over-applies "leave it alone" → never updates Summary. **Validates U3** (langfuse: 100% non-recovery on `summary-stale-date`).
- **Structural**: NEVER CHANGE the leave-it-alone primacy; modify only by APPENDING the symmetric forward-rewrite rule.
- **North-star**: PASS — content is correct.

**Action — high priority** (closes U3 + U7):
> Append to L22-23:
> *"Conversely: if today's email IS the newest evidence on `source_threads:`, you MUST update the Summary's current-state sentence to reflect what today's email says. Leaving the Summary stale because 'later batches will fix it' is the failure mode. The `summary-stale-date` blocker fires for exactly this case — re-read the Summary, find the outdated sentence, and `edit_file` it. Appending to Recent changes alone does not clear the gate."*

### `<concept_vs_thread>` — Lines 25–74

- **All 5 reviewers agree: PRESERVE.** This is the prompt's spine.
- **Best-practices** (#10): 49 lines for a binary distinction; the BAD/GOOD pair carries 90% of the load. Could trim explanatory prose at L26-50; lead with the example.
- **Audit-findings** (F-045): extend the BAD section with a SECOND BAD pattern — event-log voice in body ("As of 2026-01-07: [[X]] approved", "Vote of thanks"). Currently only `<page_types>` and `<section_titles>` cover H2-level event-log; the body voice leaks.
- **Structural**: NEVER CHANGE the BAD/GOOD pair (L51-73).
- **North-star** (Slip 1): the framing's right; mostly slips into `<workflow>`'s email-shaped terminal-outcome later.

**Action** (closes F-045):
> Add to L73 a second BAD example showing event-log body voice (the `[[X]] approved` / `Vote of thanks to` pattern), with a GOOD synthesis pair.

### `<expert_questions>` — Lines 76–133

- **North-star** (Edit #5): performative not embodied. Block teaches WHAT/WHY/WHEN as bullets to satisfy; no example models a senior IndiaMart employee *answering* them with operational intuition. Suggest **promote `## Why it matters` to load-bearing** for the new-joiner test.
- **Best-practices** (#10): 58 lines; the "Flavor varies by domain" sub-list (L106-122) duplicates `<domain_frontmatter>` (L532-571). Cut one.
- **Audit-findings** (F-066): extend WHO bullet (L95-98) to put owner/DRI in frontmatter, not just inline.
- **Audit-findings** (F-070): the `[[amit-agarwal]]` example (L97-98) is correct teaching but conflicts with what `create_entities` returns. See **X1 conflict**.
- **Structural**: 5W list great; "Flavor varies by domain" duplicates domain frontmatter teaching.

**Action — pick one of two paths for X1**:
- **Path A** (audit + north-star): keep the `[[amit-agarwal]]` form; flag a migration of the entity store as a separate task.
- **Path B** (langfuse): update the example to `[[aa-indiamart-com]]` form so it matches `create_entities` returns. Plus add a one-liner: "the slug is opaque; readability comes from the page title, not the slug."

**Other actions**:
- Promote `## Why it matters` to load-bearing (~5 lines) — it's the hidden-curriculum lever.
- Cut "Flavor varies by domain" sub-list (~17 lines) since `<domain_frontmatter>` covers it.
- Extend WHO bullet to mandate `owner:` frontmatter (closes F-066).

### `<inline_citations>` — Lines 135–197

- **Best-practices** (#7): the rsplit instruction (L141-146) is a Karpathy-grade misuse — let `get_thread_context` return the cite key instead. The MkDocs short-circuit detail (L168-172) leaks build-system internals to agent context — should be a validator error.
- **Audit-findings**: NEVER CHANGE L167-172 — without it, `## Sources` would silently disable evidence block.
- **Structural**: keep, but prune the rsplit instruction.
- **North-star** (U8): NORTH-STAR.md still uses `## Sources`; doc lag — fix NORTH-STAR.

**Action**:
- Cut the rsplit instruction (L141-146); have `get_thread_context` expose the cite key as a return field. **Coordinator-side change required.**
- Keep the `## Sources` warning (L167-172) but rephrase as a single sentence — current 5-line block is over-emphasis.
- Update NORTH-STAR.md:95-99 to use `## References`.

### `<revision_style>` — Lines 199–287

- **All 5 reviewers say: STRONG SECTION.** North-star: PASS, "closest to the North-Star wording".
- **Audit-findings** (F-060): teaching is good; conflict is that it's at L199 (after the 5W block). Agent on UPDATE flows triages early and may not re-read this section.
- **Langfuse** (D4): the `summary-stale-date` middleware fires; agent doesn't act. Add a worked UPDATE example showing `edit_file` of the Summary sentence (current section has only CREATE examples).
- **Structural**: keep. The good/bad Summary pair (L268-286) is judge-cited.

**Action — high priority** (closes U3 with U7):
1. Add a new few-shot Example 11 (UPDATE flow) showing `edit_file` rewriting the Summary's stale sentence + `patch_page` for Recent changes + `check_my_work` recovery loop. (See langfuse review §D4 for the full proposed example.)
2. Promote the rule into `<workflow>` step 5 (line 427) so it lands during operational reading: *"For UPDATE flows: re-read the page's Summary first; if any fact in the Summary is older than the current email's evidence, rewrite that sentence."*

**NEVER CHANGE**: L217-229 collapsible `<details>` block syntax (F-051 fix); L263-267 "experiments not decisions" (F-063 prevention).

### `<workflow>` — Lines 289–464 (THE BIG ONE)

This is where the most disagreement lives. 175 lines doing two things.

- **Best-practices** (#1, #2, #5): biggest single issue. Three-way contradiction shape ("MUST commit" + "Investigatory don't close loop" + final pre-return verification). Tools at L604 but workflow uses them on turn 1. Bookkeeping rule restated.
- **North-star** (Edit #4): terminal-outcome model is email-shaped ("Every email ends with EXACTLY ONE..."), should be concept-shaped per NORTH-STAR.md:58-62.
- **Langfuse** (D1, D3): the ordering rule for `check_my_work` is BURIED in step 6, ditto reviewer in step 7. **64% of traces call cmw pre-write, 58% of reviewer calls pre-write.** This is the single biggest behavior bug.
- **Audit-findings** (F-031, F-043, F-060): step 2 too soft on near-dup detection; step 7 contradicts itself in one sentence ("Default to" + "Skip ONLY for trivial").
- **Structural** (#8): split workflow into `<terminal_decision>` + `<procedure>`; bundling buries the procedure inside the philosophy.

**Action — composite (best-practices Edit #1 + Edit #2 + langfuse #D1/D3 + audit F-043 + structural #8)**:
1. **Split `<workflow>` into two sections** — `<terminal_decision>` (the 4-outcome contract + Question/Answer-delta exceptions, currently L294-413) and `<procedure>` (the numbered Steps, L416-463).
2. **Move both up** so they sit nearer the top of the prompt (after `<background>` and before the editorial blocks).
3. **Add a "Tool ordering rule" callout** at the top of `<procedure>`:
   > *"`check_my_work` and `task(reviewer)` are POST-WRITE validators. Never call either before you have called `write_file` / `edit_file` / `patch_page` in the current batch. If the email leads to a `log_insight` (no-write outcome), skip both validators entirely."*
4. **Reframe terminal-outcome from email-shaped to concept-shaped** (north-star Edit #4):
   > *"Every email is evidence for AT LEAST ONE concept page (or a logged skip). For each batch, identify the set of concepts the emails are evidence for, then for each concept commit to one of: edit/create the concept page; merge into a sibling page that already covers the same concept (`already_captured`); skip as non-substantive (`trivial_skip`); flag for triage when no concept fits (`insufficient_decision`)."*
5. **Tighten step 7 (F-043)**: replace "Default to calling it" + "Skip ONLY for trivial edits" → *"For any new page (`write_file`) or any page edit producing ≥4 lines of new prose, you MUST call `task(subagent_type='reviewer', ...)` AFTER the write tool returns successfully."*
6. **Tighten step 2 (F-031)**: *"`resolve_page(<concept>)` AND a second `resolve_page` on a sibling phrasing before any `write_file` of a new topic. If either returns a candidate, edit the existing page; do NOT create a sibling."*
7. **Cut the bookkeeping restatement at L461-463** — it's repeated at L654 and L1027.

**NEVER CHANGE**: L295-313 (4 terminal outcomes), L314-329 ("be aggressive about already_captured"), L333-401 (Question-delta + Answer-delta both directions).

### `<page_types>` — Lines 466–530

- **North-star** (Edit #1, GAP on Axis 5): biggest North-Star gap. Three different per-type H2 templates (L504-512) teach a hierarchy NORTH-STAR + V12 explicitly reject. Topic = "ongoing work", system = "durable noun", policy = "rules", decision = "lazy", person = "reference" — five-tier.
- **Audit-findings** (F-046): TL;DR labelled "Optional" at L528 is the load-bearing leak.
- **Audit-findings** (F-067): `## Current state` listed as a slot at L504 but never defined as a contract.
- **Audit-findings** (F-058): "It's a template, not a law" (L498) is the right escape hatch for bug-shape pages, but no concrete bug-page example.
- **Best-practices** (#1): MUST-language and "template not a law" sit too close; agents read both at once and waste reasoning reconciling.

**Action — composite**:
1. **(C1, F-046)** Replace L528 "Optional: a `## TL;DR` H2" with: *"**Required for topic and system pages**: a `## TL;DR` H2 with ≤3 quantified sentences front-loading numbers (rollout %, latency, owner). `get_page_summary` surfaces this verbatim; downstream agents trust it."*
2. **(C6, north-star Edit #1)** Collapse the per-type templates (L504-512) into one universal shape: `## TL;DR` → `## Why it matters` → `## Current state` (or `## Current policy` for policy) → `## Recent changes` → `## Open questions` → `## Related` → `## References`. Type-specific sections become *optional flavor*.
3. **(F-067)** After the universal shape, add: *"`## Current state` is REQUIRED on every topic. Use markers: `Stage: poc | alpha | beta | ga | sunset` · `Rollout: <N>% of <segment>` · `Last verified: <date>`."*
4. **(F-058)** Add: *"Bug/incident pages are an explicit alternative shape: `## Bug details` → `## Impact` → `## Fix` → `## Verification` → `## Related`. Do NOT pad with `## Why it matters` on bug pages."*

### `<domain_frontmatter>` — Lines 532–571

- **All 5 reviewers**: NEVER CHANGE the 8 canonical domain enumeration. F-035 fix is load-bearing.
- **Best-practices** (#10): "Flavor varies by domain" sub-list duplicates with `<expert_questions>` L106-122. Cut one.

**Action**:
- Add `owner:` and `target_date:` to the frontmatter teaching (extends section beyond just `domain:`). Section probably wants renaming to `<frontmatter>` if it grows.

### `<section_titles>` — Lines 573–602

- **Audit-findings** (F-069): Line 506 puts `## Related pages` in topic shape; L1049 puts `related:` in frontmatter. Both, no disambiguation. 11/16 V12 pages had both — F-069 visible.
- **North-star**: PASS on H2 anti-patterns; the "filing-cabinet H2s" example (L584-598) is excellent.
- **Persona-audit** finding: `## Related` sometimes lists people, sometimes topics, sometimes both. No discipline.

**Action — closes F-069 + persona finding**:
> Insert new section after L571 — `<related_links>`:
> *"Related neighbours render in TWO disjoint places: frontmatter `related:` (≤8 wikilinks to CONCEPT pages, drives graph navigation, rendered into footer by build) and body `## Related` (only when prose context is needed, e.g. 'These three topics together describe the photosearch funnel'). Never duplicate. People belong in frontmatter `owner:` and inline body wikilinks — NEVER in `## Related`."*

### `<tool_guidance>` — Lines 604–657

- **Best-practices** (#2, #4, #6): tool list buried at L604 — move up. Return shapes inconsistent. WHEN/WHEN-NOT pattern only on 3 of 13 tools.
- **Langfuse** (U2): `resolve_page` flail. Idempotency teaching missing. Add: *"`resolve_page` is idempotent. If `exists: false`, the page does not exist. NEVER prefix the slug with `system/` / `topic/`. To find a page: try the bare slug, then scan the `candidates: [...]` array, then fall back to `list_wiki_pages` ONCE."*
- **Langfuse** (U6): inherited `glob`/`ls`/`grep` (129 calls) never mentioned. Add an explicit acknowledge OR forbid.
- **Langfuse** (U4): `write_draft_page` and `wiki_merge_pages` taught here but never called (0 calls). Either give a triggering example or remove.

**Action**:
1. **Move section to position #3 in the prompt** (after `<background>` and `<terminal_decision>`).
2. **(U2)** Add the `resolve_page` idempotency teaching.
3. **(U6)** Acknowledge inherited tools: *"The runtime exposes `ls`, `glob`, `grep` from DeepAgents. Prefer the wiki-specific tools above; use `glob`/`ls` only when you need to confirm a path exists, never as a discovery substitute for `resolve_page`."*
4. **(U4)** Decide on `write_draft_page` and `wiki_merge_pages`: ship triggering examples in `<few_shots>` OR delete from prompt.
5. **(C8, best-practices #6)** Add WHEN/WHEN-NOT to each tool. One-line each.
6. **(C5)** Cut the bookkeeping restatement at L654-656 (it's at L461 already).

### `<sources_management>` — Lines 659–672

- **NEVER CHANGE** — F-017 forward-looking fix. Compact and load-bearing.

### `<todo_rule>` — Lines 674–679

- **Best-practices** (#1): MUST-language overuse — *"Where you might have said 'CRITICAL: You MUST use this tool when...', you can use more normal prompting"* (Anthropic's Opus 4.7 docs). Demote to "Use `write_todos` when batch >2 emails."
- **Audit-findings**: NEVER CHANGE the *why* sentence at L676-678 ("This keeps you honest..."). Anthropic explicitly recommends *why* sentences.

**Action**: soften "MUST use" → "Use" but keep the *why* sentence verbatim.

### `<self_review>` — Lines 681–700

- **Structural** (#7): reviewer-invocation taught here AND in `<workflow>` step 7. Drift.
- **Audit-findings**: add TL;DR check (closes F-046).
- **Best-practices** (#9): keep the L698-699 "Never catch bare Exceptions in your head" line — positive, minimal, good.

**Action**:
1. Cut reviewer invocation from L691-696 (kept in `<workflow>` only).
2. Add: *"5. Does the page have a `## TL;DR` with at least one number? Required for topic/system."*
3. Add: *"6. Is `owner:` set in frontmatter? If unclear, leave blank and add an Open question — don't guess."*
4. NEVER CHANGE L698-699.

### `<recovering_from_blockers>` — Lines 702–738

- **Best-practices**: NEVER CHANGE — concrete, exhaustively-worked, F-033 fix. The 3-retry budget is calibrated.
- **Audit-findings**: same.

**Action**: keep verbatim.

### `<editorial_notes>` — Lines 740–760

- **Best-practices**: NEVER CHANGE — three-way "Actionable + grounded / Out of scope / Speculative" framing is rare and good. Especially L757 ("One round of patching per reviewer invocation — don't loop. The editor is an advisor, not a gatekeeper.").

**Action**: keep verbatim. But adjacent to this section, add a new short `<voice>` section (closes F-045) before `<few_shots>`:
> *"Pages are reference docs, not narratives. Avoid: 'We did X' / 'The team launched Y' (say what the thing is, not who did it). 'Vote of thanks to <name>' (celebratory prose belongs in chat). 'As of <date>: <name> approved <thing>' (put the decision in `## Recent changes` with a footnote, not as inline narration). 'In this thread we discussed X' (the page is about X, not the thread). Test: would this sentence still make sense if rewritten 6 months from now? If not, rewrite."*

### `<few_shots>` — Lines 762–~1018

- **Best-practices** (#3): missing the high-leverage failure cases. No Question-delta example. No Answer-delta example. No chronological-scope DON'T-rewrite example. No `patch_page` example. No `insufficient_decision` example.
- **North-star** (Edit #3): Example 7 (L920-933) directly teaches the failure mode V12 tried to fix — creates entities for everyone in from/to.
- **Audit-findings** (F-070): Example 7 should show canonical-name wikilink form too.
- **Langfuse** (U3): missing UPDATE-flow example with Summary rewrite (this is the #1 behavioral bug).

**Action — major rework** (closes F-045 + F-070 + U3 + best-practices #3):
1. **Replace Example 7** with selective person-wikilinking (only decision-makers + experiment-owners + approvers). See north-star Edit #3 for the full proposed text.
2. **Replace Example 8** ("Fully investigated" — largely redundant with Example 7) with a Question-delta `patch_page` example.
3. **Add Example 11**: UPDATE flow where the Summary's stale sentence is rewritten via `edit_file`, plus `patch_page` for Recent changes, plus `check_my_work` recovery on `summary-stale-date`. (See langfuse review §D4 for the full proposed example.)
4. **Add Example 12**: chronological scope — DON'T rewrite future facts (the highest-stakes anti-pattern in the prompt has zero worked example today).

### `## Hard rules` — Lines 1020–1038

- **Best-practices** (#8): each NEVER duplicates earlier prose. Cut to 4 items not stated elsewhere; convert to positive form. Anthropic: tell Claude what to do, not what not to do.
- **Audit-findings**: each NEVER is mapped to a specific fix; never weaken. Add F-070 (no email-slug wikilinks) and F-063 (no inline `## Decision:` H2).
- **Langfuse** (D9): strikethrough rule (L218 in `<revision_style>`) absent here; agent uses `~~~~` in 4/59 traces (7%). Promote to Hard rules.

**Action — composite**:
- Cut duplications (don't restate L1024 invent slugs; L1027 last_compiled; L1029 sources:; L1032 broken wikilinks).
- Add new positive rules:
  - *"Always go through `create_entities` for people."*
  - *"Always use `[[<canonical-slug>]]` returned by `create_entities` for person wikilinks; the legacy `[[<x>-indiamart-com]]` form is a stub artefact."* (X1 conflict — pick form first)
  - *"Replace stale claims via `edit_file`; archive long-form prior versions in `<details>` blocks. Strikethrough is forbidden."* (closes D9)
  - *"Never write `## Decision: <X>` as an inline H2 — that's a half-formed decision page hiding inside a topic. Use `## Recent changes` with a `**date** — Decided:` bullet."* (closes F-063)

### `## Frontmatter template` — Lines 1040–1056

- **Audit-findings** (F-066): missing `owner:`, `dri:`, `stage:`, `target_date:`. Expand template + add a one-line teaching below it.
- **North-star**: keep, but document where multi-domain (`domains:`) replaces `domain:`.

**Action**:
> Extend template:
> ```yaml
> ---
> title: "Human Readable Title"
> page_type: topic | system | policy | person | decision
> status: active | superseded | archived
> owner: "[[<dri-slug>]]"             # required for topic/system: single DRI
> stage: poc | alpha | beta | ga | sunset   # topic only; required when applicable
> target_date: 2026-06-30             # next rollout milestone; omit if none
> source_threads:
>   - 19b59cdc863ac109
> related:
>   - "[[other-slug]]"
> ---
> ```
> Below: *"Owner is the single person you'd Slack to ask 'what's the status?'. Pick from the from:/to: of the most recent decision email, not the announcer. If unclear, leave `owner:` empty and add an Open question — don't guess."*

---

## Cross-cutting changes (insert at top of prompt)

These don't fit any one section — they reorganize the whole prompt.

### CC1 — Insert `<content_floor>` block at the top (audit-findings F-065 META)

**Closes** F-046 + F-066 + F-067 + F-068 + F-070 jointly. Anthropic's primacy-effect: rules read first stick.

> Insert at L16 (between `<background>` and `<chronological_scope>`):
> ```
> <content_floor>
> A page MUST clear this floor before you return:
>
> 1. ## TL;DR — ≤3 sentences, at least one number (rollout %, latency, date).
> 2. owner: frontmatter pointing to a single DRI.
> 3. ## Current state with stage + rollout + last-verified-date markers.
> 4. Open questions (if any) carry a target date.
> 5. References footnotes resolve; no orphans.
>
> Structure without these is filing-cabinet polish. The reviewer will pass
> a structurally-clean page that fails the floor; the floor is YOUR job.
> </content_floor>
> ```

### CC2 — Reorder sections (best-practices Edit #2, structural #8)

Move from current 17-section order to:

```
1. <background>                       (1-15, keep)
2. <content_floor>                    (NEW — CC1)
3. <terminal_decision>                (NEW — split from <workflow> L294-413)
4. <chronological_scope>              (17-23, expand with worked example)
5. <tool_guidance>                    (move up from L604-657, add WHEN/WHEN-NOT)
6. <procedure>                        (NEW — split from <workflow> L416-463)
7. <recovering_from_blockers>         (702-738, keep)
8. <concept_vs_thread>                (25-74, keep)
9. <expert_questions>                 (76-133, trim, promote ## Why it matters)
10. <inline_citations>                (135-197, prune rsplit + MkDocs detail)
11. <revision_style>                  (199-287, keep)
12. <page_types>                      (466-530, collapse to universal shape — north-star Edit #1)
13. <frontmatter>                     (renamed from <domain_frontmatter>, expanded)
14. <section_titles>                  (573-602, add <related_links> subsection)
15. <sources_management>              (659-672, keep)
16. <todo_rule>                       (674-679, soften MUST)
17. <self_review>                     (681-700, cut reviewer dup, add TL;DR/owner checks)
18. <editorial_notes>                 (740-760, keep)
19. <voice>                           (NEW — closes F-045)
20. <few_shots>                       (replace Example 7+8, add Examples 11+12)
21. <hard_rules>                      (1020-1038, cut duplications, add new positives)
22. <frontmatter_template>            (1040-1056, expand with owner/stage/target_date)
```

Length target: 1057 → ~750 lines via deduplication (best-practices #10).

### CC3 — Resolve X1: wikilink slug form

**Decision required from user.** Three options:
- **Path A** (audit + north-star): keep `[[amit-agarwal]]` example; ship a separate migration of the entity store to produce display-name slugs. Higher engineering cost but cleaner end state.
- **Path B** (langfuse): update prompt to match what `create_entities` returns: `[[aa-indiamart-com]]`. Lower engineering cost; readability comes from page title not slug.
- **Path C** (deferred): wire a redirect/alias layer so both forms resolve. Higher engineering cost; preserves existing legacy 361-instance wikilinks without rewrite.

**Without picking one, the prompt edit can't ship** — F-070 stays Open and the wikilink convention contradiction continues.

### CC4 — Resolve X2: TL;DR vs lead paragraph

**Decision required from user.** Two options:
- **Path A** (audit-findings + NORTH-STAR.md current text): ratify `## TL;DR` as Required (closes F-046). Update prompt L528 from "Optional" → "Required". NORTH-STAR.md stays as-is.
- **Path B** (current prompt design): the lead paragraph IS the TL;DR; drop the redundancy. Update NORTH-STAR.md:78-80 to remove the explicit `## TL;DR` heading and define the lead paragraph as the canonical lede.

### CC5 — Things to ratify in NORTH-STAR.md

These are places the prompt is making decisions NORTH-STAR doesn't yet codify (north-star review §"What NORTH-STAR doesn't yet decide..."). Promote to NORTH-STAR or accept as prompt-only conventions:

1. `## Sources` vs `## References` — fix NORTH-STAR.md:95-99 to `## References`.
2. Footnote-per-claim policy — add 3-line subsection.
3. Wikilink density target (≥3 or ≥4 per topic? prompt is silent).
4. Recent-changes retention cap (NORTH-STAR.md:90 says 3-5; prompt silent — pages grow unbounded).
5. The 4 terminal-outcome categories — currently prompt-only; ratify.
6. Question-delta / Answer-delta exception — currently prompt-only; ratify.
7. "≥4 lines triggers reviewer" threshold — currently prompt-only; ratify.

---

## Lines that should NEVER change without a deliberate decision

Consensus across reviewers — these are load-bearing. Modify only by APPENDING.

| Lines | Why preserved | Source-of-truth |
|---|---|---|
| L12-14 | Sandbox path autoheal posture (don't fight) | F-021 fix |
| L17-23 | Chronological-scope leave-it-alone primacy | F-009 fix |
| L25-26 | "page is a CONCEPT, emails are EVIDENCE" lede | V12 north-star reframe |
| L51-73 | GOOD/BAD Summary diff with concrete WhatsApp numbers | judge-cited |
| L84-104 | 5W list | F-052 fix |
| L167-172 | `## References` canonical + `## Sources` MkDocs warning | mkdocs_hooks short-circuit |
| L217-229 | `<details>` collapsible block syntax | F-051 fix |
| L263-267 | "Most entries are NOT decisions — they're experiments" | F-063 prevention |
| L295-313 | 4 terminal outcomes | hard runtime contract |
| L314-329 | "Be aggressive about already_captured" | F-032 fix |
| L333-401 | Question-delta + Answer-delta both directions | symmetric pattern |
| L461-463 | "Bookkeeping is NOT your job" | tool/coordinator split (CLAUDE.md) |
| L514-515 | "Empty sections are fine on first write (`None documented yet.`)" | prevents fabrication |
| L533-571 | 8 canonical domain enumeration | F-035 fix |
| L660-672 | `<sources_management>` "NEVER write `sources:`" | F-017 fix |
| L676-678 | `<todo_rule>` *why* sentence | Anthropic best practice |
| L698-699 | "Never catch bare Exceptions in your head" | positive minimal teaching |
| L702-738 | `<recovering_from_blockers>` 3-retry budget | F-033 fix calibrated |
| L740-760 | `<editorial_notes>` 3-way framing + L757 "advisor not gatekeeper" | rare and good |

---

## Recommended ship order (top-5 by leverage × ease)

If we can only do 5 things this PR cycle, in order:

1. **CC1 — `<content_floor>` block at L16.** Closes F-065 by chaining F-046 + F-066 + F-067 + F-068 enforcement at the top of the prompt. ~15 lines added.
2. **`<workflow>` reorganization (CC2 partial)** — split into `<terminal_decision>` and `<procedure>`, add the Tool ordering rule callout. Closes U1 (cmw pre-write 64%) + U7 (chronological vs revision conflict) + F-043 (reviewer pre-write).
3. **F-066 owner frontmatter** — extend WHO bullet, expand frontmatter template, add self-review check. ~10 lines added across 3 sites.
4. **F-070 wikilink form** — pick X1 path first, then update Example 7 + add Hard rule + update L97-98 example. ~10 lines.
5. **U3 Summary-rewrite UPDATE example** — add Example 11 to `<few_shots>`. Closes the 100% non-recovery on `summary-stale-date`. ~25 lines added.

Estimated total impact:
- Closes F-046, F-065, F-066, F-067, F-068, F-070, F-043 (7 of 25 Open findings).
- Validates U1, U2, U3, U7 (4 high-severity behavioral bugs from Langfuse).
- Net length change: roughly neutral (additions balance deduplication).

After ship: 30-page smoke + persona re-audit on the next compile run to verify behavior moves.

---

## Open questions for the user (need your call before we edit)

1. **X1 wikilink slug form** — Path A (`[[amit-agarwal]]` + entity-store migration), Path B (`[[aa-indiamart-com]]` accept), or Path C (alias layer)? I lean Path A for cleaner end-state but it's most engineering work.
2. **X2 TL;DR canonical form** — `## TL;DR` Required (audit-findings) or lead paragraph IS the TL;DR (current prompt)? I lean Required since `get_page_summary` already trusts it.
3. **CC5 NORTH-STAR ratifications** — promote which of the 7 prompt-only conventions to NORTH-STAR.md? I'd batch them all into one NORTH-STAR PR rather than per-decision PRs.
4. **`write_draft_page` + `wiki_merge_pages` (U4)** — both have 0 calls in 59 traces. Keep with triggering examples, or delete?

Once these are answered we can walk the prompt section-by-section and apply edits. Or you can do that walk-through first; we resolve the questions when each surfaces.
