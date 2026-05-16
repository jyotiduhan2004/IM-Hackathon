---
title: "Axis-2 intent review — prompt-PR set, 2026-04-29"
inputs:
  - docs/audits/prompt-review-decisions-2026-04-28.md
  - docs/audits/prompt-review-synthesis-2026-04-29.md
  - docs/audits/prompt-pr-coverage-matrix-2026-04-29.md
  - docs/audits/verification-2026-04-29.md
status: clean-with-one-followup
---

# Axis-2 intent review — prompt-PR set

Spirit-vs-implementation lens, separate from the code-quality + scope verification done earlier. Reviewed the squashed merges on `main` for PRs #254/#255/#257/#258/#259.

## VERDICT

**clean — one minor follow-up.** The five PRs faithfully ship the spirit of every brief item that was in scope, with one exception: PR #257 marks `validate_page_draft` deprecated but its docstring keeps the original `WHEN TO USE`/`WHEN NOT TO USE` block intact. The header now says "deprecated — do not call" while the body still teaches when to call. That contradiction will outlast the PR until Phase 2 deletion. Everything else is on-spirit.

---

### PR #254 — `docs(north-star): ratify conventions proven by compile prompt`

**Spirit**: ratify the eight conventions PR1 + runtime PRs prove. NS catches up to the prompt; explanation matches behavior.

**Brief items in scope (Q15.1 + A5)**: (1) Sources→References rename; (2) drop `## TL;DR`; (3) email-canonical slug sentence with `aa-indiamart-com` example; (4) wikilink density 3-8 via `related:`; (5) recent-changes retention 3-5 + `<details>`; (6) Terminal outcomes section (4 + Q-delta/A-delta); (7) Universal H2 floor; (8) ISO 8601 one-liner.

**Implementation check**: all 8 SHIPPED-AS-INTENDED. `docs/NORTH-STAR.md` L79 (lead-IS-summary), L99/L105 (References + Sources guard), L107-110 (H2 floor), L113-115 (slug + density), L117-119 (ISO 8601), L125+ (terminal outcomes, Q/A-delta). The drop of `## TL;DR` is graceful — the two surviving mentions (L79, L105) are explanatory, not prescriptive; L200 of NS still references "progressive disclosure (lead paragraph → references)" — caller swapped, no orphan.

**Wrong-shape findings**: none.

**Missing findings**: none.

**Scope creep**: none — diff stays in NS.

**Verdict**: clean.

---

### PR #255 — `feat(compile): get_page_summary tldr falls back to lead paragraph`

**Spirit**: decouple the "lead paragraph IS the summary" wiki convention (Q7.2) from the tool's return shape — runtime first, prompt PR2 follows. Pages without `## TL;DR` should not stop returning a usable `tldr`.

**Brief items in scope**: `_extract_tldr` falls back to lead paragraph; precedence preserved (explicit TL;DR wins); empty body returns None; docstring updated.

**Implementation check**:
- Fallback → SHIPPED-AS-INTENDED (`compiler.py` L2117-2123).
- Precedence (`test_tldr_prefers_explicit_section_over_lead_paragraph`) → SHIPPED.
- Empty body → SHIPPED (`test_empty_body_returns_none_tldr`).
- Docstring → SHIPPED.

**Wrong-shape findings**: subtle precedence quirk — a page with `## TL;DR` heading and *empty body under it* now returns the lead paragraph (previously None). Not in the brief either way; arguably more useful than the old behavior. Worth a one-line test if anyone cares; probably not.

**Missing findings**: none.

**Scope creep**: none.

**Verdict**: clean.

---

### PR #257 — `docs(tools): add WHEN/WHEN-NOT guidance to compile tool docstrings`

**Spirit**: tools own their own usage rules. Move trigger conditions out of the prompt into docstrings so the agent reads them next to the call site (Anthropic pattern).

**Brief items in scope**: Q6.3 (cmw AFTER-write trigger), Q6.4 task() reviewer-dispatch (acknowledged un-editable; deferred to PR2), Q4.1 (cite_key on `get_thread_context`), Q10.2 (resolve_page bare-slug), Q10.5 (WHEN/WHEN-NOT on every editable tool), Q-NEW.1 (validate_page_draft deprecate), CC-F (runtime delegation), and `write_draft_page` deprecate.

**Implementation check**:
- cmw WHEN/WHEN-NOT → SHIPPED. Adds the "skip when outcome is log_insight" carve-out the brief asked for.
- resolve_page bare-slug guidance → SHIPPED.
- `_cite_key_from_raw_path` + `cite_key` field on messages_summary → SHIPPED with helper tests.
- log_insight, create_entities WHEN/WHEN-NOT → SHIPPED.
- `write_draft_page` deprecate header → SHIPPED.
- `validate_page_draft` deprecate header → **SHIPPED-WRONG-SHAPE.** The docstring opens with "WHEN NOT to call: this tool is deprecated; do not call" but the *body* (lines after the deprecate block) still reads "Applies four cheap checks..." with the original `WHEN TO USE` framing intact. An agent that reads past the first paragraph gets contradictory steering. The right shape would have been to truncate the docstring to the deprecation notice + a one-liner pointer; or to delete the body. Recoverable in Phase 2 deletion; not blocking. Same shape risk on `write_draft_page` but its body is shorter and doesn't re-teach use, so it's only mild contradiction.
- task() reviewer-dispatch rule → MISSING (correctly acknowledged in PR body as PR2 scope; brief contemplated this, gap is tracked).

**Wrong-shape findings**: above (`validate_page_draft` half-deprecated).

**Missing findings**: task() rule deferred — explicitly tracked.

**Scope creep**: none.

**Verdict**: minor-followup (truncate `validate_page_draft` docstring body in Phase 2 alongside code deletion).

---

### PR #258 — `feat(mkdocs): auto-render References block from inline footnotes`

**Spirit**: the runtime renders structural artefacts (CC-F / Karpathy pattern). Compile agent writes inline footnotes; the hook renders the bottom block. Drops a class of agent error and prompt budget.

**Brief items in scope**: scan body for `[^msg-*]` markers; emit `## References` resolving against `raw/`; preserve legacy pages; strip body-authored `## Sources` H2 with warning; legacy pages with manual `## References` or footnote definitions bypass the auto-render.

**Implementation check**:
- Inline-footnote scan + ordered-unique → SHIPPED (`mkdocs_hooks.py` L83-96).
- raw/ index resolver → SHIPPED with stable hash key (L48-60).
- Auto-render only when no existing `## References` H2 AND no inline footnote definitions → SHIPPED (L506-511 guard).
- Legacy preservation → SHIPPED (the AND on the guard).
- `## Sources` strip + warn → SHIPPED (`_strip_sources_h2` L99-115).
- Unresolvable hash marker (no silent drop) → SHIPPED.

**Wrong-shape findings**: none. The hook's collision-guard logic (`_H2_REFERENCES_RE` AND `_FOOTNOTE_DEF_RE`) is exactly the right shape — it preserves legacy and prevents double-render.

**Missing findings**: none.

**Scope creep**: none.

**Verdict**: clean.

---

### PR #259 — `refactor(prompts): PR1 mechanical reorg + dedupe (no behavior change)`

**Spirit**: Q0.4 staging — PR1 is mechanical (reorg + dedupe + jargon/capability sweeps + boy-scout cleanups). PR2 owns content edits. PR1 must read line-by-line as "same content, different shape" plus a few targeted deletions of jargon and dead-tool refs.

**Brief items in scope**: Q0.2 mechanics-first reorg; Q2.2 + Q3.2 + Q11.1 + Q13.1 + Q6.6 dedupes; CC-A jargon sweep (`current-truth`, `message_touched_pages`, `V12-U3`); CC-J capability sweep (cut "NEVER modify /raw/" + bookkeeping bans, retain slim `last_compiled`); CC-H boy-scout (phantom `wiki_merge_pages`, dead `write_draft_page` + `validate_page_draft` mentions); A1 hoist of "Pick the terminal outcome before typing".

**Implementation check** (verified at squash commit `beabe6d`):
- Section reorder mechanics-first → SHIPPED. `<background>` → `<chronological_scope>` → `<tool_guidance>` → `<workflow>` → ... matches Q0.2 target.
- Six-step batch enum cut from `<concept_vs_thread>` → SHIPPED.
- Per-domain flavor list cut from `<expert_questions>` → SHIPPED.
- `<self_review>` item 5 reviewer-dup cut → SHIPPED.
- Bookkeeping bans cut from `<workflow>` and `<tool_guidance>` → SHIPPED.
- Slim `NEVER write last_compiled in frontmatter — it is stamped automatically after you return.` → SHIPPED in Hard rules (L957-958).
- "NEVER modify /raw/" Hard rule cut → SHIPPED.
- Phantom `wiki_merge_pages` removed; `merge_candidates` field + `apply_merge_candidate.py` pointer added → SHIPPED.
- `write_draft_page` + `validate_page_draft` mentions cut from prompt body → SHIPPED.
- A1 hoist "Pick the terminal outcome before typing" → SHIPPED PROMINENTLY at L80, the second sentence inside `### Decision: terminal outcomes` directly under `<workflow>`. Load-bearing position.
- CC-A jargon sweep — verified zero hits at squash commit on `current-truth`, `message_touched_pages`, `V12-U3`, `coordinator`, `middleware`, `ContextVar`, `deepagents`, `summary-stale-date`. Replacements ("tracked automatically", "stamped automatically after you return", "recorded automatically after you return") preserve meaning because the surrounding sentence specifies WHAT happens automatically (e.g. L654 "per-message raw paths are tracked automatically", L957 "it is stamped automatically after you return", L275 "message→page links are recorded automatically"). The agent still understands the rule; it just doesn't see the internal catalog name.

**Wrong-shape findings**: none on the merged commit. (My initial read flipped because I sampled an uncommitted PR2 working branch by mistake; corrected against `git show beabe6d:src/compile/prompts.py`.)

**Missing findings**: none.

**Scope creep**: none. The diff is mechanical: sorted-line diff against parent is empty per commit-3 message; PR1 didn't sneak content edits in. Test alignment in commit 4 is necessary because commit 1 cut renumbered examples — acknowledged in PR body.

**Verdict**: clean.

---

## Cross-cutting findings

1. **Staging discipline holds**. The three-way "NS-ahead, runtime-supports, prompt-still-teaches" staging works in practice. NS drops `## TL;DR` and `## Sources` cleanly; runtime fallbacks (PR #255 + #258) ship; prompt still teaches both — but the prompt teaching is *additive over* runtime support, not contradictory. Same for slug-form: NS ratifies email-canonical, prompt examples carry it forward in PR2. No reader is misled at any stage.

2. **CC-A sweep replacement quality**. The post-sweep "tracked automatically" / "stamped automatically" phrasing was the worry; held up. Each rephrased sentence still specifies the noun (`per-message raw paths`, `last_compiled`, `message→page links`) so the agent doesn't lose the referent.

3. **Capability-sweep retained the slim rule**. `NEVER write last_compiled in frontmatter` survives in Hard rules at L957-958 of the merged file — the only one of the bookkeeping bans that's needed because the agent CAN edit frontmatter via `edit_file`. Brief got this one right.

4. **Boy-scout cleanups paid for themselves**. Phantom tool, two deprecated tools, and three dead refs gone. Nothing else got cut along the way (verified the slim `last_compiled` rule, the strikethrough ban that moved from `<concept_vs_thread>` to canonically live in `<revision_style>`, and the broken-wikilink Example renumbered correctly).

5. **task() reviewer-rule gap is tracked**. PR #257 body says it; coverage matrix says it; synthesis sequencing says it. PR2 will land `## Reviewer call rule` in `<tool_guidance>`; current `<workflow>` step 7 carries the rule until then.

6. **Worker-level signal**: workers stayed in scope. The verification doc was right that no PR contains a sneaky out-of-scope add. No "Worker N has a tendency to drift" pattern surfaced.

---

## Recommended actions (severity-ordered)

1. **Phase 2 cleanup**: when deleting `validate_page_draft` def, also collapse the docstring above it (currently "deprecated" header sits over an intact `WHEN TO USE` body — minor but audit-flaggable). Same for `write_draft_page`. Severity: low.

2. **PR2 gates** to honor (no action this review, just reminders): land `## Reviewer call rule` heading in `<tool_guidance>` (Q6.4 follow-through); drop `## TL;DR` template references at L46/L342 of the PR1-merged prompt; drop `## References` template at L531-L544; drop the `## Sources` warning sentence at the same area.

3. **Coverage-matrix housekeeping**: still references mkdocs PR as `#256` (actual `#258`) — already flagged in earlier verification doc; non-blocking.

---

## What this review surfaced that earlier verification did not

- **Quality of CC-A *replacements*, not just absence of jargon terms**. Earlier verification confirmed zero hits on the term list. This review checked whether the replacement phrasing preserved the rule's meaning. It does — the surrounding sentence in each of the three replaced spots explicitly names the artefact (raw paths, last_compiled, message→page links), so the agent doesn't lose the rule when the catalog name is gone.
- **Hoist *position* quality, not just presence**. Earlier verification confirmed the hoist landed in `<workflow>`. This review confirms it's at L80 — the second sentence of `### Decision: terminal outcomes`, immediately below the four-outcome list — exactly where commit-discipline should anchor a reader.
- **`validate_page_draft` half-deprecation**. Earlier verification noted the docstring update; this review notices the docstring's *body* still teaches the original use. That's a contradictory steering signal an agent could follow if it reads past the first paragraph. The matching `write_draft_page` body is short enough to be benign; `validate_page_draft` is the one to watch.
- **Subtle precedence quirk in `_extract_tldr`**: a page with `## TL;DR` heading + empty body under it now falls through to the lead paragraph. Not a regression (more useful behavior than the old None), but an unstated change worth noting.
- **The `## Reviewer call rule` heading is still in WIP** (PR2 working branch), not in PR #259's merged state. Consistent with the brief and the coverage matrix; just confirming the staging held.

Report saved to /Users/amtagrwl/git/email-knowledge-base/docs/audits/intent-review-2026-04-29.md
