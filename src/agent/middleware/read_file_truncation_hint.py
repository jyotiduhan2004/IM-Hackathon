"""read_file_truncation_hint middleware — append `total_lines` + truncation hint.

Why this exists: 83% of 521 v10 compile traces use `read_file`. The
inherited deepagents tool caps at 100 lines (DEFAULT_READ_LIMIT) and
the agent sees the clipped output with no signal that there's more
below. Live traces show the model often re-reads the same file
without offset, or stops early because it doesn't know there's more.

What this does: post-process every `read_file` ToolMessage so the
agent can always see file extent. Two states:

- Truncated (more lines on disk than were returned):
    `\\n\\n[file truncated — total_lines=N, next offset=M]`

- Complete (returned <= total):
    `\\n\\n[total_lines=N]`

Pure annotation; never blocks. We only mutate text reads — binary /
multimodal content (returned as `content_blocks`) and error reads
pass through untouched. We resolve virtual paths (e.g. `/raw/foo.md`)
using the view-root the compiler passes at construction.

Why not override `read_file` directly: deepagents wires `read_file`
into FilesystemMiddleware with backend resolution + permission
checks. Re-implementing that surface risks plumbing breaks; a
post-processor is the smaller blast radius.

Edge cases handled:
- File missing on disk between read and our stat (race) — silently
  pass through; the agent already has the content it asked for.
- Empty-file warning — pass through; the warning IS the message.
- Binary/multimodal — `content_blocks` not `content`; skip.
- Error response — `status="error"`; skip.
- ``offset`` past file end — deepagents returns "Error: Line offset
  N exceeds file length" and we pass through.
- Format collision — if disk content's last "line" is the empty
  string after final `\\n`, `splitlines()` correctly drops it, so
  total_lines matches deepagents' line numbering.

Hint format note: we deliberately avoid emoji or fancy box-drawing
characters — the LLM parses bracketed `key=value` easily and the
scorecard greps `\\[file truncated` to detect adoption.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import anyio.to_thread
import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.types import Command

logger = structlog.get_logger(__name__)


# deepagents' inherited tool defaults (verified against
# .venv/.../deepagents/middleware/filesystem.py):
#   DEFAULT_READ_OFFSET = 0
#   DEFAULT_READ_LIMIT = 100
# We mirror them here so this middleware doesn't need a runtime import
# from the vendored package. If deepagents bumps these we'll see a
# truncation-hint mismatch in tests + traces — the constants stay
# in sync via the test-fixture shape, not a runtime dep.
_DEFAULT_OFFSET = 0
_DEFAULT_LIMIT = 100

# Marker key on `additional_kwargs` so a re-entrant wrap doesn't
# stamp the same ToolMessage twice.
_HINT_KEY = "read_file_extent_hinted"

# deepagents' read_file returns a bare string prefixed with "Error: "
# when the underlying backend fails (missing file, validation failure,
# permissions). The ToolMessage that wraps it doesn't always carry
# `status="error"` — the string itself is the only reliable signal.
_ERROR_PREFIX = "Error: "


def _extract_path_offset_limit(args: dict[str, object]) -> tuple[str, int, int] | None:
    """Pull the args we need out of a `read_file` tool call.

    Returns `(file_path, offset, limit)` or None when args are unusable.
    Defaults match deepagents' tool signature.
    """
    raw_path = args.get("file_path") or args.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    raw_offset = args.get("offset", _DEFAULT_OFFSET)
    raw_limit = args.get("limit", _DEFAULT_LIMIT)
    # Models occasionally pass strings ("0", "100"). Only int / str /
    # numeric-typed values can survive coercion — anything else (dict,
    # list, None) bails so the inherited tool's own error path can
    # surface. Narrow type up front so mypy doesn't see `object`.
    offset = _coerce_to_int(raw_offset, default=_DEFAULT_OFFSET)
    limit = _coerce_to_int(raw_limit, default=_DEFAULT_LIMIT)
    if offset is None or limit is None:
        return None
    return raw_path, offset, limit


def _coerce_to_int(value: object, *, default: int) -> int | None:
    """Coerce `value` to int. Returns `default` on None, None on bad input.

    Defensive against the agent passing string-encoded numerics. Accepts
    `int` and numeric-looking `str` values; `bool` and `float` are
    explicitly rejected because:

    - `bool` is an `int` subclass but silently meaning 1/0 would mask
      an agent bug where it passed `True`/`False` instead of an int.
    - `float` would be truncated by `int(1.9)` → 1, hiding an agent
      mistake. We'd rather bail and let the caller decide.

    Anything outside these types (dict, list, None, etc.) returns None
    so the caller can short-circuit.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # Reject outright rather than silently truncate via int().
        return None
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_to_disk(view_root: Path, virtual_path: str) -> Path | None:
    """Map a chrooted virtual path (`/raw/foo.md`) to a real disk path.

    Mirrors deepagents' FilesystemBackend._resolve_path semantics for
    `virtual_mode=True`: strip leading `/`, join under view_root,
    resolve, then verify the result still lives inside view_root. We
    DON'T raise on traversal — the inherited tool already rejected
    those; we just bail.

    `view_root` should already be `.resolve()`-d (the middleware does
    that once at construction). Tests pass raw `tmp_path` so we resolve
    defensively here too — cheap on local FS, idempotent if already done.

    Returns None if the path can't be resolved or escapes the root —
    in either case the middleware silently passes through.
    """
    if not virtual_path or ".." in virtual_path:
        return None
    # Path autoheal normalizes most paths to a leading-slash form, but
    # we accept relative `raw/foo.md` for parity with the inherited
    # tool's behaviour under virtual_mode.
    rel = virtual_path.lstrip("/")
    if not rel:
        return None
    try:
        resolved = (view_root / rel).resolve()
        resolved.relative_to(view_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _count_total_lines(disk_path: Path) -> int | None:
    """Return the file's total line count, or None on any IO failure.

    Uses `splitlines()` rather than `count("\\n")` so a trailing
    newline doesn't double-count. Matches deepagents' backend, which
    splits on `\\n` and drops a trailing empty line — see
    `format_content_with_line_numbers`.
    """
    try:
        text = disk_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return len(text.splitlines())


def _count_returned_lines(content: str) -> int:
    """Count how many file lines the deepagents tool actually returned.

    deepagents formats each line as ``{6-space-padded num}\\t{body}``
    (or ``{N.K:>6}\\t{chunk}`` for >5000-char continuation chunks).
    We count any line whose first non-whitespace token is digits + an
    optional `.K` continuation suffix, then COLLAPSE continuation
    chunks back to one logical line — `total_lines` from disk does
    the same.

    Lines without that format (e.g. our own appended hint, or content
    that bypassed formatting) don't count toward the returned-lines
    tally — we want a count that's directly comparable to the
    on-disk line count.
    """
    seen: set[int] = set()
    for line in content.split("\n"):
        if "\t" not in line[:12]:
            continue
        prefix, _, _ = line.partition("\t")
        head = prefix.strip()
        if not head:
            continue
        # A continuation marker is `<int>.<int>`. We want the parent
        # line number so multiple chunks collapse to one logical line.
        primary = head.split(".", 1)[0]
        if not primary.isdigit():
            continue
        seen.add(int(primary))
    return len(seen)


def _build_hint(*, total_lines: int, offset: int, returned: int) -> tuple[str, bool]:
    """Compose the footer string and the `truncated` flag.

    Returns ``(footer, truncated)``. Truncation = there are file lines
    past the window we returned. `offset` is 0-indexed line number of
    the first line we showed; the last line shown is `offset + returned`.
    If that's < total_lines there's more below, and `next offset` is
    the next line to fetch.
    """
    last_shown = offset + returned
    if last_shown < total_lines:
        return (
            f"\n\n[file truncated — total_lines={total_lines}, next offset={last_shown}]",
            True,
        )
    return f"\n\n[total_lines={total_lines}]", False


class ReadFileTruncationHintMiddleware(AgentMiddleware):
    """Append a `[total_lines=N]` (or truncation) footer to every text `read_file`.

    Constructed once per compile run with the chrooted view-root so
    we can map virtual paths back to disk. Stateless across calls —
    every `read_file` gets stamped exactly once per call (idempotent
    via `additional_kwargs[_HINT_KEY]`).
    """

    def __init__(self, view_root: Path) -> None:
        super().__init__()
        # Resolve once at construction so per-call resolution is a
        # cheap concat. Stored as Path for ergonomic joining.
        self._view_root = Path(view_root).resolve()

    @property
    def name(self) -> str:
        return "read_file_truncation_hint"

    def _prepare_context(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[object],
    ) -> tuple[ToolMessage, Path, str, int, int] | None:
        """Resolve everything we need BEFORE disk IO — pure + cheap.

        Returns `(tool_message, disk_path, virtual_path, offset, limit)`
        when the response is eligible for annotation; None when we should
        skip (wrong tool, error response, multimodal, idempotency hit,
        traversal attempt, etc.). Separating this from the IO step lets
        the async path schedule `_count_total_lines` off the event loop.
        """
        if request.tool_call.get("name") != "read_file":
            return None
        if not isinstance(result, ToolMessage) or result.status == "error":
            return None
        # Idempotent — if a re-entrant wrap somehow fires twice, we
        # shouldn't append two footers.
        if result.additional_kwargs.get(_HINT_KEY):
            return None
        # Multimodal/binary reads carry `content_blocks`, not `content`.
        # `content` will be empty/non-str for those — skip cleanly.
        if not isinstance(result.content, str) or not result.content:
            return None
        # deepagents returns `"Error: <message>"` strings on IO failure —
        # wrapped in a ToolMessage but not always with `status="error"`.
        # Don't stamp a misleading `[total_lines=N]` footer on an error
        # body (the agent would think a valid file got read).
        if result.content.startswith(_ERROR_PREFIX):
            return None

        args = request.tool_call.get("args") or {}
        parsed = _extract_path_offset_limit(args)
        if parsed is None:
            return None
        virtual_path, offset, limit = parsed

        disk_path = _resolve_to_disk(self._view_root, virtual_path)
        if disk_path is None:
            return None
        return result, disk_path, virtual_path, offset, limit

    def _apply_hint(
        self,
        *,
        result: ToolMessage,
        total_lines: int | None,
        virtual_path: str,
        offset: int,
        limit: int,
    ) -> None:
        """Finalise the annotation given a pre-computed `total_lines`.

        The IO step (`_count_total_lines`) ran separately so the async
        path could offload it. This step is pure: inspect returned-line
        count, compose footer, mutate the ToolMessage.
        """
        # `_count_total_lines` already swallows OSError for missing /
        # permission-denied / not-a-file paths, so a None here covers
        # the same TOCTOU window we'd otherwise check via `is_file()`.
        if total_lines is None:
            return

        # Edge: empty file — deepagents returns EMPTY_CONTENT_WARNING
        # ("System reminder: File exists but has empty contents") instead
        # of formatted content. A `total_lines=0` footer would be honest
        # but redundant; the warning is already self-explanatory.
        if total_lines == 0:
            return

        assert isinstance(result.content, str)  # narrowed in _prepare_context
        returned = _count_returned_lines(result.content)
        # Defensive: if we couldn't parse any line numbers (e.g. format
        # changed in a future deepagents release), fall back to `limit`
        # capped at remaining-file-lines so the agent still sees a
        # plausible footer rather than nothing. Log a warning so we
        # notice the format drift in Langfuse/structlog.
        if returned == 0:
            fallback_returned = min(limit, max(total_lines - offset, 0))
            logger.warning(
                "read_file_format_fallback",
                path=virtual_path,
                total_lines=total_lines,
                offset=offset,
                limit=limit,
                fallback_returned=fallback_returned,
                content_preview=result.content[:120],
            )
            returned = fallback_returned

        hint, truncated = _build_hint(total_lines=total_lines, offset=offset, returned=returned)
        result.content = result.content + hint
        result.additional_kwargs.update(
            {
                _HINT_KEY: True,
                "read_file_total_lines": total_lines,
                "read_file_truncated": truncated,
            }
        )

        logger.info(
            "read_file_extent_hint",
            path=virtual_path,
            total_lines=total_lines,
            returned=returned,
            offset=offset,
            truncated=truncated,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
    ) -> ToolMessage | Command[object]:
        result = handler(request)
        ctx = self._prepare_context(request, result)
        if ctx is None:
            return result
        tool_msg, disk_path, virtual_path, offset, limit = ctx
        total_lines = _count_total_lines(disk_path)
        self._apply_hint(
            result=tool_msg,
            total_lines=total_lines,
            virtual_path=virtual_path,
            offset=offset,
            limit=limit,
        )
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[object]]],
    ) -> ToolMessage | Command[object]:
        result = await handler(request)
        ctx = self._prepare_context(request, result)
        if ctx is None:
            return result
        tool_msg, disk_path, virtual_path, offset, limit = ctx
        # Offload the blocking `read_text()` — single-agent today but
        # future-proofs against concurrent agents + keeps the event
        # loop responsive when files are on slow storage.
        total_lines = await anyio.to_thread.run_sync(_count_total_lines, disk_path)
        self._apply_hint(
            result=tool_msg,
            total_lines=total_lines,
            virtual_path=virtual_path,
            offset=offset,
            limit=limit,
        )
        return result
