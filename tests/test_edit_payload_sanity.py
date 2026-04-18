"""Unit tests for src/compile/middleware/edit_payload_sanity.py."""

from __future__ import annotations

from typing import cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.compile.middleware.edit_payload_sanity import MAX_PAYLOAD_BYTES
from src.compile.middleware.edit_payload_sanity import EditPayloadSanityMiddleware
from src.compile.middleware.edit_payload_sanity import _extract_content
from src.compile.middleware.edit_payload_sanity import _maybe_reject

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_content_from_write_file() -> None:
    assert _extract_content("write_file", {"content": "hello"}) == "hello"


def test_extract_content_from_edit_file() -> None:
    assert _extract_content("edit_file", {"content": "world"}) == "world"


def test_extract_content_from_new_string_arg() -> None:
    assert _extract_content("edit_file", {"new_string": "patched"}) == "patched"


def test_extract_content_non_guarded_tool() -> None:
    assert _extract_content("read_file", {"content": "x"}) is None


def test_extract_content_missing() -> None:
    assert _extract_content("write_file", {"file_path": "/wiki/topics/x.md"}) is None


# ---------------------------------------------------------------------------
# _maybe_reject — composition of size + YAML checks
# ---------------------------------------------------------------------------


def test_reject_oversized_payload() -> None:
    content = "x" * 60_000
    result = _maybe_reject("write_file", {"content": content})
    assert result is not None
    reason, msg = result
    assert reason == "size"
    assert "60KB" in msg
    assert "limit 50KB" in msg
    assert "source_threads" in msg


def test_reject_malformed_yaml() -> None:
    # Unquoted colon after a plain scalar raises a YAMLError.
    content = "---\ntitle: Foo: Bar: Baz\n---\n\nBody.\n"
    result = _maybe_reject("edit_file", {"content": content})
    assert result is not None
    reason, msg = result
    assert reason == "yaml"
    assert "not valid YAML" in msg
    assert "Re-edit and retry" in msg


def test_accept_valid_yaml_under_limit() -> None:
    content = "---\ntitle: Foo\nstatus: active\n---\n\nBody.\n"
    assert _maybe_reject("write_file", {"content": content}) is None


def test_accept_plain_markdown_no_frontmatter() -> None:
    content = "# Heading\n\nJust prose, no frontmatter.\n"
    assert _maybe_reject("write_file", {"content": content}) is None


def test_accept_unclosed_frontmatter_marker_as_plain_content() -> None:
    # `---` at start without a matching closing delimiter isn't frontmatter —
    # skip the YAML check rather than fail; `split_frontmatter` handles this.
    content = "---\ntitle: no closer\nbody without closing delim\n"
    assert _maybe_reject("write_file", {"content": content}) is None


def test_threshold_boundary_49kb_passes() -> None:
    # Plain ASCII, so byte-count == char-count.
    content = "a" * 49_000
    assert _maybe_reject("write_file", {"content": content}) is None


def test_threshold_boundary_51kb_fails() -> None:
    content = "a" * 51_000
    result = _maybe_reject("write_file", {"content": content})
    assert result is not None
    reason, msg = result
    assert reason == "size"
    assert "51KB" in msg


def test_threshold_exact_limit_passes() -> None:
    # MAX_PAYLOAD_BYTES is the last-allowed size; equal passes.
    content = "a" * MAX_PAYLOAD_BYTES
    assert _maybe_reject("write_file", {"content": content}) is None


def test_non_guarded_tool_passes() -> None:
    # Even a huge payload on a non-guarded tool is not this middleware's job.
    content = "a" * 100_000
    assert _maybe_reject("read_file", {"content": content}) is None


# ---------------------------------------------------------------------------
# Middleware — tool is NOT invoked on rejection, IS invoked on pass-through
# ---------------------------------------------------------------------------


def _make_request(tool_name: str, args: dict[str, object]) -> ToolCallRequest:
    return cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": tool_name,
                "args": args,
                "id": "tc-1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=None,  # type: ignore[arg-type]
        ),
    )


def test_middleware_rejects_oversized_without_invoking_tool() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="ok", tool_call_id="tc-1", status="success")

    request = _make_request("write_file", {"content": "x" * 60_000})
    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "60KB" in str(result.content)
    assert invoked == []


def test_middleware_rejects_malformed_yaml_without_invoking_tool() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="ok", tool_call_id="tc-1", status="success")

    bad_yaml = "---\ntitle: Foo: Bar: Baz\n---\n\nBody.\n"
    request = _make_request("edit_file", {"content": bad_yaml})
    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "not valid YAML" in str(result.content)
    assert invoked == []


def test_middleware_passes_valid_payload_through() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="wrote page", tool_call_id="tc-1", status="success")

    good = "---\ntitle: Foo\nstatus: active\n---\n\nBody.\n"
    request = _make_request("write_file", {"content": good})
    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert invoked == [True]


def test_middleware_passes_plain_markdown_through() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="wrote page", tool_call_id="tc-1", status="success")

    request = _make_request("write_file", {"content": "# Heading\n\nPlain body.\n"})
    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert invoked == [True]


def test_middleware_ignores_non_guarded_tools() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="read", tool_call_id="tc-1", status="success")

    # read_file with a gigantic `content` kwarg — still must pass through,
    # because this middleware's scope is edit/write only.
    request = _make_request("read_file", {"content": "x" * 100_000})
    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert invoked == [True]


async def test_middleware_async_rejects_oversized() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="ok", tool_call_id="tc-1", status="success")

    request = _make_request("write_file", {"content": "x" * 60_000})
    result = await mw.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "60KB" in str(result.content)
    assert invoked == []


async def test_middleware_async_passes_valid() -> None:
    mw = EditPayloadSanityMiddleware()
    invoked: list[bool] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        invoked.append(True)
        return ToolMessage(content="ok", tool_call_id="tc-1", status="success")

    good = "---\ntitle: Foo\n---\n\nBody.\n"
    request = _make_request("edit_file", {"content": good})
    result = await mw.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert invoked == [True]
