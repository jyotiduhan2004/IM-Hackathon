"""terminal_decision_guard middleware — block batch exit without a terminal decision.

Why this exists: the V12 50-compile deep audit 2026-04-23 surfaced
batch 45 (``b3f4dd022eba``, kimi-k2.6) completing with
``turns=6 tools=8 writes=0, normalized 0 pages``. The agent read the
email + a candidate page, decided nothing, then exited. It didn't
call ``log_insight(category="trivial_skip" | "already_captured")`` to
declare the email investigated-but-skipped, and didn't write any
content. The coordinator left the email ``pending``, the claim loop
re-queued it, and the next batch paid the same cost for the same
silent non-outcome.

This middleware enforces the existing workflow-step 9 ("verify each
email has a terminal outcome") at the runtime layer rather than
relying on prompt adherence. Every batch must commit to ONE of:

1. At least one successful content-write
   (``write_file`` / ``edit_file`` / ``patch_page``) → compiled.
2. At least one successful ``log_insight`` call with a terminal
   category (``trivial_skip`` / ``already_captured`` /
   ``insufficient_decision``) → skipped with reason.

Draft writes (``write_draft_page``) and investigatory insights
(``topic_merge_candidate``, ``prompt_ambiguity``, etc.) are
deliberately excluded — a draft-only session or an observation-only
session is the same "decided nothing" pathology the audit flagged.

When ``after_model`` sees the agent's final ``AIMessage`` has no
``tool_calls`` (the LangGraph ReAct exit signal) AND no terminal
commitment has landed, the middleware:

- Appends a synthetic ``HumanMessage`` explaining the required
  decision.
- Returns ``{"jump_to": "model"}`` so the agent loops back and must
  respond with a tool call.

A bounded retry counter prevents infinite loops — after
``_MAX_NUDGES`` rejections the middleware lets the agent exit; the
coordinator (``scripts/compile_all.py``) falls back to
``mark_skipped(reason="agent_exited_without_terminal_decision")`` via
the ``_mark_batch_compiled`` not-cited branch (see
``docs/audits/v12-50-compile-deep-audit-2026-04-23.md`` §7 Tier 2 #5).

State is per-instance (one middleware per ``create_compiler`` call).
Matches the ``CheckMyWorkGateMiddleware`` instantiation pattern:
fresh object per batch, no cross-batch bleed.
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

logger = structlog.get_logger(__name__)


# Tools whose successful return counts as a terminal content commitment.
# Matches `CheckMyWorkGateMiddleware._CONTENT_WRITE_TOOLS` — drafts are
# deliberately absent (a draft-only session is the failure mode, not
# the fix). Keep this set in sync; both middlewares share the same
# semantics of "the agent actually wrote content".
_CONTENT_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "patch_page"})

# `log_insight` categories that represent a decisive outcome on the
# email. Investigatory categories (``topic_merge_candidate``,
# ``question_for_human``, ``prompt_ambiguity``, ``tool_gap``,
# ``supersession_doubt``, ``structure_suggestion``) are NOT terminal —
# they name something for human review but don't declare the email
# itself handled.
#
# ``insufficient_decision`` is added here (and surfaced in the nudge
# below) as a dedicated escape hatch for cases where the agent
# genuinely can't choose: the content is substantive, isn't clearly
# captured elsewhere, but there's no obvious target page. Without this
# the agent oscillates between picking a bad page and loop-exiting
# silently; with it, the skip is recorded and humans can triage.
_TERMINAL_LOG_INSIGHT_CATEGORIES: frozenset[str] = frozenset(
    {"trivial_skip", "already_captured", "insufficient_decision"}
)

# Upper bound on how many times we re-prompt the agent before giving
# up. Each nudge costs one model call. Three is enough to correct a
# forgotten-terminal mistake without ballooning costs on models that
# refuse to comply.
_MAX_NUDGES: int = 3

# The nudge payload. Kept as a module constant so tests + the
# coordinator can regex-match trace output without coupling to this
# file's exact wording (mirrors CheckMyWorkGateMiddleware's pattern).
TERMINAL_NUDGE_MESSAGE = (
    "Your batch is about to exit without a terminal decision. You "
    "MUST commit to ONE of:\n"
    "  (a) write the page with `write_file` / `edit_file` / "
    "`patch_page`, OR\n"
    "  (b) call `log_insight` with a terminal category:\n"
    "      - `trivial_skip` — the email is not substantive (OOO, "
    "one-line ack).\n"
    "      - `already_captured` — substantive but every fact is "
    "already on an existing page.\n"
    "      - `insufficient_decision` — substantive, not captured, "
    "but no obvious target page (human should triage).\n"
    "Returning without either leaves the email `pending`, the claim "
    "loop re-queues it, and the next batch pays the same cost. "
    "Decide now."
)


def _is_successful_tool_message(result: Any) -> bool:
    """Decide whether a tool result represents a successful call.

    Mirrors `check_my_work_gate._is_successful_tool_message`. A
    ``ToolMessage`` with ``status != "error"`` is success; anything
    else (including `Command` results we can't introspect) is
    conservatively treated as non-success so we don't prematurely
    credit the gate.
    """
    if isinstance(result, ToolMessage):
        return getattr(result, "status", None) != "error"
    return False


def _insight_category(tool_args: dict[str, Any]) -> str | None:
    """Pull the category out of a ``log_insight`` call's args.

    ``log_insight`` takes ``category`` as the canonical key (see
    ``src/agent/tools/insights.py:log_insight``). A ToolMessage only
    reaches the post-handler path with a successful call, which
    already means the args matched the tool signature — so a
    missing ``category`` here would mean the tool schema drifted.
    Return ``None`` in that case so the gate conservatively stays
    closed (we'd rather re-nudge than accept an unknown category).
    """
    category = tool_args.get("category")
    if isinstance(category, str):
        return category
    return None


def _last_ai_message_is_final(state: AgentState) -> bool:
    """True when the most recent message is an AIMessage with no tool calls.

    In LangGraph's ReAct pattern the conditional edge after the model
    routes to ``tools`` when the AIMessage has tool_calls, else to
    ``END``. ``after_model`` fires BEFORE that edge, so this is the
    unambiguous signal that the agent is about to exit.

    Guards:
    - Empty messages list (agent never ran) → False.
    - Last message isn't an AIMessage (tool result, human reply) →
      False. ``after_model`` should only see an AIMessage at the tail
      under normal graph flow, but we defend against surprise
      orderings (e.g. mid-retry states).
    """
    messages = state.get("messages") or []
    if not messages:
        return False
    last = messages[-1]
    if not isinstance(last, AIMessage):
        return False
    tool_calls = getattr(last, "tool_calls", None) or []
    return not tool_calls


class TerminalDecisionGuardMiddleware(AgentMiddleware):
    """Force every batch to end with a content write or a terminal log_insight.

    Two hooks:

    - ``wrap_tool_call`` tracks successful terminal commitments
      (content writes, terminal ``log_insight`` categories).
    - ``after_model`` intercepts the ReAct exit edge: if the agent is
      about to end without committing, inject a
      ``HumanMessage(TERMINAL_NUDGE_MESSAGE)`` and jump back to the
      model. Bounded by ``_MAX_NUDGES``.
    """

    @property
    def name(self) -> str:
        return "terminal_decision_guard"

    def __init__(self) -> None:
        super().__init__()
        # Committed once either terminal condition lands. Latching is
        # intentional — a subsequent failed tool call mustn't un-commit
        # a prior success.
        self._committed: bool = False
        # Number of nudges issued. Bounded so a model that refuses to
        # comply doesn't burn the recursion_limit with a nudge loop.
        self._nudges: int = 0

    # ------------------------------------------------------------------
    # Tool-call tracking
    # ------------------------------------------------------------------

    def _record_success(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Record a successful tool call toward terminal commitment.

        Only content writes and terminal-category ``log_insight``
        calls set the commitment flag; everything else is ignored.
        """
        if tool_name in _CONTENT_WRITE_TOOLS:
            self._committed = True
            return
        if tool_name == "log_insight":
            category = _insight_category(tool_args)
            if category is not None and category in _TERMINAL_LOG_INSIGHT_CATEGORIES:
                self._committed = True

    def _maybe_record(self, request: ToolCallRequest, result: Any) -> None:
        """Post-handler: update commitment state if this call succeeded."""
        if not _is_successful_tool_message(result):
            return
        tool_name = request.tool_call.get("name") or ""
        tool_args = request.tool_call.get("args") or {}
        self._record_success(tool_name, tool_args)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        self._maybe_record(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        self._maybe_record(request, result)
        return result

    # ------------------------------------------------------------------
    # after_model — the actual gate
    # ------------------------------------------------------------------

    def _build_nudge_update(self) -> dict[str, Any]:
        """Compose the state update that forces the model to reconsider.

        - Appends the nudge ``HumanMessage`` (``messages`` uses the
          ``add_messages`` reducer, so append is the default).
        - Sets ``jump_to="model"`` so the graph loops back instead of
          going to END.
        """
        self._nudges += 1
        logger.warning(
            "terminal_decision_guard.nudge_injected",
            nudge_count=self._nudges,
            max_nudges=_MAX_NUDGES,
        )
        return {
            "jump_to": "model",
            "messages": [HumanMessage(content=TERMINAL_NUDGE_MESSAGE)],
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Check terminal commitment before the agent exits.

        Fires after every model call. The guard only engages when BOTH
        - the agent is about to END (last AIMessage has no tool_calls)
        - no terminal commitment has landed this batch
        AND we still have nudges left in the budget.

        Returns ``None`` when the agent isn't at the exit edge (model
        is continuing via tool_calls) — cheap early-out that skips the
        expensive commitment check.
        """
        _ = runtime  # unused; included for signature compatibility
        if not _last_ai_message_is_final(state):
            return None
        if self._committed:
            return None
        if self._nudges >= _MAX_NUDGES:
            logger.error(
                "terminal_decision_guard.exhausted",
                nudges_issued=self._nudges,
                note=(
                    "agent exited without a terminal decision even after "
                    f"{_MAX_NUDGES} nudges; coordinator will mark skipped "
                    "with reason=agent_exited_without_terminal_decision"
                ),
            )
            return None
        return self._build_nudge_update()

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Async variant — delegates to the sync implementation.

        No IO, no awaits; the sync path is safe to call from an async
        context. Mirrors ``ModelCallLimitMiddleware.aafter_model``.
        """
        return self.after_model(state, runtime)
