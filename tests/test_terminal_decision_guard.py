"""Tests for `TerminalDecisionGuardMiddleware`.

The middleware has two responsibilities:

  1. Track whether the batch has committed to a terminal decision
     (content write or terminal `log_insight`) via `wrap_tool_call`.
  2. Gate the ReAct exit edge via `after_model`: inject a synthetic
     `HumanMessage` + `jump_to: model` when the agent is about to exit
     without a commitment, bounded by `_MAX_NUDGES`.

These tests drive the middleware directly — synthesized
`ToolCallRequest` objects for the tool-call path, and hand-built state
dicts with AI/tool messages for the `after_model` path. No live model,
no LangGraph runner.

V12 50-compile deep audit 2026-04-23 batch 45 motivates this module —
see docstring in `src/compile/middleware/terminal_decision_guard.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.compile.middleware.terminal_decision_guard import _MAX_NUDGES
from src.compile.middleware.terminal_decision_guard import TERMINAL_NUDGE_MESSAGE
from src.compile.middleware.terminal_decision_guard import TerminalDecisionGuardMiddleware

# ---------------------------------------------------------------------------
# Request / handler helpers — mirror test_check_my_work_gate.py shape.
# ---------------------------------------------------------------------------


def _make_request(
    name: str,
    args: dict[str, Any] | None = None,
    tool_call_id: str = "call_1",
) -> ToolCallRequest:
    """Build a minimal `ToolCallRequest` for middleware tests.

    `state` is a dict with an empty messages list — enough to satisfy
    the middleware's attribute access without pulling in the full
    LangGraph runtime.
    """
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": args or {},
            "id": tool_call_id,
            "type": "tool_call",
        },
        tool=MagicMock(name=name),
        state={"messages": []},
        runtime=MagicMock(),
    )


def _success_handler(name: str) -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that returns a successful ToolMessage. Deterministic."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    return handler


def _error_handler(name: str) -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that returns an error-status ToolMessage."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="boom",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    return handler


def _final_state() -> Any:
    """Build an agent state whose last message is a tool-call-less AIMessage.

    This is the ReAct exit signal — the conditional edge after `model`
    will route to END. `after_model` fires before that edge, so the
    middleware sees this exact shape when it needs to act.

    Returns `Any` (not `AgentState`) to sidestep TypedDict variance in
    tests — the middleware reads ``state["messages"]`` via `.get`
    which doesn't require a proper `AgentState` cast.
    """
    return {"messages": [AIMessage(content="All done!")]}


def _continuing_state() -> Any:
    """Build a state whose last AIMessage has tool_calls (agent continuing)."""
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "raw/foo.md"},
                        "id": "call_n",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


# ---------------------------------------------------------------------------
# Content-write commitments — all three writing tools unlock the gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file", "patch_page"])
def test_content_write_commits_the_gate(tool_name: str) -> None:
    """A successful content write (write_file/edit_file/patch_page) commits."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request(tool_name, {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(request, _success_handler(tool_name))

    assert mw._committed is True
    # Final-state model exit must now pass through cleanly.
    assert mw.after_model(_final_state(), MagicMock()) is None


def test_failed_content_write_does_not_commit() -> None:
    """Error-status content writes don't count — they didn't land."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(request, _error_handler("write_file"))

    assert mw._committed is False


def test_write_draft_page_does_not_commit() -> None:
    """write_draft_page is exploratory — not a terminal commitment."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request("write_draft_page", {"slug": "maybe-page"})
    mw.wrap_tool_call(request, _success_handler("write_draft_page"))

    assert mw._committed is False


# ---------------------------------------------------------------------------
# log_insight — terminal categories commit; investigatory ones don't.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category",
    ["trivial_skip", "already_captured", "insufficient_decision"],
)
def test_terminal_insight_categories_commit(category: str) -> None:
    """Every terminal category (trivial/already/insufficient) commits the gate."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request(
        "log_insight",
        {"category": category, "message": "why", "email_path": "raw/foo.md"},
    )
    mw.wrap_tool_call(request, _success_handler("log_insight"))

    assert mw._committed is True
    assert mw.after_model(_final_state(), MagicMock()) is None


def test_investigatory_insight_category_does_not_commit() -> None:
    """Non-terminal ``log_insight`` categories must leave the gate open."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request(
        "log_insight",
        {
            "category": "structure_suggestion",
            "message": "could split X from Y",
            "email_path": "raw/foo.md",
        },
    )
    mw.wrap_tool_call(request, _success_handler("log_insight"))

    assert mw._committed is False


def test_failed_log_insight_does_not_commit() -> None:
    """An error-status log_insight (invalid args) mustn't credit the gate."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request(
        "log_insight",
        {"category": "trivial_skip", "message": "oops"},
    )
    mw.wrap_tool_call(request, _error_handler("log_insight"))

    assert mw._committed is False


def test_log_insight_without_category_does_not_commit() -> None:
    """Missing ``category`` arg → gate stays closed (defensive).

    A successful ToolMessage from ``log_insight`` should always carry
    a ``category`` in its args (tool signature makes it required), so
    a missing category means the tool schema drifted. Preferring to
    re-nudge over accepting an unknown category is the safe default.
    """
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request("log_insight", {"message": "no category"})
    mw.wrap_tool_call(request, _success_handler("log_insight"))

    assert mw._committed is False


# ---------------------------------------------------------------------------
# after_model gate — inject nudge when uncommitted + about to exit.
# ---------------------------------------------------------------------------


def test_after_model_injects_nudge_when_uncommitted() -> None:
    """Uncommitted + final AIMessage → middleware nudges back to model."""
    mw = TerminalDecisionGuardMiddleware()

    update = mw.after_model(_final_state(), MagicMock())

    assert isinstance(update, dict)
    assert update["jump_to"] == "model"
    messages = update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == TERMINAL_NUDGE_MESSAGE
    assert mw._nudges == 1


def test_after_model_passes_through_when_continuing() -> None:
    """AIMessage with tool_calls → agent isn't exiting → middleware abstains."""
    mw = TerminalDecisionGuardMiddleware()

    assert mw.after_model(_continuing_state(), MagicMock()) is None
    assert mw._nudges == 0


def test_after_model_passes_through_when_committed() -> None:
    """A prior content write means the gate is already open."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(request, _success_handler("write_file"))

    assert mw.after_model(_final_state(), MagicMock()) is None
    assert mw._nudges == 0


# ---------------------------------------------------------------------------
# Bounded retries — after _MAX_NUDGES the guard gives up (coordinator takes over).
# ---------------------------------------------------------------------------


def test_after_model_exhausts_nudges_then_lets_agent_exit() -> None:
    """After _MAX_NUDGES rejections, middleware returns None (agent exits)."""
    mw = TerminalDecisionGuardMiddleware()

    for _ in range(_MAX_NUDGES):
        update = mw.after_model(_final_state(), MagicMock())
        assert isinstance(update, dict)

    # One past the budget — agent is allowed to exit; coordinator
    # fallback must kick in instead of the middleware.
    assert mw.after_model(_final_state(), MagicMock()) is None
    assert mw._nudges == _MAX_NUDGES


def test_empty_state_does_not_trip_gate() -> None:
    """No messages yet → nothing to gate on; middleware abstains."""
    mw = TerminalDecisionGuardMiddleware()

    assert mw.after_model({"messages": []}, MagicMock()) is None


# ---------------------------------------------------------------------------
# Tools unrelated to the gate pass through without touching commitment state.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["read_file", "resolve_page", "get_thread_context", "list_wiki_pages", "glob"],
)
def test_read_only_tools_do_not_commit(tool_name: str) -> None:
    """read_file/resolve_page/etc. must not flip the commitment flag."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request(tool_name, {"file_path": "raw/foo.md"})
    mw.wrap_tool_call(request, _success_handler(tool_name))

    assert mw._committed is False


def test_check_my_work_alone_is_not_terminal() -> None:
    """check_my_work isn't terminal — it validates, doesn't commit content."""
    mw = TerminalDecisionGuardMiddleware()

    request = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})
    mw.wrap_tool_call(request, _success_handler("check_my_work"))

    assert mw._committed is False
    # Gate must still fire when the agent tries to exit.
    update = mw.after_model(_final_state(), MagicMock())
    assert isinstance(update, dict)
    assert update["jump_to"] == "model"


# ---------------------------------------------------------------------------
# Async path — same logic, different code path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awrap_tool_call_records_commitment() -> None:
    """Async write path commits the gate identically to sync."""
    mw = TerminalDecisionGuardMiddleware()

    async def async_handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name="write_file",
        )

    request = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    await mw.awrap_tool_call(request, async_handler)

    assert mw._committed is True


@pytest.mark.asyncio
async def test_aafter_model_matches_sync() -> None:
    """Async after_model returns the same nudge update."""
    mw = TerminalDecisionGuardMiddleware()

    update = await mw.aafter_model(_final_state(), MagicMock())

    assert isinstance(update, dict)
    assert update["jump_to"] == "model"
    assert update["messages"][0].content == TERMINAL_NUDGE_MESSAGE


# ---------------------------------------------------------------------------
# Integration smoke — middleware is wired into create_compiler.
# ---------------------------------------------------------------------------


def test_middleware_wired_into_compiler() -> None:
    """The compiler includes the terminal-decision guard at construction time.

    Smoke-level: we don't run an agent, just confirm the middleware's
    `wrap_tool_call` is composed into the ToolNode's wrapper chain so
    the wiring survives refactors. Mirrors the pattern used by
    `test_check_my_work_gate.test_middleware_wired_into_compiler`.
    """
    from src.compile.compiler import create_compiler

    agent = create_compiler("z-ai/glm-5")
    tools_node = agent.nodes["tools"].bound
    wrapper = getattr(tools_node, "_wrap_tool_call", None)
    assert wrapper is not None, "ToolNode missing _wrap_tool_call — LangGraph API shifted"

    found: list[str] = []
    seen: set[int] = set()

    def walk(fn: object) -> None:
        if not callable(fn) or id(fn) in seen:
            return
        seen.add(id(fn))
        closure = getattr(fn, "__closure__", None) or ()
        for cell in closure:
            try:
                contents = cell.cell_contents
            except ValueError:
                continue
            qualname = getattr(contents, "__qualname__", None) or ""
            if "TerminalDecisionGuardMiddleware" in qualname:
                found.append(qualname)
            if callable(contents):
                walk(contents)

    walk(wrapper)
    assert found, (
        "TerminalDecisionGuardMiddleware.wrap_tool_call not found in "
        "ToolNode's wrapper chain — check that create_compiler passes "
        "the middleware into create_deep_agent(middleware=[...])."
    )
