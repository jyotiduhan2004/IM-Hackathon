"""Tests for `CheckMyWorkGateMiddleware`.

The middleware is stateful: one instance owns a per-session set of
successful content-page writes. These tests drive it directly —
building `ToolCallRequest` objects with hand-rolled handlers — so we
don't need a live model or LangGraph runner.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.agent.middleware.check_my_work_gate import GATE_REJECT_MESSAGE
from src.agent.middleware.check_my_work_gate import GATE_REJECT_PAT
from src.agent.middleware.check_my_work_gate import POST_WRITE_NUDGE_MESSAGE
from src.agent.middleware.check_my_work_gate import POST_WRITE_NUDGE_PAT
from src.agent.middleware.check_my_work_gate import CheckMyWorkGateMiddleware

# ---------------------------------------------------------------------------
# Request / handler helpers
# ---------------------------------------------------------------------------


def _make_request(
    name: str,
    args: dict[str, Any] | None = None,
    tool_call_id: str = "call_1",
) -> ToolCallRequest:
    """Build a minimal ToolCallRequest for middleware tests.

    `state` is a dict with an empty messages list — enough to satisfy
    the middleware's access pattern without pulling in the full
    LangGraph runtime.
    """
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": args or {},
            "id": tool_call_id,
            "type": "tool_call",
        },
        tool=MagicMock(name=name),
        state={"messages": []},
        runtime=MagicMock(),
    )


def _success_handler(name: str = "write_file") -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that returns a successful ToolMessage. Deterministic."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    return handler


def _error_handler(name: str = "write_file") -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that returns an error-status ToolMessage."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="disk full",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    return handler


def _critique_handler() -> Callable[[ToolCallRequest], ToolMessage]:
    """Handler that simulates a successful check_my_work result."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": "true", "status": "clean"}',
            tool_call_id=request.tool_call["id"],
            name="check_my_work",
        )

    return handler


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


def test_empty_session_check_my_work_is_rejected() -> None:
    """No successful writes → check_my_work is blocked with synthetic error."""
    mw = CheckMyWorkGateMiddleware()
    request = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})
    # Handler should NOT be called — we assert by failing if it is.
    handler = MagicMock(side_effect=AssertionError("handler must not run"))

    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.name == "check_my_work"
    assert result.tool_call_id == "call_1"
    assert result.content == GATE_REJECT_MESSAGE
    handler.assert_not_called()


def test_successful_write_then_check_my_work_passes_through() -> None:
    """write_file success → check_my_work handler runs normally."""
    mw = CheckMyWorkGateMiddleware()

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"}, "call_2")
    result = mw.wrap_tool_call(check_req, _critique_handler())

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    assert "clean" in str(result.content)


def test_write_draft_page_success_alone_is_rejected() -> None:
    """write_draft_page doesn't count — drafts aren't content writes."""
    mw = CheckMyWorkGateMiddleware()

    draft_req = _make_request("write_draft_page", {"slug": "maybe-page"})
    mw.wrap_tool_call(draft_req, _success_handler("write_draft_page"))

    assert mw.successful_write_tools == set(), (
        "write_draft_page must NOT be tracked as a content write"
    )

    check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})
    handler = MagicMock(side_effect=AssertionError("handler must not run"))
    result = mw.wrap_tool_call(check_req, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert GATE_REJECT_PAT.search(str(result.content))


def test_failed_write_does_not_count() -> None:
    """Error-status write → not counted → check_my_work still rejected."""
    mw = CheckMyWorkGateMiddleware()

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _error_handler("write_file"))

    assert mw.successful_write_tools == set()

    check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})
    handler = MagicMock(side_effect=AssertionError("handler must not run"))
    result = mw.wrap_tool_call(check_req, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert GATE_REJECT_PAT.search(str(result.content))


def test_other_tools_pass_through() -> None:
    """Tools other than check_my_work are not intercepted."""
    mw = CheckMyWorkGateMiddleware()

    for name in ("read_file", "ls", "glob", "list_wiki_pages", "resolve_page"):
        request = _make_request(name, {"file_path": "raw/foo.md"})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="x", name=name))
        result = mw.wrap_tool_call(request, handler)
        assert result.content == "ok", f"{name}: unexpected interception"
        handler.assert_called_once()


def test_edit_file_and_patch_page_also_count() -> None:
    """edit_file and patch_page successes both unlock check_my_work."""
    for tool in ("edit_file", "patch_page"):
        mw = CheckMyWorkGateMiddleware()
        write_req = _make_request(tool, {"file_path": "wiki/topics/foo.md"})
        mw.wrap_tool_call(write_req, _success_handler(tool))

        check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})
        result = mw.wrap_tool_call(check_req, _critique_handler())

        assert isinstance(result, ToolMessage)
        assert result.status != "error", f"{tool}: gate did not open"


def test_gate_reject_pattern_matches_canonical_message() -> None:
    """The shared regex matches the middleware's own rejection message."""
    assert GATE_REJECT_PAT.search(GATE_REJECT_MESSAGE) is not None


def test_touched_paths_recorded_on_success() -> None:
    """Successful writes record their file_path into touched_paths."""
    mw = CheckMyWorkGateMiddleware()
    request = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(request, _success_handler("write_file"))

    assert mw.touched_paths == {"wiki/topics/foo.md"}
    assert mw.successful_write_tools == {"write_file"}


# ---------------------------------------------------------------------------
# Async path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awrap_tool_call_rejects_empty_session() -> None:
    """Async variant rejects premature check_my_work identically."""
    mw = CheckMyWorkGateMiddleware()
    request = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        raise AssertionError("handler must not run")

    result = await mw.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.content == GATE_REJECT_MESSAGE


@pytest.mark.asyncio
async def test_awrap_tool_call_passes_through_after_write() -> None:
    """Async path opens the gate after a successful async write."""
    mw = CheckMyWorkGateMiddleware()

    async def async_success(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name="write_file",
        )

    async def async_critique(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": "true"}',
            tool_call_id=request.tool_call["id"],
            name="check_my_work",
        )

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    await mw.awrap_tool_call(write_req, async_success)

    check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"})
    result = await mw.awrap_tool_call(check_req, async_critique)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


# ---------------------------------------------------------------------------
# Post-write requirement (after_model nudge)
# ---------------------------------------------------------------------------


def _state_with_final_ai(content: str = "done") -> dict[str, Any]:
    """Build an AgentState snapshot where the agent just issued an empty AIMessage.

    An empty AIMessage = no tool calls, which is the classic exit
    signal in the langchain agent loop.
    """
    return {"messages": [AIMessage(content=content)]}


def _state_still_looping() -> dict[str, Any]:
    """Build an AgentState snapshot mid-loop (agent still calling tools)."""
    ai = AIMessage(
        content="calling check",
        tool_calls=[
            {
                "name": "check_my_work",
                "args": {"raw_email_path": "raw/foo.md"},
                "id": "tc_1",
                "type": "tool_call",
            }
        ],
    )
    return {"messages": [ai]}


def test_post_write_requirement_nudges_on_exit_without_check() -> None:
    """Agent writes content, then tries to exit without check_my_work → nudge."""
    mw = CheckMyWorkGateMiddleware()
    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    update = mw.after_model(_state_with_final_ai(), MagicMock())

    assert update is not None
    assert update["jump_to"] == "model"
    messages = update["messages"]
    assert len(messages) == 1
    injected = messages[0]
    assert isinstance(injected, AIMessage)
    assert POST_WRITE_NUDGE_PAT.search(str(injected.content))


def test_post_write_requirement_cleared_by_successful_check() -> None:
    """Write, then successful check_my_work, then exit → no nudge."""
    mw = CheckMyWorkGateMiddleware()

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"}, "call_2")
    mw.wrap_tool_call(check_req, _critique_handler())

    update = mw.after_model(_state_with_final_ai(), MagicMock())

    assert update is None


def test_post_write_requirement_no_nudge_when_no_writes() -> None:
    """Pure trivial_skip batch (no content writes) → no nudge on exit."""
    mw = CheckMyWorkGateMiddleware()
    # Simulate `log_insight("trivial_skip", ...)` — not a content
    # write, doesn't flip the pending flag.
    insight_req = _make_request("log_insight", {"kind": "trivial_skip"})
    mw.wrap_tool_call(
        insight_req,
        lambda r: ToolMessage(content='{"ok": true}', tool_call_id=r.tool_call["id"]),
    )

    update = mw.after_model(_state_with_final_ai(), MagicMock())

    assert update is None


def test_post_write_requirement_no_nudge_mid_loop() -> None:
    """Agent wrote content but is still issuing tool calls → don't nudge yet."""
    mw = CheckMyWorkGateMiddleware()
    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    # Still calling tools — the agent hasn't tried to exit yet.
    update = mw.after_model(_state_still_looping(), MagicMock())

    assert update is None


def test_post_write_requirement_does_not_double_nudge() -> None:
    """Two consecutive exit attempts without an intervening write → nudge once."""
    mw = CheckMyWorkGateMiddleware()
    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    first = mw.after_model(_state_with_final_ai(), MagicMock())
    assert first is not None

    # Agent ignores the nudge and exits again — don't keep injecting.
    # Downstream guards (PR C) catch the repeat refusal.
    second = mw.after_model(_state_with_final_ai(), MagicMock())
    assert second is None


def test_post_write_requirement_rearms_after_fresh_write() -> None:
    """Nudge once, agent writes more content → nudge again on next exit attempt."""
    mw = CheckMyWorkGateMiddleware()
    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))
    assert mw.after_model(_state_with_final_ai(), MagicMock()) is not None

    # Agent does another write without ever calling check_my_work — a
    # fresh unchecked write should re-arm the nudge.
    write_req2 = _make_request("edit_file", {"file_path": "wiki/topics/bar.md"}, "call_3")
    mw.wrap_tool_call(write_req2, _success_handler("edit_file"))

    update = mw.after_model(_state_with_final_ai(), MagicMock())
    assert update is not None


def test_post_write_requirement_failed_check_does_not_clear() -> None:
    """Write, then error-status check_my_work result → still nudge on exit."""
    mw = CheckMyWorkGateMiddleware()
    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    check_req = _make_request("check_my_work", {"raw_email_path": "raw/foo.md"}, "call_2")
    mw.wrap_tool_call(check_req, _error_handler("check_my_work"))

    update = mw.after_model(_state_with_final_ai(), MagicMock())
    assert update is not None


@pytest.mark.asyncio
async def test_post_write_requirement_async_path() -> None:
    """Async `aafter_model` mirrors sync behavior."""
    mw = CheckMyWorkGateMiddleware()

    async def async_success(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name="write_file",
        )

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    await mw.awrap_tool_call(write_req, async_success)

    update = await mw.aafter_model(_state_with_final_ai(), MagicMock())

    assert update is not None
    assert update["jump_to"] == "model"
    assert POST_WRITE_NUDGE_PAT.search(str(update["messages"][0].content))


def test_post_write_nudge_pattern_matches_canonical_message() -> None:
    """Shared regex matches the middleware's own nudge message."""
    assert POST_WRITE_NUDGE_PAT.search(POST_WRITE_NUDGE_MESSAGE) is not None


# ---------------------------------------------------------------------------
# Integration smoke — middleware is wired into create_compiler
# ---------------------------------------------------------------------------


def test_middleware_wired_into_compiler() -> None:
    """The compiler includes the gate middleware at construction time.

    Smoke-level: we don't run an agent, just confirm the middleware's
    `wrap_tool_call` is composed into the ToolNode's wrapper chain so
    the wiring survives refactors. deepagents nests middlewares pairwise
    via `_chain_tool_call_wrappers.compose_two`, so with >1 middleware
    the gate's `wrap_tool_call` lives several layers deep in the closure
    chain rather than at the outermost level — we walk recursively.
    """
    from src.agent.compiler_agent import create_compiler

    agent = create_compiler("z-ai/glm-5")
    tools_node = agent.nodes["tools"].bound
    wrapper = getattr(tools_node, "_wrap_tool_call", None)
    assert wrapper is not None, "ToolNode missing _wrap_tool_call — LangGraph API shifted"

    found: list[str] = []
    seen: set[int] = set()

    def walk(fn: object) -> None:
        if not callable(fn) or id(fn) in seen:
            return
        seen.add(id(fn))
        closure = getattr(fn, "__closure__", None) or ()
        for cell in closure:
            try:
                contents = cell.cell_contents
            except ValueError:
                continue
            qualname = getattr(contents, "__qualname__", None) or ""
            if "CheckMyWorkGateMiddleware" in qualname:
                found.append(qualname)
            if callable(contents):
                walk(contents)

    walk(wrapper)
    assert found, (
        "CheckMyWorkGateMiddleware.wrap_tool_call not found anywhere in "
        "ToolNode's wrapper chain — check that create_compiler passes the "
        "middleware into create_deep_agent(middleware=[...])."
    )


# ---------------------------------------------------------------------------
# Idempotent check_my_work cache (#168)
#
# After PR #225 wired the post-write nudge, agents started spinning on
# broken-wikilink blockers — 4-7 consecutive blocked `check_my_work`
# calls with zero intervening writes. Each call re-ran the full critique
# for no reason. This cache short-circuits repeat calls when the module-
# level write_epoch hasn't changed, returning the prior payload with
# `unchanged_since: true` + a nudge to stop spinning.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _reset_check_my_work_cache():
    """Clear module-level cache + write_epoch between tests.

    Tests in this block share module state; without this fixture a
    leftover cache entry from one test can mask a miss expected by
    another.
    """
    from src.agent import run_state as _run_state

    _run_state._check_my_work_cache.clear()
    _run_state._write_epoch = 0
    yield
    _run_state._check_my_work_cache.clear()
    _run_state._write_epoch = 0


def test_check_my_work_caches_when_no_writes_between(
    monkeypatch, tmp_path, _reset_check_my_work_cache
) -> None:
    """Two consecutive calls with no edit between → second call returns
    cached payload with `unchanged_since: true`, and critique_pages runs
    only once (not twice).
    """
    from src.agent import critique as critique_mod
    from src.agent.critique import CritiqueResult
    from src.agent.tools import legacy as compiler_mod

    # Pretend the repo_root is tmp_path so no real wiki is touched.
    monkeypatch.setattr(compiler_mod.Path, "cwd", staticmethod(lambda: tmp_path))

    call_count = {"critique_pages": 0, "find_touched_pages": 0, "write_audit": 0}

    def fake_find_touched_pages(_raw_path, _wiki_dir):
        call_count["find_touched_pages"] += 1
        return []

    def fake_critique_pages(_paths, _wiki_dir, _repo_root):
        call_count["critique_pages"] += 1
        return CritiqueResult(
            issues=[],
            pages_critiqued=["wiki/topics/foo.md"],
        )

    def fake_write_audit(_result, _raw, _status, audit_dir, acknowledged_ids=None):
        call_count["write_audit"] += 1
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "stub.md"
        audit_path.write_text("stub\n", encoding="utf-8")
        return audit_path

    monkeypatch.setattr(critique_mod, "find_touched_pages", fake_find_touched_pages)
    monkeypatch.setattr(critique_mod, "critique_pages", fake_critique_pages)
    monkeypatch.setattr(critique_mod, "write_audit", fake_write_audit)

    raw_path = "raw/2026-04-23_test.md"
    # First call — runs critique, populates cache.
    first = compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    assert call_count["critique_pages"] == 1
    assert first["status"] == "clean"
    assert "unchanged_since" not in first

    # Second call, no intervening write — hits cache.
    second = compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    assert call_count["critique_pages"] == 1, "critique_pages must NOT run again"
    assert second.get("unchanged_since") is True
    assert "Same blockers" in second["message"] or "Same" in second["message"]


def test_check_my_work_reruns_after_write(
    monkeypatch, tmp_path, _reset_check_my_work_cache
) -> None:
    """Call, bump write_epoch (simulate an edit_file success), call again
    → critique_pages must run twice.
    """
    from src.agent import critique as critique_mod
    from src.agent.critique import CritiqueResult
    from src.agent.tools import legacy as compiler_mod

    monkeypatch.setattr(compiler_mod.Path, "cwd", staticmethod(lambda: tmp_path))

    call_count = {"critique_pages": 0}

    def fake_find_touched_pages(_raw_path, _wiki_dir):
        return []

    def fake_critique_pages(_paths, _wiki_dir, _repo_root):
        call_count["critique_pages"] += 1
        return CritiqueResult(
            issues=[],
            pages_critiqued=["wiki/topics/foo.md"],
        )

    def fake_write_audit(_result, _raw, _status, audit_dir, acknowledged_ids=None):
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "stub.md"
        audit_path.write_text("stub\n", encoding="utf-8")
        return audit_path

    monkeypatch.setattr(critique_mod, "find_touched_pages", fake_find_touched_pages)
    monkeypatch.setattr(critique_mod, "critique_pages", fake_critique_pages)
    monkeypatch.setattr(critique_mod, "write_audit", fake_write_audit)

    from src.agent import run_state as _run_state

    raw_path = "raw/2026-04-23_test.md"
    compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    assert call_count["critique_pages"] == 1

    # Simulate a successful write bumping the epoch.
    _run_state._bump_write_epoch()

    compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    assert call_count["critique_pages"] == 2, (
        "After an edit, critique must re-run — cache should have invalidated."
    )


def test_cached_payload_preserves_audit_path(
    monkeypatch, tmp_path, _reset_check_my_work_cache
) -> None:
    """The audit markdown path in the cached response matches the first run.

    Prevents a regression where the cache copies only a subset of fields.
    """
    from src.agent import critique as critique_mod
    from src.agent.critique import CritiqueResult
    from src.agent.tools import legacy as compiler_mod

    monkeypatch.setattr(compiler_mod.Path, "cwd", staticmethod(lambda: tmp_path))

    def fake_find_touched_pages(_raw_path, _wiki_dir):
        return []

    def fake_critique_pages(_paths, _wiki_dir, _repo_root):
        return CritiqueResult(
            issues=[],
            pages_critiqued=["wiki/topics/foo.md"],
        )

    audit_paths: list[Path] = []

    def fake_write_audit(_result, _raw, _status, audit_dir, acknowledged_ids=None):
        audit_dir.mkdir(parents=True, exist_ok=True)
        # Unique name per call so the test can prove cache reuse —
        # if the cache re-ran, write_audit would emit a second path
        # and the cached payload would still point at the first.
        audit_path = audit_dir / f"critique-{len(audit_paths)}.md"
        audit_path.write_text("stub\n", encoding="utf-8")
        audit_paths.append(audit_path)
        return audit_path

    monkeypatch.setattr(critique_mod, "find_touched_pages", fake_find_touched_pages)
    monkeypatch.setattr(critique_mod, "critique_pages", fake_critique_pages)
    monkeypatch.setattr(critique_mod, "write_audit", fake_write_audit)

    raw_path = "raw/2026-04-23_test.md"
    first = compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    first_audit = first["audit"]
    assert first_audit.endswith("critique-0.md")

    second = compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    assert second["audit"] == first_audit, (
        "Cached payload must expose the SAME audit path — not a new one."
    )
    # write_audit should have fired exactly once (cache served the second call).
    assert len(audit_paths) == 1


def test_cache_invalidated_by_different_acknowledge(
    monkeypatch, tmp_path, _reset_check_my_work_cache
) -> None:
    """Changing the acknowledge list must force a fresh critique — different
    ack set ≠ same question, so the cache key must include ack_hash.
    """
    from src.agent import critique as critique_mod
    from src.agent.critique import CritiqueResult
    from src.agent.tools import legacy as compiler_mod

    monkeypatch.setattr(compiler_mod.Path, "cwd", staticmethod(lambda: tmp_path))

    call_count = {"critique_pages": 0}

    def fake_find_touched_pages(_raw_path, _wiki_dir):
        return []

    def fake_critique_pages(_paths, _wiki_dir, _repo_root):
        call_count["critique_pages"] += 1
        return CritiqueResult(
            issues=[],
            pages_critiqued=["wiki/topics/foo.md"],
        )

    def fake_write_audit(_result, _raw, _status, audit_dir, acknowledged_ids=None):
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "stub.md"
        audit_path.write_text("stub\n", encoding="utf-8")
        return audit_path

    monkeypatch.setattr(critique_mod, "find_touched_pages", fake_find_touched_pages)
    monkeypatch.setattr(critique_mod, "critique_pages", fake_critique_pages)
    monkeypatch.setattr(critique_mod, "write_audit", fake_write_audit)

    raw_path = "raw/2026-04-23_test.md"
    compiler_mod.check_my_work.invoke({"raw_email_path": raw_path})
    compiler_mod.check_my_work.invoke({"raw_email_path": raw_path, "acknowledge": ["issue-abc"]})
    # Second call had a different ack_hash → cache miss → critique runs again.
    assert call_count["critique_pages"] == 2


def test_bump_write_epoch_called_on_successful_content_write(
    _reset_check_my_work_cache,
) -> None:
    """The middleware wires `_bump_write_epoch` into `_record_success`, so
    recording a successful content write must increment the counter.
    """
    from src.agent import run_state as _run_state

    mw = CheckMyWorkGateMiddleware()
    start = _run_state._write_epoch

    write_req = _make_request("write_file", {"file_path": "wiki/topics/foo.md"})
    mw.wrap_tool_call(write_req, _success_handler("write_file"))

    assert _run_state._write_epoch == start + 1, (
        "CheckMyWorkGateMiddleware._record_success must bump the module-level "
        "write_epoch so the check_my_work cache invalidates."
    )
