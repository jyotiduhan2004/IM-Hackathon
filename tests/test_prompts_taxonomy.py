"""Assert COMPILER_SYSTEM_PROMPT covers Tier A taxonomy + Phase A U3
catalog-truth guidance.

Tier A wholesale-rewrote the prompt into tagged sections
(`<background>`, `<workflow>`, `<page_types>`, ...). Phase A U3 layered
in `<chronological_scope>` + `<decision_tree>` and swapped `sources:`
for `source_threads:` semantics. v10-U4 merged `<decision_tree>` back
into `<workflow>` to remove duplicated prose — decision guidance now
lives in `<workflow>` under a `### Decision: terminal outcomes`
subsection. Tests assert both generations of markers the agent still
needs.
"""

from __future__ import annotations

from pathlib import Path

from src.agent.prompts import COMPILER_SYSTEM_PROMPT

_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMPILE_ALL_SRC = (_REPO_ROOT / "scripts" / "compile_all.py").read_text(encoding="utf-8")


def _workflow_block() -> str:
    """Return the body of the `<workflow>` section (for assertions that
    used to target `<decision_tree>` before v10-U4 merged them)."""
    start = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    end = COMPILER_SYSTEM_PROMPT.find("</workflow>")
    assert start != -1 and end != -1 and end > start, "workflow section missing"
    return COMPILER_SYSTEM_PROMPT[start:end]


def test_tagged_sections_present() -> None:
    """All structured sections appear in the prompt.

    v10-U4: `<decision_tree>` was merged into `<workflow>` — it must NOT
    be present as a separate tag anymore. The decision content now lives
    inside `<workflow>`; see `test_decision_tree_absent_after_merge` and
    the decision-content tests below.
    """
    required = (
        "<background>",
        "<chronological_scope>",
        # V12 sections (U1-U4) — complete canonical inventory so a later
        # rename or accidental deletion fails this test, not just the
        # more-specific per-section tests.
        "<concept_vs_thread>",
        "<expert_questions>",
        "<inline_citations>",
        "<revision_style>",
        "<workflow>",
        "<page_types>",
        "<section_titles>",
        "<tool_guidance>",
        "<sources_management>",
        "<todo_rule>",
        "<self_review>",
        "<editorial_notes>",
        "<few_shots>",
    )
    missing = [tag for tag in required if tag not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt missing sections: {missing}"


def test_decision_tree_absent_after_merge() -> None:
    """v10-U4: `<decision_tree>` was merged into `<workflow>` to kill
    duplicated prose (trivial_skip / already_captured definitions, the
    terminal-outcome mandate, the "edit / create a page" bullet). The
    tag must be gone; regression-proof the merge."""
    assert "<decision_tree>" not in COMPILER_SYSTEM_PROMPT
    assert "</decision_tree>" not in COMPILER_SYSTEM_PROMPT


def test_decision_guidance_lives_in_workflow() -> None:
    """v10-U4: after the merge, the `<workflow>` section must carry the
    full decision guidance — the three terminal outcomes, the
    aggressive-`already_captured` nudge, the `sibling email`/`same
    thread`/`near-duplicate` triggers, the investigatory-insights
    carve-out, and the `waffle` anti-pattern label. Without this the
    agent loses its decision ladder along with the merged tag."""
    block = _workflow_block()
    lowered = block.lower()
    # Three terminal outcomes named.
    assert "trivial_skip" in block
    assert "already_captured" in block
    assert "cites this email's thread" in block
    # "Aggressive" nudge + triggers.
    assert "aggressive" in lowered
    assert "sibling email" in lowered or "same thread" in lowered
    assert "near-duplicate" in lowered or "zero new facts" in lowered
    # Investigatory-insights carve-out.
    assert "investigatory" in lowered
    assert "do not close the loop" in lowered or "do not satisfy" in lowered
    # Waffle anti-pattern named.
    assert "waffle" in lowered
    # The "leave evidence" carve-out is still present (per CC-A, the
    # internal `message_touched_pages` catalog name was rephrased to
    # passive automatic framing; the rule itself stays).
    assert "leave evidence" in lowered


def test_workflow_prompt_under_budget() -> None:
    """Informational token-budget guard.

    PR2 (2026-04-28 prompt-review): added `<content_floor>`,
    `<voice>`, `<related_links>`, six new few-shot examples
    (selective-wikilinking, Q-delta, forward-update, chronological
    DON'T, bug-page shape, meta-insight) and expanded
    `<tool_guidance>` with the reviewer-call rule + check_my_work
    contract. Net growth pushed the ceiling to ~60k chars; the
    procedure cuts (Q6-meta CC-G hybrid) and the `<expert_questions>`
    5W move to reviewer offset some of it but not all. Raise only on
    a deliberate feature that genuinely needs more space."""
    assert len(COMPILER_SYSTEM_PROMPT) < 60000, (
        f"prompt grew to {len(COMPILER_SYSTEM_PROMPT)} chars; PR2 "
        "baseline is ~58k. If the growth is deliberate, raise this "
        "ceiling; otherwise it's probably re-introduced duplication."
    )


def test_section_titles_rule_bans_dates_and_names() -> None:
    """Bug F: H2 titles must be canonical structure, not dated per-email
    entries. The rule must name the anti-pattern AND the fix (inline
    dates in bullets), not just one or the other — otherwise the model
    has the menu but not the translation."""
    start = COMPILER_SYSTEM_PROMPT.find("<section_titles>")
    end = COMPILER_SYSTEM_PROMPT.find("</section_titles>")
    assert start != -1 and end != -1 and end > start
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # The rule forbids the bad shape and surfaces the canonical vocab.
    lowered = block.lower()
    assert "never bake a date" in lowered
    assert "canonical" in lowered
    # At least two of the canonical sections must be listed explicitly so
    # the agent has a concrete menu to reach for.
    canonical_examples = ("## Current state", "## Testing results", "## Recent changes")
    present = [s for s in canonical_examples if s in block]
    assert len(present) >= 2, f"need ≥2 canonical H2 examples; got {present}"
    # BAD/GOOD worked example keeps the rule concrete — verify both sides.
    assert "BAD" in block and "GOOD" in block


def test_editorial_notes_teaches_three_outcomes() -> None:
    """Reviewer returns editorial_notes as a free-form channel. The
    writer must know to read each note and classify it into one of
    three buckets: patch / log_insight / acknowledge. Without this
    teaching the channel is observability-only."""
    start = COMPILER_SYSTEM_PROMPT.find("<editorial_notes>")
    end = COMPILER_SYSTEM_PROMPT.find("</editorial_notes>")
    assert start != -1 and end != -1 and end > start
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # Three outcome branches named:
    assert "Actionable" in block  # patch
    assert "Out of scope" in block and "log_insight" in block  # log_insight
    assert "Speculative" in block or "acknowledge" in block  # acknowledge
    # Anti-loop guard — don't re-patch if the next reviewer cycle
    # surfaces the same note.
    assert "don't loop" in block.lower() or "not a gatekeeper" in block.lower()


def test_reviewer_call_guidance_points_to_editorial_notes() -> None:
    """PR2 (2026-04-28 prompt-review Q6-meta CC-G hybrid): the 9-step
    procedure was replaced with an outcome-based lean procedure +
    examples. The reviewer-call rule moved into `<tool_guidance>`
    under `## Reviewer call rule`. The section must name-drop
    `<editorial_notes>` so the writer knows where to go for nuance.
    """
    start = COMPILER_SYSTEM_PROMPT.find("<tool_guidance>")
    end = COMPILER_SYSTEM_PROMPT.find("</tool_guidance>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    assert "Reviewer call rule" in block
    assert "editorial_notes" in block


def test_taxonomy_covers_three_plus_two() -> None:
    """3+2 taxonomy (post glossary removal 2026-04-24): topic / system /
    policy visible; decision / person lazy.

    Glossary was removed because the regex extractor produced misleading
    definitions (`BL = "Monolith + Modular"`, `DAU = "January 8, 2026"`).
    Reintroduce via an LLM pass if a real demand appears.
    """
    visible = ("**topic**", "**system**", "**policy**")
    lazy = ("**decision**", "**person**")
    for v in visible:
        assert v in COMPILER_SYSTEM_PROMPT, f"missing visible type: {v}"
    for lz in lazy:
        assert lz in COMPILER_SYSTEM_PROMPT, f"missing lazy type: {lz}"
    # Regression guard: glossary must NOT come back.
    assert "**glossary**" not in COMPILER_SYSTEM_PROMPT
    assert "page_type: glossary" not in COMPILER_SYSTEM_PROMPT


def test_new_status_vocabulary_taught() -> None:
    """Statuses the agent WRITES: active / superseded / archived."""
    assert "active" in COMPILER_SYSTEM_PROMPT
    assert "superseded" in COMPILER_SYSTEM_PROMPT
    assert "archived" in COMPILER_SYSTEM_PROMPT


def test_legacy_status_compat_talk_removed() -> None:
    """Phase A U3: prompt should NOT instruct the agent to accept legacy
    `current` / `contested` statuses. Compatibility is for readers, not
    writers — and mentioning them tempts the LLM to emit them.
    """
    assert "both old and new vocabularies" not in COMPILER_SYSTEM_PROMPT
    assert "legacy statuses" not in COMPILER_SYSTEM_PROMPT.lower()


def test_sandbox_boundaries_are_named() -> None:
    """Prompt mentions the virtual `/raw/` + `/wiki/` roots so the agent
    knows where to send paths."""
    assert "/raw/" in COMPILER_SYSTEM_PROMPT
    assert "/wiki/" in COMPILER_SYSTEM_PROMPT


def test_forbidden_tools_not_mentioned() -> None:
    """Tier A removes references to tools the agent cannot call
    (mark_as_compiled, stamp_page_compiled_at, update_wiki_index).
    Prompt leakage of those names wastes tokens and confuses the model.
    """
    forbidden = (
        "mark_as_compiled",
        "stamp_page_compiled_at",
        "update_wiki_index",
        "append_to_log",
    )
    leaked = [t for t in forbidden if t in COMPILER_SYSTEM_PROMPT]
    assert not leaked, f"prompt mentions forbidden/coordinator-only tools: {leaked}"


def test_dead_constants_removed() -> None:
    """Phase A U3: CLASSIFY_EMAIL_PROMPT and SUPERSESSION_DETECTION_PROMPT
    were dead code — imported nowhere. They must not come back."""
    from src.agent import prompts as prompts_module

    assert not hasattr(prompts_module, "CLASSIFY_EMAIL_PROMPT")
    assert not hasattr(prompts_module, "SUPERSESSION_DETECTION_PROMPT")


def test_reviewer_subagent_invocation_example_present() -> None:
    """Few-shots should model `task(subagent_type="reviewer", ...)` so
    the LLM recognises the idiom."""
    assert 'subagent_type="reviewer"' in COMPILER_SYSTEM_PROMPT


def test_create_entities_guidance_present() -> None:
    """`create_entities` is the only path to make people pages — call this
    out in the prompt."""
    assert "create_entities" in COMPILER_SYSTEM_PROMPT


def test_cc_only_weak_evidence_rule_preserved() -> None:
    """Weak-evidence guidance for person pages still matters — the Tier A
    rewrite keeps it, just in compressed form."""
    # Either the new-style hint survived or the old "CC-only" marker did.
    assert "weak" in COMPILER_SYSTEM_PROMPT.lower()


def test_source_threads_replaces_sources_frontmatter() -> None:
    """Phase A U3: pages cite at THREAD level. The prompt should teach
    `source_threads:` and explicitly forbid per-message `sources:` writes
    and list-replacement on edits."""
    assert "source_threads" in COMPILER_SYSTEM_PROMPT
    assert "NEVER write `sources:`" in COMPILER_SYSTEM_PROMPT
    assert "NEVER replace the list" in COMPILER_SYSTEM_PROMPT


def test_no_per_message_raw_paths_in_frontmatter_template() -> None:
    """The frontmatter template example should show `source_threads:`
    with a thread_id, not a `raw/YYYY-MM-DD_*.md` path."""
    template_marker = "## Frontmatter template"
    assert template_marker in COMPILER_SYSTEM_PROMPT
    template = COMPILER_SYSTEM_PROMPT.split(template_marker, 1)[1]
    assert "raw/" not in template, (
        "frontmatter template leaks per-message raw paths; use source_threads instead"
    )


def test_already_captured_outcome_taught() -> None:
    """Phase A U3: `already_captured` is a no-op outcome distinct from
    `trivial_skip`. The decision tree must teach why forcing an edit
    "for evidence" is wrong (PR1 CC-A dropped the internal
    `message_touched_pages` catalog name; the rule itself stays in
    passive-automatic framing)."""
    assert "already_captured" in COMPILER_SYSTEM_PROMPT
    assert "trivial_skip" in COMPILER_SYSTEM_PROMPT
    assert "leave evidence" in COMPILER_SYSTEM_PROMPT.lower()


def test_already_captured_trigger_conditions_spelled_out() -> None:
    """Missed `already_captured` calls on substantive follow-ups are
    the dominant remaining failure class (agent loiters then bails
    without a terminal outcome). The decision block (post-v10-U4:
    inside `<workflow>`) must spell out the trigger conditions
    explicitly. Context lives in the PR description + cycle summary,
    NOT in the prompt itself (the prompt is timeless — no Cycle-N /
    Bug-letter references)."""
    block = _workflow_block()
    lowered = block.lower()
    # Nudge telling the agent to PICK the call rather than loiter.
    assert "aggressive" in lowered
    # Specific trigger: sibling email in the same thread.
    assert "sibling email" in lowered or "same thread" in lowered
    # Self-check: if your edit would be near-duplicate / zero new, stop.
    assert "near-duplicate" in lowered or "zero new facts" in lowered


def test_prompt_has_no_temporal_project_leaks() -> None:
    """Per Anthropic's effective-prompting guidance: the prompt is
    timeless. Cycle-N counts, bug-letter labels, and PR numbers
    belong in the PR description + case studies, not in the system
    prompt. The agent doesn't know what Cycle 7 is; those tokens
    waste context AND will rot as the project ages."""
    import re

    leaks: list[str] = []
    # "Cycle 7" / "Cycle 12" / etc.
    leaks.extend(re.findall(r"\bCycle \d+", COMPILER_SYSTEM_PROMPT))
    # "Bug A" / "Bug K" / etc. (single letter after "Bug ")
    leaks.extend(re.findall(r"\bBug [A-Z]\b", COMPILER_SYSTEM_PROMPT))
    # PR refs like "#142" (3-digit) — too specific for a timeless prompt
    leaks.extend(re.findall(r"\B#\d{3,}", COMPILER_SYSTEM_PROMPT))
    assert not leaks, f"prompt contains temporal/project leaks: {leaks}"


def test_chronological_scope_framing_present() -> None:
    """Phase A U3: agent must be told not to leak future-thread info
    into a past-message compile. `leave it alone` is the load-bearing
    nudge that preserves later-batch content."""
    lowered = COMPILER_SYSTEM_PROMPT.lower()
    assert "chronological" in lowered
    assert "leave it alone" in lowered


def test_workflow_ladder_thread_first() -> None:
    """Inside <workflow>, the investigation sequence must be
    get_thread_context → resolve_page → read_file."""
    start = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    end = COMPILER_SYSTEM_PROMPT.find("</workflow>")
    assert start != -1 and end != -1 and end > start
    workflow = COMPILER_SYSTEM_PROMPT[start:end]
    gtc_idx = workflow.find("get_thread_context")
    resolve_idx = workflow.find("resolve_page")
    read_idx = workflow.find("read_file")
    assert gtc_idx != -1 and resolve_idx != -1 and read_idx != -1
    assert gtc_idx < resolve_idx < read_idx, (
        "workflow ladder should teach get_thread_context → resolve_page → read_file, in that order"
    )


def test_people_pages_live_at_wiki_people() -> None:
    """Phase A U3: writes go to `wiki/people/` only. Transitional
    `wiki/entities/` guidance should be gone."""
    assert "/wiki/people/" in COMPILER_SYSTEM_PROMPT
    assert "/wiki/entities/" not in COMPILER_SYSTEM_PROMPT
    assert "currently filed as" not in COMPILER_SYSTEM_PROMPT


def test_terminal_decision_requirement_named() -> None:
    """Cycle 3 Bug D (waffle fix): prompt must explicitly require the
    agent to commit to one terminal outcome per email. "Investigate
    thoroughly then bail" is the anti-pattern to kill."""
    assert "commit to one terminal outcome" in COMPILER_SYSTEM_PROMPT
    # `waffle` names the anti-pattern — naming it helps the model recognise it.
    assert "waffle" in COMPILER_SYSTEM_PROMPT.lower()


def test_three_terminal_outcomes_listed() -> None:
    """The three allowed terminal outcomes must be listed clearly in
    the decision block (post-v10-U4: inside `<workflow>`) so the agent
    can map its work onto one of them:
      1) content-page write/edit that cites the thread
      2) `log_insight("trivial_skip", ...)`
      3) `log_insight("already_captured", ...)`
    """
    block = _workflow_block()
    # All three terminal outcomes named in the workflow.
    assert "trivial_skip" in block
    assert "already_captured" in block
    # Content edit terminal outcome — phrased as a write/edit/patch
    # that cites the thread. "cites this email's thread" is the test
    # anchor because that exact phrasing rules out "investigatory"
    # tool calls like get_thread_context or log_insight.
    assert "cites this email's thread" in block


def test_investigatory_insights_marked_non_terminal() -> None:
    """Investigatory insight categories (topic_merge_candidate,
    structure_suggestion, question_for_human, prompt_ambiguity,
    tool_gap, supersession_doubt) must be explicitly labelled as
    non-terminal in the decision block (post-v10-U4: inside
    `<workflow>`). Otherwise the agent treats logging a
    `topic_merge_candidate` as "done" and the email stays pending."""
    block = _workflow_block()

    # The investigatory categories are named at decision time in the
    # workflow, not just in <tool_guidance>, so the agent sees them
    # when it's picking its terminal outcome.
    investigatory = (
        "topic_merge_candidate",
        "structure_suggestion",
        "question_for_human",
        "prompt_ambiguity",
        "tool_gap",
        "supersession_doubt",
    )
    missing = [cat for cat in investigatory if cat not in block]
    assert not missing, (
        f"workflow should name investigatory categories to mark them "
        f"non-terminal; missing: {missing}"
    )

    # And there must be explicit language calling them investigatory /
    # non-terminal so the model doesn't read the list as a menu of
    # terminal options.
    lowered = block.lower()
    assert "investigatory" in lowered
    assert "do not close the loop" in lowered or "do not satisfy" in lowered


def test_check_my_work_taught_with_correct_contract() -> None:
    """PR2 (Q6-meta CC-G hybrid): the 9-step procedure became a lean
    outcome-based section + examples. The check_my_work contract
    (clean / blocked / gate-rejected) moved into `<tool_guidance>`
    under the Quality block. Same three return shapes must be
    taught — including the gate-rejected one ("Rejected: call
    check_my_work only after…") — otherwise the agent loops retrying
    the same gate with no write in between."""
    assert "check_my_work" in COMPILER_SYSTEM_PROMPT, "check_my_work must be named in the prompt"
    # All three return shapes taught:
    assert "clean" in COMPILER_SYSTEM_PROMPT and "blocked" in COMPILER_SYSTEM_PROMPT
    assert "Rejected: call check_my_work only after" in COMPILER_SYSTEM_PROMPT, (
        "gate-rejection shape (plain ToolMessage content) must be taught so "
        "the agent doesn't loop retrying"
    )
    lowered = COMPILER_SYSTEM_PROMPT.lower()
    # Recovery on gate rejection: go write a page, don't retry the gate.
    assert "go write a page first" in lowered or "write a page first" in lowered


def test_workflow_has_terminal_decision_check() -> None:
    """The workflow's pre-return checklist must tell the agent to
    verify each email has a terminal outcome before returning.

    PR2 (Q6-meta CC-G hybrid): the 9-step procedure was replaced
    with a lean outcome-based section that ends with a "Pre-return
    checklist". The first checklist item is the terminal-outcome
    self-audit; the section also names the Devin "take one beat"
    one-liner. Without these the agent has no exit gate."""
    workflow = _workflow_block()
    # Devin one-liner OR the checklist anchor — either survives if a
    # later edit reshuffles the procedure prose.
    assert "terminal outcome" in workflow
    assert (
        "Pre-return checklist" in workflow
        or "Before you return" in workflow
        or "Before returning" in workflow
        or "before you return" in workflow
    )


def test_wikilink_recovery_guidance_present() -> None:
    """Cycle 4 Bug E: when `check_my_work` returns blocked with a
    `broken-wikilink` issue, the agent must recover by calling
    `create_entities` to create the missing person stub, then re-run
    `check_my_work`. Three clauses must co-occur: the blocker name,
    the recovery tool, and the retry instruction — otherwise the agent
    just bails after 3-9 iterations as in Cycle 4."""
    prompt = COMPILER_SYSTEM_PROMPT
    assert "broken-wikilink" in prompt
    assert "create_entities" in prompt
    # "retry" co-occurs with create_entities guidance — the model must
    # see "retry" / "re-run" semantics near the recovery instruction.
    lowered = prompt.lower()
    assert "retry" in lowered or "re-run" in lowered


def test_wikilink_recovery_budget_named() -> None:
    """The recovery loop needs a budget so the agent doesn't spin
    forever. Prompt must mention the 3-retry cap explicitly."""
    assert "3 retry" in COMPILER_SYSTEM_PROMPT or "up to 3" in COMPILER_SYSTEM_PROMPT


def test_wikilink_recovery_example_present() -> None:
    """Cycle 4 Bug E: the few-shots must model the recovery flow end-to-
    end (write_file → reviewer blocks → create_entities → retry review).
    Without a worked example the guidance is abstract and the model
    defaults to its priors (bail).

    PR1 renumbered the few-shots after dropping the deprecated
    `write_draft_page` example — the broken-wikilink recovery is now
    Example 8 (was Example 9). Find by title rather than number so a
    future renumber doesn't silently regress."""
    title = "### Example 8 — Blocked by broken wikilink"
    assert title in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find(title)
    end = COMPILER_SYSTEM_PROMPT.find("### Example 9", start)
    if end == -1:
        end = COMPILER_SYSTEM_PROMPT.find("</few_shots>", start)
    example = COMPILER_SYSTEM_PROMPT[start:end]
    # The worked flow must show the block, the recovery, and the retry.
    assert "broken-wikilink" in example or "broken wikilink" in example
    assert "create_entities" in example
    assert "retry" in example.lower() or "re-run" in example.lower()


def test_prompt_has_no_internals() -> None:
    """v9-U1: the prompt must not leak internal implementation vocabulary
    the LLM has no mental model for. `coordinator`, `ContextVar`,
    `middleware`, `AgentMiddleware` are all names for *our* deterministic
    plumbing. Naming them in the prompt wastes tokens and risks the model
    trying to reason about internals it cannot see. Rewrite any such
    reference as a direct prohibition ("NEVER call X — it's not a tool")
    or passive/automatic framing ("stamped automatically after you
    return")."""
    forbidden = ("coordinator", "contextvar", "middleware", "agentmiddleware")
    prompt_lower = COMPILER_SYSTEM_PROMPT.lower()
    leaked = [word for word in forbidden if word in prompt_lower]
    assert not leaked, (
        f"prompt leaks internal vocabulary: {leaked}. "
        "Rewrite as direct prohibition or passive automatic framing."
    )


def test_topic_suggested_h2_sections_taught() -> None:
    """PR2 (2026-04-28 prompt-review Q7.1, Q7.2): the universal H2
    floor for topic pages drops `## Summary` (lead paragraph IS the
    summary) and `## Key decisions` (decisions live on their own
    pages and are surfaced via wikilinks). The runtime auto-renders
    `## References` from inline footnotes. The remaining H2 floor:
    `## Why it matters` → `## Current state` → `## Recent changes`
    → `## Open questions` → `## Related`. Pin the heading list so
    future edits can't quietly drop the menu."""
    suggested = (
        "## Why it matters",
        "## Current state",
        "## Recent changes",
        "## Open questions",
        "## Related",
    )
    missing = [s for s in suggested if s not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt must name topic universal H2 floor; missing: {missing}"
    # Regression guard for Q7.2 (lead paragraph IS the summary): the
    # `## Summary` H2 must not come back.
    assert "## Summary" not in COMPILER_SYSTEM_PROMPT, (
        "PR2 (Q7.2) dropped `## Summary` H2 — lead paragraph IS the summary"
    )
    # Regression guard for Q7.2 (drop ## TL;DR H2):
    assert "## TL;DR" not in COMPILER_SYSTEM_PROMPT, (
        "PR2 (Q7.2) dropped `## TL;DR` H2 — runtime parses lead paragraph"
    )


def test_system_suggested_h2_sections_taught() -> None:
    """v11-U7: system pages have a canonical H2 shape suggested in the
    prompt. `## Role` and `## Active related topics` are the
    system-specific ones; `## Summary` / `## References` / `## Related
    pages` overlap with topic. Pin them so the prompt mirrors the
    template."""
    system_specific = ("## Role", "## Active related topics", "## Dependencies", "## Known issues")
    missing = [s for s in system_specific if s not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt must name system suggested H2 sections; missing: {missing}"


def test_universal_h2_floor_framing_present() -> None:
    """PR2 (2026-04-28 prompt-review Q7.1): the H2 list is now framed
    as a "Universal H2 floor" (a direction, not a law) — the reviewer
    judges fit. Old "Suggested H2 sections" / "Required H2 sections"
    framings should not be back."""
    assert "Universal H2 floor" in COMPILER_SYSTEM_PROMPT, (
        "PR2 (Q7.1) expects 'Universal H2 floor' framing in <page_types>"
    )
    # Old "Required" / "MUST NOT omit" / "drift breaks validation" framing
    # must be gone — that vocabulary teaches the agent the wrong contract.
    assert "Required H2 sections" not in COMPILER_SYSTEM_PROMPT, (
        "v11-U7 dropped 'Required H2 sections' framing — use 'Universal H2 floor'"
    )
    assert "MUST NOT do\nis omit the heading" not in COMPILER_SYSTEM_PROMPT
    assert "drift breaks validation" not in COMPILER_SYSTEM_PROMPT


def test_thread_subject_vocabulary_named_as_antipattern() -> None:
    """v11-U7: the prompt must name concrete thread-subject H2 examples
    so the agent recognises the failure pattern. Without examples the
    nudge is abstract and the model defaults to its priors (templating
    H2s off the thread Subject line)."""
    examples = ("Launch Announcement", "Bug report", "QA Testing Results")
    present = [ex for ex in examples if ex in COMPILER_SYSTEM_PROMPT]
    assert len(present) >= 2, (
        f"prompt must name at least 2 thread-subject H2 anti-patterns; got {present}"
    )
    # The reviewer rule names must be referenced so the agent knows
    # what verdict to expect.
    assert "filing_cabinet" in COMPILER_SYSTEM_PROMPT
    assert "structure_mismatch" in COMPILER_SYSTEM_PROMPT


def test_lead_paragraph_requirement_taught() -> None:
    """v9-U1: validator warns when a topic or policy page lacks a
    ≥2-sentence lead paragraph before the first H2. The prompt must
    teach that shape. Three load-bearing tokens: the count (`2`), the
    scope (`lead paragraph` or `first H2`), and the tense (`present
    tense`) — without tense, the model writes past-tense recaps."""
    prompt = COMPILER_SYSTEM_PROMPT
    assert "lead paragraph" in prompt.lower(), "prompt must use the phrase 'lead paragraph'"
    # ≥ 2 sentences, specifically — not "a paragraph" or "a line".
    assert "≥ 2" in prompt or "≥2" in prompt or "two sentence" in prompt.lower(), (
        "prompt must name the 2-sentence minimum"
    )
    assert "present tense" in prompt.lower(), (
        "prompt must specify present-tense framing for the lead paragraph"
    )


def test_domain_frontmatter_block_present() -> None:
    """v9-U1: add `<domain_frontmatter>` teaching the 8 canonical
    domains. Without this, 399 pages lacked `domain:` as of Cycle 9.
    The block must list each canonical slug explicitly so the model
    has a concrete menu."""
    assert "<domain_frontmatter>" in COMPILER_SYSTEM_PROMPT
    assert "</domain_frontmatter>" in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find("<domain_frontmatter>")
    end = COMPILER_SYSTEM_PROMPT.find("</domain_frontmatter>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # All 8 canonical domain slugs named in the block.
    canonical = (
        "buyer-experience",
        "seller-experience",
        "marketplace-discovery",
        "platform-reliability",
        "trust-safety",
        "ai-automation",
        "growth-monetization",
        "engineering-productivity",
    )
    missing = [d for d in canonical if d not in block]
    assert not missing, (
        f"<domain_frontmatter> must list all 8 canonical domains; missing: {missing}"
    )


def test_example_1_exhibits_required_shape() -> None:
    """PR2 (2026-04-28 prompt-review Q7.1, Q7.2, Q12.1): Example 1 is
    the canonical-shape worked example. It must show `domain:` +
    `owner:` frontmatter, a ≥2-sentence lead paragraph (no
    `## Summary` H2), and the universal H2 floor sections so the
    model has a full template to pattern-match against."""
    start = COMPILER_SYSTEM_PROMPT.find("### Example 1")
    end = COMPILER_SYSTEM_PROMPT.find("### Example 2")
    assert start != -1 and end != -1 and end > start
    example = COMPILER_SYSTEM_PROMPT[start:end]
    assert "domain:" in example, "Example 1 must show `domain:` frontmatter"
    assert "owner:" in example, "Example 1 must show `owner:` frontmatter (PR2 Q3.4)"
    # Each H2 in the universal H2 floor for topic pages.
    for section in (
        "## Why it matters",
        "## Current state",
        "## Recent changes",
        "## Open questions",
    ):
        assert section in example, f"Example 1 missing required section heading: {section}"
    # Regression guard: PR2 (Q7.2) dropped `## Summary` H2.
    assert "## Summary" not in example, (
        "PR2 (Q7.2) dropped `## Summary` H2 — Example 1 must not regress"
    )


def test_prompt_domain_list_matches_compiler() -> None:
    """v9-U1: the prompt lists 8 canonical domains in <domain_frontmatter>.
    The source of truth for domain slugs is `src.wiki.domains._DOMAINS`;
    if someone adds or renames a domain there without updating the prompt,
    the agent's world-model drifts from the validator's world-model.
    Pin the invariant."""
    from src.wiki.domains import _DOMAIN_BY_SLUG

    for slug in _DOMAIN_BY_SLUG:
        assert slug in COMPILER_SYSTEM_PROMPT, (
            f"domain slug {slug!r} missing from prompt — keep prompt "
            "in sync with src.wiki.domains._DOMAINS"
        )


def test_prompt_topic_sections_match_validator() -> None:
    """PR2 (2026-04-28 prompt-review Q7.1, Q7.2): the prompt's
    universal H2 floor must match the validator's
    `SUGGESTED_SECTIONS['topic']`. Drift here silently produces
    pages the agent thinks are valid but the validator flags as
    missing. Both were updated together in PR2 — the floor dropped
    `Summary`, `Key decisions`, `References`, and renamed
    `Related pages` → `Related`.

    v11-U7: dict was renamed REQUIRED_SECTIONS → SUGGESTED_SECTIONS
    and now lives in `src.wiki.sections`."""
    from src.wiki.sections import SUGGESTED_SECTIONS

    for section in SUGGESTED_SECTIONS["topic"]:
        heading = f"## {section}"
        assert heading in COMPILER_SYSTEM_PROMPT, (
            f"validator suggests {heading!r} on topic pages but prompt "
            "doesn't teach it — keep prompt in sync with "
            "src.wiki.sections.SUGGESTED_SECTIONS"
        )


def test_domain_frontmatter_teaches_multi_value_form() -> None:
    """v10-U2: topics that span two domains (e.g. payment-fraud touching
    trust-safety + growth-monetization) should use the plural `domains:`
    list form. Prompt must mention the list form explicitly so the model
    doesn't force-pick one domain when the concept genuinely spans two."""
    assert "domains: [" in COMPILER_SYSTEM_PROMPT, (
        "prompt must teach the `domains: [a, b]` multi-value form "
        "so topics spanning two domains render on both hubs"
    )


def test_concept_vs_thread_section_present() -> None:
    """v12-U1: the prompt must carry a `<concept_vs_thread>` section
    teaching the reframe — page is a CONCEPT, emails are EVIDENCE.
    Without this section the agent falls back to email-summarization
    priors and the Summary line reads like a thread intro. The section
    must name both the concept/evidence split and the thread-subject
    H2 anti-pattern concretely — abstract rules without examples don't
    survive first contact with real batches.

    PR1 dedupe removed the in-section "Never use strikethrough"
    bullet (it was a duplicate of the canonical teaching in
    `<revision_style>`); the strikethrough ban is now asserted by
    `test_revision_style_bans_strikethrough_literally` only."""
    assert "<concept_vs_thread>" in COMPILER_SYSTEM_PROMPT
    assert "</concept_vs_thread>" in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find("<concept_vs_thread>")
    end = COMPILER_SYSTEM_PROMPT.find("</concept_vs_thread>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # Core reframe vocabulary present.
    assert "CONCEPT" in block and "EVIDENCE" in block
    # Anti-pattern named by example so the model recognizes it.
    assert "Launch Announcement" in block or "Final Decision" in block
    # Worked good/bad example grounds the rule.
    assert "GOOD" in block and "BAD" in block


def test_concept_vs_thread_follows_workflow() -> None:
    """PR1 (Q0.2 mechanics-first reorg): `<workflow>` now loads BEFORE
    the philosophy block. The agent reads role + tools + decision
    contract first, then the CONCEPT/EVIDENCE reframe. Section order
    matters for how the model weights guidance — this test guards the
    new direction."""
    concept_idx = COMPILER_SYSTEM_PROMPT.find("<concept_vs_thread>")
    workflow_idx = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    assert concept_idx != -1 and workflow_idx != -1
    assert workflow_idx < concept_idx, (
        "<workflow> must precede <concept_vs_thread> in the mechanics-first ordering picked in Q0.2"
    )


def test_topic_page_type_teaches_concept_not_thread() -> None:
    """v12-U1: the `<page_types>` topic description must explicitly say
    the page is about the concept, not the emails, and must call out
    thread-subject H2s (`## Launch Announcement`, `## Bug Report`,
    `## Testing Results`, `## Final Decision`) by name as the
    anti-pattern. Without the explicit listing the model defaults to
    templating H2s off the thread Subject line."""
    start = COMPILER_SYSTEM_PROMPT.find("<page_types>")
    end = COMPILER_SYSTEM_PROMPT.find("</page_types>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # Concept-not-thread framing on the topic bullet itself.
    assert "about the concept" in block
    # All four thread-subject H2 anti-patterns named in the topic bullet.
    for antipattern in (
        "## Launch Announcement",
        "## Bug Report",
        "## Testing Results",
        "## Final Decision",
    ):
        assert antipattern in block, (
            f"topic page_type must name `{antipattern}` as an anti-pattern H2"
        )


def test_per_batch_instruction_uses_concept_frame() -> None:
    """v12-U1: the per-batch instruction template in
    `scripts/compile_all.py` must carry the CONCEPT/EVIDENCE reframe.
    The old "Compile the following N uncompiled raw emails" frame
    biased the agent toward email-summarization; the V12 frame asks
    the agent to update/create the CONCEPT page the emails are
    evidence for. We assert on the source text because the template
    is inlined in `main()` — there is no extractable helper."""
    # The new CONCEPT framing must be present, case-sensitive.
    assert "CONCEPT page" in _COMPILE_ALL_SRC, (
        "per-batch instruction must use the `CONCEPT page` framing per v12-U1"
    )
    # Evidence-vs-concept split called out explicitly.
    assert "EVIDENCE" in _COMPILE_ALL_SRC


def test_per_batch_instruction_drops_old_framing() -> None:
    """v12-U1: the "Compile the following …" opener biased the agent
    toward treating the batch as an email-summarization task. The new
    instruction reframes as "update or create the CONCEPT page". Guard
    against a revert."""
    assert "Compile the following" not in _COMPILE_ALL_SRC, (
        "per-batch instruction still uses the pre-v12 `Compile the "
        "following …` opener; the v12-U1 reframe should have replaced it"
    )


def test_expert_questions_section_present() -> None:
    """PR2 (2026-04-28 prompt-review Q3.2 / Q16.6 / CC-C):
    `<expert_questions>` was reframed per-archetype (launch / bug /
    policy / decision / system) instead of per-domain, and the depth-
    teaching 5W block moved to the reviewer prompt
    (`src/agent/reviewer.py`). The compiler-side block keeps a
    5-line `## Why it matters` pointer so depth doesn't structurally
    collapse on the writer.

    The reviewer-side rubric is asserted by
    `test_reviewer_carries_5w_coverage_rubric` below."""
    assert "<expert_questions>" in COMPILER_SYSTEM_PROMPT
    assert "</expert_questions>" in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find("<expert_questions>")
    end = COMPILER_SYSTEM_PROMPT.find("</expert_questions>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # Per-archetype framing — at least three archetypes named.
    archetypes = ("Launch", "Bug", "Policy", "Decision", "System overview")
    present = [a for a in archetypes if a in block]
    assert len(present) >= 3, f"<expert_questions> must name ≥3 archetypes per CC-C; got {present}"
    # `## Why it matters` is load-bearing — keep the 5-line pointer.
    assert "Why it matters" in block
    assert "load-bearing" in block.lower() or "operational constraint" in block.lower()


def test_reviewer_carries_5w_coverage_rubric() -> None:
    """PR2 (Q16.6): the depth-teaching 5W block moved to the reviewer
    prompt. Without this assertion, the compiler-side test cut would
    silently drop the rubric across both surfaces."""
    from src.agent.reviewer import REVIEWER_SYSTEM_PROMPT

    assert "5W" in REVIEWER_SYSTEM_PROMPT, (
        "PR2 (Q16.6) moved 5W coverage to the reviewer prompt; can't "
        "find '5W' in REVIEWER_SYSTEM_PROMPT"
    )
    for w in ("WHAT", "WHY", "HOW", "WHO", "WHEN", "WHERE"):
        assert w in REVIEWER_SYSTEM_PROMPT, f"reviewer 5W block must name {w}"


def test_expert_questions_follows_concept_vs_thread() -> None:
    """PR1 (Q0.2 mechanics-first reorg): the philosophy chain
    `<concept_vs_thread>` → `<expert_questions>` now lives AFTER
    `<workflow>` rather than priming it. The pair-ordering inside the
    philosophy chain is preserved — concept reframe still primes the
    5W checklist."""
    concept_idx = COMPILER_SYSTEM_PROMPT.find("<concept_vs_thread>")
    expert_idx = COMPILER_SYSTEM_PROMPT.find("<expert_questions>")
    workflow_idx = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    assert concept_idx != -1 and expert_idx != -1 and workflow_idx != -1
    assert workflow_idx < concept_idx < expert_idx, (
        "<workflow> must precede the philosophy chain "
        "<concept_vs_thread> → <expert_questions> per Q0.2"
    )


def test_inline_citations_section_present() -> None:
    """v12-U3: the prompt must carry an `<inline_citations>` section
    teaching inline `[^msg-*]` footnote syntax. Without claim-level
    citations the reader can't verify where each fact came from —
    the judge persona study flagged "unverifiable claims" repeatedly.
    The section must name the footnote syntax, the `## References`
    footnote block (NOT `## Sources` — that heading trips the
    MkDocs hook and disables the viewer's raw-email evidence block;
    Codex caught this on PR #218), and point at the actual helper
    that returns the raw_path the agent needs to build the hash
    (`get_thread_context`, which surfaces `raw_path` in its
    `messages_summary`). Abstract rules without the syntax snippet
    + References block shape don't survive first contact with real
    batches."""
    assert "<inline_citations>" in COMPILER_SYSTEM_PROMPT
    assert "</inline_citations>" in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find("<inline_citations>")
    end = COMPILER_SYSTEM_PROMPT.find("</inline_citations>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # Footnote syntax present — this is the load-bearing token. The
    # literal bracket+caret sequence must survive pre-commit / YAML
    # escaping unchanged.
    assert "[^msg-" in block, "inline footnote syntax `[^msg-` missing"
    # PR2 (Q4.2): the runtime renders `## References` from inline
    # footnotes (mkdocs_hooks.py auto-builder); the prompt no longer
    # carries the bottom-block template, but it still mentions
    # `## References` to anchor the contract.
    assert "## References" in block
    # PR2 (Q4.2): the `## Sources` warning was cut from the prompt
    # because the mkdocs hook now strips + warns at render time. The
    # block stays free of either an embedded warning or a `## Sources`
    # reference (Codex's original P1 from PR #218 is now enforced by
    # mkdocs_hooks.py, not by prompt prose).
    assert "## Sources" not in block, "PR2 (Q4.2) cut the `## Sources` warning — runtime owns it"
    # PR2 (Q4.1): the helper returns a `cite_key` field; prompt should
    # reference it.
    assert "cite_key" in block, (
        "inline_citations must reference the `cite_key` field that "
        "get_thread_context returns (Q4.1)"
    )
    assert "get_thread_context" in block, (
        "inline_citations must reference the real helper that returns "
        "cite_key (`get_thread_context` in src/compile/tools/raw_access.py)"
    )


def test_inline_citations_after_concept_vs_thread() -> None:
    """v12-U3: `<inline_citations>` loads AFTER `<concept_vs_thread>`
    AND `<expert_questions>` so the concept/evidence reframe and 5W
    checklist prime the agent before it learns claim-level citation
    mechanics. Guards against a future edit that slips citations
    between the reframe and the question list — Claude review caught
    this gap on PR #218."""
    concept_idx = COMPILER_SYSTEM_PROMPT.find("<concept_vs_thread>")
    expert_idx = COMPILER_SYSTEM_PROMPT.find("<expert_questions>")
    citations_idx = COMPILER_SYSTEM_PROMPT.find("<inline_citations>")
    assert concept_idx != -1 and expert_idx != -1 and citations_idx != -1
    assert concept_idx < expert_idx < citations_idx, (
        "<inline_citations> must load after <concept_vs_thread> and "
        "<expert_questions> so the reframe + 5W checklist prime the "
        "citation teaching"
    )


def test_revision_style_section_present() -> None:
    """v12-U4: the prompt must carry a `<revision_style>` section
    teaching the wiki's revision style — current-truth in lead
    paragraph, Recent changes bullet, collapsible `<details>` archive,
    never strikethrough. Significant changes mint decision/experiment
    pages. Without this the agent falls back to its email-summarization
    priors and writes strikethrough tombstones or lineage-in-summary
    prose.

    PR2 (2026-04-28 prompt-review Q7.2): "Current truth in Summary"
    became "Current truth in the lead paragraph" since the
    `## Summary` H2 was dropped."""
    assert "<revision_style>" in COMPILER_SYSTEM_PROMPT
    assert "</revision_style>" in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find("<revision_style>")
    end = COMPILER_SYSTEM_PROMPT.find("</revision_style>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    # Load-bearing phrases from the user's design instincts:
    assert "Current truth in the lead paragraph" in block
    assert "NEVER use strikethrough" in block
    # Collapsible archive — literal HTML tag must appear so the model
    # reaches for `<details>` instead of strikethrough.
    assert "<details>" in block
    assert "</details>" in block
    # Recent changes bullet is the companion placement.
    assert "Recent changes" in block
    # Experiments, not decisions — the framing that de-escalates
    # iterative work from "decision" to "experiment".
    assert "experiments" in block.lower()
    # Decision-page minting path is still available for meaningful
    # pivots — the doc spec names "decision" pages explicitly.
    assert "decision" in block.lower()
    # Worked good/bad example grounds the rule concretely.
    assert "GOOD" in block and "BAD" in block


def test_revision_style_follows_concept_vs_thread() -> None:
    """PR1 (Q0.2 mechanics-first reorg): `<workflow>` precedes the
    philosophy chain. `<revision_style>` loads AFTER
    `<concept_vs_thread>` so the concept/evidence reframe primes the
    revision discipline; the whole chain follows `<workflow>`."""
    concept_idx = COMPILER_SYSTEM_PROMPT.find("<concept_vs_thread>")
    revision_idx = COMPILER_SYSTEM_PROMPT.find("<revision_style>")
    workflow_idx = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    assert concept_idx != -1 and revision_idx != -1 and workflow_idx != -1
    assert workflow_idx < concept_idx < revision_idx, (
        "<workflow> must precede <concept_vs_thread> → "
        "<revision_style> per Q0.2 mechanics-first ordering"
    )


def test_revision_style_bans_strikethrough_literally() -> None:
    """v12-U4: the user's design instinct is that strikethrough is
    offensive ("tombstone aesthetic is wrong"). The prompt must ban it
    literally — `NEVER use strikethrough` — and must name the preferred
    alternative (collapsible `<details>` block) so the agent has a
    concrete fix. A ban without an alternative doesn't survive first
    contact with a rollback email."""
    start = COMPILER_SYSTEM_PROMPT.find("<revision_style>")
    end = COMPILER_SYSTEM_PROMPT.find("</revision_style>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    assert "NEVER use strikethrough" in block
    # The alternative — both the literal tag and the conceptual word so
    # the model has two paths to pattern-match on.
    assert "<details>" in block
    assert "collapsible" in block.lower()


def test_revision_style_teaches_lazy_decision_wikilink() -> None:
    """v12-U4 (post-Codex #219 fix): significant pivots ("we rolled
    back", "scaled to 50%", "killed the feature") must surface via a
    `[[decision/<slug>]]` wikilink from the topic's Recent changes
    bullet so the lineage is discoverable from the graph. The prompt
    must NAME the trigger (meaningful pivot) AND the mechanic (plant a
    wikilink, don't create the page) AND explicitly warn against
    proactive decision-page creation — which would contradict
    CLAUDE.md's lazy-decision-page rule and `<page_types>`'s own
    "decision pages are lazy" clause.

    Codex caught this contradiction on PR #219 (merged); this test
    is the regression guard."""
    start = COMPILER_SYSTEM_PROMPT.find("<revision_style>")
    end = COMPILER_SYSTEM_PROMPT.find("</revision_style>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    lowered = block.lower()
    # Trigger vocabulary — "meaningful pivot" / "significant change".
    assert "significant" in lowered or "meaningful" in lowered
    # Mechanic — wikilink to decision/<slug>, NOT create the page.
    assert "[[decision/" in block, (
        "revision_style must teach the `[[decision/<slug>]]` wikilink "
        "as the mechanic — that's how decision pages materialize per "
        "the lazy-auto-stub rule"
    )
    # Explicit contradiction-guard: the block must warn against
    # proactive creation so the agent never conflicts with
    # CLAUDE.md's lazy-decision rule.
    normalised = " ".join(block.split()).lower()
    assert "do not create the decision page proactively" in normalised, (
        "revision_style must explicitly warn against proactively "
        "creating decision pages — otherwise it contradicts "
        "CLAUDE.md's 'decision pages are lazy' rule"
    )
    # Wikilink from the topic's Recent changes bullet — the agent
    # must know the link-from location.
    assert "wikilink" in lowered
    assert "Recent changes" in block


def test_prompts_have_question_delta_exception() -> None:
    """Wave-1-U6: `already_captured` must carve out emails that extend an
    existing page with open questions / leadership asks.

    The audit caught a false-skip on `central-smart-orchestrator-api`
    Batch 9 — three director-level open questions from Amit Agarwal
    were uncaptured because the concept page existed, so the agent
    logged `already_captured` instead of extending the page's
    `## Open questions` section. The carve-out must live inside the
    decision block (post-v10-U4: `<workflow>`) so the agent sees it
    BEFORE committing to `already_captured`."""
    block = _workflow_block()
    # Section marker must be present so the carve-out is visible at a glance.
    assert "Question-delta exception" in block
    # The fix mechanic — extend via `## Open questions`, not skip.
    assert "Open questions" in block
    # Trigger vocabulary the model can pattern-match against. Any ONE of
    # these three phrases is enough — we avoid forcing prompt bloat by
    # demanding all three.
    lowered = block.lower()
    assert any(
        phrase in lowered for phrase in ("unanswered question", "open decision", "leadership ask")
    ), "question-delta block must name at least one trigger vocabulary"
    # The fix must explicitly rule out `log_insight("already_captured", ...)`
    # — otherwise the agent still hears the "be aggressive about
    # already_captured" nudge and defaults to the skip. Normalise
    # whitespace so a line wrap between "do NOT" and the rest still
    # matches.
    normalised = " ".join(block.split())
    assert 'do NOT call `log_insight("already_captured"' in normalised, (
        "question-delta block must explicitly forbid logging already_captured"
    )


def test_prompts_have_answer_delta_exception() -> None:
    """Audit `smoke-99a267f4-2026-04-28` caught the symmetric miss:
    grok skipped a Jan-30 GST follow-up as `already_captured` because
    the topic page existed, even though the email was the ANSWER to an
    open Jan-26 ask from a director ("await last week impact"). The
    Question-delta exception covers question arrival; this carve-out
    covers answer arrival. Both belong in the workflow block where the
    agent sees them BEFORE committing to `already_captured`."""
    block = _workflow_block()
    # Section marker (kept distinct from "Question-delta exception" so
    # the agent does not collapse the two cases together).
    assert "Answer-delta exception" in block
    # Trigger vocabulary the model can pattern-match against. Any one
    # of these phrases is enough — they cover the three triggers
    # (existing-section reference, same-thread reply, leadership
    # commit verb).
    lowered = block.lower()
    assert any(
        phrase in lowered
        for phrase in (
            "answer to an open question",
            "this email is the\n  reply",
            "leadership commit verb",
        )
    ), "answer-delta block must name at least one trigger vocabulary"
    # The block must also reference the Open questions structure so
    # the agent knows where to attach the answer (mirrors the
    # Question-delta test's `"Open questions" in block` assertion).
    assert "Open questions" in block, (
        "answer-delta block must name `## Open questions` as the "
        "structure to update with the resolved answer"
    )
    # The fix mechanic must call out the same forbidden path as the
    # Question-delta exception so the model treats both as siblings.
    normalised = " ".join(block.split())
    assert 'do NOT call `log_insight("already_captured"' in normalised, (
        "answer-delta block must explicitly forbid logging already_captured"
    )


def test_revision_style_teaches_experiments_not_decisions() -> None:
    """v12-U4: per wiki_design_philosophy, most entries are experiments,
    not decisions. The prompt must frame iterative work as experiments
    so the agent doesn't force a "decision" frame onto every Recent
    changes bullet. This is the non-obvious half of the
    decision/experiment split."""
    start = COMPILER_SYSTEM_PROMPT.find("<revision_style>")
    end = COMPILER_SYSTEM_PROMPT.find("</revision_style>")
    block = COMPILER_SYSTEM_PROMPT[start:end]
    lowered = block.lower()
    # The de-escalation: NOT decisions — experiments.
    assert "experiments" in lowered
    # The tried-X framing is the load-bearing example.
    assert "tried" in lowered or "worked" in lowered


def test_boy_scout_clause_fires_unconditionally() -> None:
    """The boy-scout clause must not be gated on multi-page traversal.

    Scoping the trigger to "every page you read or edited today" is the
    fix for the 0-1/3 boyscout score caught in the page-quality audit;
    the prior gate ("If you traversed multiple pages") rarely fired.
    """
    workflow = _workflow_block()
    assert "traversed multiple" not in workflow, (
        "boy-scout clause must not be gated on multi-page traversal"
    )
    assert "every page you read" in workflow.lower(), (
        "boy-scout must trigger on every touched page"
    )
    # Scope guard against #251 ReconnaissanceParalysisMiddleware: the
    # clause must not invite multi-page exploration.
    assert "multi-page expedition" in workflow, (
        "scope guard against #251 read-without-write nudge must be present"
    )
