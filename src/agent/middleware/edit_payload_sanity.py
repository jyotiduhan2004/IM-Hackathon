"""edit_payload_sanity middleware — reject oversized + malformed-YAML writes.

Two failure modes we've seen in production traces (v10 cycle 10 prep):

1. **Oversized payloads.** The agent occasionally pastes an entire raw-email
   body into a wiki page on `edit_file` / `write_file`. The reviewer catches
   it on the next turn and rejects, wasting a compile turn. Raw-email
   content belongs in `source_threads:` frontmatter, not page bodies.

2. **Malformed YAML frontmatter.** 6 current wiki pages have unparseable
   YAML (unquoted colons, indentation drift). There's no upstream guard —
   these pages were committed and the next tool that tried to parse them
   (validators, scorecard) crashes or silently skips.

This middleware intercepts `edit_file` and `write_file` tool calls,
inspects the content payload, and rejects with an agent-actionable
message before the tool runs. Pass-through otherwise.

Rules:
  - Size guard: content > MAX_PAYLOAD_BYTES (50_000) → reject.
  - YAML dry-parse: if content starts with `---\\n` and contains a
    second `---`, extract the frontmatter block and run
    `yaml.safe_load`. On `yaml.YAMLError`, reject.
  - Both checks skip on any non-`edit_file`/`write_file` tool call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import structlog
import yaml
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from src.utils import split_frontmatter

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.types import Command

logger = structlog.get_logger(__name__)

_GUARDED_TOOLS: frozenset[str] = frozenset({"edit_file", "write_file"})

# Real wiki pages top out ~10-15KB; a 50KB payload is almost certainly a
# raw-email paste or duplicated content.
MAX_PAYLOAD_BYTES = 50_000


def _extract_content(tool_name: str, args: dict[str, Any]) -> str | None:
    """Return the content payload from an edit/write tool call, or None.

    `write_file` uses a `content` arg in deepagents' FilesystemMiddleware;
    `edit_file` uses `new_string` for the replacement text. Some surfaces
    also expose `text`. We check all three for resilience.
    """
    if tool_name not in _GUARDED_TOOLS:
        return None
    for key in ("content", "text", "new_string"):
        val = args.get(key)
        if isinstance(val, str):
            return val
    return None


def _size_rejection(content: str) -> str | None:
    """Return an agent-actionable rejection string if payload is too large."""
    size = len(content.encode("utf-8"))
    if size <= MAX_PAYLOAD_BYTES:
        return None
    n_kb = size // 1000
    limit_kb = MAX_PAYLOAD_BYTES // 1000
    return (
        f"Payload is {n_kb}KB (limit {limit_kb}KB). Summarize or split "
        f"into multiple smaller edits — raw-email dumps belong in "
        f"source_threads, not page bodies."
    )


def _yaml_rejection(content: str) -> str | None:
    """Return an agent-actionable rejection string if frontmatter is invalid.

    None when there's no frontmatter to check, or when parsing succeeds.
    Uses `split_frontmatter` (src/utils) which correctly requires `---`
    on its own line — raw `content.split("---", 2)` incorrectly splits
    on `---` anywhere in the body.
    """
    fm_text, _ = split_frontmatter(content)
    if not fm_text:
        return None
    try:
        yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        return (
            f"Frontmatter is not valid YAML: {exc}. Common fixes: quote "
            f"strings with colons, escape special chars, match indentation. "
            f"Re-edit and retry."
        )
    return None


def _maybe_reject(tool_name: str, args: dict[str, Any]) -> tuple[str, str] | None:
    """Return `(reason, message)` when this call should be blocked; else None.

    `reason` is a stable tag for logging (`"size"` | `"yaml"`); `message` is
    the agent-facing rejection string. Size check runs first (cheap, most
    common failure mode), then YAML.
    """
    content = _extract_content(tool_name, args)
    if content is None:
        return None
    size_rej = _size_rejection(content)
    if size_rej is not None:
        return ("size", size_rej)
    yaml_rej = _yaml_rejection(content)
    if yaml_rej is not None:
        return ("yaml", yaml_rej)
    return None


def _rejection_message(tool_call_id: str, message: str) -> ToolMessage:
    return ToolMessage(
        content=message,
        status="error",
        tool_call_id=tool_call_id,
    )


def _build_rejection_response(
    request: ToolCallRequest,
    rejection: tuple[str, str],
    args: dict[str, Any],
    tool_name: str,
) -> ToolMessage:
    """Emit the structured reject log and build the agent-facing ToolMessage.

    Extracted so the sync + async `wrap_tool_call` paths don't drift on
    log fields (v10 followup #189 added `file_path` — any future
    additions land here once).
    """
    reason, message = rejection
    logger.warning(
        "edit_payload_sanity_reject",
        tool=tool_name,
        reason=reason,
        file_path=args.get("file_path") or args.get("path") or "",
    )
    tool_call_id = request.tool_call.get("id") or ""
    return _rejection_message(tool_call_id, message)


class EditPayloadSanityMiddleware(AgentMiddleware):
    """Reject oversized or malformed-YAML edit_file / write_file payloads."""

    MAX_PAYLOAD_BYTES = MAX_PAYLOAD_BYTES

    @property
    def name(self) -> str:
        return "edit_payload_sanity"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejection = _maybe_reject(tool_name, args)
        if rejection is None:
            return handler(request)
        return _build_rejection_response(request, rejection, args, tool_name)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejection = _maybe_reject(tool_name, args)
        if rejection is None:
            return await handler(request)
        return _build_rejection_response(request, rejection, args, tool_name)
