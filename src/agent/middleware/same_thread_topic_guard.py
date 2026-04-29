"""same_thread_topic_guard middleware — block same-thread duplicate topic pages.

Why this exists: Codex 2026-04-17 audit flagged Seller BL thread
`19bb72fc748876c2` as producing TWO topic pages (`seller-bl-api-
optimization.md` + `seller-bl-user-details-verification-api-optimization.md`)
for a single concept stream. Per the prompt, a thread producing a topic
+ a system page is valid (systems describe durable nouns, topics
describe changes on them — see `src/agent/prompts.py` lines 37 +
164). Only two *topic* pages for one thread is the bug.

This middleware intercepts `write_file` on `/wiki/topics/<slug>.md`
when the slug does NOT already exist and the thread in scope has
already touched another topic page. It rejects with a structured
error naming the existing topic so the agent can redirect to
`edit_file` / `patch_page`.

Catalog-truth: we query `message_touched_pages JOIN messages JOIN
wiki_pages` on `thread_id`, NOT frontmatter `source_threads:`. Only
status in ('active', 'current') pages count — superseded/archived
pages are legitimately "the old topic" and shouldn't block a fresh
one.

Explicitly NOT blocked:
- `write_file` to an existing topic slug (merge into own page — expected).
- `write_file` to `/wiki/systems/...`, `/wiki/policies/...`,
  `/wiki/decisions/...` (valid per prompt — topic + system is fine).
- `patch_page` / `edit_file` anywhere (those are edits on existing pages).
- When `_current_batch_thread_id` is None (outside compile run, tests).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from typing import Any

import psycopg
import structlog
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.types import Command

logger = structlog.get_logger(__name__)


# Match virtual ("/wiki/topics/slug.md") and unrooted ("wiki/topics/slug.md")
# topic paths. Capturing group yields the slug stem.
_TOPIC_PATH_RE = re.compile(r"^/?wiki/topics/([^/]+)\.md$")


def _extract_topic_slug(path: str) -> str | None:
    """Return the topic slug if `path` points at /wiki/topics/<slug>.md; else None."""
    m = _TOPIC_PATH_RE.match(path.strip())
    if not m:
        return None
    return m.group(1)


def _topic_slug_exists(slug: str) -> bool:
    """True when `wiki_pages` already has a row for this slug.

    We don't filter on page_type here — if the slug is taken at all,
    `write_file` is a merge/overwrite, not a new-topic-creation, and
    the guard should pass through.
    """
    from src.db.wiki_pages import find_by_slug

    return find_by_slug(slug) is not None


def _existing_topic_slug_for_thread(thread_id: str) -> str | None:
    """Return the slug of an active topic already linked to this thread, or None.

    Thin wrapper around `db.wiki_pages.find_active_topic_for_thread` —
    kept at module scope so the middleware can patch it in tests without
    reaching into the db layer, and so the short-circuit logic inside
    `_maybe_reject` reads cleanly.
    """
    from src.db.wiki_pages import find_active_topic_for_thread

    return find_active_topic_for_thread(thread_id)


def _rejection_payload(*, existing_slug: str, new_slug: str, thread_id: str) -> dict[str, Any]:
    """Build the structured rejection body returned as ToolMessage content."""
    return {
        "ok": False,
        "reason": "same_thread_duplicate_topic",
        "existing_slug": existing_slug,
        "attempted_slug": new_slug,
        "thread_id": thread_id,
        "guidance": (
            f"Rejected: thread {thread_id} already has an active topic page "
            f"`{existing_slug}`. One thread, one topic page — merge the new "
            f"evidence there instead of creating `{new_slug}`. Use "
            f"`edit_file` or `patch_page` on `wiki/topics/{existing_slug}.md`. "
            f"If this truly is a separate concept (e.g. a system page), "
            f"write it under `/wiki/systems/` instead."
        ),
    }


def _topic_write_slug(tool_name: str, args: dict[str, Any]) -> str | None:
    """Return the topic slug targeted by this tool call, or None.

    A tool call counts as a topic-write when it's `write_file` with a
    string `file_path` (or `path`) resolving to /wiki/topics/<slug>.md.
    """
    if tool_name != "write_file":
        return None
    path = args.get("file_path") or args.get("path")
    if not isinstance(path, str):
        return None
    return _extract_topic_slug(path)


def _maybe_reject(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Return a rejection payload when this call is a duplicate topic write; else None.

    Short-circuits on any condition that makes the guard inapplicable:
      - tool is not write_file,
      - path is not a topic path,
      - no active thread_id in scope (outside compile run / tests),
      - slug already exists (merging into own page is fine),
      - no other topic linked to this thread (first topic is always allowed).
    """
    new_slug = _topic_write_slug(tool_name, args)
    if new_slug is None:
        return None

    # Import inside the function to avoid a circular import at module load.
    from src.agent.run_state import _current_batch_thread_id
    from src.agent.run_state import _current_batch_topic_slugs_written

    thread_id = _current_batch_thread_id.get()
    if not thread_id:
        return None

    # In-run check first (cheap + catches the case where the coordinator
    # hasn't yet synced the catalog for an earlier same-batch topic
    # write). Codex P1 on PR #171.
    in_run_slugs = _current_batch_topic_slugs_written.get() or set()
    in_run_existing = next((s for s in in_run_slugs if s != new_slug), None)
    if in_run_existing is not None:
        return _rejection_payload(
            existing_slug=in_run_existing, new_slug=new_slug, thread_id=thread_id
        )

    # DB calls can fail (transient network, schema drift). A guard that
    # crashes the whole tool call is worse than a guard that silently
    # passes through — we log and fall through. Narrow psycopg.Error so
    # we still surface programmer bugs (TypeError, etc.).
    try:
        if _topic_slug_exists(new_slug):
            return None
        existing = _existing_topic_slug_for_thread(thread_id)
    except psycopg.Error as exc:
        logger.warning(
            "same_thread_topic_guard_db_error",
            error=str(exc),
            thread_id=thread_id,
            new_slug=new_slug,
        )
        return None

    if existing is None or existing == new_slug:
        return None

    return _rejection_payload(existing_slug=existing, new_slug=new_slug, thread_id=thread_id)


def _record_in_run_topic_write(new_slug: str) -> None:
    """Record a successful topic write so later calls can detect duplicates.

    The ContextVar holds a mutable set populated fresh at `run_compilation`
    entry. Mutation is in-place; if the var is None (tests / outside a
    run), we silently no-op.
    """
    from src.agent.run_state import _current_batch_topic_slugs_written

    slugs = _current_batch_topic_slugs_written.get()
    if slugs is None:
        return
    slugs.add(new_slug)


def _rejection_message(tool_call_id: str, payload: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        status="error",
        tool_call_id=tool_call_id,
    )


class SameThreadTopicGuardMiddleware(AgentMiddleware):
    """Reject a second /wiki/topics/ write on the same batch thread."""

    @property
    def name(self) -> str:
        return "same_thread_topic_guard"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejection = _maybe_reject(tool_name, args)
        if rejection is not None:
            logger.warning(
                "guard.same_thread_topic",
                existing_slug=rejection["existing_slug"],
                new_slug=rejection["attempted_slug"],
                thread_id=rejection["thread_id"],
            )
            tool_call_id = request.tool_call.get("id") or ""
            return _rejection_message(tool_call_id, rejection)
        result = handler(request)
        new_slug = _topic_write_slug(tool_name, args)
        if new_slug is not None:
            _record_in_run_topic_write(new_slug)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        rejection = _maybe_reject(tool_name, args)
        if rejection is not None:
            logger.warning(
                "guard.same_thread_topic",
                existing_slug=rejection["existing_slug"],
                new_slug=rejection["attempted_slug"],
                thread_id=rejection["thread_id"],
            )
            tool_call_id = request.tool_call.get("id") or ""
            return _rejection_message(tool_call_id, rejection)
        result = await handler(request)
        new_slug = _topic_write_slug(tool_name, args)
        if new_slug is not None:
            _record_in_run_topic_write(new_slug)
        return result
