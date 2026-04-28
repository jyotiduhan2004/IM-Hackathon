"""edit_staleness middleware — nudge a re-read when the agent's mental model drifts.

Why this exists: smoke-99a267f4 deep-trace audit (2026-04-28) found
glm-5.1 burned 12 of 30 turns on thread `19be9885492d95b1` recovering
from its own ``edit_file`` failures. Pattern: agent makes 5
sequential edits to a topic page WITHOUT an intervening ``read_file``;
the 6th edit's ``old_string`` is built from a stale mental model
(prior edits disturbed the structure); ``edit_file`` returns
``Error: String not found in file``; agent retries with a slightly
different anchor — fails again — re-reads — discovers it created
duplicate sections — tries to clean up — recursion limit hits.

Two interventions, both `wrap_tool_call`:

1. **Reactive (post-failure)** — when ``edit_file`` returns the
   "string not found" error, append an explicit re-read reminder to
   the ToolMessage. Today the agent has to figure that out from the
   error string + retries (Agent 2's audit observed 4-6 wasted turns
   per drift episode).

2. **Proactive (pre-failure)** — count consecutive successful
   ``edit_file`` calls per path, reset on ``read_file``. After
   ``_PROACTIVE_THRESHOLD`` (3) successes without a re-read, append
   a "drift warning" to the next successful ``edit_file`` result so
   the agent re-reads before the 4th change rather than discovering
   drift via failure.

Pure annotation; never blocks. The agent decides whether to obey the
reminder. The middleware is stateful within a compile run (one dict
per ``EditStalenessMiddleware`` instance) and stateless across runs
(``compiler.create_compiler`` mints a fresh instance per call).

Edge cases:
- A ``write_file`` (full rewrite) resets the per-path counter
  because the on-disk content now matches what the agent just
  wrote — its mental model can't be stale.
- Errors that don't match the "string not found" signature pass
  through (e.g. "Permission denied", "File not found") — those
  are different bugs.
- A successful edit on path A doesn't bump the counter for path
  B (path-keyed state).
- Idempotent via ``additional_kwargs[_REMINDER_KEY]`` so a
  re-entrant wrap doesn't stamp twice.
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


# Tools whose outcome / args feed the staleness state machine.
_EDIT_TOOL = "edit_file"
_WRITE_TOOL = "write_file"  # resets counter — full rewrite knows current content
_READ_TOOL = "read_file"

# After this many consecutive successful ``edit_file`` calls on the
# same path WITHOUT an intervening ``read_file``, the next successful
# edit gets a "drift warning" reminder appended.
#
# 3 was Agent 2's recommendation: matches the observed failure pattern
# (5 sequential edits → 6th drifts), and gives the agent the warning
# BEFORE the 4th edit — early enough to re-read but late enough that
# we're not nagging on every other call.
_PROACTIVE_THRESHOLD = 3

# Substring fragments that identify the "agent's old_string didn't
# match the file" failure shape. We match on lower-cased fragments to
# survive small wording shifts in the underlying tool. The deepagents
# default phrasing is "String to replace not found in file"; older
# revisions shipped "String not found in file". Match both.
_STALENESS_ERROR_FRAGMENTS: tuple[str, ...] = (
    "string to replace not found",
    "string not found in file",
)

# Marker key on ``additional_kwargs`` so a re-entrant wrap doesn't
# stamp the same ToolMessage twice with the same reminder.
_REMINDER_KEY = "edit_staleness_reminder"

# Reminder texts. Kept as bracketed annotations (not prose) so the
# scorecard can grep for adoption later.
_REACTIVE_REMINDER = (
    "\n\n[edit_staleness] Your `old_string` did not match. The file has "
    "likely changed since your last read — call `read_file` on this path "
    "BEFORE retrying the edit. Building a fresh `old_string` from the "
    "real current content is faster than guessing anchors."
)


def _proactive_reminder(*, path: str, edits: int) -> str:
    return (
        f"\n\n[edit_staleness] You've made {edits} edits to `{path}` "
        f"without re-reading. The on-disk content has drifted from "
        f"your mental model — call `read_file` before the next edit "
        f"so your `old_string` matches the real current state."
    )


def _extract_path(args: dict[str, Any]) -> str | None:
    """Pull the ``file_path`` (or ``path``) arg out of a tool call."""
    for key in ("file_path", "path"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _is_staleness_error(content: str) -> bool:
    """True when ``content`` looks like the "old_string didn't match" failure."""
    lowered = content.lower()
    return any(fragment in lowered for fragment in _STALENESS_ERROR_FRAGMENTS)


def _is_failed_response(result: ToolMessage) -> bool:
    """True when the ToolMessage represents a failure (either by
    ``status='error'`` OR by an ``"Error: ..."`` content prefix).

    deepagents' tools occasionally return ``status='success'`` with the
    error string in ``content``. ``ReadFileTruncationHintMiddleware``
    handles the same shape via an explicit ``Error:`` prefix check;
    we mirror that here so the read/write reset paths can't be
    fooled by a phantom failure.
    """
    if result.status == "error":
        return True
    return isinstance(result.content, str) and result.content.startswith("Error:")


class EditStalenessMiddleware(AgentMiddleware):
    """Track per-path edit_file calls; nudge a re-read on drift.

    Stateful within a compile run (one counter dict per instance);
    stateless across runs (``compiler.create_compiler`` builds a fresh
    instance every call).

    The middleware fires on every tool call but only mutates the
    ToolMessage on ``edit_file`` returns — every other tool path is
    a no-op pass-through plus state-keeping for ``read_file`` /
    ``write_file`` resets.
    """

    PROACTIVE_THRESHOLD = _PROACTIVE_THRESHOLD

    def __init__(self) -> None:
        super().__init__()
        # Per-path counter of consecutive successful edits without an
        # intervening read. Reset to 0 on read_file or write_file
        # (write_file = full rewrite, agent's mental model is fresh).
        self._consecutive_edits: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "edit_staleness"

    # ------------------------------------------------------------------
    # State helpers — kept tiny so the wrap methods read top-down.
    # ------------------------------------------------------------------

    def _on_read(self, path: str | None) -> None:
        """A read on `path` resets that path's drift counter."""
        if path is not None:
            self._consecutive_edits.pop(path, None)

    def _on_write(self, path: str | None) -> None:
        """A full ``write_file`` rewrite resets — the agent just authored
        the content, so its mental model matches disk.
        """
        if path is not None:
            self._consecutive_edits.pop(path, None)

    def _on_edit_failure(self, path: str | None) -> None:
        """Reset on a STALENESS edit failure (the only failure shape that
        invokes this hook — non-staleness errors like ``Permission
        denied`` or ``File not found`` are orthogonal and leave the
        counter alone). The reactive reminder will tell the agent to
        re-read; once it does, the proactive warning should fire fresh
        after a new run of edits, not piggyback on the pre-failure
        count.
        """
        if path is not None:
            self._consecutive_edits.pop(path, None)

    def _on_edit_success(self, path: str | None) -> int:
        """Increment + return the consecutive-edits count for `path`."""
        if path is None:
            return 0
        count = self._consecutive_edits.get(path, 0) + 1
        self._consecutive_edits[path] = count
        return count

    # ------------------------------------------------------------------
    # Result mutation — both reminders share the same idempotency guard.
    # ------------------------------------------------------------------

    def _stamp_reminder(self, msg: ToolMessage, *, kind: str, body: str) -> None:
        """Append `body` to the ToolMessage if not already stamped.

        ``kind`` is logged + stored in additional_kwargs so the
        scorecard can split adoption between reactive and proactive.
        """
        if msg.additional_kwargs.get(_REMINDER_KEY):
            return
        if not isinstance(msg.content, str):
            # Multimodal content_blocks shape — skip cleanly. The
            # underlying tool produces strings for the edit/error
            # paths today, but defending against future shape drift.
            return
        msg.content = msg.content + body
        msg.additional_kwargs[_REMINDER_KEY] = kind

    def _process(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> None:
        """Single state-machine step. Mutates ``result`` in place when needed.

        Called from both sync and async wrap paths after the underlying
        handler returns. Pure logic — no IO — so the async path doesn't
        need to offload it.
        """
        tool_name = request.tool_call.get("name", "")
        if tool_name not in (_EDIT_TOOL, _WRITE_TOOL, _READ_TOOL):
            return
        if not isinstance(result, ToolMessage):
            # Command[...] paths (rare for these tools) — skip cleanly.
            return

        args = request.tool_call.get("args") or {}
        path = _extract_path(args)

        if tool_name == _READ_TOOL:
            # Don't reset on read errors (file missing, etc.) — the
            # agent's mental model is unaffected by a failed read.
            # deepagents can return ``status="success"`` with an
            # ``"Error: ..."`` body on certain read failures (see
            # ``tests/test_read_file_truncation.py::test_skips_error_prefix
            # _even_when_status_success``); ``ReadFileTruncationHintMiddleware``
            # uses the same dual-check. Without it, a phantom failed
            # read would silently reset the counter.
            if not _is_failed_response(result):
                self._on_read(path)
            return

        if tool_name == _WRITE_TOOL:
            # Same dual-check as the read path: a failed write that
            # carries ``status="success"`` must not reset the counter,
            # because the agent's mental model wasn't actually
            # refreshed by the (failed) write.
            if not _is_failed_response(result):
                self._on_write(path)
            return

        # tool_name == _EDIT_TOOL
        # ``_is_failed_response`` covers both shapes deepagents can
        # return — ``status="error"`` and ``status="success"`` with
        # an ``"Error: ..."`` content prefix. edit_file's success
        # path returns ``"Successfully edited..."`` (never starting
        # with ``Error:``), so the prefix branch can't false-positive
        # on a true success today.
        is_error = _is_failed_response(result)
        if is_error:
            if isinstance(result.content, str) and _is_staleness_error(result.content):
                self._on_edit_failure(path)
                self._stamp_reminder(result, kind="reactive", body=_REACTIVE_REMINDER)
                logger.info(
                    "edit_staleness_reactive",
                    path=path or "",
                    reason="old_string_not_found",
                )
            # Other edit errors (permission, missing path, payload size)
            # don't drift the counter — they're orthogonal failures.
            return

        # Successful edit — bump the counter and check the proactive threshold.
        edits = self._on_edit_success(path)
        if path is not None and edits >= _PROACTIVE_THRESHOLD:
            self._stamp_reminder(
                result,
                kind="proactive",
                body=_proactive_reminder(path=path, edits=edits),
            )
            logger.info(
                "edit_staleness_proactive",
                path=path,
                consecutive_edits=edits,
            )
            # Reset so the warning doesn't repeat on every subsequent
            # edit — the agent gets one nudge per drift episode.
            self._consecutive_edits.pop(path, None)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        self._process(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        self._process(request, result)
        return result
