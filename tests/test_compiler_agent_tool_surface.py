"""Agent tool-surface test: what the compile agent can actually call.

Codex audit P2 follow-up (2026-04-17): `find_new_sources` was bound
at runtime but never taught in the prompt. Trace history showed the
agent misusing it as a thread-context lookup — `get_thread_context`
is the correct tool for that. Unbinding `find_new_sources` keeps
context lean and removes the wrong-tool trap.

This test pins the agent-visible tool list so additions + removals
are deliberate."""

from __future__ import annotations

import inspect

from src.compile.compiler import create_compiler


def _bound_tool_names() -> set[str]:
    """Extract the tools kwarg from create_compiler's call site.

    Reading create_compiler source is the cleanest way to get the
    agent's tool surface without actually instantiating an agent
    (which needs API keys + a model). We look for the `tools=[...]`
    literal in the function's source and pull the bare identifiers.
    """
    source = inspect.getsource(create_compiler)
    tools_start = source.find("tools=[")
    assert tools_start != -1, "could not find tools=[ in create_compiler"
    tools_end = source.find("]", tools_start)
    tools_block = source[tools_start + len("tools=[") : tools_end]
    return {
        line.strip().rstrip(",")
        for line in tools_block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def test_agent_tool_surface_contains_expected_10_tools() -> None:
    """The agent's tool surface is 10 named tools. Filesystem tools
    (ls/read_file/write_file/edit_file/glob/grep) are inherited from
    deepagents' FilesystemMiddleware; task (subagent invocation) is
    also a deepagents default. Don't assert those here — they're
    library concerns.

    Adding or removing a tool should update this test consciously."""
    expected = {
        "list_wiki_pages",
        "resolve_page",
        "create_entities",
        "write_draft_page",
        "log_insight",
        "check_my_work",
        "get_page_summary",
        "get_thread_context",
        "patch_page",
        "validate_page_draft",
    }
    assert _bound_tool_names() == expected


def test_find_new_sources_is_unbound() -> None:
    """Agent-facing unbind: `find_new_sources` was removed from the
    tool surface because (a) the coordinator owns the queue and
    already feeds the batch, and (b) the agent used it as a stand-in
    for `get_thread_context`, which is the right tool for thread
    lookup."""
    assert "find_new_sources" not in _bound_tool_names()


def test_find_new_sources_still_importable() -> None:
    """The function remains in the module for coordinator /
    script-side use. Only the @tool-binding at agent-visible surface
    was removed."""
    from src.compile.compiler import find_new_sources

    # LangChain wraps @tool functions; the callable is still there.
    assert find_new_sources is not None
