"""Unit tests for src/agent/middleware/reconnaissance_paralysis.py."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.agent.middleware.reconnaissance_paralysis import RECONNAISSANCE_NUDGE_MESSAGE
from src.agent.middleware.reconnaissance_paralysis import ReconnaissanceParalysisMiddleware

NUDGE_TAG = "[reconnaissance_paralysis]"
THRESHOLD = ReconnaissanceParalysisMiddleware.READ_THRESHOLD


# ---------------------------------------------------------------------------
# Fixtures — mirror tests/test_edit_staleness.py shape
# ---------------------------------------------------------------------------


def _make_request(tool_name: str, args: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": tool_name,
            "args": args,
            "id": "tc-1",
            "type": "tool_call",
        },
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


def _ok(content: str = "ok") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc-1", status="success")


def _err(content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc-1", status="error")


def _read(mw: ReconnaissanceParalysisMiddleware, n: int = 1) -> ToolMessage:
    """Drive `n` successful reads through the middleware. Return the last result."""

    def handler(_: ToolCallRequest) -> ToolMessage:
        return _ok("file body")

    last: ToolMessage | None = None
    for i in range(n):
        result = mw.wrap_tool_call(
            _make_request("read_file", {"file_path": f"/wiki/topics/x-{i}.md"}),
            handler,
        )
        assert isinstance(result, ToolMessage)
        last = result
    assert last is not None
    return last


# ---------------------------------------------------------------------------
# Threshold trigger — 8 reads + 0 commits → nudge fires on the 8th
# ---------------------------------------------------------------------------


def test_eight_reads_with_zero_commits_fires_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()
    # Reads 1..7 — no nudge yet.
    for _ in range(THRESHOLD - 1):
        result = _read(mw, 1)
        assert NUDGE_TAG not in str(result.content), "fired before threshold"
    # 8th read — threshold reached, nudge fires.
    last = _read(mw, 1)
    assert NUDGE_TAG in str(last.content)
    assert "edit, write, or terminal-decision insight" in str(last.content)
    assert last.additional_kwargs.get("reconnaissance_paralysis_reminder") is True


def test_seven_reads_under_threshold_no_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()
    last = _read(mw, THRESHOLD - 1)
    assert NUDGE_TAG not in str(last.content)


# ---------------------------------------------------------------------------
# Commit cancels the nudge — order-independent
# ---------------------------------------------------------------------------


def test_write_file_before_threshold_cancels_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # 7 reads then a successful write_file — commit_count=1, nudge will not fire.
    _read(mw, THRESHOLD - 1)
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": "/wiki/topics/y.md", "content": "..."}),
        ok_handler,
    )
    # 8th read — nudge MUST NOT fire because a commit landed first.
    last = _read(mw, 1)
    assert NUDGE_TAG not in str(last.content)


def test_edit_file_before_threshold_cancels_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    _read(mw, THRESHOLD - 1)
    mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": "/wiki/topics/y.md", "old_string": "a"}),
        ok_handler,
    )
    last = _read(mw, 1)
    assert NUDGE_TAG not in str(last.content)


def test_patch_page_before_threshold_cancels_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    _read(mw, THRESHOLD - 1)
    mw.wrap_tool_call(
        _make_request("patch_page", {"slug": "x", "patches": []}),
        ok_handler,
    )
    last = _read(mw, 1)
    assert NUDGE_TAG not in str(last.content)


def test_log_insight_any_category_cancels_nudge() -> None:
    """Any successful log_insight (terminal OR investigatory) cancels.
    The nudge is provoking a *decision*, and even an investigatory
    log_insight proves the agent has started deciding."""
    mw = ReconnaissanceParalysisMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    _read(mw, THRESHOLD - 1)
    mw.wrap_tool_call(
        _make_request(
            "log_insight",
            {"category": "topic_merge_candidate", "summary": "..."},
        ),
        ok_handler,
    )
    last = _read(mw, 1)
    assert NUDGE_TAG not in str(last.content)


def test_commit_at_any_position_cancels_nudge() -> None:
    """Order-independent: a write at the START of the read sequence
    cancels just as well as one before the threshold.
    """
    mw = ReconnaissanceParalysisMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # write first, then 8 reads.
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": "/wiki/topics/y.md", "content": "..."}),
        ok_handler,
    )
    last = _read(mw, THRESHOLD)
    assert NUDGE_TAG not in str(last.content)


# ---------------------------------------------------------------------------
# Idempotency — fire at most once per batch
# ---------------------------------------------------------------------------


def test_nudge_fires_at_most_once_per_batch() -> None:
    mw = ReconnaissanceParalysisMiddleware()
    # 8 reads → nudge on the 8th.
    last = _read(mw, THRESHOLD)
    assert NUDGE_TAG in str(last.content)
    # 9th, 10th, 11th reads must NOT carry the nudge again.
    for _ in range(3):
        result = _read(mw, 1)
        assert NUDGE_TAG not in str(result.content), "nudge re-fired after first stamp"


# ---------------------------------------------------------------------------
# Per-instance state — fresh middleware = fresh batch
# ---------------------------------------------------------------------------


def test_new_batch_resets_counter() -> None:
    """Batch A: 8 reads → nudges. Batch B: a single read on a fresh
    instance must NOT nudge — counters are per-instance."""
    mw_a = ReconnaissanceParalysisMiddleware()
    last_a = _read(mw_a, THRESHOLD)
    assert NUDGE_TAG in str(last_a.content)

    mw_b = ReconnaissanceParalysisMiddleware()
    last_b = _read(mw_b, 1)
    assert NUDGE_TAG not in str(last_b.content)


# ---------------------------------------------------------------------------
# Failure paths — failed calls don't advance counters
# ---------------------------------------------------------------------------


def test_failed_reads_do_not_count() -> None:
    """A failed read must not advance the read counter — otherwise a
    string of permission-denied reads could trip the nudge with no
    real reconnaissance happening."""
    mw = ReconnaissanceParalysisMiddleware()

    def err_handler(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: File not found")

    # 8 failed reads — counter stays at 0, no nudge.
    for i in range(THRESHOLD):
        result = mw.wrap_tool_call(
            _make_request("read_file", {"file_path": f"/wiki/x-{i}.md"}),
            err_handler,
        )
        assert NUDGE_TAG not in str(result.content)


def test_failed_commit_does_not_cancel_nudge() -> None:
    """A failed write_file leaves no real commit — the nudge must
    still fire when the read threshold lands. Mirrors the
    edit_staleness phantom-failure handling."""
    mw = ReconnaissanceParalysisMiddleware()

    def err_handler(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: Permission denied")

    _read(mw, THRESHOLD - 1)
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": "/wiki/y.md", "content": "..."}),
        err_handler,
    )
    last = _read(mw, 1)
    assert NUDGE_TAG in str(last.content), (
        "failed commit should not cancel; nudge must still fire on 8th read"
    )


def test_phantom_success_status_with_error_body_does_not_count() -> None:
    """deepagents can return ``status='success'`` with an ``Error: ...``
    body. This must NOT count as a successful commit (nor a successful
    read). Mirrors EditStalenessMiddleware._is_failed_response."""
    mw = ReconnaissanceParalysisMiddleware()

    def phantom_write(_: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Error: Read-only filesystem",
            tool_call_id="tc-1",
            status="success",
        )

    _read(mw, THRESHOLD - 1)
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": "/wiki/y.md", "content": "..."}),
        phantom_write,
    )
    last = _read(mw, 1)
    assert NUDGE_TAG in str(last.content), "phantom-success commit must not cancel the nudge"


# ---------------------------------------------------------------------------
# Idempotency on result mutation — re-entrant wrap doesn't double-stamp
# ---------------------------------------------------------------------------


def test_idempotent_does_not_double_stamp() -> None:
    """If something pre-marks the ToolMessage and we'd otherwise stamp
    again, the kwargs guard short-circuits cleanly. We also verify the
    nudge body appears at most once. After the pre-stamped 8th read
    counts as a successful nudge, the latch must hold so a 9th read
    does NOT fire (Claude review on PR #251)."""
    mw = ReconnaissanceParalysisMiddleware()
    pre_stamped = _ok("file body")
    pre_stamped.additional_kwargs["reconnaissance_paralysis_reminder"] = True
    pre_stamped.content = str(pre_stamped.content) + RECONNAISSANCE_NUDGE_MESSAGE

    def handler_static(_: ToolCallRequest) -> ToolMessage:
        return pre_stamped

    # Drive 7 fresh reads, then the 8th returns the pre-stamped message.
    _read(mw, THRESHOLD - 1)
    result = mw.wrap_tool_call(
        _make_request("read_file", {"file_path": "/wiki/x.md"}),
        handler_static,
    )
    assert isinstance(result, ToolMessage)
    assert str(result.content).count(NUDGE_TAG) == 1
    # 9th read on a fresh ToolMessage must NOT re-fire — _nudged latch is set
    # unconditionally when threshold is met, even when _stamp_reminder returns
    # False (re-entrant guard).
    result9 = _read(mw, 1)
    assert NUDGE_TAG not in str(result9.content)


# ---------------------------------------------------------------------------
# Pass-through: unrelated tools never trigger anything
# ---------------------------------------------------------------------------


def test_unrelated_tools_pass_through() -> None:
    mw = ReconnaissanceParalysisMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok("[ok]")

    # 8 unrelated tool calls — none increment the read counter.
    for _ in range(THRESHOLD):
        result = mw.wrap_tool_call(
            _make_request("resolve_page", {"query": "x"}),
            ok_handler,
        )
        assert NUDGE_TAG not in str(result.content)
    # A single read after that — counter is still 1, no nudge.
    last = _read(mw, 1)
    assert NUDGE_TAG not in str(last.content)


# ---------------------------------------------------------------------------
# Async path — must mirror sync behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_eight_reads_fires_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()

    async def handler(_: ToolCallRequest) -> ToolMessage:
        return _ok("file body")

    last: ToolMessage | None = None
    for i in range(THRESHOLD):
        result = await mw.awrap_tool_call(
            _make_request("read_file", {"file_path": f"/wiki/x-{i}.md"}),
            handler,
        )
        assert isinstance(result, ToolMessage)
        last = result
    assert last is not None
    assert NUDGE_TAG in str(last.content)


@pytest.mark.asyncio
async def test_async_commit_cancels_nudge() -> None:
    mw = ReconnaissanceParalysisMiddleware()

    async def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # 7 async reads then async write_file — nudge cancelled.
    for i in range(THRESHOLD - 1):
        await mw.awrap_tool_call(
            _make_request("read_file", {"file_path": f"/wiki/x-{i}.md"}),
            ok_handler,
        )
    await mw.awrap_tool_call(
        _make_request("write_file", {"file_path": "/wiki/y.md", "content": "..."}),
        ok_handler,
    )
    result = await mw.awrap_tool_call(
        _make_request("read_file", {"file_path": "/wiki/x-final.md"}),
        ok_handler,
    )
    assert isinstance(result, ToolMessage)
    assert NUDGE_TAG not in str(result.content)
