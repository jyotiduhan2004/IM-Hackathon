"""Unit tests for src/compile/middleware/path_autoheal.py.

Covers the rewrite logic in isolation — full middleware integration with
deepagents is exercised by the live compile recipe in the PR description.
"""

from __future__ import annotations

from typing import cast

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.compile.middleware.path_autoheal import PathAutohealMiddleware
from src.compile.middleware.path_autoheal import _rewrite_args
from src.compile.middleware.path_autoheal import _try_rewrite

# ---------------------------------------------------------------------------
# _try_rewrite — pure function
# ---------------------------------------------------------------------------


def test_try_rewrite_returns_none_for_relative_path() -> None:
    assert _try_rewrite("raw/foo.md") is None
    assert _try_rewrite("wiki/topics/bar.md") is None


def test_try_rewrite_returns_none_for_already_virtual_path() -> None:
    assert _try_rewrite("/raw/foo.md") is None
    assert _try_rewrite("/wiki/topics/bar.md") is None


def test_try_rewrite_strips_host_prefix_for_raw() -> None:
    result = _try_rewrite("/Users/amtagrwl/git/email-knowledge-base/raw/foo.md")
    assert result == "/raw/foo.md"


def test_try_rewrite_strips_host_prefix_for_wiki() -> None:
    result = _try_rewrite("/Users/amtagrwl/git/email-knowledge-base/wiki/topics/bar.md")
    assert result == "/wiki/topics/bar.md"


def test_try_rewrite_ambiguous_double_occurrence_is_skipped() -> None:
    # Two /raw/ segments — we don't know which the agent meant.
    assert _try_rewrite("/foo/raw/bar/raw/baz.md") is None


def test_try_rewrite_no_virtual_segment_is_skipped() -> None:
    assert _try_rewrite("/etc/passwd") is None
    assert _try_rewrite("/Users/amtagrwl/Library/logs/foo.md") is None


def test_try_rewrite_handles_none_and_empty() -> None:
    assert _try_rewrite("") is None
    assert _try_rewrite("   ") is None


# ---------------------------------------------------------------------------
# _rewrite_args — tool-aware dispatch
# ---------------------------------------------------------------------------


def test_rewrite_args_rewrites_read_file_path() -> None:
    args = {"file_path": "/Users/foo/git/email-knowledge-base/raw/x.md"}
    new_args, corrections = _rewrite_args("read_file", args)
    assert new_args["file_path"] == "/raw/x.md"
    assert corrections == [
        {
            "key": "file_path",
            "from": "/Users/foo/git/email-knowledge-base/raw/x.md",
            "to": "/raw/x.md",
        }
    ]


def test_rewrite_args_skips_unknown_tool() -> None:
    args = {"file_path": "/Users/foo/raw/x.md"}
    new_args, corrections = _rewrite_args("resolve_page", args)
    assert new_args == args
    assert corrections == []


def test_rewrite_args_leaves_relative_path_unchanged() -> None:
    args = {"file_path": "raw/x.md"}
    new_args, corrections = _rewrite_args("read_file", args)
    assert new_args == args
    assert corrections == []


def test_rewrite_args_handles_multi_key_tool() -> None:
    # grep takes both path and pattern; only path should get rewritten.
    args = {
        "path": "/Users/foo/git/email-knowledge-base/wiki/topics",
        "pattern": "whatsapp",
    }
    new_args, corrections = _rewrite_args("grep", args)
    assert new_args["path"] == "/wiki/topics"
    assert new_args["pattern"] == "whatsapp"
    assert len(corrections) == 1
    assert corrections[0]["key"] == "path"


# ---------------------------------------------------------------------------
# Middleware integration — wrap_tool_call annotates the ToolMessage
# ---------------------------------------------------------------------------


def test_middleware_annotates_tool_message_on_rewrite() -> None:
    mw = PathAutohealMiddleware()

    captured_args: dict[str, object] = {}

    def handler(request: ToolCallRequest) -> ToolMessage:
        captured_args.update(request.tool_call.get("args") or {})
        return ToolMessage(content="ok", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "read_file",
                "args": {"file_path": "/Users/foo/git/repo/raw/x.md"},
                "id": "1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=None,  # type: ignore[arg-type]
        ),
    )
    result = mw.wrap_tool_call(request, handler)

    # Args got rewritten before the handler saw them.
    assert captured_args["file_path"] == "/raw/x.md"
    # Result is annotated for scorecard pickup.
    assert isinstance(result, ToolMessage)
    assert result.additional_kwargs["auto_corrected_from"] == ["/Users/foo/git/repo/raw/x.md"]
    assert result.additional_kwargs["auto_corrected_to"] == ["/raw/x.md"]
    assert result.additional_kwargs["auto_corrected_confidence"] == "high"


def test_middleware_leaves_clean_request_unannotated() -> None:
    mw = PathAutohealMiddleware()

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "read_file",
                "args": {"file_path": "/raw/already/virtual.md"},
                "id": "1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=None,  # type: ignore[arg-type]
        ),
    )
    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    # No correction → no kwargs.
    assert "auto_corrected_from" not in result.additional_kwargs


def test_middleware_passes_unknown_tool_unchanged() -> None:
    mw = PathAutohealMiddleware()

    seen_name: list[str] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        seen_name.append(request.tool_call.get("name", ""))
        return ToolMessage(content="ok", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "resolve_page",
                "args": {"query": "something"},
                "id": "1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=None,  # type: ignore[arg-type]
        ),
    )
    result = mw.wrap_tool_call(request, handler)
    assert seen_name == ["resolve_page"]
    assert isinstance(result, ToolMessage)
    assert "auto_corrected_from" not in result.additional_kwargs


async def test_middleware_async_path_works() -> None:
    mw = PathAutohealMiddleware()

    captured_args: dict[str, object] = {}

    async def handler(request: ToolCallRequest) -> ToolMessage:
        captured_args.update(request.tool_call.get("args") or {})
        return ToolMessage(content="ok", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "write_file",
                "args": {
                    "file_path": "/Users/foo/git/repo/wiki/topics/bar.md",
                    "content": "hello",
                },
                "id": "1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=None,  # type: ignore[arg-type]
        ),
    )
    result = await mw.awrap_tool_call(request, handler)
    assert captured_args["file_path"] == "/wiki/topics/bar.md"
    assert isinstance(result, ToolMessage)
    assert result.additional_kwargs["auto_corrected_to"] == ["/wiki/topics/bar.md"]


@pytest.mark.parametrize(
    "input_path,expected",
    [
        ("/some/prefix/wiki/systems/buylead.md", "/wiki/systems/buylead.md"),
        ("/tmp/compile-view-abc/wiki/people/amit.md", "/wiki/people/amit.md"),
        ("/tmp/compile-view-abc/raw/2026-04-15_foo.md", "/raw/2026-04-15_foo.md"),
    ],
)
def test_try_rewrite_parametrised(input_path: str, expected: str) -> None:
    assert _try_rewrite(input_path) == expected
