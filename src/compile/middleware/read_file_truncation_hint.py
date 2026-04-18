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

    Defensive against the agent passing string-encoded numerics. Anything
    that isn't `int | str | float | None` fails the isinstance gate and
    we return None so the caller can bail.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        # `bool` is an `int` subclass but we never want True/False here
        # to silently mean 1/0 — that would mask an agent bug.
        return None
    if not isinstance(value, int | str | float):
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

    def _maybe_append_hint(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[object],
    ) -> None:
        """Mutate `result.content` in-place when eligible."""
        if request.tool_call.get("name") != "read_file":
            return
        if not isinstance(result, ToolMessage) or result.status == "error":
            return
        # Idempotent — if a re-entrant wrap somehow fires twice, we
        # shouldn't append two footers.
        if result.additional_kwargs.get(_HINT_KEY):
            return
        # Multimodal/binary reads carry `content_blocks`, not `content`.
        # `content` will be empty/non-str for those — skip cleanly.
        if not isinstance(result.content, str) or not result.content:
            return

        args = request.tool_call.get("args") or {}
        parsed = _extract_path_offset_limit(args)
        if parsed is None:
            return
        virtual_path, offset, limit = parsed

        disk_path = _resolve_to_disk(self._view_root, virtual_path)
        if disk_path is None:
            return
        total_lines = _count_total_lines(disk_path)
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

        returned = _count_returned_lines(result.content)
        # Defensive: if we couldn't parse any line numbers (e.g. format
        # changed in a future deepagents release), fall back to `limit`
        # capped at remaining-file-lines so the agent still sees a
        # plausible footer rather than nothing.
        if returned == 0:
            returned = min(limit, max(total_lines - offset, 0))

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
        self._maybe_append_hint(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[object]]],
    ) -> ToolMessage | Command[object]:
        result = await handler(request)
        self._maybe_append_hint(request, result)
        return result
