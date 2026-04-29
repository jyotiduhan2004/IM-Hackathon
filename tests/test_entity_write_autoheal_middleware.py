"""Unit tests for src/compile/middleware/entity_write_autoheal.py."""

from __future__ import annotations

from typing import cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.agent.middleware.entity_write_autoheal import EntityWriteAutohealMiddleware
from src.agent.middleware.entity_write_autoheal import _is_entity_write

# ---------------------------------------------------------------------------
# _is_entity_write — pure detection
# ---------------------------------------------------------------------------


def test_detects_entity_write_on_virtual_path() -> None:
    ok, target = _is_entity_write(
        "write_file",
        {"file_path": "/wiki/entities/amit-indiamart-com.md"},
    )
    assert ok
    assert target == "/wiki/entities/amit-indiamart-com.md"


def test_detects_entity_write_on_relative_path() -> None:
    ok, target = _is_entity_write(
        "edit_file",
        {"file_path": "wiki/entities/amit.md"},
    )
    assert ok
    assert target == "wiki/entities/amit.md"


def test_detects_people_path_post_tier_p() -> None:
    ok, target = _is_entity_write(
        "write_file",
        {"file_path": "/wiki/people/lucky.md"},
    )
    assert ok
    assert target == "/wiki/people/lucky.md"


def test_ignores_non_entity_paths() -> None:
    ok, _ = _is_entity_write(
        "write_file",
        {"file_path": "/wiki/topics/whatsapp-rollout.md"},
    )
    assert not ok


def test_ignores_non_write_tools() -> None:
    ok, _ = _is_entity_write(
        "read_file",
        {"file_path": "/wiki/entities/amit.md"},
    )
    assert not ok


def test_ignores_non_md_files() -> None:
    ok, _ = _is_entity_write(
        "write_file",
        {"file_path": "/wiki/entities/README.txt"},
    )
    assert not ok


# ---------------------------------------------------------------------------
# Middleware — hint is appended to content + kwargs
# ---------------------------------------------------------------------------


def test_middleware_appends_hint_on_entity_write() -> None:
    mw = EntityWriteAutohealMiddleware()

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="wrote page", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "write_file",
                "args": {"file_path": "/wiki/entities/amit.md"},
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
    assert "create_entities" in str(result.content)
    assert result.additional_kwargs.get("entity_write_hinted") is True
    assert result.additional_kwargs.get("entity_write_target") == "/wiki/entities/amit.md"


def test_middleware_does_not_hint_on_topic_write() -> None:
    mw = EntityWriteAutohealMiddleware()

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="wrote page", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "write_file",
                "args": {"file_path": "/wiki/topics/bar.md"},
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
    assert result.content == "wrote page"
    assert "entity_write_hinted" not in result.additional_kwargs


def test_middleware_does_not_hint_on_error() -> None:
    mw = EntityWriteAutohealMiddleware()

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="error: disk full", tool_call_id="1", status="error")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "write_file",
                "args": {"file_path": "/wiki/entities/amit.md"},
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
    # Error messages are untouched — the agent already has a signal.
    assert "create_entities" not in str(result.content)
    assert "entity_write_hinted" not in result.additional_kwargs


async def test_middleware_async_path() -> None:
    mw = EntityWriteAutohealMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="1", status="success")

    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "edit_file",
                "args": {"file_path": "/wiki/people/lucky.md"},
                "id": "1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=None,  # type: ignore[arg-type]
        ),
    )
    result = await mw.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.additional_kwargs.get("entity_write_hinted") is True
