"""Unit tests for src/compile/middleware/edit_staleness.py."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.compile.middleware.edit_staleness import EditStalenessMiddleware
from src.compile.middleware.edit_staleness import _is_staleness_error

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_is_staleness_error_matches_modern_phrasing() -> None:
    assert _is_staleness_error("Error: String to replace not found in file: /wiki/x.md")


def test_is_staleness_error_matches_legacy_phrasing() -> None:
    # Older deepagents revisions shipped the shorter form. Both must match.
    assert _is_staleness_error("Error: String not found in file")


def test_is_staleness_error_case_insensitive() -> None:
    assert _is_staleness_error("STRING TO REPLACE NOT FOUND in file")


def test_is_staleness_error_does_not_match_unrelated_error() -> None:
    assert not _is_staleness_error("Error: Permission denied")
    assert not _is_staleness_error("Error: File not found")
    assert not _is_staleness_error("Error: Invalid frontmatter")


def test_is_staleness_error_matches_prose_with_phrase_but_caller_gates_first() -> None:
    """The matcher itself is a permissive substring check — prose
    that contains "string not found in file" verbatim WILL match.

    This is intentional. ``_is_staleness_error`` is only invoked
    inside the ``is_error`` branch of ``_process`` (i.e. when the
    ToolMessage has ``status="error"`` or content starts with
    ``Error:``). A successful ``edit_file`` response never reaches
    this matcher, so a wiki page body that incidentally contains the
    phrase can't trigger a false-positive reactive reminder.

    The double-gate (``is_error`` → ``_is_staleness_error``) is the
    real false-positive hedge; the matcher itself stays permissive
    so wording shifts in deepagents' error messages don't silently
    miss real failures.
    """
    # True positive: the actual error shape we care about.
    assert _is_staleness_error("Error: String to replace not found in file: /wiki/x.md")
    # The "prose contains it" case ALSO matches by design — but
    # _process gates with is_error first, so this never fires in
    # production for a successful edit.
    assert _is_staleness_error("string not found in file (prose example)")


# ---------------------------------------------------------------------------
# Middleware — request fixtures
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


def _ok(content: str = "wrote 1 line") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc-1", status="success")


def _err(content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc-1", status="error")


# ---------------------------------------------------------------------------
# Reactive: staleness error → re-read reminder appended
# ---------------------------------------------------------------------------


def test_reactive_reminder_appended_on_string_not_found() -> None:
    mw = EditStalenessMiddleware()

    def handler(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: String to replace not found in file: /wiki/topics/x.md")

    request = _make_request("edit_file", {"file_path": "/wiki/topics/x.md", "old_string": "..."})
    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "[edit_staleness]" in str(result.content)
    assert "call `read_file` on this path" in str(result.content)
    assert result.additional_kwargs.get("edit_staleness_reminder") == "reactive"


def test_reactive_reminder_not_stamped_on_unrelated_error() -> None:
    mw = EditStalenessMiddleware()

    def handler(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: Permission denied")

    request = _make_request("edit_file", {"file_path": "/wiki/topics/x.md"})
    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert "[edit_staleness]" not in str(result.content)
    assert "edit_staleness_reminder" not in result.additional_kwargs


# ---------------------------------------------------------------------------
# Proactive: 3 successful edits without a re-read → drift warning
# ---------------------------------------------------------------------------


def test_proactive_warning_after_three_consecutive_edits() -> None:
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/big.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # 1st and 2nd edits — counter rises but no warning yet.
    for i in range(2):
        result = mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
        assert "[edit_staleness]" not in str(result.content), f"warned too early at edit {i + 1}"

    # 3rd edit — threshold met, warning appended.
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "n3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content)
    assert "3 edits" in str(result.content)
    assert path in str(result.content)
    assert result.additional_kwargs.get("edit_staleness_reminder") == "proactive"


def test_proactive_warning_resets_after_read_file() -> None:
    """A read_file in the middle of an edit chain resets the counter,
    so the next 2 edits must NOT trigger the warning (count starts at 0).
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # 2 edits, then a re-read, then 2 more edits — none should warn.
    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw.wrap_tool_call(
        _make_request("read_file", {"file_path": path}),
        ok_handler,
    )
    for i in range(2):
        result = mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"m{i}"}),
            ok_handler,
        )
        assert "[edit_staleness]" not in str(result.content), (
            f"counter not reset after read at edit {i + 1}"
        )


def test_proactive_counter_is_per_path() -> None:
    """An edit on path A doesn't bump path B's counter."""
    mw = EditStalenessMiddleware()

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # 2 edits on path A.
    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": "/wiki/topics/a.md", "new_string": f"n{i}"}),
            ok_handler,
        )
    # 1 edit on path B — should NOT trip the threshold (B's counter = 1).
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": "/wiki/topics/b.md", "new_string": "x"}),
        ok_handler,
    )
    assert "[edit_staleness]" not in str(result.content)


def test_write_file_resets_counter() -> None:
    """A full write_file rewrites the file — agent's mental model is
    fresh, so the counter resets even if the same path was edited
    multiple times before."""
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": path, "content": "full rewrite"}),
        ok_handler,
    )
    # Two more edits — fresh counter, no warning.
    for i in range(2):
        result = mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"m{i}"}),
            ok_handler,
        )
        assert "[edit_staleness]" not in str(result.content)


def test_proactive_warning_does_not_repeat_each_edit() -> None:
    """Once the warning fires (edit #3), the counter resets; the
    next 2 edits must NOT re-trigger. We don't want to nag every
    subsequent call.
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    for i in range(3):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    # Edits 4 and 5 should not have the warning (count is back to 0).
    for i in range(2):
        result = mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"m{i}"}),
            ok_handler,
        )
        assert "[edit_staleness]" not in str(result.content)


def test_staleness_failure_resets_counter_but_unrelated_failure_does_not() -> None:
    """Staleness failures reset (the agent will re-read, fresh state).
    Non-staleness failures (Permission denied, File not found) are
    orthogonal — counter must stay intact so a real drift sequence
    isn't masked.
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    def staleness_err(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: String to replace not found in file")

    def permission_err(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: Permission denied")

    # Path A: 2 edits + staleness failure → counter resets, fresh
    # 2 edits should NOT warn (count=2).
    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "old_string": "stale"}),
        staleness_err,
    )
    for i in range(2):
        result = mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"m{i}"}),
            ok_handler,
        )
        assert "[edit_staleness]" not in str(result.content), (
            "staleness failure should reset counter — got premature warning"
        )

    # Path B: 2 edits + non-staleness failure → counter UNCHANGED,
    # next successful edit hits 3 and warns.
    mw2 = EditStalenessMiddleware()
    for i in range(2):
        mw2.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw2.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "old_string": "x"}),
        permission_err,
    )
    # Counter still at 2 — the next successful edit lands on 3.
    result = mw2.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "m3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content), (
        "permission-denied failure should NOT reset the counter; "
        "the third successful edit must still trigger the proactive warning"
    )


def test_proactive_warning_fires_again_after_reset() -> None:
    """After the warning fires on edit 3, the counter resets. A new
    sequence of 3 successful edits must trigger the warning again
    (so a long-running edit storm gets nudged repeatedly, not just
    once).
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    # First 3 edits → warning at #3.
    for i in range(3):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    # Next 2 edits → no warning yet (count is 1, then 2).
    for i in range(2):
        result = mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"m{i}"}),
            ok_handler,
        )
        assert "[edit_staleness]" not in str(result.content)
    # 6th edit overall = 3rd of the new sequence → warning fires again.
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "m3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content)
    assert "3 edits" in str(result.content)


def test_failed_read_file_does_not_reset_counter() -> None:
    """A failed read (file missing, permission denied) leaves the
    agent's mental model unaffected — the counter stays. This mirrors
    the staleness-vs-unrelated split for edit_file errors."""
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    def read_err(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: File not found")

    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    # Failed read on the same path — counter must stay at 2.
    mw.wrap_tool_call(
        _make_request("read_file", {"file_path": path}),
        read_err,
    )
    # Next successful edit hits 3 → warning fires.
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "n3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content), (
        "failed read should not reset counter; warning must still fire on edit 3"
    )


def test_failed_write_file_does_not_reset_counter() -> None:
    """Same shape as the failed-read test: a failed write_file
    didn't actually rewrite the file, so the agent's mental model is
    still potentially stale relative to disk. Counter must persist.
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    def write_err(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: Permission denied")

    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": path, "content": "..."}),
        write_err,
    )
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "n3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content), (
        "failed write should not reset counter; warning must still fire on edit 3"
    )


def test_phantom_failed_read_with_success_status_does_not_reset_counter() -> None:
    """deepagents quirk: certain read failures return
    ``status="success"`` with an ``"Error: ..."`` body. Without the
    content-prefix dual-check, this would silently reset the counter
    even though no real read happened.
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    def phantom_read(_: ToolCallRequest) -> ToolMessage:
        # status="success" but the body announces an error — same
        # shape ReadFileTruncationHintMiddleware tests defend against.
        return ToolMessage(
            content="Error: Line offset 999 exceeds file length",
            tool_call_id="tc-1",
            status="success",
        )

    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw.wrap_tool_call(
        _make_request("read_file", {"file_path": path}),
        phantom_read,
    )
    # Counter still at 2 — next successful edit hits 3 and warns.
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "n3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content), (
        "phantom failed read (status=success + Error: body) must not reset counter"
    )


def test_phantom_failed_write_with_success_status_does_not_reset_counter() -> None:
    """Same shape for write_file: a phantom failure that carries
    ``status="success"`` but an ``"Error: ..."`` body must not reset.
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    def ok_handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    def phantom_write(_: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Error: Read-only filesystem",
            tool_call_id="tc-1",
            status="success",
        )

    for i in range(2):
        mw.wrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": f"n{i}"}),
            ok_handler,
        )
    mw.wrap_tool_call(
        _make_request("write_file", {"file_path": path, "content": "..."}),
        phantom_write,
    )
    result = mw.wrap_tool_call(
        _make_request("edit_file", {"file_path": path, "new_string": "n3"}),
        ok_handler,
    )
    assert "[edit_staleness]" in str(result.content), (
        "phantom failed write (status=success + Error: body) must not reset counter"
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_does_not_double_stamp() -> None:
    """If something double-wraps the same ToolMessage, we shouldn't
    append two copies of the reminder.
    """
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"
    msg = _err("Error: String to replace not found in file")
    msg.additional_kwargs["edit_staleness_reminder"] = "reactive"
    msg.content = msg.content + "[edit_staleness] already stamped"

    def handler(_: ToolCallRequest) -> ToolMessage:
        return msg

    request = _make_request("edit_file", {"file_path": path})
    result = mw.wrap_tool_call(request, handler)
    # Reminder text appears exactly once.
    assert isinstance(result, ToolMessage)
    assert str(result.content).count("[edit_staleness]") == 1


# ---------------------------------------------------------------------------
# Pass-through: unrelated tools / non-text content / Command results
# ---------------------------------------------------------------------------


def test_non_edit_tool_passes_through_unchanged() -> None:
    mw = EditStalenessMiddleware()

    def handler(_: ToolCallRequest) -> ToolMessage:
        return _ok("[ok]")

    result = mw.wrap_tool_call(
        _make_request("resolve_page", {"query": "x"}),
        handler,
    )
    assert isinstance(result, ToolMessage)
    assert "[edit_staleness]" not in str(result.content)


def test_edit_without_path_arg_does_not_crash() -> None:
    """Defensive: a malformed edit_file call without file_path / path
    must not break the middleware. Reminder is silently skipped.
    """
    mw = EditStalenessMiddleware()

    def handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    result = mw.wrap_tool_call(
        _make_request("edit_file", {"new_string": "x"}),
        handler,
    )
    assert isinstance(result, ToolMessage)
    assert "[edit_staleness]" not in str(result.content)


# ---------------------------------------------------------------------------
# Async path — must mirror sync behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_reactive_reminder() -> None:
    mw = EditStalenessMiddleware()

    async def handler(_: ToolCallRequest) -> ToolMessage:
        return _err("Error: String to replace not found in file")

    request = _make_request("edit_file", {"file_path": "/wiki/topics/x.md"})
    result = await mw.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert "[edit_staleness]" in str(result.content)


@pytest.mark.asyncio
async def test_async_proactive_warning() -> None:
    mw = EditStalenessMiddleware()
    path = "/wiki/topics/x.md"

    async def handler(_: ToolCallRequest) -> ToolMessage:
        return _ok()

    for _ in range(3):
        result = await mw.awrap_tool_call(
            _make_request("edit_file", {"file_path": path, "new_string": "x"}),
            handler,
        )
    assert isinstance(result, ToolMessage)
    assert "[edit_staleness]" in str(result.content)
    assert "3 edits" in str(result.content)
