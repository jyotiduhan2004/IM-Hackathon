"""Lock the compile agent's bound tool surface against drift."""

from __future__ import annotations

import pytest
from src.compile.compiler import create_compiler

EXPECTED_CUSTOM_TOOLS = frozenset(
    {
        "find_new_sources",
        "list_wiki_pages",
        "resolve_page",
        "create_entities",
        "create_entity",
        "write_draft_page",
        "log_insight",
        "check_my_work",
    }
)

FORBIDDEN_TOOLS = frozenset(
    {
        "list_uncompiled_emails",
        "mark_as_compiled",
        "update_wiki_index",
        "stamp_page_compiled_at",
        "append_to_log",
    }
)


@pytest.fixture(scope="module")
def bound_tool_names() -> frozenset[str]:
    agent = create_compiler("z-ai/glm-5")
    return frozenset(agent.nodes["tools"].bound.tools_by_name.keys())


def test_forbidden_coordinator_tools_not_exposed(bound_tool_names: frozenset[str]) -> None:
    leaked = FORBIDDEN_TOOLS & bound_tool_names
    assert not leaked, (
        f"coordinator-owned tools leaked into agent surface: {sorted(leaked)}. "
        f"These must not be bound — see comments in compiler.create_compiler."
    )


def test_all_expected_custom_tools_are_bound(bound_tool_names: frozenset[str]) -> None:
    missing = EXPECTED_CUSTOM_TOOLS - bound_tool_names
    assert not missing, (
        f"expected custom tools missing from bound set: {sorted(missing)}. "
        f"Check the tools=[...] list in create_compiler."
    )
