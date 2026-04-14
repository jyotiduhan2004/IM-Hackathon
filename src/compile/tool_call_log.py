"""Callback handler that captures per-tool-call telemetry for compile runs.

`BatchStatsCallback` (cache_stats.py) only aggregates COUNT of tool calls. This
handler records one row per tool invocation (name, inputs, output preview,
latency, status) so we can answer "which tool is slowest / most error-prone /
called most often" per run. The coordinator (`scripts/compile_all.py`) reads
`records()` after each batch, flushes to Postgres via
`src/db/tool_call_log.py`, and calls `clear()` to bound in-memory growth.
"""

from __future__ import annotations

import json
import time
from typing import Any
from typing import TypedDict
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler


class ToolCallRecord(TypedDict):
    """One row of per-tool-call telemetry — matches `compile_tool_calls` schema."""

    tool_name: str
    inputs_json: str | None
    output_preview: str | None
    output_bytes: int | None
    latency_ms: int | None
    status: str
    error_message: str | None
    started_at: float
    finished_at: float | None


class ToolCallLogHandler(BaseCallbackHandler):
    """Buffer per-tool-call data in memory; the coordinator flushes per batch."""

    def __init__(self) -> None:
        super().__init__()
        # Keyed by LangChain run_id so overlapping tool calls stay separate.
        self._in_flight: dict[str, ToolCallRecord] = {}
        self._completed: list[ToolCallRecord] = []

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        tool_name = serialized.get("name", "unknown") if serialized else "unknown"
        try:
            inputs_json = json.dumps(inputs, default=str) if inputs else None
        except (TypeError, ValueError):
            # Non-serializable input — drop the payload but keep the call.
            inputs_json = None
        self._in_flight[str(run_id)] = ToolCallRecord(
            tool_name=tool_name,
            inputs_json=inputs_json,
            output_preview=None,
            output_bytes=None,
            latency_ms=None,
            status="ok",
            error_message=None,
            started_at=time.time(),
            finished_at=None,
        )

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        rec = self._in_flight.pop(str(run_id), None)
        if rec is None:
            return
        now = time.time()
        out_str = str(output)
        rec["output_preview"] = out_str[:300]
        # Actual byte length (UTF-8); `len(str)` returns char count which
        # undercounts multi-byte (emoji, non-ASCII) payloads.
        rec["output_bytes"] = len(out_str.encode("utf-8"))
        rec["finished_at"] = now
        rec["latency_ms"] = int((now - rec["started_at"]) * 1000)
        rec["status"] = "ok"
        self._completed.append(rec)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        rec = self._in_flight.pop(str(run_id), None)
        if rec is None:
            return
        now = time.time()
        rec["finished_at"] = now
        rec["latency_ms"] = int((now - rec["started_at"]) * 1000)
        rec["status"] = "error"
        rec["error_message"] = str(error)[:500]
        self._completed.append(rec)

    def records(self) -> list[ToolCallRecord]:
        """Return a snapshot of completed records (shallow copy of list).

        NOTE: inner dict rows are shared with internal state. Do not mutate
        returned dicts — the coordinator should `dict(r)` each row if it
        needs a private copy. Use `flush_all()` to take ownership of both
        completed and in-flight records and empty internal state.
        """
        return list(self._completed)

    def flush_all(self) -> list[ToolCallRecord]:
        """Return completed + abandoned in-flight records, emptying state.

        Called by the coordinator on batch boundary *and* on exceptions so
        the most-diagnostic rows (the tool call the agent was running when
        it crashed) aren't silently lost. In-flight rows are marked
        `status="abandoned"` so operators can tell them from successful
        completions.
        """
        now = time.time()
        abandoned: list[ToolCallRecord] = []
        for rec in self._in_flight.values():
            rec["finished_at"] = now
            rec["latency_ms"] = int((now - rec["started_at"]) * 1000)
            rec["status"] = "abandoned"
            abandoned.append(rec)
        out = self._completed + abandoned
        self._in_flight.clear()
        self._completed.clear()
        return out

    def clear(self) -> None:
        """Drop all buffered state. Call after flushing to the DB per batch."""
        self._in_flight.clear()
        self._completed.clear()
