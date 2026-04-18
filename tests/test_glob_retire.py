"""Tests for `GlobNarrowingMiddleware` — the v10-U5 glob retirement.

Per-observation Langfuse scores (#185) showed 24.5% of glob calls
timing out at the hardcoded 20s `GLOB_TIMEOUT` — every observed
timeout pattern was `**/<slug>.md`, i.e. the agent using `glob` as a
fuzzy slug lookup. That's `resolve_page`'s job.

`deepagents.create_deep_agent` has no `exclude_filesystem_tools`
kwarg (verified via `inspect.signature`), so the retirement path is
narrowing via middleware: reject slug-lookup patterns, pass
enumeration patterns through.

This file covers the narrowing invariant directly; no live model or
LangGraph runner needed. The middleware instance is driven with
synthetic `ToolCallRequest`s following `test_same_thread_topic_guard`'s
shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.compile.middleware.glob_narrowing import GlobNarrowingMiddleware
from src.compile.middleware.glob_narrowing import _is_slug_lookup_pattern

# ---------------------------------------------------------------------------
# Request / handler helpers (mirror test_same_thread_topic_guard.py)
# ---------------------------------------------------------------------------


def _make_request(
    name: str,
    args: dict[str, Any] | None = None,
    tool_call_id: str = "call_1",
) -> ToolCallRequest:
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


def _success_handler(name: str = "glob") -> Callable[[ToolCallRequest], ToolMessage]:
    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='["wiki/topics/foo.md"]',
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    return handler


# ---------------------------------------------------------------------------
# Pattern classifier unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        "**/seller-isq.md",
        "**/foo.md",
        "**/cap-notif-frequency.md",
        "  **/spaces-ok.md  ",  # leading/trailing whitespace
    ],
)
def test_slug_lookup_patterns_are_rejected(pattern: str) -> None:
    assert _is_slug_lookup_pattern(pattern) is True


@pytest.mark.parametrize(
    "pattern",
    [
        "wiki/topics/*.md",  # legitimate enumerate-all
        "wiki/systems/*.md",
        "/raw/*.md",
        "**/*.md",  # enumerate-all with recursive wildcard, still has * in stem
        "**/foo*.md",  # wildcard in stem
        "**/foo?.md",  # single-char wildcard in stem
        "**/foo[12].md",  # charclass in stem
        "**/foo.txt",  # not .md
        "*.md",  # not a **/ prefix
        "raw/**/msg-*.md",  # enumerate by thread
        "",  # empty
    ],
)
def test_non_slug_patterns_pass_through(pattern: str) -> None:
    assert _is_slug_lookup_pattern(pattern) is False


# ---------------------------------------------------------------------------
# Middleware — sync path
# ---------------------------------------------------------------------------


def test_slug_lookup_glob_is_rejected_sync() -> None:
    middleware = GlobNarrowingMiddleware()
    request = _make_request("glob", {"pattern": "**/seller-isq.md"})
    # Handler should NOT be invoked when the middleware rejects.
    handler_called = False

    def handler(_req: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="unreachable", tool_call_id="call_1", name="glob")

    result = middleware.wrap_tool_call(request, handler)

    assert handler_called is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "resolve_page" in str(result.content)
    assert "**/seller-isq.md" in str(result.content)


def test_valid_enumeration_glob_passes_through_sync() -> None:
    middleware = GlobNarrowingMiddleware()
    request = _make_request("glob", {"pattern": "wiki/topics/*.md"})
    result = middleware.wrap_tool_call(request, _success_handler("glob"))

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    assert "wiki/topics/foo.md" in str(result.content)


def test_non_glob_tool_calls_pass_through_sync() -> None:
    middleware = GlobNarrowingMiddleware()
    # Even if args carry a slug-lookup-shaped pattern, non-glob tools
    # must not be affected.
    request = _make_request("read_file", {"pattern": "**/seller-isq.md"})
    result = middleware.wrap_tool_call(request, _success_handler("read_file"))

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_missing_pattern_arg_passes_through_sync() -> None:
    middleware = GlobNarrowingMiddleware()
    request = _make_request("glob", {})  # no pattern
    result = middleware.wrap_tool_call(request, _success_handler("glob"))

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


# ---------------------------------------------------------------------------
# Middleware — async path (parity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slug_lookup_glob_is_rejected_async() -> None:
    middleware = GlobNarrowingMiddleware()
    request = _make_request("glob", {"pattern": "**/foo.md"})
    handler_called = False

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="unreachable", tool_call_id="call_1", name="glob")

    result = await middleware.awrap_tool_call(request, handler)

    assert handler_called is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "resolve_page" in str(result.content)


@pytest.mark.asyncio
async def test_valid_enumeration_glob_passes_through_async() -> None:
    middleware = GlobNarrowingMiddleware()
    request = _make_request("glob", {"pattern": "wiki/topics/*.md"})

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content='["ok"]', tool_call_id="call_1", name="glob")

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


# ---------------------------------------------------------------------------
# Registration smoke test
# ---------------------------------------------------------------------------


def test_middleware_registered_in_compiler() -> None:
    """Confirm `GlobNarrowingMiddleware` is exported from the middleware package
    and imported by `create_compiler`. We avoid calling `create_compiler()` here
    (it builds a filesystem view + LiteLLM model) — grep-style assertion is
    enough to prove the wiring. Direct tests above cover behaviour.
    """
    from src.compile import middleware as mw_pkg

    assert hasattr(mw_pkg, "GlobNarrowingMiddleware")
    assert "GlobNarrowingMiddleware" in mw_pkg.__all__

    # And it's imported in compiler.create_compiler's body.
    import inspect

    from src.compile import compiler as comp_mod

    source = inspect.getsource(comp_mod.create_compiler)
    assert "GlobNarrowingMiddleware" in source
    assert "GlobNarrowingMiddleware()" in source
