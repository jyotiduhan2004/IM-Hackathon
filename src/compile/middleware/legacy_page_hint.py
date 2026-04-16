"""legacy_page_hint middleware — nudge once per legacy-ontology page read.

When the agent reads a wiki page that still uses the pre-migration
ontology — `status: current` / `status: contested`, `page_type:
entity`, or a topic/system page missing `domain:` — we append a
hint to the `ToolMessage` content so the next turn sees a reminder
to migrate while the page is open ("touch-it-fix-it"). Pure
annotation: we never reject.

Legacy debt signals:
  - `status: current` (v0 default — Tier B standardises on `active`)
  - `status: contested` (old name for `contested-active` post-Tier-P)
  - `page_type: entity` (entities/ → people/ rename under Tier P)
  - topic/system page with no `domain:` field (Tier A's north-star
    navigation slug — see scripts/validate_wiki.py::check_missing_domain)

State is per-instance — one middleware per agent run — and we keep
a `hinted_paths` set so each page fires at most once per run. This
matches the CheckMyWorkGate pattern: per-run state lives on the
middleware, not in the agent graph state.

Design rationale: live traces show the agent often reads a legacy
page, edits unrelated fields, and leaves the legacy-status fields
alone — because nothing in the prompt or tool output nudged it. A
single in-context hint is cheaper than a prompt-level rule and less
noisy than a blocking validator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from src.utils import extract_frontmatter

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.types import Command

logger = structlog.get_logger(__name__)


# Only hint on reads — writes have entity_write_autoheal. Keeping the
# scope narrow also keeps the per-run state footprint small.
_READ_TOOL = "read_file"

# Wiki directories we hint inside. `raw/` is deliberately excluded —
# raw emails are immutable source docs; a "migration" hint there is
# noise. `wiki/people/` is Tier-P's post-migration destination so a
# page there is not legacy by definition.
_WIKI_PREFIXES: tuple[str, ...] = (
    "/wiki/",
    "wiki/",
)

# Page types where "missing `domain:`" is a legacy-debt signal. Per
# scripts/validate_wiki.py::check_missing_domain, only topics and
# systems get the domain field.
_DOMAIN_REQUIRED_TYPES: frozenset[str] = frozenset({"topic", "system"})

# Statuses that pre-date Tier P's migration to `active`. `superseded`
# is still valid (explicit lineage marker), so it's NOT in this set.
_LEGACY_STATUSES: frozenset[str] = frozenset({"current", "contested"})


def _strip_line_numbers(text: str) -> str:
    """Remove deepagents' `     N\\t` prefix from each line.

    `read_file` formats text content as ``<space-padded-num>\\t<line>``
    before returning. We need the raw text to parse YAML frontmatter.
    Lines without a tab pass through untouched — resilient to content
    that never went through the formatter (e.g. tests, empty file
    placeholders).
    """
    out: list[str] = []
    for line in text.split("\n"):
        # Fast path: lines with a tab in the first ~10 chars are
        # almost certainly prefixed. Splitting once is cheap and
        # correct even when the line body contains further tabs.
        if "\t" in line[:12]:
            prefix, _, rest = line.partition("\t")
            if prefix.strip().isdigit():
                out.append(rest)
                continue
        out.append(line)
    return "\n".join(out)


def _is_wiki_page_path(path: str) -> bool:
    """True if `path` points at a file under a wiki/ root.

    Post-path_autoheal we typically see leading-slash virtual paths
    (`/wiki/...`). Relative `wiki/...` is tolerated for symmetry
    with entity_write_autoheal. Non-.md reads are skipped — we only
    hint on wiki pages, not stylesheets or other assets.
    """
    stripped = path.strip()
    if not stripped or not stripped.endswith(".md"):
        return False
    return any(stripped.startswith(p) for p in _WIKI_PREFIXES)


def _detect_legacy_reasons(fm: dict[str, Any]) -> list[str]:
    """Return the list of legacy-debt reasons for a page's frontmatter.

    Empty list means the page is clean (no hint needed). Reasons are
    short human strings — they end up in the ToolMessage content so
    the agent can read them directly.
    """
    reasons: list[str] = []

    status = fm.get("status")
    if isinstance(status, str) and status.strip() in _LEGACY_STATUSES:
        reasons.append(f"status:{status.strip()}")

    page_type = fm.get("page_type")
    if isinstance(page_type, str) and page_type.strip() == "entity":
        reasons.append("page_type:entity (rename to people/)")

    # `domain:` is only meaningful for topics and systems. Relying on
    # directory heuristics would mis-fire on index.md files, so we
    # key off the frontmatter-declared page_type.
    if isinstance(page_type, str) and page_type.strip() in _DOMAIN_REQUIRED_TYPES:
        domain = fm.get("domain")
        if domain is None or (isinstance(domain, str) and not domain.strip()):
            reasons.append(f"missing domain: (on {page_type.strip()} page)")

    return reasons


class LegacyPageHintMiddleware(AgentMiddleware):
    """Hint on `read_file` for wiki pages that still use legacy ontology.

    Fires at most once per (page path, run). The set of hinted paths
    is per-instance; one middleware instance = one agent run, so
    we never leak state across compile batches.
    """

    @property
    def name(self) -> str:
        return "legacy_page_hint"

    def __init__(self) -> None:
        super().__init__()
        # Paths we've already hinted this run — keyed by the path
        # string the agent passed. We key on the exact arg the agent
        # sent, so `/wiki/foo.md` and `wiki/foo.md` are distinct.
        # That's deliberate: if the model is drifting between the
        # two, surfacing it twice is cheap and the scorecard will
        # still only count one hinted-path per page.
        self.hinted_paths: set[str] = set()

    def _maybe_hint(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> None:
        """Apply the hint in-place if the read is eligible."""
        tool_name = request.tool_call.get("name") or ""
        if tool_name != _READ_TOOL:
            return
        if not isinstance(result, ToolMessage) or result.status == "error":
            return
        args = request.tool_call.get("args") or {}
        path = args.get("file_path") or args.get("path") or ""
        if not isinstance(path, str) or not _is_wiki_page_path(path):
            return
        if path in self.hinted_paths:
            return
        # Read the content the tool actually returned. We do NOT
        # re-read from disk — the agent saw whatever this ToolMessage
        # carries, and that's what should drive the hint. Text reads
        # always come back as str (binary reads use content_blocks,
        # but those can't pass _is_wiki_page_path's .md gate).
        if not isinstance(result.content, str) or not result.content:
            return
        fm = extract_frontmatter(_strip_line_numbers(result.content))
        if not fm:
            return
        reasons = _detect_legacy_reasons(fm)
        if not reasons:
            return
        hint = (
            f"\n\n(legacy-debt hint: this page needs migration — {', '.join(reasons)}. "
            "When you edit, flip status:current→active, rename entities/→people/, "
            "or add a `domain:` field. Once-per-page-per-run.)"
        )
        result.content = result.content + hint
        result.additional_kwargs["legacy_page_hinted"] = True
        result.additional_kwargs["legacy_page_path"] = path
        result.additional_kwargs["legacy_page_reasons"] = reasons
        self.hinted_paths.add(path)
        logger.info(
            "hint.legacy_page_read",
            slug=path.rsplit("/", 1)[-1].removesuffix(".md"),
            reasons=reasons,
            path=path,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        self._maybe_hint(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        self._maybe_hint(request, result)
        return result
