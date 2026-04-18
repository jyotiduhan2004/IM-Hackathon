"""glob_narrowing middleware — reject slug-lookup glob patterns.

Why this exists: per-observation Langfuse scores (issue #185) show
24.5% of `glob` calls time out at deepagents' hardcoded 20s
`GLOB_TIMEOUT`. p50 latency is 7.6s and p95 is 27s. Every observed
timeout pattern is `**/<slug>.md` — the agent is using `glob` as a
fuzzy slug lookup, which is `resolve_page`'s job. Wall-time burned
was ~23 minutes per 5-day compile window.

This middleware intercepts `glob` tool calls whose `pattern` both
starts with `**/` AND ends with `.md`, and rejects them with a
pointer to `resolve_page`. Legitimate enumeration patterns
(`wiki/topics/*.md`, `/raw/*.md`, etc.) pass through untouched.

Shape mirrors `same_thread_topic_guard.py` — subclass
`AgentMiddleware` with sync + async `wrap_tool_call` hooks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.types import Command

logger = structlog.get_logger(__name__)


_REJECTION_MESSAGE = (
    "Use resolve_page for slug lookups; glob is only for enumerate-all "
    "cases (e.g., 'wiki/topics/*.md')."
)


def _is_slug_lookup_pattern(pattern: str) -> bool:
    """Return True for recursive-wildcard single-file patterns like `**/foo.md`.

    These are the 24.5%-timeout cases per issue #185. Legitimate enumeration
    patterns that start with `**/` but match many files (e.g. `**/*.md`)
    contain a `*` in the filename stem and pass through.

    A leading slash is stripped before matching so the rooted form
    `/**/seller-isq.md` is caught as easily as the relative form
    `**/seller-isq.md` (v10-U5 followup P2, #190). The sandbox's
    filesystem adapter accepts either, so both have to be guarded.
    """
    stripped = pattern.strip().lstrip("/")
    if not stripped.startswith("**/"):
        return False
    if not stripped.endswith(".md"):
        return False
    # `**/*.md` is enumerate-all, not slug lookup — allow.
    stem = stripped[len("**/") : -len(".md")]
    return "*" not in stem and "?" not in stem and "[" not in stem


def _rejection_tool_message(tool_call_id: str, pattern: str) -> ToolMessage:
    return ToolMessage(
        content=f"{_REJECTION_MESSAGE} (rejected pattern: {pattern!r})",
        status="error",
        tool_call_id=tool_call_id,
    )


def _maybe_reject(tool_name: str, args: dict[str, Any]) -> str | None:
    """Return the offending pattern when this call is a slug-lookup glob, else None."""
    if tool_name != "glob":
        return None
    pattern = args.get("pattern")
    if not isinstance(pattern, str):
        return None
    if not _is_slug_lookup_pattern(pattern):
        return None
    return pattern


class GlobNarrowingMiddleware(AgentMiddleware):
    """Reject `glob('**/<slug>.md')` slug-lookup calls; pass others through."""

    @property
    def name(self) -> str:
        return "glob_narrowing"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejected_pattern = _maybe_reject(tool_name, args)
        if rejected_pattern is not None:
            logger.warning("guard.glob_narrowing", pattern=rejected_pattern)
            tool_call_id = request.tool_call.get("id") or ""
            return _rejection_tool_message(tool_call_id, rejected_pattern)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejected_pattern = _maybe_reject(tool_name, args)
        if rejected_pattern is not None:
            logger.warning("guard.glob_narrowing", pattern=rejected_pattern)
            tool_call_id = request.tool_call.get("id") or ""
            return _rejection_tool_message(tool_call_id, rejected_pattern)
        return await handler(request)
