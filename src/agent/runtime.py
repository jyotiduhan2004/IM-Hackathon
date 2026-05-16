"""Agent runtime infrastructure — model factory, tracing, async invoke.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). Agent-tool code
imports from here when it needs to instantiate a chat model, register
the langfuse handler, or call `agent.ainvoke` with a wall-clock cap.

Promote to a separate `src/llm/` package only when a second non-compile
agent imports the same code — see plan's "What this plan does NOT do".
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

import structlog

from src.config import settings

if TYPE_CHECKING:
    from src.agent.middleware.stuck_heartbeat import StuckHeartbeatState

logger = structlog.get_logger(__name__)


class InvokeWallClockTimeout(Exception):  # noqa: N818 — public contract name
    """Raised when a single `agent.ainvoke` round exceeds `invoke_timeout_s`.

    Distinct from the outer `concurrent.futures.TimeoutError` raised by
    `scripts/compile_all.py::_run_with_timeout`: that one tracks cumulative
    batch wall-clock across model retries, while this one caps a single LLM
    round. Without the inner cap, a wedged proxy (grok-4.1-fast 2026-04-22:
    5h31m hang mid-round) exhausts the outer budget silently instead of
    surfacing as a timeout.
    """


class StuckLLMRoundError(Exception):
    """Raised when no tool call has returned in `compile_stuck_after_s` seconds.

    Distinct from `InvokeWallClockTimeout` (whole-round wall-clock cap):
    this fires when the agent loop is genuinely idle on the model side
    while productive deliberation looks the same to a wall-clock alone.
    A 5-min heartbeat catches wedged provider connections at 5 min instead
    of waiting out the full `invoke_timeout_s` budget (~16 min today).

    The coordinator (`scripts/compile_all.py::_is_model_unavailable_error`)
    treats this as `outcome='timeout'` and routes to pool retry — same path
    as `SilentModelFailError` and other infrastructure failures.
    """


def _make_chat_model(model_name: str) -> Any:
    """Build a chat model, routing through LiteLLM proxy if configured.

    LiteLLM proxies expose an OpenAI-compatible API, so we use langchain-openai's
    ChatOpenAI and point it at the proxy's base URL. This works for any model
    string the proxy knows (e.g. "z-ai/glm-5", "anthropic/claude-opus-4-6"),
    regardless of whether langchain has a native provider for it.

    timeout=300s prevents the "half-open TCP socket" stall we hit
    twice on 2026-04-14 (laptop sleep / network blip → kernel keepalive
    defaults to hours → Python blocks in recv() forever) AND
    accommodates legitimately slow long-context rounds — kimi-k2.6 on
    50k+ input tokens takes 5+ minutes to think, and the 2026-04-28
    smoke audit (run 02c9d536) caught a string of "Request timed out"
    failures that were really 120s x 3 SDK-retry attempts killing
    productive deliberation. 300s x 3 SDK retries = 900s wall-clock
    per ultimate-failure; the ``--batch-timeout`` default is now
    1800s (raised in the same PR) so one ultimate-failure plus one
    fallback retry on a different pool model still fits within a
    single batch budget.
    """
    if settings.litellm_base_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            base_url=settings.litellm_base_url,
            api_key=settings.openai_api_key or "dummy",
            timeout=300,
        )

    # Fallback: use langchain's provider inference. No timeout knob at this
    # layer — direct providers don't exhibit the proxy-socket-stall issue.
    from langchain.chat_models import init_chat_model

    return init_chat_model(model_name)


def get_langfuse_handler(
    *,
    update_trace: bool = True,
) -> Any | None:
    """Return a Langfuse callback handler if configured, else None.

    Langfuse v3+ removed the legacy `langfuse.callback` module. The handler
    now lives in `langfuse.langchain` and instantiates its own internal
    Langfuse client — it does NOT take a `langfuse_client` arg (verified
    against 3.14.6). That means a `Langfuse(...)` constructor call we make
    here is discarded by the time `CallbackHandler()` runs. So we configure
    everything via env vars instead, which both the OTel pipeline and the
    Langfuse client read at instantiation time.

    **Hang-safety**: the self-hosted server's OTLP ingestion endpoint
    (`/api/public/otel/v1/traces`) has been observed to Read-timeout for
    minutes at a time. Without bounded export timeouts, LangChain's
    synchronous callback path can block compile runs for ~8+ minutes per
    batch when the queue fills. Capping per-export attempts via env vars
    drains failures fast so tracing degrades to best-effort no-op instead
    of stalling the agent. See issue #17 for the server-side root cause.
    """
    if not settings.langfuse_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None

    # Set timeouts via env vars so they apply to BOTH the OTel exporter
    # (used by CallbackHandler internally) AND the Langfuse client
    # singletons. `setdefault` lets operators override when the server
    # is known-healthy.
    import os as _os

    # OTel BatchSpanProcessor + OTLP exporter (the actual span shipping)
    _os.environ.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "2000")  # ms per export
    _os.environ.setdefault("OTEL_BSP_SCHEDULE_DELAY", "5000")  # ms between flushes
    _os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "512")  # drop oldest on full
    # NB: opentelemetry-sdk Python parses OTEL_EXPORTER_OTLP_TIMEOUT as
    # SECONDS despite the OTel spec defining it in ms. Don't normalize to
    # 2000 to "fix the units" — that would give a 2s timeout, not 2 ms.
    _os.environ.setdefault("OTEL_EXPORTER_OTLP_TIMEOUT", "2")

    # Langfuse client config — picked up by CallbackHandler's internal client.
    _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    _os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    _os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    _os.environ.setdefault("LANGFUSE_TIMEOUT", "2")  # seconds for the SDK's HTTP client
    _os.environ.setdefault("LANGFUSE_FLUSH_AT", "50")  # batch size before forced flush
    _os.environ.setdefault("LANGFUSE_FLUSH_INTERVAL", "5")  # seconds between flushes

    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("langfuse not installed, tracing disabled")
        return None

    return CallbackHandler(update_trace=update_trace)


async def _ainvoke_with_timeout(
    agent: Any,
    instruction: str,
    config: dict[str, Any],
    timeout_s: int,
    *,
    heartbeat_state: StuckHeartbeatState | None = None,
    stuck_after_s: int | None = None,
) -> dict[str, Any]:
    """Call `agent.ainvoke(...)` capped by two timeouts.

    - `timeout_s` — whole-round wall-clock cap. Expiry raises
      `InvokeWallClockTimeout`.
    - `heartbeat_state` + `stuck_after_s` — tool-return heartbeat. If
      no tool call has returned in `stuck_after_s` seconds while the
      agent task is still running, raise `StuckLLMRoundError`. Pass
      both to enable; pass neither to keep the single-cap behavior
      tests rely on.

    `_is_model_unavailable_error` matches both exception types so
    wedged-round cases route to pool retry uniformly.
    """
    agent_task = asyncio.create_task(
        agent.ainvoke(
            {"messages": [{"role": "user", "content": instruction}]},
            config=config,
        )
    )
    watcher_task: asyncio.Task[None] | None = None
    if heartbeat_state is not None and stuck_after_s is not None and stuck_after_s > 0:
        watcher_task = asyncio.create_task(
            _stuck_heartbeat_watcher(agent_task, heartbeat_state, stuck_after_s)
        )

    try:
        try:
            return cast(
                dict[str, Any],
                await asyncio.wait_for(asyncio.shield(agent_task), timeout=timeout_s),
            )
        except TimeoutError as exc:
            agent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await agent_task
            logger.error("invoke_wall_clock_timeout", timeout_s=timeout_s)
            raise InvokeWallClockTimeout(
                f"agent.ainvoke exceeded {timeout_s}s wall-clock limit"
            ) from exc
        except asyncio.CancelledError:
            # The shield only blocks outside cancels from killing the
            # agent task — when the watcher cancels it directly, the
            # shielded await still completes with CancelledError. Wait
            # for the watcher to surface its StuckLLMRoundError before
            # deciding whether this is a stuck-round cancellation or a
            # genuine outside cancel.
            if watcher_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watcher_task
                if watcher_task.done() and not watcher_task.cancelled():
                    watcher_exc = watcher_task.exception()
                    if isinstance(watcher_exc, StuckLLMRoundError):
                        raise watcher_exc from None
            raise
    finally:
        if watcher_task is not None and not watcher_task.done():
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
        # Consume agent_task cancellation in all exit paths (stuck-round
        # via watcher, external cancel through the shield wrapper) so the
        # event loop doesn't warn "Task destroyed but pending". The
        # TimeoutError branch already does this inline; finally handles
        # the rest. Claude review on PR #253.
        if not agent_task.done():
            agent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await agent_task


async def _stuck_heartbeat_watcher(
    agent_task: asyncio.Task[Any],
    heartbeat_state: StuckHeartbeatState,
    stuck_after_s: int,
) -> None:
    """Cancel `agent_task` if the heartbeat goes stale.

    Polls at `min(30, stuck_after_s/5)` second intervals — fine-grained
    enough to fire near the configured threshold without making the
    watcher itself a source of overhead.
    """
    poll_interval = min(30.0, max(1.0, stuck_after_s / 5.0))
    while not agent_task.done():
        await asyncio.sleep(poll_interval)
        if agent_task.done():
            return
        # Skip until first tool call returns: a slow first round (no tool
        # returns yet) is the outer wall-clock's job, not the heartbeat's.
        if heartbeat_state.last_tool_return_at is None:
            continue
        idle_for = time.monotonic() - heartbeat_state.last_tool_return_at
        if idle_for > stuck_after_s:
            logger.error(
                "stuck_llm_round_detected",
                idle_for_s=round(idle_for, 1),
                stuck_after_s=stuck_after_s,
            )
            agent_task.cancel()
            raise StuckLLMRoundError(f"agent stuck — no tool-call return in {stuck_after_s}s")
