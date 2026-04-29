"""Unit tests for src/compile/middleware/read_file_truncation_hint.py.

The middleware post-processes deepagents' inherited `read_file` tool
output so the agent always sees `total_lines` (and a `next offset=` hint
when truncated). Tests cover:

- pure helpers (`_count_returned_lines`, `_resolve_to_disk`,
  `_build_hint`, `_extract_path_offset_limit`, `_count_total_lines`)
- middleware end-to-end via `wrap_tool_call` / `awrap_tool_call`
- skip paths: error responses, multimodal blocks, non-`read_file` tools

Real files on disk drive total_lines computation. We use `tmp_path`
(pytest's per-test tempdir) instead of a shared fixture so tests don't
collide on file content.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.agent.middleware.read_file_truncation_hint import ReadFileTruncationHintMiddleware
from src.agent.middleware.read_file_truncation_hint import _build_hint
from src.agent.middleware.read_file_truncation_hint import _coerce_to_int
from src.agent.middleware.read_file_truncation_hint import _count_returned_lines
from src.agent.middleware.read_file_truncation_hint import _count_total_lines
from src.agent.middleware.read_file_truncation_hint import _extract_path_offset_limit
from src.agent.middleware.read_file_truncation_hint import _resolve_to_disk

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_args_uses_defaults_when_omitted() -> None:
    parsed = _extract_path_offset_limit({"file_path": "/raw/foo.md"})
    assert parsed == ("/raw/foo.md", 0, 100)


def test_extract_args_accepts_explicit_offset_limit() -> None:
    parsed = _extract_path_offset_limit({"file_path": "/raw/foo.md", "offset": 100, "limit": 200})
    assert parsed == ("/raw/foo.md", 100, 200)


def test_extract_args_coerces_string_numerics() -> None:
    parsed = _extract_path_offset_limit(
        {"file_path": "/raw/foo.md", "offset": "50", "limit": "150"}
    )
    assert parsed == ("/raw/foo.md", 50, 150)


def test_extract_args_returns_none_on_garbage_offset() -> None:
    parsed = _extract_path_offset_limit({"file_path": "/raw/foo.md", "offset": "abc"})
    assert parsed is None


def test_extract_args_returns_none_when_path_missing() -> None:
    assert _extract_path_offset_limit({}) is None
    assert _extract_path_offset_limit({"file_path": ""}) is None
    assert _extract_path_offset_limit({"file_path": 42}) is None


def test_extract_args_supports_path_alias() -> None:
    # path_autoheal sometimes hands us `path` instead of `file_path`.
    parsed = _extract_path_offset_limit({"path": "/raw/foo.md"})
    assert parsed == ("/raw/foo.md", 0, 100)


class TestCoerceToInt:
    """P2 (#201 followup): explicit rejection of `float` + `bool`.

    Silently truncating `int(1.9)` → 1 masks a real agent bug — we'd
    rather return `None` and let the caller bail.
    """

    def test_int_passes_through(self) -> None:
        assert _coerce_to_int(42, default=0) == 42
        assert _coerce_to_int(0, default=100) == 0

    def test_none_uses_default(self) -> None:
        assert _coerce_to_int(None, default=7) == 7

    def test_string_numeric_accepted(self) -> None:
        assert _coerce_to_int("99", default=0) == 99

    def test_string_garbage_rejected(self) -> None:
        assert _coerce_to_int("abc", default=0) is None

    def test_float_rejected_not_truncated(self) -> None:
        # Agent passed a float — we could truncate to 1 but that hides a
        # bug. Reject so the caller falls back to defaults / bails.
        assert _coerce_to_int(1.9, default=0) is None
        assert _coerce_to_int(0.0, default=0) is None

    def test_bool_rejected(self) -> None:
        # `bool` is an `int` subclass but True/False meaning 1/0 would
        # mask a type confusion in the agent's args.
        assert _coerce_to_int(True, default=0) is None
        assert _coerce_to_int(False, default=0) is None

    def test_other_types_rejected(self) -> None:
        assert _coerce_to_int([1], default=0) is None
        assert _coerce_to_int({"a": 1}, default=0) is None


def test_count_returned_lines_basic() -> None:
    formatted = "     1\thello\n     2\tworld\n     3\t!"
    assert _count_returned_lines(formatted) == 3


def test_count_returned_lines_collapses_continuation_chunks() -> None:
    # `5.1`, `5.2` are deepagents' continuation markers for >5000-char
    # lines. They share a parent line number — we count the parent
    # line once.
    formatted = (
        "     1\tshort\n"
        "     2\tshort\n"
        "     3\tshort\n"
        "     4\tshort\n"
        "     5\tlong-head\n"
        "   5.1\tlong-mid\n"
        "   5.2\tlong-tail"
    )
    assert _count_returned_lines(formatted) == 5


def test_count_returned_lines_ignores_unprefixed_text() -> None:
    # Footers / pre-existing hints should NOT count as returned lines.
    formatted = "     1\thello\n     2\tworld\n\n[total_lines=2]"
    assert _count_returned_lines(formatted) == 2


def test_count_returned_lines_empty() -> None:
    assert _count_returned_lines("") == 0


def test_count_total_lines_drops_trailing_newline(tmp_path: Path) -> None:
    # `splitlines()` drops the trailing empty after final `\n`, matching
    # deepagents' format_content_with_line_numbers behaviour.
    f = tmp_path / "a.md"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    assert _count_total_lines(f) == 3


def test_count_total_lines_handles_no_trailing_newline(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("alpha\nbeta\ngamma", encoding="utf-8")
    assert _count_total_lines(f) == 3


def test_count_total_lines_returns_zero_for_empty(tmp_path: Path) -> None:
    f = tmp_path / "empty.md"
    f.write_text("", encoding="utf-8")
    assert _count_total_lines(f) == 0


def test_count_total_lines_returns_none_when_missing(tmp_path: Path) -> None:
    assert _count_total_lines(tmp_path / "nope.md") is None


def test_resolve_to_disk_accepts_leading_slash(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    f = tmp_path / "raw" / "foo.md"
    f.write_text("hi\n", encoding="utf-8")
    assert _resolve_to_disk(tmp_path, "/raw/foo.md") == f.resolve()


def test_resolve_to_disk_accepts_relative_form(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir()
    f = tmp_path / "wiki" / "home.md"
    f.write_text("hi\n", encoding="utf-8")
    assert _resolve_to_disk(tmp_path, "wiki/home.md") == f.resolve()


def test_resolve_to_disk_blocks_traversal(tmp_path: Path) -> None:
    assert _resolve_to_disk(tmp_path, "/../etc/passwd") is None


def test_resolve_to_disk_returns_none_for_empty(tmp_path: Path) -> None:
    assert _resolve_to_disk(tmp_path, "") is None
    assert _resolve_to_disk(tmp_path, "/") is None


def test_build_hint_truncated() -> None:
    # 100 returned starting at 0, file has 452 lines — next offset=100
    hint, truncated = _build_hint(total_lines=452, offset=0, returned=100)
    assert hint == "\n\n[file truncated — total_lines=452, next offset=100]"
    assert truncated is True


def test_build_hint_truncated_with_offset() -> None:
    # 100 returned starting at 100 — next offset=200
    hint, truncated = _build_hint(total_lines=452, offset=100, returned=100)
    assert hint == "\n\n[file truncated — total_lines=452, next offset=200]"
    assert truncated is True


def test_build_hint_complete() -> None:
    hint, truncated = _build_hint(total_lines=78, offset=0, returned=78)
    assert hint == "\n\n[total_lines=78]"
    assert truncated is False


def test_build_hint_exact_window_complete() -> None:
    # File has exactly 100 lines, agent asked for 100 starting at 0 —
    # no truncation, no follow-up read needed.
    hint, truncated = _build_hint(total_lines=100, offset=0, returned=100)
    assert hint == "\n\n[total_lines=100]"
    assert truncated is False


# ---------------------------------------------------------------------------
# Test fixtures + helpers for end-to-end middleware tests
# ---------------------------------------------------------------------------


def _read_request(
    *, path: str, offset: int = 0, limit: int = 100, tool_call_id: str = "r1"
) -> ToolCallRequest:
    """Build a minimal `read_file` ToolCallRequest."""
    return ToolCallRequest(
        tool_call={
            "name": "read_file",
            "args": {"file_path": path, "offset": offset, "limit": limit},
            "id": tool_call_id,
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=None,  # type: ignore[arg-type]
    )


def _format_lines(start_line: int, body_lines: list[str]) -> str:
    """Mimic deepagents' format_content_with_line_numbers output."""
    return "\n".join(f"{i + start_line:6d}\t{line}" for i, line in enumerate(body_lines))


def _read_handler(content: str, *, status: str = "success") -> object:
    """Return a fake handler that yields a ToolMessage with `content`."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call["id"],
            name="read_file",
            status=status,
        )

    return handler


def _make_view(tmp_path: Path, raw_files: dict[str, str]) -> Path:
    """Set up a chrooted view-root with `/raw/<name>` files."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for name, content in raw_files.items():
        (raw_dir / name).write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Middleware — end-to-end via wrap_tool_call
# ---------------------------------------------------------------------------


def test_short_file_gets_total_lines_footer(tmp_path: Path) -> None:
    """A 78-line file read in full — footer is `[total_lines=78]`."""
    body = [f"line {i}" for i in range(1, 79)]
    view = _make_view(tmp_path, {"a.md": "\n".join(body) + "\n"})
    formatted = _format_lines(1, body)

    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/a.md", offset=0, limit=100)
    result = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert result.content.endswith("\n\n[total_lines=78]")
    assert result.additional_kwargs["read_file_total_lines"] == 78
    assert result.additional_kwargs["read_file_truncated"] is False


def test_long_file_offset_zero_truncated(tmp_path: Path) -> None:
    """452-line file, returned 100 lines from offset 0 — next offset=100."""
    body = [f"line {i}" for i in range(1, 453)]
    view = _make_view(tmp_path, {"big.md": "\n".join(body) + "\n"})
    # Simulate deepagents returning the first 100 lines, formatted.
    formatted = _format_lines(1, body[:100])

    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/big.md", offset=0, limit=100)
    result = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert result.content.endswith("\n\n[file truncated — total_lines=452, next offset=100]")
    assert result.additional_kwargs["read_file_total_lines"] == 452
    assert result.additional_kwargs["read_file_truncated"] is True


def test_long_file_offset_100_next_offset_200(tmp_path: Path) -> None:
    """Same 452-line file at offset=100 — next offset=200 (still truncated)."""
    body = [f"line {i}" for i in range(1, 453)]
    view = _make_view(tmp_path, {"big.md": "\n".join(body) + "\n"})
    # Lines 101..200 returned with cat-n numbers starting at 101.
    formatted = _format_lines(101, body[100:200])

    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/big.md", offset=100, limit=100)
    result = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert result.content.endswith("\n\n[file truncated — total_lines=452, next offset=200]")
    assert result.additional_kwargs["read_file_truncated"] is True


def test_final_window_not_truncated(tmp_path: Path) -> None:
    """Reading the tail window of a long file — footer is `[total_lines=...]`."""
    body = [f"line {i}" for i in range(1, 121)]  # 120 lines total
    view = _make_view(tmp_path, {"med.md": "\n".join(body) + "\n"})
    # Read offset=100, lines 101..120 (20 lines) — no more after.
    formatted = _format_lines(101, body[100:120])

    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/med.md", offset=100, limit=100)
    result = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert result.content.endswith("\n\n[total_lines=120]")
    assert result.additional_kwargs["read_file_truncated"] is False


def test_skips_error_response(tmp_path: Path) -> None:
    """Errors pass through untouched — no footer."""
    view = _make_view(tmp_path, {})  # empty view
    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/missing.md")
    result = mw.wrap_tool_call(
        request,
        _read_handler("Error: File '/raw/missing.md' not found", status="error"),  # type: ignore[arg-type]
    )
    assert isinstance(result, ToolMessage)
    assert "[total_lines=" not in str(result.content)
    assert "[file truncated" not in str(result.content)
    assert "read_file_total_lines" not in result.additional_kwargs


def test_skips_error_prefix_even_when_status_success(tmp_path: Path) -> None:
    """P1-ish (#201 followup): `"Error: ..."` body MUST be skipped.

    deepagents returns a plain `str` starting with `Error:` on read
    failure — the ToolMessage wrapper doesn't always carry
    `status="error"`. A misleading `[total_lines=42]` footer on an
    error body would make the agent think a valid file got read.
    Happens to match the ACTUAL disk path here (file DOES exist) so
    the middleware's disk stat would otherwise succeed and stamp a
    real footer — the only thing stopping that is the prefix check.
    """
    body = [f"line {i}" for i in range(1, 11)]
    view = _make_view(tmp_path, {"a.md": "\n".join(body) + "\n"})
    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/a.md")
    # status="success" but body is an error string — the handler layer
    # lost the error signal; our middleware must still skip.
    error_body = "Error: Line offset 999 exceeds file length"
    result = mw.wrap_tool_call(request, _read_handler(error_body, status="success"))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert str(result.content) == error_body  # unchanged — no footer
    assert "[total_lines=" not in str(result.content)
    assert "read_file_total_lines" not in result.additional_kwargs


def test_skips_when_disk_path_missing(tmp_path: Path) -> None:
    """Disk file gone (race) — pass through silently."""
    view = _make_view(tmp_path, {})
    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/ghost.md")
    formatted = _format_lines(1, ["a", "b"])
    result = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    # No append — deepagents tool would have errored anyway, but our
    # input handler gave us bytes; we just pass through.
    assert "[total_lines=" not in str(result.content)


def test_skips_non_read_tools(tmp_path: Path) -> None:
    """`write_file` / `edit_file` / etc. are not touched."""
    view = _make_view(tmp_path, {"a.md": "x\n"})
    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/raw/a.md", "content": "x"},
            "id": "w1",
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=None,  # type: ignore[arg-type]
    )
    result = mw.wrap_tool_call(request, _read_handler("ok"))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    assert "[total_lines=" not in str(result.content)
    assert "read_file_total_lines" not in result.additional_kwargs


def test_skips_multimodal_content_blocks(tmp_path: Path) -> None:
    """Image reads carry `content_blocks` instead of `content` — skip cleanly."""
    view = _make_view(tmp_path, {"a.png": "fake-png-bytes"})
    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/a.png")

    def handler(req: ToolCallRequest) -> ToolMessage:
        # Multimodal: empty `content`, populated `content_blocks`.
        return ToolMessage(
            content="",
            content_blocks=[{"type": "image", "base64": "x", "mime_type": "image/png"}],
            tool_call_id=req.tool_call["id"],
            name="read_file",
            status="success",
        )

    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    # Empty `content` — no footer appended; original content stays empty.
    assert "[total_lines=" not in str(result.content)
    assert "read_file_total_lines" not in result.additional_kwargs


def test_idempotent_double_wrap(tmp_path: Path) -> None:
    """Second pass through the middleware does not append a second footer."""
    body = [f"line {i}" for i in range(1, 11)]
    view = _make_view(tmp_path, {"a.md": "\n".join(body) + "\n"})
    formatted = _format_lines(1, body)

    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/a.md")
    first = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]
    assert isinstance(first, ToolMessage)
    # Re-wrap the SAME ToolMessage — middleware should detect the
    # `read_file_extent_hinted` flag and pass through.
    second = mw.wrap_tool_call(request, lambda _r: first)
    assert isinstance(second, ToolMessage)
    assert str(second.content).count("[total_lines=") == 1


def test_works_for_wiki_paths(tmp_path: Path) -> None:
    """Same hint surfaces on `/wiki/...` reads, not just `/raw/...`."""
    (tmp_path / "wiki" / "topics").mkdir(parents=True)
    body = [f"line {i}" for i in range(1, 61)]
    (tmp_path / "wiki" / "topics" / "foo.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    formatted = _format_lines(1, body)
    mw = ReadFileTruncationHintMiddleware(view_root=tmp_path)
    request = _read_request(path="/wiki/topics/foo.md")
    result = mw.wrap_tool_call(request, _read_handler(formatted))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert result.content.endswith("\n\n[total_lines=60]")


def test_falls_back_to_limit_when_format_unparseable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If the response doesn't carry deepagents line prefixes, fall back to limit.

    Also verifies the fallback path emits a `read_file_format_fallback`
    warning so we notice deepagents format drift in Langfuse/structlog
    (P2 #201 followup). structlog routes through its own pipeline (not
    stdlib `caplog`) so we assert against captured stdout.
    """
    body = [f"line {i}" for i in range(1, 200)]
    view = _make_view(tmp_path, {"big.md": "\n".join(body) + "\n"})
    # No line-number prefixes at all — defensive fallback should engage.
    raw_content = "alpha\nbeta\ngamma\n"

    mw = ReadFileTruncationHintMiddleware(view_root=view)
    request = _read_request(path="/raw/big.md", offset=0, limit=50)
    result = mw.wrap_tool_call(request, _read_handler(raw_content))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    # 50 (fallback) starting at 0, total 199 — truncated, next=50.
    assert "[file truncated" in result.content
    assert "total_lines=199" in result.content
    assert "next offset=50" in result.content
    # Warning surfaced so we notice format drift.
    out = capsys.readouterr().out
    assert "read_file_format_fallback" in out


@pytest.mark.asyncio
async def test_async_path_appends_hint(tmp_path: Path) -> None:
    body = [f"line {i}" for i in range(1, 41)]
    view = _make_view(tmp_path, {"a.md": "\n".join(body) + "\n"})
    formatted = _format_lines(1, body)

    mw = ReadFileTruncationHintMiddleware(view_root=view)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=formatted,
            tool_call_id=request.tool_call["id"],
            name="read_file",
            status="success",
        )

    request = _read_request(path="/raw/a.md")
    result = await mw.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert result.content.endswith("\n\n[total_lines=40]")
