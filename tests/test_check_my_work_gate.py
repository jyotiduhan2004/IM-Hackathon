"""Tests for `CheckMyWorkGateMiddleware`.

The middleware is stateful: one instance owns a per-session set of
successful content-page writes. These tests drive it directly —
building `ToolCallRequest` objects with hand-rolled handlers — so we
don't need a live model or LangGraph runner.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.compile.middleware.check_my_work_gate import GATE_REJECT_MESSAGE
from src.compile.middleware.check_my_work_gate import GATE_REJECT_PAT
from src.compile.middleware.check_my_work_gate import CheckMyWorkGateMiddleware

# ---------------------------------------------------------------------------
# Request / handler helpers
# ---------------------------------------------------------------------------


def _make_request(
    name: str,
    args: dict[str, Any] | None = None,
    tool_call_id: str = "call_1",
) -> ToolCallRequest:
    """Build a minimal ToolCallRequest for middleware tests.

    `state` is a dict with an empty messages list — enough to satisfy
    the middleware's access pattern without pulling in the full
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


def _success_handler(name: str = "write_file") -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that returns a successful ToolMessage. Deterministic."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    return handler


def _error_handler(name: str = "write_file") -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that returns an error-status ToolMessage."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="disk full",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    return handler


def _critique_handler() -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that simulates a successful check_my_work result."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": "true", "status": "clean"}',
            tool_call_id=request.tool_call["id"],
            name="check_my_work",
        )

    return handler


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


def test_empty_session_check_my_work_is_rejected() -> None:
    """No successful writes → check_my_work is blocked with synthetic error."""
    mw = CheckMyWorkGateMiddleware()
    request = _make_request("check_my_work", {"file_path": "raw/foo.md"})
    # Handler should NOT be called — we assert by failing if it is.
    handler = MagicMock(side_effect=AssertionError("handler must not run"))

    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.name == "check_my_work"
    assert result.tool_call_id == "call_1"
    assert result.content == GATE_REJECT_MESSAGE
    handler.assert_not_called()


def test_successful_write_then_check_my_work_passes_through() -> None:
    """write_file success → check_my_work handler runs normally."""
    mw = CheckMyWorkGateMiddleware()

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    check_req = _make_request("check_my_work", {"file_path": "raw/foo.md"}, "call_2")
    result = mw.wrap_tool_call(check_req, _critique_handler())

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    assert "clean" in str(result.content)


def test_write_draft_page_success_alone_is_rejected() -> None:
    """write_draft_page doesn't count — drafts aren't content writes."""
    mw = CheckMyWorkGateMiddleware()

    draft_req = _make_request("write_draft_page", {"slug": "maybe-page"})
    mw.wrap_tool_call(draft_req, _success_handler("write_draft_page"))

    assert mw.successful_write_tools == set(), (
        "write_draft_page must NOT be tracked as a content write"
    )

    check_req = _make_request("check_my_work", {"file_path": "raw/foo.md"})
    handler = MagicMock(side_effect=AssertionError("handler must not run"))
    result = mw.wrap_tool_call(check_req, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert GATE_REJECT_PAT.search(str(result.content))


def test_failed_write_does_not_count() -> None:
    """Error-status write → not counted → check_my_work still rejected."""
    mw = CheckMyWorkGateMiddleware()

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _error_handler("write_file"))

    assert mw.successful_write_tools == set()

    check_req = _make_request("check_my_work", {"file_path": "raw/foo.md"})
    handler = MagicMock(side_effect=AssertionError("handler must not run"))
    result = mw.wrap_tool_call(check_req, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert GATE_REJECT_PAT.search(str(result.content))


def test_other_tools_pass_through() -> None:
    """Tools other than check_my_work are not intercepted."""
    mw = CheckMyWorkGateMiddleware()

    for name in ("read_file", "ls", "glob", "list_wiki_pages", "resolve_page"):
        request = _make_request(name, {"file_path": "raw/foo.md"})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="x", name=name))
        result = mw.wrap_tool_call(request, handler)
        assert result.content == "ok", f"{name}: unexpected interception"
        handler.assert_called_once()


def test_edit_file_and_patch_page_also_count() -> None:
    """edit_file and patch_page successes both unlock check_my_work."""
    for tool in ("edit_file", "patch_page"):
        mw = CheckMyWorkGateMiddleware()
        write_req = _make_request(tool, {"file_path": "wiki/topics/foo.md"})
        mw.wrap_tool_call(write_req, _success_handler(tool))

        check_req = _make_request("check_my_work", {"file_path": "raw/foo.md"})
        result = mw.wrap_tool_call(check_req, _critique_handler())

        assert isinstance(result, ToolMessage)
        assert result.status != "error", f"{tool}: gate did not open"


def test_gate_reject_pattern_matches_canonical_message() -> None:
    """The shared regex matches the middleware's own rejection message."""
    assert GATE_REJECT_PAT.search(GATE_REJECT_MESSAGE) is not None


def test_touched_paths_recorded_on_success() -> None:
    """Successful writes record their file_path into touched_paths."""
    mw = CheckMyWorkGateMiddleware()
    request = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(request, _success_handler("write_file"))

    assert mw.touched_paths == {"wiki/topics/foo.md"}
    assert mw.successful_write_tools == {"write_file"}


# ---------------------------------------------------------------------------
# Async path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awrap_tool_call_rejects_empty_session() -> None:
    """Async variant rejects premature check_my_work identically."""
    mw = CheckMyWorkGateMiddleware()
    request = _make_request("check_my_work", {"file_path": "raw/foo.md"})

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        raise AssertionError("handler must not run")

    result = await mw.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.content == GATE_REJECT_MESSAGE


@pytest.mark.asyncio
async def test_awrap_tool_call_passes_through_after_write() -> None:
    """Async path opens the gate after a successful async write."""
    mw = CheckMyWorkGateMiddleware()

    async def async_success(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name="write_file",
        )

    async def async_critique(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": "true"}',
            tool_call_id=request.tool_call["id"],
            name="check_my_work",
        )

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    await mw.awrap_tool_call(write_req, async_success)

    check_req = _make_request("check_my_work", {"file_path": "raw/foo.md"})
    result = await mw.awrap_tool_call(check_req, async_critique)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


# ---------------------------------------------------------------------------
# Integration smoke — middleware is wired into create_compiler
# ---------------------------------------------------------------------------


def test_middleware_wired_into_compiler() -> None:
    """The compiler includes the gate middleware at construction time.

    Smoke-level: we don't run an agent, just confirm the middleware's
    `wrap_tool_call` is composed into the ToolNode's wrapper chain so
    the wiring survives refactors. deepagents nests middlewares pairwise
    via `_chain_tool_call_wrappers.compose_two`, so with >1 middleware
    the gate's `wrap_tool_call` lives several layers deep in the closure
    chain rather than at the outermost level — we walk recursively.
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
            if "CheckMyWorkGateMiddleware" in qualname:
                found.append(qualname)
            if callable(contents):
                walk(contents)

    walk(wrapper)
    assert found, (
        "CheckMyWorkGateMiddleware.wrap_tool_call not found anywhere in "
        "ToolNode's wrapper chain — check that create_compiler passes the "
        "middleware into create_deep_agent(middleware=[...])."
    )
