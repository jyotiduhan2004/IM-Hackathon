"""Gate `check_my_work` on actual successful content-page writes.

Why this exists: live trace data shows the compile agent calling
`check_my_work` BEFORE doing any real edits (78% baseline → 59% after
Tier B prompt-level nudges). That makes the validator useless — it
runs over an empty touched-pages set, returns "clean", and the agent
moves on without ever having written anything.

This middleware has TWO responsibilities:

1. **Pre-write block**: any `check_my_work` call made before a
   successful content write is short-circuited with a synthetic error
   `ToolMessage`. Explicitly NOT counted as content writes:
   - `write_draft_page` — drafts are exploratory scratch work, not wiki
     content. A draft-only session isn't ready for a quality critique.
   - Failed writes (tool returned an error-status `ToolMessage` or raised)
     — if the write didn't land, the critique has nothing to inspect.
2. **Post-write requirement** (V12 audit fix-B): the V12 50-compile
   deep audit found NEW pages shipping with zero reviewer cycles —
   the agent wrote once and returned without ever self-checking. To
   close that loop, when the agent signals batch exit (an `AIMessage`
   with no tool calls) AND a content write has landed without a
   subsequent `check_my_work`, we inject a synthetic `AIMessage`
   asking for one and force `jump_to: "model"` so the agent stays in
   the loop for one more cycle.

The post-write requirement is a **soft nudge**, not a crash: the
agent can still choose to re-exit without calling `check_my_work`,
and downstream guards (PR C — compile-loop terminal-decision guard)
catch agents that explicitly refuse.

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
from langchain.agents.middleware.types import AgentState
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage
from langchain_core.messages import ToolCall
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
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

# The pre-write rejection message. Kept as a module constant so the
# scorecard can regex-match against trace output without coupling to
# this file's exact wording.
GATE_REJECT_MESSAGE = (
    "Rejected: call check_my_work only after you've successfully "
    "edited or written content-page files in this session. Draft "
    "writes do not count."
)

# The post-write nudge message. Distinct wording from the pre-write
# rejection so trace analyses can tell the two apart. Injected as an
# `AIMessage` into the agent's own history — we want the model to
# re-read it on the next cycle the way it would re-read any tool
# message.
POST_WRITE_NUDGE_MESSAGE = (
    "You wrote content without running check_my_work. Call "
    "check_my_work(raw_email_path=...) now so the page's structure, "
    "citations, and wikilinks are validated before you return."
)

# Canonical regex for detecting pre-write rejections in Langfuse trace
# output. Shared with scripts/trace_scorecard.py +
# scripts/nightly_trace_audit.py so the two reports use the same truth.
GATE_REJECT_PAT = re.compile(
    r"Rejected:\s*call\s+check_my_work\s+only\s+after",
    re.IGNORECASE,
)

# Canonical regex for detecting post-write nudges. Same sharing pattern
# as GATE_REJECT_PAT.
POST_WRITE_NUDGE_PAT = re.compile(
    r"wrote\s+content\s+without\s+running\s+check_my_work",
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
    """Block premature `check_my_work` AND require post-write check.

    Stateful per agent run. Both sync and async tool-call paths are
    implemented so this works whether the caller uses `invoke` or
    `ainvoke`. Other tool names pass through without any inspection
    beyond updating the trackers.
    """

    def __init__(self) -> None:
        super().__init__()
        self.successful_write_tools: set[str] = set()
        self.touched_paths: set[str] = set()
        # True iff a content write has landed since the last
        # successful `check_my_work` call. Used by `after_model` to
        # decide whether to nudge a premature exit.
        self._unchecked_write_pending: bool = False
        # Guard against double-nudging the same exit attempt. The
        # agent may respond to the nudge with another empty
        # AIMessage — if so, PR C (separate) catches it; we do NOT
        # keep nudging.
        self._nudged_this_exit: bool = False

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

    def _record_success(self, tool_name: str, tool_call: ToolCall) -> None:
        """Record a successful content write for gating purposes.

        `file_path` / `slug` are pulled purely for the breadcrumb in
        `touched_paths`; the gate rule itself only needs the tool name.
        """
        self.successful_write_tools.add(tool_name)
        self._unchecked_write_pending = True
        # A fresh write invalidates any prior "already nudged" state —
        # the agent has done more work that deserves another check.
        self._nudged_this_exit = False
        args = tool_call.get("args") or {}
        path = args.get("file_path") or args.get("slug") or ""
        if isinstance(path, str) and path:
            self.touched_paths.add(path)

    def _record_check_my_work(self, result: ToolMessage | Command[Any]) -> None:
        """Clear the unchecked-write flag after a successful critique call.

        Only successful `ToolMessage` results count — a rejected
        pre-write call would otherwise clear the flag without the
        agent actually running the critique.
        """
        if _is_successful_tool_message(result):
            self._unchecked_write_pending = False
            self._nudged_this_exit = False

    def _maybe_record(self, request: ToolCallRequest, result: ToolMessage | Command[Any]) -> None:
        """Post-handler: count a content write or clear on critique."""
        tool_name = request.tool_call.get("name") or ""
        if tool_name in _CONTENT_WRITE_TOOLS and _is_successful_tool_message(result):
            self._record_success(tool_name, request.tool_call)
        elif tool_name == "check_my_work":
            self._record_check_my_work(result)

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

    def _post_write_nudge(self, state: AgentState[Any]) -> dict[str, Any] | None:
        """If agent is exiting with an unchecked write, inject a nudge.

        Returns a state update that (a) appends an `AIMessage` asking
        the agent to call `check_my_work`, and (b) sets `jump_to:
        "model"` so the graph routes back to the model node for one
        more cycle instead of ending.

        Returns None when:
        - No content write has happened, OR
        - A content write happened and `check_my_work` was called
          since, OR
        - The agent is still mid-loop (the last AIMessage has tool
          calls — it hasn't tried to exit yet), OR
        - We already nudged this exit attempt (don't infinite-loop).
        """
        if not self._unchecked_write_pending or self._nudged_this_exit:
            return None

        messages = state.get("messages") or []
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai is None:
            return None
        # Only nudge when the agent intends to exit — i.e. its last
        # message carries no tool calls. If it's still issuing tool
        # calls, leave it alone; the next post-tool cycle will
        # re-enter `after_model` and we'll check again.
        if last_ai.tool_calls:
            return None

        self._nudged_this_exit = True
        logger.info(
            "check_my_work_gate_post_write_nudge",
            touched_paths=sorted(self.touched_paths),
        )
        return {
            "jump_to": "model",
            "messages": [AIMessage(content=POST_WRITE_NUDGE_MESSAGE)],
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Nudge the agent back into the loop when it tries to exit with an unchecked write."""
        return self._post_write_nudge(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Async variant. Delegates to the sync logic — no IO here."""
        return self._post_write_nudge(state)
