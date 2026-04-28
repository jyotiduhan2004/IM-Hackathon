"""Tests for the stuck-LLM-round heartbeat detector.

The detector pairs a middleware (`StuckHeartbeatMiddleware`) that stamps
`last_tool_return_at` on every tool-call return with an asyncio watcher
inside `_ainvoke_with_timeout` that cancels the agent task when the
stamp goes stale.

Two failure shapes the heartbeat distinguishes:

1. Wedged provider — no tool call returns for >= STUCK_AFTER_S → fire.
2. Slow but productive deliberation — tool calls keep returning even
   if the LLM round is long → don't fire.

Tests use compressed time horizons (`stuck_after_s=2`) so they run in
a few seconds; the production default is 300 s.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from src.compile.compiler import InvokeWallClockTimeout
from src.compile.compiler import StuckLLMRoundError
from src.compile.compiler import _ainvoke_with_timeout
from src.compile.middleware.stuck_heartbeat import StuckHeartbeatMiddleware
from src.compile.middleware.stuck_heartbeat import StuckHeartbeatState

# ---------------------------------------------------------------------------
# State + middleware: the cheap parts.
# ---------------------------------------------------------------------------


def test_state_default_is_none_until_first_mark() -> None:
    """Before any tool returns, `last_tool_return_at` is None.

    Codex review (PR #253): if the default were a timestamp, a slow
    first LLM round (no tool returns yet) would falsely trip the
    heartbeat. None means "no tool returned yet"; the watcher skips
    its check until `mark()` is called for the first time.
    """
    state = StuckHeartbeatState()
    assert state.last_tool_return_at is None
    state.mark()
    assert state.last_tool_return_at is not None
    assert state.last_tool_return_at > 0.0


def test_state_mark_advances_timestamp() -> None:
    state = StuckHeartbeatState(last_tool_return_at=0.0)
    assert state.last_tool_return_at == 0.0
    state.mark()
    assert state.last_tool_return_at > 0.0


@pytest.mark.asyncio
async def test_middleware_stamps_on_async_tool_return() -> None:
    """The middleware must update `last_tool_return_at` in awrap_tool_call.

    Constructed bare here (no real LangGraph runtime) — we exercise the
    `awrap_tool_call` method directly to confirm the timestamp moves.
    """
    state = StuckHeartbeatState(last_tool_return_at=0.0)
    middleware = StuckHeartbeatMiddleware(state)

    async def handler(_request):  # type: ignore[no-untyped-def]
        return "ok"

    request = {"tool_call": {"name": "x", "args": {}}}
    await middleware.awrap_tool_call(request, handler)  # type: ignore[arg-type]
    assert state.last_tool_return_at > 0.0


@pytest.mark.asyncio
async def test_middleware_stamps_even_on_handler_exception() -> None:
    """Tool returns count regardless of outcome — stamp on exception too.

    The contract is "agent loop is making progress"; an exception is
    still a return. Without `try/finally`, a flaky tool would be
    indistinguishable from a wedged round.
    """
    state = StuckHeartbeatState(last_tool_return_at=0.0)
    middleware = StuckHeartbeatMiddleware(state)

    async def boom(_request):  # type: ignore[no-untyped-def]
        raise ValueError("boom")

    request = {"tool_call": {"name": "x", "args": {}}}
    with pytest.raises(ValueError):
        await middleware.awrap_tool_call(request, boom)  # type: ignore[arg-type]
    assert state.last_tool_return_at > 0.0


# ---------------------------------------------------------------------------
# Watcher integration with _ainvoke_with_timeout.
# ---------------------------------------------------------------------------


class _StuckAgent:
    """Agent stub that never returns and never produces a tool call."""

    async def ainvoke(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(60)
        return {"messages": []}


class _ActiveAgent:
    """Agent stub that simulates a tool returning every `tick_s`.

    Stamps `state` directly to mimic what the middleware would do under
    a real ToolNode call. Returns after `total_s` of activity.
    """

    def __init__(self, state: StuckHeartbeatState, tick_s: float, total_s: float) -> None:
        self.state = state
        self.tick_s = tick_s
        self.total_s = total_s

    async def ainvoke(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + self.total_s
        while time.monotonic() < deadline:
            await asyncio.sleep(self.tick_s)
            self.state.mark()
        return {"messages": [{"role": "assistant", "content": "done"}]}


@pytest.mark.asyncio
async def test_stuck_agent_raises_stuck_llm_round_error() -> None:
    """A simulated stuck round must raise StuckLLMRoundError after stuck_after_s.

    `timeout_s` is large so the heartbeat fires first — that's the
    whole point of having two caps. The first `mark()` simulates the
    agent having completed at least one tool call before going silent
    (otherwise the watcher correctly skips its check; see Codex P1
    review on PR #253).
    """
    state = StuckHeartbeatState()
    state.mark()  # simulate one tool return so the watcher arms
    started = time.monotonic()
    with pytest.raises(StuckLLMRoundError) as excinfo:
        await _ainvoke_with_timeout(
            _StuckAgent(),
            "do work",
            {},
            timeout_s=30,
            heartbeat_state=state,
            stuck_after_s=2,
        )
    elapsed = time.monotonic() - started
    # Must fire within ~3-5 s of the 2 s threshold (poll interval is
    # min(30, stuck_after_s/5) → 1 s for stuck_after_s=2 plus the
    # threshold itself).
    assert elapsed < 6.0, f"heartbeat should fire near 2s, took {elapsed:.2f}s"
    assert "no tool-call return in 2s" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stuck_before_first_tool_return_does_not_fire_heartbeat() -> None:
    """Codex P1 regression: a slow first round (no tool returns yet) must
    fall through to the outer wall-clock, not the heartbeat.

    With `last_tool_return_at=None` the watcher skips. Set timeout_s low
    so the outer wall-clock is what trips first → InvokeWallClockTimeout,
    not StuckLLMRoundError.
    """
    from src.compile.compiler import InvokeWallClockTimeout

    state = StuckHeartbeatState()  # default None — never marked
    with pytest.raises(InvokeWallClockTimeout):
        await _ainvoke_with_timeout(
            _StuckAgent(),
            "do work",
            {},
            timeout_s=3,
            heartbeat_state=state,
            stuck_after_s=1,
        )


@pytest.mark.asyncio
async def test_active_agent_does_not_fire() -> None:
    """A simulated active round (stamp every 1 s for 6 s) must not fire."""
    state = StuckHeartbeatState()
    agent = _ActiveAgent(state, tick_s=1.0, total_s=6.0)
    result = await _ainvoke_with_timeout(
        agent,
        "do work",
        {},
        timeout_s=30,
        heartbeat_state=state,
        stuck_after_s=2,
    )
    assert result == {"messages": [{"role": "assistant", "content": "done"}]}


@pytest.mark.asyncio
async def test_just_under_threshold_does_not_fire() -> None:
    """A stamp that arrives just under the threshold must keep the agent alive.

    Schedule a stamp at 1.5 s into a 2 s threshold; the agent should
    return cleanly. This guards against an off-by-one in the
    `idle_for > stuck_after_s` comparison.
    """
    state = StuckHeartbeatState()

    class _OneStampAgent:
        async def ainvoke(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            # Stamp before the threshold, then return shortly after.
            await asyncio.sleep(1.5)
            state.mark()
            await asyncio.sleep(0.2)
            return {"messages": [{"role": "assistant", "content": "ok"}]}

    result = await _ainvoke_with_timeout(
        _OneStampAgent(),
        "do work",
        {},
        timeout_s=10,
        heartbeat_state=state,
        stuck_after_s=2,
    )
    assert result == {"messages": [{"role": "assistant", "content": "ok"}]}


@pytest.mark.asyncio
async def test_legacy_call_without_heartbeat_still_works() -> None:
    """Calling _ainvoke_with_timeout without heartbeat keeps old behavior.

    Existing tests + scripts pass only the four positional args. The
    new feature must be opt-in.
    """

    class _FastAgent:
        async def ainvoke(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return {"messages": [{"role": "assistant", "content": "ok"}]}

    result = await _ainvoke_with_timeout(_FastAgent(), "do work", {}, timeout_s=5)
    assert result["messages"][0]["content"] == "ok"


@pytest.mark.asyncio
async def test_outer_wall_clock_still_fires_when_heartbeat_off() -> None:
    """`InvokeWallClockTimeout` must still fire on the outer cap.

    Watcher off → stuck round must still surface as the wall-clock
    timeout, not hang forever.
    """
    started = time.monotonic()
    with pytest.raises(InvokeWallClockTimeout):
        await _ainvoke_with_timeout(_StuckAgent(), "do work", {}, timeout_s=1)
    assert time.monotonic() - started < 4.0


# ---------------------------------------------------------------------------
# Coordinator integration: must route StuckLLMRoundError to pool retry.
# ---------------------------------------------------------------------------


def test_is_model_unavailable_error_catches_stuck_llm_round() -> None:
    from scripts.compile_all import _is_model_unavailable_error

    exc = StuckLLMRoundError("agent stuck — no tool-call return in 300s")
    assert _is_model_unavailable_error(exc) is True


def test_is_model_unavailable_error_catches_invoke_wall_clock_timeout() -> None:
    """Both single-round timeouts route to pool retry.

    Before this PR, `InvokeWallClockTimeout` wasn't matched, so a
    wedged provider that exceeded `invoke_timeout_s` was logged as a
    plain failure instead of triggering pool retry. The heartbeat
    feature flips that contract.
    """
    from scripts.compile_all import _is_model_unavailable_error

    exc = InvokeWallClockTimeout("agent.ainvoke exceeded 960s wall-clock limit")
    assert _is_model_unavailable_error(exc) is True


# ---------------------------------------------------------------------------
# Config wiring.
# ---------------------------------------------------------------------------


def test_compile_stuck_after_s_has_default() -> None:
    from src.config import settings

    assert isinstance(settings.compile_stuck_after_s, int)
    assert settings.compile_stuck_after_s > 0


def test_compile_stuck_after_s_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pydantic-settings picks up `COMPILE_STUCK_AFTER_S` env override."""
    from src.config import Settings

    monkeypatch.setenv("COMPILE_STUCK_AFTER_S", "42")
    s = Settings()
    assert s.compile_stuck_after_s == 42
