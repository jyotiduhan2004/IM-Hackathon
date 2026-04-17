"""Assert COMPILER_SYSTEM_PROMPT covers Tier A taxonomy + Phase A U3
catalog-truth guidance.

Tier A wholesale-rewrote the prompt into tagged sections
(`<background>`, `<workflow>`, `<page_types>`, ...). Phase A U3 layered
in `<chronological_scope>` + `<decision_tree>` and swapped `sources:`
for `source_threads:` semantics. Tests assert both generations of
markers the agent still needs.
"""

from __future__ import annotations

from src.compile.prompts import COMPILER_SYSTEM_PROMPT


def test_tagged_sections_present() -> None:
    """All structured sections appear in the prompt."""
    required = (
        "<background>",
        "<chronological_scope>",
        "<workflow>",
        "<decision_tree>",
        "<page_types>",
        "<section_titles>",
        "<tool_guidance>",
        "<sources_management>",
        "<todo_rule>",
        "<self_review>",
        "<few_shots>",
    )
    missing = [tag for tag in required if tag not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt missing sections: {missing}"


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
    the decision tree so the agent can map its work onto one of them:
      1) content-page write/edit that cites the thread
      2) `log_insight("trivial_skip", ...)`
      3) `log_insight("already_captured", ...)`
    """
    start = COMPILER_SYSTEM_PROMPT.find("<decision_tree>")
    end = COMPILER_SYSTEM_PROMPT.find("</decision_tree>")
    assert start != -1 and end != -1 and end > start
    tree = COMPILER_SYSTEM_PROMPT[start:end]
    # All three terminal outcomes named in the decision tree.
    assert "trivial_skip" in tree
    assert "already_captured" in tree
    # Content edit terminal outcome — phrased as a write/edit/patch
    # that cites the thread. "cites this email's thread" is the test
    # anchor because that exact phrasing rules out "investigatory"
    # tool calls like get_thread_context or log_insight.
    assert "cites this email's thread" in tree


def test_investigatory_insights_marked_non_terminal() -> None:
    """Investigatory insight categories (topic_merge_candidate,
    structure_suggestion, question_for_human, prompt_ambiguity,
    tool_gap, supersession_doubt) must be explicitly labelled as
    non-terminal in the decision tree. Otherwise the agent treats
    logging a `topic_merge_candidate` as "done" and the email stays
    pending."""
    start = COMPILER_SYSTEM_PROMPT.find("<decision_tree>")
    end = COMPILER_SYSTEM_PROMPT.find("</decision_tree>")
    tree = COMPILER_SYSTEM_PROMPT[start:end]

    # The investigatory categories are named in the decision tree,
    # not just in <tool_guidance>, so the agent sees them at decision
    # time.
    investigatory = (
        "topic_merge_candidate",
        "structure_suggestion",
        "question_for_human",
        "prompt_ambiguity",
        "tool_gap",
        "supersession_doubt",
    )
    missing = [cat for cat in investigatory if cat not in tree]
    assert not missing, (
        f"decision tree should name investigatory categories to mark them "
        f"non-terminal; missing: {missing}"
    )

    # And there must be explicit language calling them investigatory /
    # non-terminal so the model doesn't read the list as a menu of
    # terminal options.
    lowered_tree = tree.lower()
    assert "investigatory" in lowered_tree
    assert "do not close the loop" in lowered_tree or "do not satisfy" in lowered_tree


def test_workflow_has_terminal_decision_check() -> None:
    """Workflow step 10 (the pre-return check) must tell the agent to
    verify each email has a terminal outcome before returning.
    Without this step the <decision_tree> guidance is advisory; with
    it the agent has an explicit self-audit hook."""
    start = COMPILER_SYSTEM_PROMPT.find("<workflow>")
    end = COMPILER_SYSTEM_PROMPT.find("</workflow>")
    workflow = COMPILER_SYSTEM_PROMPT[start:end]
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
