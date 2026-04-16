"""Gate `check_my_work` on actual successful content-page writes.

Why this exists: live trace data shows the compile agent calling
`check_my_work` BEFORE doing any real edits (78% baseline → 59% after
Tier B prompt-level nudges). That makes the validator useless — it
runs over an empty touched-pages set, returns "clean", and the agent
moves on without ever having written anything.

This middleware is a hard gate, not a nudge. Until the session
records a successful write from a content-writing tool (`write_file`,
`edit_file`, `patch_page`), any `check_my_work` call is short-circuited
with a synthetic error `ToolMessage` that tells the agent to write
first, check after.

Explicitly NOT counted as "content writes":
- `write_draft_page` — drafts are exploratory scratch work, not wiki
  content. A draft-only session isn't ready for a quality critique.
- Failed writes (tool returned an error-status `ToolMessage` or raised)
  — if the write didn't land, the critique has nothing to inspect.

State is per-instance (one middleware instance per agent run). We
intentionally track state internally rather than scanning
`request.state["messages"]` on every call because:
- A scan is O(n) in the message history; the gate runs on EVERY tool
  call, so this adds up in long batches.
- The scan would need to reconstruct "did this tool succeed?" from
  noisy `ToolMessage` shapes, which is more failure modes than the
  gate itself justifies.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = structlog.get_logger(__name__)


# Tools that count as "content page writes" when they succeed. Kept
# narrow — `write_draft_page` is deliberately absent (drafts ≠ content).
# If the agent's tool surface grows a new content-writing tool, add it
# here AND to the scorecard rubric so the metric stays in sync.
_CONTENT_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "patch_page",
    }
)

# The rejection message. Kept as a module constant so the scorecard
# can regex-match against trace output without coupling to this file's
# exact wording.
GATE_REJECT_MESSAGE = (
    "Rejected: call check_my_work only after you've successfully "
    "edited or written content-page files in this session. Draft "
    "writes do not count."
)

# Canonical regex for detecting rejections in Langfuse trace output.
# Shared with scripts/trace_scorecard.py + scripts/nightly_trace_audit.py
# so the two reports use the same truth.
GATE_REJECT_PAT = re.compile(
    r"Rejected:\s*call\s+check_my_work\s+only\s+after",
    re.IGNORECASE,
)


def _is_successful_tool_message(result: Any) -> bool:
    """Decide whether a tool result represents a successful write.

    A `ToolMessage` with `status != "error"` is success; `status ==
    "error"` is failure. `Command` results skip the tool-message path
    entirely — we treat those as "not a content write" because the
    agent hasn't actually produced a `ToolMessage` record the critique
    can chase. That's conservative, which is what we want here.
    """
    if isinstance(result, ToolMessage):
        return getattr(result, "status", None) != "error"
    return False


class CheckMyWorkGateMiddleware(AgentMiddleware):
    """Block `check_my_work` calls until at least one content-page write succeeds.

    Stateful per agent run. Both sync and async tool-call paths are
    implemented so this works whether the caller uses `invoke` or
    `ainvoke`. Other tool names pass through without any inspection
    beyond updating the success tracker.
    """

    def __init__(self) -> None:
        super().__init__()
        self.successful_write_tools: set[str] = set()
        self.touched_paths: set[str] = set()

    def _short_circuit_if_premature(self, request: ToolCallRequest) -> ToolMessage | None:
        """Return a synthetic rejection when `check_my_work` is premature, else None.

        Premature = the tool call is `check_my_work` AND no content
        write has landed yet this session. Callers fall through to the
        real handler when None is returned.
        """
        tool_name = request.tool_call.get("name") or ""
        if tool_name != "check_my_work" or self.successful_write_tools:
            return None
        tool_call_id = request.tool_call.get("id") or ""
        logger.info("check_my_work_gate_rejected", tool_call_id=tool_call_id)
        return ToolMessage(
            content=GATE_REJECT_MESSAGE,
            tool_call_id=tool_call_id,
            name="check_my_work",
            status="error",
        )

    def _record_success(self, tool_name: str, tool_call: dict[str, Any]) -> None:
        """Record a successful content write for gating purposes.

        `file_path` / `slug` are pulled purely for the breadcrumb in
        `touched_paths`; the gate rule itself only needs the tool name.
        """
        self.successful_write_tools.add(tool_name)
        args = tool_call.get("args") or {}
        path = args.get("file_path") or args.get("slug") or ""
        if isinstance(path, str) and path:
            self.touched_paths.add(path)

    def _maybe_record(self, request: ToolCallRequest, result: ToolMessage | Command[Any]) -> None:
        """Post-handler: count a content write toward gate state if it succeeded."""
        tool_name = request.tool_call.get("name") or ""
        if tool_name in _CONTENT_WRITE_TOOLS and _is_successful_tool_message(result):
            self._record_success(tool_name, request.tool_call)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._short_circuit_if_premature(request)
        if rejection is not None:
            return rejection
        result = handler(request)
        self._maybe_record(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._short_circuit_if_premature(request)
        if rejection is not None:
            return rejection
        result = await handler(request)
        self._maybe_record(request, result)
        return result
