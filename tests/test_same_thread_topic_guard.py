"""Tests for `SameThreadTopicGuardMiddleware`.

Seeds a `wiki_pages` + `message_touched_pages` row linked to a
specific `messages.thread_id`, sets `_current_batch_thread_id` to that
thread, and drives the middleware directly. No live model, no
LangGraph runner.

Scenarios covered:
  - Duplicate topic write (same thread, new slug) → rejection.
  - System/policy/decision path same scenario → pass through.
  - Existing-slug write (edit path) → pass through.
  - Thread in scope but no existing topic → pass through (first topic).
  - ContextVar unset → pass through.
  - Async path parity.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.compile.compiler import _current_batch_thread_id
from src.compile.compiler import _current_batch_topic_slugs_written
from src.compile.middleware.same_thread_topic_guard import SameThreadTopicGuardMiddleware
from src.compile.middleware.same_thread_topic_guard import _extract_topic_slug

_THREAD_ID = "T-thread-abc"
_EXISTING_SLUG = "seller-bl-api-optimization"
_EXISTING_PAGE_PATH = f"wiki/topics/{_EXISTING_SLUG}.md"
_NEW_SLUG = "seller-bl-user-details-verification-api-optimization"


# ---------------------------------------------------------------------------
# Request / handler helpers
# ---------------------------------------------------------------------------


def _make_request(
    name: str,
    args: dict[str, Any] | None = None,
    tool_call_id: str = "call_1",
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": args or {},
            "id": tool_call_id,
            "type": "tool_call",
        },
        tool=MagicMock(name=name),
        state={"messages": []},
        runtime=MagicMock(),
    )


def _success_handler(name: str = "write_file") -> Callable[[ToolCallRequest], ToolMessage]:
    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content='{"ok": true}',
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    return handler


# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------


def _seed_existing_topic(db_conn: psycopg.Connection) -> None:
    """Seed: one message in _THREAD_ID, linked to an active topic page."""
    db_conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, compile_state)
        VALUES (%s, %s, %s, 'compiled')
        """,
        ("msg-1", "raw/2026-01-09_seller-bl_abc.md", _THREAD_ID),
    )
    db_conn.execute(
        """
        INSERT INTO wiki_pages (slug, path, title, page_type, status)
        VALUES (%s, %s, %s, 'topic', 'active')
        """,
        (_EXISTING_SLUG, _EXISTING_PAGE_PATH, "Seller BL API Optimization"),
    )
    db_conn.execute(
        """
        INSERT INTO message_touched_pages (message_id, page_id)
        SELECT 'msg-1', page_id FROM wiki_pages WHERE slug = %s
        """,
        (_EXISTING_SLUG,),
    )
    db_conn.commit()


# ---------------------------------------------------------------------------
# _extract_topic_slug unit tests (pure string parsing; no DB)
# ---------------------------------------------------------------------------


class TestExtractTopicSlug:
    def test_virtual_path(self) -> None:
        assert _extract_topic_slug("/wiki/topics/foo.md") == "foo"

    def test_unrooted_path(self) -> None:
        assert _extract_topic_slug("wiki/topics/bar-baz.md") == "bar-baz"

    def test_not_topic_path(self) -> None:
        assert _extract_topic_slug("/wiki/systems/foo.md") is None
        assert _extract_topic_slug("/wiki/policies/foo.md") is None
        assert _extract_topic_slug("/wiki/entities/alice.md") is None

    def test_not_md(self) -> None:
        assert _extract_topic_slug("/wiki/topics/foo.txt") is None

    def test_nested(self) -> None:
        # Slug cannot contain a slash — nested paths would be a subdirectory
        # convention we don't use.
        assert _extract_topic_slug("/wiki/topics/subdir/foo.md") is None


# ---------------------------------------------------------------------------
# Guard middleware — sync path
# ---------------------------------------------------------------------------


def test_duplicate_topic_is_rejected(db_conn: psycopg.Connection) -> None:
    """New topic write on a thread that already has an active topic → rejection."""
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": f"/wiki/topics/{_NEW_SLUG}.md", "content": "stub"},
        )
        handler = MagicMock(side_effect=AssertionError("handler must not run"))
        result = mw.wrap_tool_call(request, handler)
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["reason"] == "same_thread_duplicate_topic"
    assert payload["existing_slug"] == _EXISTING_SLUG
    assert payload["attempted_slug"] == _NEW_SLUG
    assert payload["thread_id"] == _THREAD_ID
    assert _EXISTING_SLUG in payload["guidance"]
    handler.assert_not_called()


def test_system_path_passes_through(db_conn: psycopg.Connection) -> None:
    """Topic + system for one thread is valid per prompt — system write is allowed."""
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": "/wiki/systems/user-details-api.md", "content": "stub"},
        )
        result = mw.wrap_tool_call(request, _success_handler())
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_policy_and_decision_paths_pass_through(db_conn: psycopg.Connection) -> None:
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        for path in ("/wiki/policies/new.md", "/wiki/decisions/new.md"):
            request = _make_request("write_file", {"file_path": path})
            result = mw.wrap_tool_call(request, _success_handler())
            assert isinstance(result, ToolMessage)
            assert result.status != "error", f"{path} unexpectedly blocked"
    finally:
        _current_batch_thread_id.reset(token)


def test_existing_topic_slug_passes_through(db_conn: psycopg.Connection) -> None:
    """Write to the existing topic's own slug is a merge — allowed."""
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": f"/wiki/topics/{_EXISTING_SLUG}.md", "content": "merged"},
        )
        result = mw.wrap_tool_call(request, _success_handler())
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_first_topic_on_thread_passes_through(db_conn: psycopg.Connection) -> None:
    """Thread with no active topic yet → write a new one freely."""
    # Seed only the message, no wiki_pages row yet.
    db_conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, compile_state)
        VALUES ('msg-1', 'raw/2026-01-09_seed_abc.md', %s, 'pending')
        """,
        (_THREAD_ID,),
    )
    db_conn.commit()

    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/first-one.md", "content": "new page"},
        )
        result = mw.wrap_tool_call(request, _success_handler())
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_context_var_unset_passes_through(db_conn: psycopg.Connection) -> None:
    """No active batch (tests, outside run_compilation) → guard inert."""
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    # Explicitly None (the default).
    token = _current_batch_thread_id.set(None)
    try:
        request = _make_request(
            "write_file",
            {"file_path": f"/wiki/topics/{_NEW_SLUG}.md", "content": "stub"},
        )
        result = mw.wrap_tool_call(request, _success_handler())
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_superseded_topic_does_not_block(db_conn: psycopg.Connection) -> None:
    """A superseded topic is no longer the live page — the thread can get a fresh topic."""
    db_conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, compile_state)
        VALUES ('msg-1', 'raw/2026-01-09_x_abc.md', %s, 'compiled')
        """,
        (_THREAD_ID,),
    )
    db_conn.execute(
        """
        INSERT INTO wiki_pages (slug, path, title, page_type, status)
        VALUES ('old-topic', 'wiki/topics/old-topic.md',
                'Old Topic', 'topic', 'superseded')
        """,
    )
    db_conn.execute(
        """
        INSERT INTO message_touched_pages (message_id, page_id)
        SELECT 'msg-1', page_id FROM wiki_pages WHERE slug = 'old-topic'
        """,
    )
    db_conn.commit()

    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/new-topic.md"},
        )
        result = mw.wrap_tool_call(request, _success_handler())
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_edit_file_passes_through(db_conn: psycopg.Connection) -> None:
    """edit_file is an existing-page edit — guard only wraps write_file."""
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "edit_file",
            {"file_path": f"/wiki/topics/{_NEW_SLUG}.md", "content": "x"},
        )
        result = mw.wrap_tool_call(request, _success_handler("edit_file"))
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_patch_page_passes_through(db_conn: psycopg.Connection) -> None:
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request("patch_page", {"slug": _NEW_SLUG, "section": "Facts"})
        result = mw.wrap_tool_call(request, _success_handler("patch_page"))
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_duplicate_topic_via_path_arg_alias_is_rejected(db_conn: psycopg.Connection) -> None:
    """Claude P1 on PR #171: `_maybe_reject` accepts both `file_path`
    and `path` keys. The rejection must fire either way — the agent
    could call the tool with the `path` alias."""
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"path": f"/wiki/topics/{_NEW_SLUG}.md", "content": "stub"},
        )
        handler = MagicMock(side_effect=AssertionError("handler must not run"))
        result = mw.wrap_tool_call(request, handler)
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["reason"] == "same_thread_duplicate_topic"
    assert payload["attempted_slug"] == _NEW_SLUG
    handler.assert_not_called()


def test_in_run_duplicate_topic_rejected_before_catalog_sync(
    db_conn: psycopg.Connection,
) -> None:
    """Codex P1 on PR #171: the catalog (`message_touched_pages`) is
    populated by the coordinator AFTER `run_compilation` returns. So if
    the agent writes topic A then topic B in the same batch, the catalog
    check can't see A yet. The in-run ContextVar set covers this case —
    the second call must still be rejected."""
    # Seed only the message row (thread in scope), NO wiki_pages yet.
    db_conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, compile_state)
        VALUES ('msg-1', 'raw/2026-01-09_seed_abc.md', %s, 'pending')
        """,
        (_THREAD_ID,),
    )
    db_conn.commit()

    mw = SameThreadTopicGuardMiddleware()
    thread_token = _current_batch_thread_id.set(_THREAD_ID)
    slugs_token = _current_batch_topic_slugs_written.set(set())
    try:
        # First topic write — succeeds + records slug in the in-run set.
        first = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/first-topic.md", "content": "stub"},
        )
        result_first = mw.wrap_tool_call(first, _success_handler())
        assert isinstance(result_first, ToolMessage)
        assert result_first.status != "error"
        assert _current_batch_topic_slugs_written.get() == {"first-topic"}

        # Second topic write — catalog is still empty (coordinator
        # hasn't synced yet), but the in-run set knows. Must reject.
        second = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/second-topic.md", "content": "stub"},
        )
        handler = MagicMock(side_effect=AssertionError("handler must not run"))
        result_second = mw.wrap_tool_call(second, handler)
    finally:
        _current_batch_topic_slugs_written.reset(slugs_token)
        _current_batch_thread_id.reset(thread_token)

    assert isinstance(result_second, ToolMessage)
    assert result_second.status == "error"
    payload = json.loads(str(result_second.content))
    assert payload["reason"] == "same_thread_duplicate_topic"
    assert payload["existing_slug"] == "first-topic"
    assert payload["attempted_slug"] == "second-topic"
    handler.assert_not_called()


# ---------------------------------------------------------------------------
# Async path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awrap_rejects_duplicate_topic(db_conn: psycopg.Connection) -> None:
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": f"/wiki/topics/{_NEW_SLUG}.md", "content": "stub"},
        )

        async def handler(_req: ToolCallRequest) -> ToolMessage:
            raise AssertionError("handler must not run")

        result = await mw.awrap_tool_call(request, handler)
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["reason"] == "same_thread_duplicate_topic"


@pytest.mark.asyncio
async def test_awrap_passes_through_existing_slug(db_conn: psycopg.Connection) -> None:
    _seed_existing_topic(db_conn)
    mw = SameThreadTopicGuardMiddleware()
    token = _current_batch_thread_id.set(_THREAD_ID)
    try:
        request = _make_request(
            "write_file",
            {"file_path": f"/wiki/topics/{_EXISTING_SLUG}.md", "content": "merged"},
        )

        async def handler(req: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content='{"ok": true}',
                tool_call_id=req.tool_call["id"],
                name="write_file",
            )

        result = await mw.awrap_tool_call(request, handler)
    finally:
        _current_batch_thread_id.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
