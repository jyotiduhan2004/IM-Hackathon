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

from src.compile.prompts import COMPILER_SYSTEM_PROMPT


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
    # message_touched_pages bookkeeping carve-out.
    assert "message_touched_pages" in block


def test_workflow_prompt_under_budget() -> None:
    """Informational token-budget guard. v10-U4 merged two ~1700-token
    sections into one; post-merge the prompt is ~28k chars / ~7k tokens.
    The ceiling is 30000 chars — crossing it means a later edit
    re-introduced duplication or bloat. Raise only on a deliberate
    feature that genuinely needs more space."""
    assert len(COMPILER_SYSTEM_PROMPT) < 30000, (
        f"prompt grew to {len(COMPILER_SYSTEM_PROMPT)} chars; v10-U4 baseline "
        "was ~28k. If the growth is deliberate, raise this ceiling; otherwise "
        "it's probably re-introduced duplication."
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


def test_workflow_step_7_points_to_editorial_notes_section() -> None:
    """Step 7 teaches the call, but the nuance lives in the
    <editorial_notes> block. Workflow must name-drop the section so
    the writer knows to go read it."""
    start = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    end = COMPILER_SYSTEM_PROMPT.find("</workflow>")
    workflow = COMPILER_SYSTEM_PROMPT[start:end]
    assert "editorial_notes" in workflow


def test_taxonomy_covers_four_plus_two() -> None:
    """4+2 taxonomy: topic / system / policy / glossary visible; decision /
    person lazy."""
    visible = ("**topic**", "**system**", "**policy**", "**glossary**")
    lazy = ("**decision**", "**person**")
    for v in visible:
        assert v in COMPILER_SYSTEM_PROMPT, f"missing visible type: {v}"
    for lz in lazy:
        assert lz in COMPILER_SYSTEM_PROMPT, f"missing lazy type: {lz}"


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
    from src.compile import prompts as prompts_module

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
    `trivial_skip`. The decision tree must name the catalog so the agent
    understands why forcing an edit "for evidence" is wrong."""
    assert "already_captured" in COMPILER_SYSTEM_PROMPT
    assert "trivial_skip" in COMPILER_SYSTEM_PROMPT
    assert "message_touched_pages" in COMPILER_SYSTEM_PROMPT


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


def test_check_my_work_taught_in_workflow_with_correct_contract() -> None:
    """Codex P1: `check_my_work` is bound at runtime but the prompt
    never mentioned it. Workflow now teaches all three possible
    return shapes (clean / blocked / gate-rejected). The gate-
    rejected shape is the key one — it's a plain error ToolMessage
    whose content starts 'Rejected: call check_my_work only after…'
    — and if the prompt doesn't describe it the agent loops retrying
    the same gate with no write in between."""
    start = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    end = COMPILER_SYSTEM_PROMPT.find("</workflow>")
    workflow = COMPILER_SYSTEM_PROMPT[start:end]
    assert "check_my_work" in workflow, "check_my_work must be named in workflow"
    # All three return shapes taught:
    assert "clean" in workflow and "blocked" in workflow
    assert "Rejected: call check_my_work only after" in workflow, (
        "gate-rejection shape (plain ToolMessage content) must be taught so "
        "the agent doesn't loop retrying"
    )
    # Recovery on gate rejection: go write a page, don't retry the gate.
    lowered = workflow.lower()
    assert "go write a page first" in lowered or "write a page first" in lowered


def test_workflow_has_terminal_decision_check() -> None:
    """The workflow's pre-return step must tell the agent to verify
    each email has a terminal outcome before returning. Without this
    step the decision guidance (post-v10-U4: inside `<workflow>`) is
    advisory; with it the agent has an explicit self-audit hook."""
    workflow = _workflow_block()
    assert "Before returning" in workflow
    assert "terminal outcome" in workflow


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
    defaults to its priors (bail)."""
    assert "Example 9" in COMPILER_SYSTEM_PROMPT
    start = COMPILER_SYSTEM_PROMPT.find("### Example 9")
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


def test_topic_required_h2_sections_taught() -> None:
    """v9-U1: the validator enforces 8 required H2 sections on topic
    pages (Summary / Current state / Why it matters / Key decisions /
    Recent changes / Open questions / Related pages / References).
    Until Cycle 9 the prompt didn't name any of them; every fresh
    compile added to the missing-sections backlog. This test pins the
    teaching in place so future edits can't quietly drop it."""
    required = (
        "## Summary",
        "## Current state",
        "## Why it matters",
        "## Key decisions",
        "## Recent changes",
        "## Open questions",
        "## Related pages",
        "## References",
    )
    missing = [s for s in required if s not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt must name topic required H2 sections; missing: {missing}"


def test_system_required_h2_sections_taught() -> None:
    """v9-U1: system pages also have a canonical H2 shape enforced by
    the validator. `## Role` and `## Active related topics` are the
    system-specific ones; `## Summary` / `## References` / `## Related
    pages` overlap with topic. Pin them so the prompt mirrors the
    validator."""
    system_specific = ("## Role", "## Active related topics", "## Dependencies", "## Known issues")
    missing = [s for s in system_specific if s not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt must name system required H2 sections; missing: {missing}"


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
    """v9-U1: Example 1 is the canonical-shape worked example. It must
    show `domain:` frontmatter, a ≥2-sentence lead paragraph, and all 8
    topic H2 sections in order so the model has a full template to
    pattern-match against."""
    start = COMPILER_SYSTEM_PROMPT.find("### Example 1")
    end = COMPILER_SYSTEM_PROMPT.find("### Example 2")
    assert start != -1 and end != -1 and end > start
    example = COMPILER_SYSTEM_PROMPT[start:end]
    assert "domain:" in example, "Example 1 must show `domain:` frontmatter"
    # Each of the 8 topic H2 sections appears in Example 1.
    for section in (
        "## Summary",
        "## Current state",
        "## Why it matters",
        "## Key decisions",
        "## Recent changes",
        "## Open questions",
        "## Related pages",
        "## References",
    ):
        assert section in example, f"Example 1 missing required section heading: {section}"


def test_prompt_domain_list_matches_compiler() -> None:
    """v9-U1: the prompt lists 8 canonical domains in <domain_frontmatter>.
    The source of truth for domain slugs is `src.compile.compiler._DOMAINS`;
    if someone adds or renames a domain there without updating the prompt,
    the agent's world-model drifts from the validator's world-model.
    Pin the invariant."""
    from src.compile.compiler import _DOMAIN_BY_SLUG

    for slug in _DOMAIN_BY_SLUG:
        assert slug in COMPILER_SYSTEM_PROMPT, (
            f"domain slug {slug!r} missing from prompt — keep prompt "
            "in sync with src.compile.compiler._DOMAINS"
        )


def test_prompt_topic_sections_match_validator() -> None:
    """v9-U1: the prompt's required-topic-sections list must match the
    validator's `REQUIRED_SECTIONS['topic']`. Drift here silently
    produces pages the agent thinks are valid but the validator rejects —
    the exact failure mode Cycle 9 surfaced. Pin the invariant."""
    from scripts.validate_wiki import REQUIRED_SECTIONS

    for section in REQUIRED_SECTIONS["topic"]:
        heading = f"## {section}"
        assert heading in COMPILER_SYSTEM_PROMPT, (
            f"validator requires {heading!r} on topic pages but prompt "
            "doesn't teach it — keep prompt in sync with "
            "scripts.validate_wiki.REQUIRED_SECTIONS"
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
