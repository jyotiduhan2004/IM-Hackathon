"""reconnaissance_paralysis middleware — nudge when reads pile up without commits.

Why this exists: smoke-02c9d536 audit (2026-04-28) caught a NEW failure
mode for ``moonshotai/kimi-k2.6`` on decision-heavy threads. Trace from
thread ``19be9883c6d921a6`` (Real-Time D-Rank DA/DB Removal,
17 emails, 7 stakeholders):

- 14x ``read_file``, 6x ``resolve_page``, 3x grep, 2x ``get_page_summary``
  — but **0 edits, 0 writes, 0 ``log_insight`` calls**.
- 3 LLM rounds consumed 575 s of the 900 s batch budget (64 %); the
  agent timed out in the *researching-which-page-to-edit* phase and
  never reached the write step.

``EditStalenessMiddleware`` is the wrong tool here: it watches for an
edit storm that never started. ``TerminalDecisionGuardMiddleware`` is
also the wrong tool: it fires at the END of the agent's loop, after the
batch wall-clock has already burned. We need an EARLY signal that
breaks the spiral while there's still time to commit.

The pattern is per-batch (one middleware instance per
``create_compiler`` call, no cross-batch bleed — same lifecycle as
``EditStalenessMiddleware``). State:

- ``read_file`` count
- count of any ``edit_file`` / ``write_file`` / ``patch_page`` /
  ``log_insight`` call (any category — even an investigatory
  ``log_insight`` proves the agent has stopped researching and started
  deciding; ``patch_page`` is treated as a commit alongside the other
  write tools, mirroring ``terminal_decision_guard``)
- ``nudged`` latch — fire AT MOST ONCE per batch

Trigger: the FIRST ``read_file`` whose result lands when
``read_count >= _READ_THRESHOLD`` AND ``commit_count == 0`` gets the
nudge appended to its ToolMessage. After that, ``nudged`` flips True
and we never fire again. If the agent ignores the nudge and keeps
reading, ``EditStalenessMiddleware`` and the per-batch wall-clock catch
real hangs downstream.

Threshold (8) is hardcoded — post-smoke validation pending. Failed
batch today hit 14 reads; healthy glm-5 baseline averages 5-7. 8 splits
the gap. Don't make it configurable until we have data to tune.

Pure annotation; never blocks. The agent decides whether to obey.
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


# Tools whose successful return advances each counter.
_READ_TOOL = "read_file"
# Any of these (on success) cancels the nudge — the agent has committed
# either content (write/edit) or a recorded judgement (log_insight, any
# category). We deliberately include investigatory log_insight categories:
# even a ``topic_merge_candidate`` or ``prompt_ambiguity`` proves the
# agent has stopped researching and started deciding, which is what the
# nudge is trying to provoke.
_COMMIT_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        "patch_page",
        "log_insight",
    }
)

# Reads-without-commit threshold. Hardcoded — see module docstring for
# sizing rationale. Don't promote to config until we have post-smoke
# data showing the threshold needs tuning.
_READ_THRESHOLD = 8

# Idempotency marker on additional_kwargs so a re-entrant wrap doesn't
# stamp the same ToolMessage twice.
_REMINDER_KEY = "reconnaissance_paralysis_reminder"

# Nudge body. Module constant so tests / scorecard can grep for it
# without coupling to this file's exact wording (mirrors
# ``TERMINAL_NUDGE_MESSAGE`` and the ``edit_staleness`` reminders).
RECONNAISSANCE_NUDGE_MESSAGE = (
    "\n\n[reconnaissance_paralysis] You have read "
    f"{_READ_THRESHOLD} files in this batch without producing any "
    "edit, write, or terminal-decision insight. The reconnaissance "
    "phase is long enough — pick a page now and either (a) commit a "
    "draft via `write_file` / `edit_file`, or (b) call `log_insight` "
    "with a terminal outcome (`already_captured`, `trivial_skip`, "
    "`insufficient_decision`) and return."
)


def _is_successful_tool_message(result: ToolMessage | Command[Any]) -> bool:
    """Mirror ``edit_staleness._is_failed_response`` inversion.

    A ``ToolMessage`` is "successful" when ``status != 'error'`` AND its
    body doesn't carry the deepagents quirk of ``status='success'`` with
    an ``Error: ...`` content prefix. ``Command[...]`` results we can't
    introspect are conservatively treated as non-success — better to
    miss a count bump than falsely cancel the nudge.
    """
    if not isinstance(result, ToolMessage):
        return False
    if result.status == "error":
        return False
    return not (isinstance(result.content, str) and result.content.startswith("Error:"))


class ReconnaissanceParalysisMiddleware(AgentMiddleware):
    """Nudge once per batch when reads accumulate without any commit.

    Stateful within a compile run (per-instance counters); stateless
    across runs (``compiler.create_compiler`` mints a fresh instance
    per call). Sibling to ``EditStalenessMiddleware`` — that one
    catches edit storms after they start; this one catches the
    *failure to start* the edit phase at all.
    """

    READ_THRESHOLD = _READ_THRESHOLD

    def __init__(self) -> None:
        super().__init__()
        # Per-batch tally: number of successful ``read_file`` calls.
        self._read_count: int = 0
        # Per-batch tally: any successful tool in ``_COMMIT_TOOLS``. A
        # single commit cancels the nudge for the rest of the batch
        # (idempotency latch — see ``_nudged``).
        self._commit_count: int = 0
        # Fire-once latch. Once True, the middleware is a pure
        # pass-through for the remainder of the batch.
        self._nudged: bool = False

    @property
    def name(self) -> str:
        return "reconnaissance_paralysis"

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _stamp_reminder(self, msg: ToolMessage) -> bool:
        """Append the nudge to ``msg`` if not already stamped.

        Returns True when the stamp was applied (caller flips
        ``_nudged`` so we never re-fire). Returns False on idempotent
        re-entry, on non-text content, or on already-marked messages.
        """
        if msg.additional_kwargs.get(_REMINDER_KEY):
            return False
        if not isinstance(msg.content, str):
            # Multimodal content_blocks shape — skip cleanly. read_file
            # produces strings today; defending against future drift.
            return False
        msg.content = msg.content + RECONNAISSANCE_NUDGE_MESSAGE
        msg.additional_kwargs[_REMINDER_KEY] = True
        return True

    def _process(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> None:
        """Single state-machine step. Mutates ``result`` in place when needed.

        Called from both sync and async wrap paths after the underlying
        handler returns. Pure logic — no IO.
        """
        if self._nudged:
            return  # fire-once latch; pass-through for the rest of the batch

        tool_name = request.tool_call.get("name", "")
        if tool_name not in _COMMIT_TOOLS and tool_name != _READ_TOOL:
            return  # unrelated tool — no counter touched

        if not _is_successful_tool_message(result):
            return  # failed call doesn't advance any counter

        if tool_name in _COMMIT_TOOLS:
            self._commit_count += 1
            return  # commit landed — nudge will never fire this batch

        # tool_name == _READ_TOOL and it succeeded.
        self._read_count += 1
        if self._read_count < _READ_THRESHOLD or self._commit_count > 0:
            return  # not yet at threshold, or commit already cancels

        # Threshold met with zero commits — fire once.
        if not isinstance(result, ToolMessage):
            return  # Command[...] success path — can't stamp a body
        # Latch unconditionally: even when _stamp_reminder no-ops (multimodal
        # content, re-entrant wrap), threshold has already been crossed and
        # we don't want to keep churning past it. Reviewer feedback: PR #251.
        self._nudged = True
        if self._stamp_reminder(result):
            logger.info(
                "reconnaissance_paralysis_nudge",
                read_count=self._read_count,
                commit_count=self._commit_count,
                threshold=_READ_THRESHOLD,
            )

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
