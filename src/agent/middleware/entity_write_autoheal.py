"""entity_write_autoheal middleware — nudge raw person-page writes toward create_entities.

When the agent uses `write_file` or `edit_file` on `/wiki/people/<slug>.md`
(or `/wiki/entities/<slug>.md`, the legacy path retained until #67), the
write succeeds but we append a hint to the returned `ToolMessage` content
so the next turn sees a reminder to prefer
`create_entities(email=..., display_name=...)` — which generates a
deterministic email-canonical slug and initialises the stub.

The middleware name + class keep their historical `entity_write_autoheal`
label because the public tool the hint points at is still
`create_entities`.

Rationale: for three concrete production failures (see
docs/BACKLOG.md → "coordinators verify, llms propose"), letting the LLM
invent person-page slugs produced:
  - duplicates (`arjun-gaur`, `arjun-gaur-clean`, `arjun-gaur-v2`)
  - garbage (`vishakha-indiamart` from `vishakha.01@indiamart.com`)
  - numeric drift (`akash-singh6` where `6` is not a name component)

The hint is pure annotation — never blocks. The scorecard reads the
`entity_write_hinted` additional_kwarg to track adoption.
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

_ENTITY_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})

# Paths we consider person-ish — anchored at virtual roots. Leading-slash
# form is what the sandbox presents post-path_autoheal. Both `entities/`
# (legacy) and `people/` (v9-U5 canonical) match — shim retired in #67.
_ENTITY_PATH_PREFIXES: tuple[str, ...] = (
    "/wiki/entities/",
    "/wiki/people/",
    "wiki/entities/",
    "wiki/people/",
)

_HINT = (
    "\n\n(hint: for people pages, prefer create_entities(entities=[{"
    "email: ..., display_name: ...}]) — it derives a deterministic "
    "email-canonical slug and initialises the stub so you don't have "
    "to invent filenames.)"
)


def _is_entity_write(tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(is_person_page_write, target_path)``.

    Function name kept for test compatibility; semantics: does the tool
    call write to a person page (under ``wiki/people/`` post v9-U5, or
    legacy ``wiki/entities/`` until #67).
    """
    if tool_name not in _ENTITY_WRITE_TOOLS:
        return False, ""
    target = args.get("file_path") or args.get("path") or ""
    if not isinstance(target, str):
        return False, ""
    target_s = target.strip()
    if not target_s:
        return False, ""
    if not target_s.endswith(".md"):
        return False, ""
    for prefix in _ENTITY_PATH_PREFIXES:
        if target_s.startswith(prefix):
            return True, target_s
    return False, ""


def _append_hint(message: ToolMessage | Command[Any], target_path: str) -> None:
    """Append the hint to a successful ToolMessage; mark `entity_write_hinted`."""
    if not isinstance(message, ToolMessage):
        return
    # Only nudge successful writes — errors already tell the agent something
    # went wrong.
    if message.status != "success":
        return
    current = message.content
    if isinstance(current, str):
        message.content = current + _HINT
    elif isinstance(current, list):
        # content_blocks list — append a trailing text block so renderers
        # that concatenate see the hint too.
        message.content = [*current, _HINT]
    message.additional_kwargs["entity_write_hinted"] = True
    message.additional_kwargs["entity_write_target"] = target_path


class EntityWriteAutohealMiddleware(AgentMiddleware):
    """Append a create_entities nudge to raw writes on person pages.

    Class name retained for import-stability; pages are person pages
    post v9-U5 (legacy ``entities/`` writes still match the path shim).
    """

    @property
    def name(self) -> str:
        return "entity_write_autoheal"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        is_person_write, target = _is_entity_write(tool_name, request.tool_call.get("args") or {})
        result = handler(request)
        if is_person_write:
            logger.info("entity_write_hint", tool=tool_name, target=target)
            _append_hint(result, target)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        is_person_write, target = _is_entity_write(tool_name, request.tool_call.get("args") or {})
        result = await handler(request)
        if is_person_write:
            logger.info("entity_write_hint", tool=tool_name, target=target)
            _append_hint(result, target)
        return result
