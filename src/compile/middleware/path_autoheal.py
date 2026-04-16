"""path_autoheal middleware — rewrite host-path leaks in filesystem tool args.

The compile sandbox chroots the agent's filesystem view to `/raw` + `/wiki`
(symlinks inside a per-run temp view-root). Despite prompt guidance, models
sometimes emit a full host path — e.g.

    `/Users/amtagrwl/git/email-knowledge-base/raw/2026-04-11_subject_abc.md`

— instead of the virtual path `/raw/2026-04-11_subject_abc.md`. That
triggers a sandbox-outside-root reject and the agent burns turns retrying.

This middleware intercepts the six filesystem tools
(`read_file`, `write_file`, `edit_file`, `glob`, `grep`, `ls`) and, when the
arg looks like a host path that CONTAINS a `/raw/` or `/wiki/` segment,
rewrites it to start from that segment. The rewrite is annotated on the
ToolMessage via `additional_kwargs["auto_corrected_from" / "_to" /
"_confidence"]` so the scorecard can measure adoption. On ambiguity (segment
appears multiple times, or not at all) we pass the request through
untouched — the sandbox's normal reject still fires.

Pure annotation; never blocks. The design rationale is:
"Coordinators verify, LLMs propose" — we rewrite trivially-recoverable
mistakes rather than rejecting them, because rejection adds a turn without
adding information.
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

# Tools that take a path-like argument we might need to heal.
# Keep in sync with FilesystemMiddleware's tool list.
_PATH_TOOLS: frozenset[str] = frozenset(
    {"read_file", "write_file", "edit_file", "glob", "grep", "ls"}
)

# Per-tool, the kwarg key(s) that carry a path. `edit_file` takes a
# `file_path`; `glob` takes a `pattern`; `grep` takes `path` AND `pattern`.
# We only heal when the full string starts with `/` (absolute-style) — a
# relative `raw/foo.md` path works fine under virtual_mode.
_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "read_file": ("file_path", "path"),
    "write_file": ("file_path", "path"),
    "edit_file": ("file_path",),
    "glob": ("pattern", "path"),
    "grep": ("path", "pattern"),
    "ls": ("path",),
}

# Virtual roots the chroot exposes. A host path that contains `/raw/` or
# `/wiki/` somewhere in the middle is the canonical leak shape.
_VIRTUAL_ROOTS: tuple[str, ...] = ("/raw/", "/wiki/")


def _try_rewrite(value: str) -> str | None:
    """If `value` is a leaked host path pointing into /raw or /wiki, rewrite.

    Returns the rewritten virtual path on success, or None when:
      - the value does not start with `/`
      - the value already starts with `/raw/` or `/wiki/` (nothing to do)
      - the virtual root segment appears zero or >1 times (ambiguous)
      - the value is a root path like `/raw` or `/wiki` (no suffix)
    """
    value = value.strip()
    if not value.startswith("/"):
        return None
    # Already a virtual path — no-op.
    for root in _VIRTUAL_ROOTS:
        if value == root.rstrip("/") or value.startswith(root):
            return None
    # Find the FIRST virtual root segment; require exactly one occurrence
    # to avoid double-rewriting a path like `/foo/raw/bar/raw/baz.md`.
    for root in _VIRTUAL_ROOTS:
        first = value.find(root)
        if first == -1:
            continue
        if value.find(root, first + 1) != -1:
            # Ambiguous — let sandbox handle it.
            return None
        rewritten = value[first:]
        if rewritten.rstrip("/") == root.rstrip("/"):
            return None
        return rewritten
    return None


def _rewrite_args(
    tool_name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return (new_args, corrections). `corrections` is empty when nothing changed."""
    keys = _PATH_KEYS.get(tool_name, ())
    if not keys:
        return args, []
    new_args = dict(args)
    corrections: list[dict[str, str]] = []
    for key in keys:
        original = new_args.get(key)
        if not isinstance(original, str):
            continue
        rewritten = _try_rewrite(original)
        if rewritten is not None and rewritten != original:
            new_args[key] = rewritten
            corrections.append({"key": key, "from": original, "to": rewritten})
    return new_args, corrections


def _annotate(message: ToolMessage | Command[Any], corrections: list[dict[str, str]]) -> None:
    """Stamp `auto_corrected_*` on a ToolMessage's additional_kwargs.

    Commands are passed through unchanged — they're a branching return that
    deepagents uses sparingly; the scorecard only reads ToolMessages.
    """
    if not corrections:
        return
    if not isinstance(message, ToolMessage):
        return
    message.additional_kwargs["auto_corrected_from"] = [c["from"] for c in corrections]
    message.additional_kwargs["auto_corrected_to"] = [c["to"] for c in corrections]
    message.additional_kwargs["auto_corrected_confidence"] = "high"


class PathAutohealMiddleware(AgentMiddleware):
    """Rewrite host-path leaks in filesystem-tool args to virtual paths."""

    @property
    def name(self) -> str:
        return "path_autoheal"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        if tool_name not in _PATH_TOOLS:
            return handler(request)

        args = request.tool_call.get("args") or {}
        new_args, corrections = _rewrite_args(tool_name, args)
        if corrections:
            logger.info(
                "path_autoheal_rewrite",
                tool=tool_name,
                corrections=corrections,
            )
            request.tool_call["args"] = new_args

        result = handler(request)
        _annotate(result, corrections)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        if tool_name not in _PATH_TOOLS:
            return await handler(request)

        args = request.tool_call.get("args") or {}
        new_args, corrections = _rewrite_args(tool_name, args)
        if corrections:
            logger.info(
                "path_autoheal_rewrite",
                tool=tool_name,
                corrections=corrections,
            )
            request.tool_call["args"] = new_args

        result = await handler(request)
        _annotate(result, corrections)
        return result
