"""Assert COMPILER_SYSTEM_PROMPT covers the Tier A taxonomy guidance.

Tier A wholesale-rewrote the prompt into tagged sections
(`<background>`, `<workflow>`, `<page_types>`, ...). The OLD "## Topic vs
system" / "## Entity evidence strength" headings are gone. Tests now
assert the new section tags plus the key semantic markers the agent
still needs.
"""

from __future__ import annotations

from src.compile.prompts import COMPILER_SYSTEM_PROMPT


def test_tagged_sections_present() -> None:
    """All seven Tier-A structured sections appear in the prompt."""
    required = (
        "<background>",
        "<workflow>",
        "<page_types>",
        "<tool_guidance>",
        "<todo_rule>",
        "<self_review>",
        "<few_shots>",
    )
    missing = [tag for tag in required if tag not in COMPILER_SYSTEM_PROMPT]
    assert not missing, f"Tier A prompt missing sections: {missing}"


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
    """Statuses: active / superseded / archived (with legacy current/
    contested also accepted during transition)."""
    assert "active" in COMPILER_SYSTEM_PROMPT
    assert "superseded" in COMPILER_SYSTEM_PROMPT
    assert "archived" in COMPILER_SYSTEM_PROMPT


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


def test_reviewer_subagent_invocation_example_present() -> None:
    """Few-shots should model `task(subagent_type=\"reviewer\", ...)` so
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
