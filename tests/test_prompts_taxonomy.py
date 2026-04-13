"""Assert COMPILER_SYSTEM_PROMPT covers the Phase 1 taxonomy guidance."""

from __future__ import annotations

from src.compile.prompts import COMPILER_SYSTEM_PROMPT


def test_topic_vs_system_section_present() -> None:
    assert "## Topic vs system" in COMPILER_SYSTEM_PROMPT


def test_entity_evidence_section_present() -> None:
    assert "## Entity evidence strength" in COMPILER_SYSTEM_PROMPT


def test_topic_vs_system_core_phrases() -> None:
    assert "what is happening" in COMPILER_SYSTEM_PROMPT
    assert "what is this thing" in COMPILER_SYSTEM_PROMPT


def test_worked_example_anchors() -> None:
    assert "Lens" in COMPILER_SYSTEM_PROMPT
    assert "WhatsApp 9696" in COMPILER_SYSTEM_PROMPT


def test_cc_only_weak_evidence_rule() -> None:
    assert "CC-only" in COMPILER_SYSTEM_PROMPT or "CC only" in COMPILER_SYSTEM_PROMPT


def test_sections_ordered_before_wikilinks() -> None:
    topic_idx = COMPILER_SYSTEM_PROMPT.index("## Topic vs system")
    entity_idx = COMPILER_SYSTEM_PROMPT.index("## Entity evidence strength")
    wikilink_idx = COMPILER_SYSTEM_PROMPT.index("## Wikilink rules")
    assert topic_idx < entity_idx < wikilink_idx
