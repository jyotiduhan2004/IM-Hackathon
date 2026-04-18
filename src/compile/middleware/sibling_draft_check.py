"""sibling_draft_check middleware — block batch-local near-duplicate page writes.

Why this exists: Cycle 10 smoke batch 5 wrote two near-duplicate topic
pages (`seller-bl-api-optimization` + `seller-bl-api-hit-optimisation`)
from the same thread in one batch. The v9-U14 reviewer queues these to
`wiki/merge_candidates.md` POST-WRITE — so both pages still ship before
anyone notices. This middleware catches the second write BEFORE it
lands, telling the agent "you just wrote X; consider merging Y into it
instead of creating a new page".

Companion to `same_thread_topic_guard`: that middleware is hard-blocking
on a stricter trigger (same thread + new topic slug); this one is a
softer, looser sibling check that fires across systems too and can be
bypassed via `force_sibling=True` when the agent insists the two pages
are legitimately distinct.

Trigger: a `write_file` to `/wiki/topics/<slug>.md` or
`/wiki/systems/<slug>.md`, OR a `write_draft_page(slug=...)` call,
where the new slug shares ≥3 dominant tokens (or ≥70% of the shorter
slug's tokens — whichever fires first) with a slug already written in
this batch. Stopwords (`the`, `a`, `for`, ...) are filtered before
comparison so they don't pad the overlap.

Escape hatch: pass `force_sibling=True` in the tool args. The
middleware logs a `sibling_check_bypass` event (visible in Langfuse via
structlog) and lets the write through. Agents that genuinely need a
second related page (e.g. a system + a topic that share vocabulary)
get a way out without us re-writing the prompt.

Explicitly NOT blocked:
- The first write of a batch (no prior slug to compare against).
- Writes to non-sibling-tracked paths (decisions, policies, people,
  drafts that aren't via `write_draft_page`).
- Slugs with fewer than `_MIN_SLUG_TOKENS` (3) meaningful tokens —
  short slugs are rare + likely legitimate.
"""

from __future__ import annotations

import json
import re
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


# Match topic + system page paths; we deliberately do NOT cover
# decisions/policies/people because (a) decisions are lazy-created
# anyway, (b) policies are rare, (c) people pages share vocabulary
# trivially (everyone has "kumar" or "sharma") and would false-trigger.
_SIBLING_PATH_RE = re.compile(r"^/?wiki/(topics|systems)/([^/]+)\.md$")

# Stopwords stripped before token comparison. Kept short — these are
# the words that most often pad slug overlap without carrying any
# concept signal. Add more only with a real false-negative case.
_STOPWORDS: frozenset[str] = frozenset({"the", "a", "and", "for", "of", "to", "in", "on"})


def _extract_sibling_slug(path: str) -> str | None:
    """Return the slug if `path` points at /wiki/{topics,systems}/<slug>.md; else None."""
    m = _SIBLING_PATH_RE.match(path.strip())
    if not m:
        return None
    return m.group(2)


def _slug_tokens(slug: str) -> set[str]:
    """Lowercase-tokenize a slug, dropping stopwords and empty parts."""
    return {tok for tok in slug.lower().split("-") if tok and tok not in _STOPWORDS}


def _target_slug(tool_name: str, args: dict[str, Any]) -> str | None:
    """Return the slug this tool call would create, or None if not in scope.

    Two entry points:
      - `write_file` with file_path/path under /wiki/topics or /wiki/systems
      - `write_draft_page` with an explicit `slug` arg

    Drafts are sibling-tracked too: a draft page that overlaps a freshly
    written topic is the same fragmentation bug in disguise.
    """
    if tool_name == "write_file":
        path = args.get("file_path") or args.get("path")
        if not isinstance(path, str):
            return None
        return _extract_sibling_slug(path)
    if tool_name == "write_draft_page":
        slug = args.get("slug")
        if isinstance(slug, str) and slug:
            return slug
    return None


class SiblingDraftCheckMiddleware(AgentMiddleware):
    """Reject a near-duplicate sibling page write in the same batch.

    Looks at the in-batch set populated by this middleware on prior
    successful writes; rejects when token overlap exceeds the threshold;
    short-circuits via `force_sibling=True`.
    """

    # Minimum meaningful tokens in a slug for the check to even fire.
    # Slugs of 1-2 tokens are rare and likely intentional (`lens`, `mcat-tag`).
    _MIN_SLUG_TOKENS: int = 3
    # Absolute count threshold: ≥ this many shared tokens triggers reject.
    _OVERLAP_COUNT: int = 3
    # Relative threshold: shared / shorter ≥ this ratio also triggers.
    _OVERLAP_RATIO: float = 0.7

    @property
    def name(self) -> str:
        return "sibling_draft_check"

    def _maybe_reject(self, new_slug: str) -> tuple[str, set[str]] | None:
        """Return (existing_slug, overlap) when this write should be rejected.

        Conservative: skips the check entirely when the slug is too short,
        the ContextVar is unset (tests / outside a run), or no prior sibling
        has landed in this batch. Caller is responsible for resolving the
        target slug and short-circuiting on `None`.
        """
        new_tokens = _slug_tokens(new_slug)
        if len(new_tokens) < self._MIN_SLUG_TOKENS:
            return None

        # Import inside the function to avoid a circular import at module load.
        from src.compile.compiler import _current_batch_sibling_slugs_written

        prior_slugs = _current_batch_sibling_slugs_written.get()
        if not prior_slugs:
            return None

        for prev_slug in prior_slugs:
            if prev_slug == new_slug:
                # Same slug = a merge / re-write of own page; not a sibling dupe.
                return None
            prev_tokens = _slug_tokens(prev_slug)
            if len(prev_tokens) < self._MIN_SLUG_TOKENS:
                continue
            overlap = new_tokens & prev_tokens
            if not overlap:
                continue
            shorter = min(len(new_tokens), len(prev_tokens))
            triggers = len(overlap) >= self._OVERLAP_COUNT or (
                shorter > 0 and len(overlap) / shorter >= self._OVERLAP_RATIO
            )
            if triggers:
                return prev_slug, overlap
        return None

    def _rejection_payload(
        self, *, prev_slug: str, new_slug: str, overlap: set[str]
    ) -> dict[str, Any]:
        sorted_overlap = sorted(overlap)
        guidance = (
            f"You just wrote `{prev_slug}` in this batch. The new slug "
            f"`{new_slug}` shares {len(overlap)} tokens "
            f"({', '.join(sorted_overlap)}). Before writing a second page:\n"
            f'  1. Call `resolve_page("{new_slug}")` to see if this concept '
            f"already exists.\n"
            f"  2. If the concepts genuinely overlap, patch_page() into "
            f"`{prev_slug}` instead.\n"
            f"  3. If they're distinct topics that share words, proceed — "
            f"but make the slug more specific to differentiate (e.g., "
            f"`{new_slug}-for-<qualifier>`) and add a `related:` wikilink "
            f"to `{prev_slug}`.\n"
            f"To bypass this check, pass `force_sibling=True` in the tool args."
        )
        return {
            "ok": False,
            "reason": "sibling_draft_overlap",
            "previous_slug": prev_slug,
            "attempted_slug": new_slug,
            "overlap_tokens": sorted_overlap,
            "guidance": guidance,
        }

    @staticmethod
    def _should_record(result: ToolMessage | Command[Any]) -> bool:
        """True when the handler's result is a successful persisted write.

        Error results must NOT pollute the sibling-slug set: a downstream
        middleware (e.g. `EditPayloadSanityMiddleware`, `SameThreadTopicGuardMiddleware`)
        can reject a write after us, so nothing actually lands on disk.
        Recording anyway would block a legitimate retry with a spurious
        `sibling_draft_overlap`. Non-`ToolMessage` results (e.g. `Command`
        returned by a handler that rerouted) are treated conservatively:
        don't record, since we can't confirm the write succeeded.
        """
        if not isinstance(result, ToolMessage):
            return False
        return result.status != "error"

    def _record_write(self, slug: str | None, result: ToolMessage | Command[Any]) -> None:
        """After a successful write, register the slug for future comparisons.

        No-ops when:
          - the slug isn't sibling-tracked;
          - the ContextVar is unset (tests / outside a compile run);
          - the tool result indicates an error (see `_should_record`).
        """
        if slug is None:
            return
        if not self._should_record(result):
            return
        from src.compile.compiler import _current_batch_sibling_slugs_written

        slugs = _current_batch_sibling_slugs_written.get()
        if slugs is None:
            return
        slugs.add(slug)

    def _pre_handler(self, request: ToolCallRequest) -> tuple[str | None, ToolMessage | None]:
        """Resolve the target slug and any pre-handler rejection.

        Returns (slug_to_record, rejection_message). When the rejection is
        non-None, the caller should short-circuit and return it. When it's
        None, the caller proceeds to the real handler and then passes
        slug_to_record to `_record_write`.

        Bypass path: strips `force_sibling` from the request args (so the
        underlying tool doesn't see an unknown kwarg) and returns the slug
        without running the overlap check.
        """
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        slug = _target_slug(tool_name, args)
        if args.get("force_sibling"):
            logger.info("sibling_check_bypass", tool_name=tool_name, slug=slug)
            args.pop("force_sibling", None)
            return slug, None
        if slug is None:
            return None, None
        rejection = self._maybe_reject(slug)
        if rejection is None:
            return slug, None
        prev_slug, overlap = rejection
        logger.warning(
            "guard.sibling_draft",
            previous_slug=prev_slug,
            attempted_slug=slug,
            overlap=sorted(overlap),
        )
        payload = self._rejection_payload(prev_slug=prev_slug, new_slug=slug, overlap=overlap)
        return None, ToolMessage(
            content=json.dumps(payload),
            status="error",
            tool_call_id=request.tool_call.get("id") or "",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        slug, rejection = self._pre_handler(request)
        if rejection is not None:
            return rejection
        result = handler(request)
        self._record_write(slug, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        slug, rejection = self._pre_handler(request)
        if rejection is not None:
            return rejection
        result = await handler(request)
        self._record_write(slug, result)
        return result
