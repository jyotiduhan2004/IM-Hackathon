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
        "<tool_guidance>",
        "<sources_management>",
        "<todo_rule>",
        "<self_review>",
        "<few_shots>",
    )
    missing = [tag for tag in required if tag not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"prompt missing sections: {missing}"


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
