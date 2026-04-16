"""Unit tests for src/compile/middleware/legacy_page_hint.py.

Covers the pure frontmatter/path helpers and the middleware's
hint-once-per-run behaviour. Async path is smoke-tested so both
`wrap_tool_call` and `awrap_tool_call` stay in sync.
"""

from __future__ import annotations

from typing import cast

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.compile.middleware.legacy_page_hint import LegacyPageHintMiddleware
from src.compile.middleware.legacy_page_hint import _detect_legacy_reasons
from src.compile.middleware.legacy_page_hint import _is_wiki_page_path
from src.compile.middleware.legacy_page_hint import _strip_line_numbers

# ---------------------------------------------------------------------------
# _strip_line_numbers — resilient to formatted + raw input
# ---------------------------------------------------------------------------


def test_strip_line_numbers_removes_prefix() -> None:
    formatted = "     1\t---\n     2\ttitle: Foo\n     3\t---"
    assert _strip_line_numbers(formatted) == "---\ntitle: Foo\n---"


def test_strip_line_numbers_passes_through_unprefixed() -> None:
    raw = "---\ntitle: Foo\n---"
    assert _strip_line_numbers(raw) == raw


def test_strip_line_numbers_preserves_tab_in_body() -> None:
    # A body line containing a tab should not have its tab-bearing
    # portion nuked — only the numeric prefix counts.
    formatted = "     1\tcol1\tcol2\n     2\tb"
    assert _strip_line_numbers(formatted) == "col1\tcol2\nb"


# ---------------------------------------------------------------------------
# _is_wiki_page_path
# ---------------------------------------------------------------------------


def test_wiki_page_path_accepts_virtual_leading_slash() -> None:
    assert _is_wiki_page_path("/wiki/entities/amit.md")
    assert _is_wiki_page_path("/wiki/topics/foo.md")


def test_wiki_page_path_accepts_relative_form() -> None:
    assert _is_wiki_page_path("wiki/entities/amit.md")


def test_wiki_page_path_rejects_raw() -> None:
    assert not _is_wiki_page_path("/raw/2026-04-16_subject.md")
    assert not _is_wiki_page_path("raw/foo.md")


def test_wiki_page_path_rejects_non_md() -> None:
    assert not _is_wiki_page_path("/wiki/stylesheets/extra.css")
    assert not _is_wiki_page_path("/wiki/entities/README.txt")


# ---------------------------------------------------------------------------
# _detect_legacy_reasons
# ---------------------------------------------------------------------------


def test_detect_status_current_flagged() -> None:
    reasons = _detect_legacy_reasons({"status": "current", "page_type": "topic"})
    assert any("status:current" in r for r in reasons)


def test_detect_status_contested_flagged() -> None:
    reasons = _detect_legacy_reasons(
        {"status": "contested", "page_type": "topic", "domain": "seller"}
    )
    assert any("status:contested" in r for r in reasons)


def test_detect_page_type_entity_flagged() -> None:
    reasons = _detect_legacy_reasons({"status": "active", "page_type": "entity"})
    assert any("entity" in r for r in reasons)


def test_detect_missing_domain_on_topic_flagged() -> None:
    reasons = _detect_legacy_reasons({"status": "active", "page_type": "topic"})
    assert any("domain" in r for r in reasons)


def test_detect_missing_domain_on_system_flagged() -> None:
    reasons = _detect_legacy_reasons({"status": "active", "page_type": "system"})
    assert any("domain" in r for r in reasons)


def test_detect_domain_present_clean() -> None:
    reasons = _detect_legacy_reasons({"status": "active", "page_type": "topic", "domain": "seller"})
    assert reasons == []


def test_detect_policy_without_domain_clean() -> None:
    # Policy pages don't require `domain:`.
    reasons = _detect_legacy_reasons({"status": "active", "page_type": "policy"})
    assert reasons == []


def test_detect_superseded_not_flagged() -> None:
    # `superseded` is legitimate lineage, not legacy debt.
    reasons = _detect_legacy_reasons(
        {"status": "superseded", "page_type": "topic", "domain": "seller"}
    )
    assert reasons == []


# ---------------------------------------------------------------------------
# Middleware — end-to-end via wrap_tool_call
# ---------------------------------------------------------------------------


def _read_request(path: str, tool_call_id: str = "r1") -> ToolCallRequest:
    """Build a minimal `read_file` ToolCallRequest."""
    return cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "read_file",
                "args": {"file_path": path},
                "id": tool_call_id,
                "type": "tool_call",
            },
            tool=None,
            state={"messages": []},
            runtime=None,  # type: ignore[arg-type]
        ),
    )


def _read_handler(content: str, *, status: str = "success") -> object:
    """Return a handler that yields a ToolMessage with `content` verbatim."""

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call["id"],
            name="read_file",
            status=status,
        )

    return handler


# The typical deepagents read_file output: space-padded line numbers,
# tab, then line content.
_ENTITY_PAGE_CONTENT = (
    "     1\t---\n"
    "     2\ttitle: Amit\n"
    "     3\tpage_type: entity\n"
    "     4\tstatus: current\n"
    "     5\t---\n"
    "     6\t\n"
    "     7\tBody.\n"
)

_CLEAN_TOPIC_CONTENT = (
    "     1\t---\n"
    "     2\ttitle: WhatsApp Rollout\n"
    "     3\tpage_type: topic\n"
    "     4\tstatus: active\n"
    "     5\tdomain: seller\n"
    "     6\t---\n"
    "     7\t\n"
    "     8\tBody.\n"
)


def test_middleware_hints_on_entity_page() -> None:
    mw = LegacyPageHintMiddleware()
    request = _read_request("/wiki/entities/amit.md")
    result = mw.wrap_tool_call(request, _read_handler(_ENTITY_PAGE_CONTENT))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    assert "legacy-debt hint" in str(result.content)
    assert result.additional_kwargs.get("legacy_page_hinted") is True
    reasons = result.additional_kwargs.get("legacy_page_reasons") or []
    assert any("status:current" in r for r in reasons)
    assert any("entity" in r for r in reasons)


def test_middleware_hints_once_per_path_per_run() -> None:
    mw = LegacyPageHintMiddleware()
    request1 = _read_request("/wiki/entities/amit.md", tool_call_id="r1")
    request2 = _read_request("/wiki/entities/amit.md", tool_call_id="r2")

    first = mw.wrap_tool_call(request1, _read_handler(_ENTITY_PAGE_CONTENT))  # type: ignore[arg-type]
    second = mw.wrap_tool_call(request2, _read_handler(_ENTITY_PAGE_CONTENT))  # type: ignore[arg-type]

    assert isinstance(first, ToolMessage)
    assert isinstance(second, ToolMessage)
    assert first.additional_kwargs.get("legacy_page_hinted") is True
    # Second read of the same path — no hint.
    assert "legacy_page_hinted" not in second.additional_kwargs
    assert "legacy-debt hint" not in str(second.content)


def test_middleware_does_not_hint_on_clean_topic() -> None:
    mw = LegacyPageHintMiddleware()
    request = _read_request("/wiki/topics/whatsapp-rollout.md")
    result = mw.wrap_tool_call(request, _read_handler(_CLEAN_TOPIC_CONTENT))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    assert "legacy-debt hint" not in str(result.content)
    assert "legacy_page_hinted" not in result.additional_kwargs


def test_middleware_does_not_hint_on_raw_read() -> None:
    mw = LegacyPageHintMiddleware()
    request = _read_request("/raw/2026-04-16_subject_abc.md")
    result = mw.wrap_tool_call(request, _read_handler(_ENTITY_PAGE_CONTENT))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    # Even though the content looks like an entity page, path is /raw/.
    assert "legacy-debt hint" not in str(result.content)
    assert "legacy_page_hinted" not in result.additional_kwargs


def test_middleware_does_not_hint_on_error_status() -> None:
    mw = LegacyPageHintMiddleware()
    request = _read_request("/wiki/entities/amit.md")
    result = mw.wrap_tool_call(
        request,
        _read_handler("Error: not found", status="error"),  # type: ignore[arg-type]
    )
    assert isinstance(result, ToolMessage)
    assert "legacy-debt hint" not in str(result.content)


def test_middleware_ignores_non_read_tools() -> None:
    mw = LegacyPageHintMiddleware()
    request = cast(
        ToolCallRequest,
        ToolCallRequest(
            tool_call={
                "name": "write_file",
                "args": {"file_path": "/wiki/entities/amit.md"},
                "id": "w1",
                "type": "tool_call",
            },
            tool=None,
            state={"messages": []},
            runtime=None,  # type: ignore[arg-type]
        ),
    )
    result = mw.wrap_tool_call(request, _read_handler("ok"))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    assert "legacy_page_hinted" not in result.additional_kwargs


def test_middleware_hints_topic_missing_domain() -> None:
    mw = LegacyPageHintMiddleware()
    # Topic page with active status BUT no `domain:` — Tier A signal.
    content = (
        "     1\t---\n"
        "     2\ttitle: Foo\n"
        "     3\tpage_type: topic\n"
        "     4\tstatus: active\n"
        "     5\t---\n"
        "     6\tBody.\n"
    )
    request = _read_request("/wiki/topics/foo.md")
    result = mw.wrap_tool_call(request, _read_handler(content))  # type: ignore[arg-type]
    assert isinstance(result, ToolMessage)
    assert "legacy-debt hint" in str(result.content)
    reasons = result.additional_kwargs.get("legacy_page_reasons") or []
    assert any("domain" in r for r in reasons)


@pytest.mark.asyncio
async def test_middleware_async_path() -> None:
    mw = LegacyPageHintMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=_ENTITY_PAGE_CONTENT,
            tool_call_id=request.tool_call["id"],
            name="read_file",
            status="success",
        )

    request = _read_request("/wiki/entities/amit.md")
    result = await mw.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.additional_kwargs.get("legacy_page_hinted") is True
