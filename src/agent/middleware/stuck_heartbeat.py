"""stuck_heartbeat middleware — track wall-clock since last tool-call return.

This middleware is half of the stuck-LLM-round detector. It owns nothing
except a mutable timestamp — every successful or failed tool-call return
stamps the current monotonic time onto a shared `StuckHeartbeatState`.
The other half (an asyncio task in `compiler._ainvoke_with_timeout`) wakes
periodically, reads the same timestamp, and cancels the agent task if it
hasn't moved in `STUCK_AFTER_S` seconds.

Why split the responsibility this way:

- The middleware runs *inside* the agent's coroutine on every tool-call
  return, so it sees those events without any extra plumbing.
- The watcher runs *alongside* the agent's coroutine via
  `asyncio.create_task`, so it can fire while the agent is blocked
  waiting on the model. A purely middleware-based approach can't fire
  during a wedged LLM round because middleware only runs around tool
  calls.

The state object is a tiny dataclass-style holder (not an asyncio.Event)
because we only need a monotonic timestamp; a "ping" event has no extra
information over the timestamp itself, and the watcher polls anyway.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = structlog.get_logger(__name__)


@dataclass
class StuckHeartbeatState:
    """Shared mutable state between the middleware and the heartbeat watcher.

    `last_tool_return_at` is `time.monotonic()` from the last tool-call
    return, or `None` before any tool has returned. The middleware writes;
    the watcher reads. The watcher skips its check when `None` so a slow
    first LLM round (high-context deliberation > `stuck_after_s` before
    the first tool call) doesn't get falsely killed — the outer
    `invoke_timeout_s` is the safety net for that initial phase.
    """

    last_tool_return_at: float | None = None

    def mark(self) -> None:
        """Reset the heartbeat to now. Cheap; called on every tool return."""
        self.last_tool_return_at = time.monotonic()


class StuckHeartbeatMiddleware(AgentMiddleware):
    """Update `state.last_tool_return_at` on every tool-call return.

    Stamping happens whether the tool succeeded, errored, or returned a
    `Command` — any return is evidence the agent loop is making progress.
    The watcher decides what counts as "stuck"; this middleware never
    blocks or modifies the tool-call return.
    """

    def __init__(self, state: StuckHeartbeatState) -> None:
        super().__init__()
        self.state = state

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            result = handler(request)
        finally:
            self.state.mark()
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            result = await handler(request)
        finally:
            self.state.mark()
        return result
