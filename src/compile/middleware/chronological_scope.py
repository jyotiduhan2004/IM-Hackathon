"""chronological_scope middleware — reject future-dated raw reads (Bug H).

The prompt tells the agent it's processing email N of a thread "as a writer
at that point in time" and should not see future replies. `get_thread_context`
now enforces this with a cutoff (see `src/compile/compiler.py`), but the
agent can still leak future content via `read_file` if it happens to know
or guess a future-dated raw path (e.g. from an earlier cached response).

This middleware is the belt-and-suspenders guard. When the coordinator has
set the `_current_batch_cutoff_date` ContextVar for this batch, any
`read_file` call on a path under `/raw/` (or a virtual `raw/`) whose
filename prefix is `YYYY-MM-DD_` beyond the cutoff is rejected with a
structured error that names both the cutoff and the offending file, so
the agent can redirect to in-scope reads.

The raw-filename contract is set by `scripts/ingest_backlog.py`:
``YYYY-MM-DD_<slug>_<msgid-hash>.md``. We rely on that prefix — no DB
call per read. If a future-dated read slips through (non-canonical
filename, pre-ingest migration fossil), the rejection does not fire and
the read proceeds — correctness preserved.
"""

from __future__ import annotations

import json
import re
from datetime import date
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

# Match the raw-filename prefix produced by the ingest pipeline. Anchors
# on a path segment boundary (``/`` or start-of-string) so a stray
# substring like ``stuff/2026-12-31_notes.md`` in a non-raw path still
# parses — but see `_is_raw_path` which filters by segment.
_RAW_DATE_PREFIX_RE = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})_")


def _is_raw_path(path: str) -> bool:
    """True when ``path`` refers to the ``/raw/`` mount (virtual or relative)."""
    return path.startswith("raw/") or "/raw/" in path


def _raw_file_date(path: str) -> date | None:
    """Extract YYYY-MM-DD from a raw path's filename, or None if unparseable."""
    match = _RAW_DATE_PREFIX_RE.search(path)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _cutoff_date() -> date | None:
    """Pull the batch cutoff from the ContextVar and parse to a date.

    The ContextVar stores an ISO8601 timestamp string (possibly with TZ
    suffix); we only need the date part for the filename comparison.
    """
    from src.compile.compiler import _current_batch_cutoff_date

    raw = _current_batch_cutoff_date.get()
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _check_future_raw(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Return a rejection dict when this is a future-dated raw read; else None."""
    if tool_name != "read_file":
        return None
    path = args.get("file_path") or args.get("path")
    if not isinstance(path, str):
        return None
    if not _is_raw_path(path):
        return None
    cutoff = _cutoff_date()
    if cutoff is None:
        return None
    file_date = _raw_file_date(path)
    if file_date is None:
        return None
    if file_date <= cutoff:
        return None
    return {
        "ok": False,
        "reason": "future_dated_raw",
        "file_path": path,
        "file_date": file_date.isoformat(),
        "cutoff_date": cutoff.isoformat(),
        "guidance": (
            f"Rejected: raw file dated {file_date.isoformat()} is later than "
            f"this batch's cutoff ({cutoff.isoformat()}). You are compiling "
            f"email N of a thread as a writer at that point in time — later "
            f"replies in the thread are out of scope. Use `get_thread_context` "
            f"(which auto-applies the same cutoff) for in-scope messages only."
        ),
    }


def _rejection_message(tool_call_id: str, payload: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        status="error",
        tool_call_id=tool_call_id,
    )


class ChronologicalScopeMiddleware(AgentMiddleware):
    """Reject `read_file` on raws dated later than the batch's cutoff (Bug H)."""

    @property
    def name(self) -> str:
        return "chronological_scope"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejection = _check_future_raw(tool_name, args)
        if rejection is None:
            return handler(request)
        logger.info(
            "chronological_scope_reject",
            tool=tool_name,
            file_path=rejection["file_path"],
            cutoff=rejection["cutoff_date"],
            file_date=rejection["file_date"],
        )
        return _rejection_message(request.tool_call["id"], rejection)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejection = _check_future_raw(tool_name, args)
        if rejection is None:
            return await handler(request)
        logger.info(
            "chronological_scope_reject",
            tool=tool_name,
            file_path=rejection["file_path"],
            cutoff=rejection["cutoff_date"],
            file_date=rejection["file_date"],
        )
        return _rejection_message(request.tool_call["id"], rejection)
