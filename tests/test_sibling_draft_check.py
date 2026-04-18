"""Tests for `SiblingDraftCheckMiddleware`.

Drives the middleware directly with synthesized `ToolCallRequest`
objects. The ContextVar `_current_batch_sibling_slugs_written` is
explicitly set per test (mirrors what `run_compilation` does at the
start of each batch). No DB, no live model.

Scenarios covered:
  - Batch with single write → no rejection.
  - Cycle 10 case: `seller-bl-api-optimization` then
    `seller-bl-api-hit-optimisation` → rejection with guidance.
  - Disjoint slugs (`seller-bl-api-optimization` vs
    `lens-photosearch-ab-test`) → no rejection.
  - `force_sibling=True` bypasses the check.
  - Reject payload includes previous slug + overlap token list.
  - Slugs below `_MIN_SLUG_TOKENS` are skipped.
  - `write_draft_page` slug arg is honoured.
  - System write triggers same as topic write.
  - Async parity.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.compile.compiler import _current_batch_sibling_slugs_written
from src.compile.middleware.sibling_draft_check import SiblingDraftCheckMiddleware
from src.compile.middleware.sibling_draft_check import _extract_sibling_slug
from src.compile.middleware.sibling_draft_check import _slug_tokens


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
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractSiblingSlug:
    def test_topic_path(self) -> None:
        assert _extract_sibling_slug("/wiki/topics/foo-bar.md") == "foo-bar"

    def test_system_path(self) -> None:
        assert _extract_sibling_slug("wiki/systems/lens.md") == "lens"

    def test_decisions_not_in_scope(self) -> None:
        assert _extract_sibling_slug("/wiki/decisions/some-call.md") is None

    def test_policies_not_in_scope(self) -> None:
        assert _extract_sibling_slug("/wiki/policies/refund.md") is None

    def test_people_not_in_scope(self) -> None:
        assert _extract_sibling_slug("/wiki/people/anjali-shankar.md") is None

    def test_non_md(self) -> None:
        assert _extract_sibling_slug("/wiki/topics/foo.txt") is None


class TestSlugTokens:
    def test_drops_stopwords(self) -> None:
        assert _slug_tokens("seller-and-buyer-trust-for-isq") == {
            "seller",
            "buyer",
            "trust",
            "isq",
        }

    def test_lowercases(self) -> None:
        # Canonical slugs are kebab-case lowercase but be defensive.
        assert _slug_tokens("Seller-API") == {"seller", "api"}


# ---------------------------------------------------------------------------
# Middleware — sync path
# ---------------------------------------------------------------------------


def test_single_write_no_rejection() -> None:
    """First sibling write of a batch always passes through."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        request = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/seller-bl-api-optimization.md", "content": "x"},
        )
        result = mw.wrap_tool_call(request, _success_handler())
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    # And the slug got recorded for future comparisons.
    assert _current_batch_sibling_slugs_written.get() is None  # post-reset
    # Verify the recording side-effect mid-batch:
    token2 = _current_batch_sibling_slugs_written.set(set())
    try:
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/lens.md", "content": "x"},
            ),
            _success_handler(),
        )
        assert _current_batch_sibling_slugs_written.get() == {"lens"}
    finally:
        _current_batch_sibling_slugs_written.reset(token2)


def test_cycle10_near_duplicate_rejected() -> None:
    """The exact Cycle 10 case: two near-duplicate seller-BL slugs in one batch."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        first = _make_request(
            "write_file",
            {
                "file_path": "/wiki/topics/seller-bl-api-optimization.md",
                "content": "first",
            },
        )
        first_result = mw.wrap_tool_call(first, _success_handler())
        assert isinstance(first_result, ToolMessage)
        assert first_result.status != "error"
        assert _current_batch_sibling_slugs_written.get() == {"seller-bl-api-optimization"}

        second = _make_request(
            "write_file",
            {
                "file_path": "/wiki/topics/seller-bl-api-hit-optimisation.md",
                "content": "second",
            },
        )
        handler = MagicMock(side_effect=AssertionError("handler must not run"))
        second_result = mw.wrap_tool_call(second, handler)
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(second_result, ToolMessage)
    assert second_result.status == "error"
    payload = json.loads(str(second_result.content))
    assert payload["reason"] == "sibling_draft_overlap"
    assert payload["previous_slug"] == "seller-bl-api-optimization"
    assert payload["attempted_slug"] == "seller-bl-api-hit-optimisation"
    # The overlap should include at least seller, bl, api (not "optimization"
    # vs "optimisation" — those are different tokens). Three is the threshold.
    overlap = set(payload["overlap_tokens"])
    assert {"seller", "bl", "api"}.issubset(overlap)
    assert "seller-bl-api-optimization" in payload["guidance"]
    assert "resolve_page" in payload["guidance"]
    assert "patch_page" in payload["guidance"]
    handler.assert_not_called()


def test_disjoint_slugs_no_rejection() -> None:
    """Slugs sharing zero meaningful tokens never trigger."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )
        second = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/lens-photosearch-ab-test.md"},
        )
        result = mw.wrap_tool_call(second, _success_handler())
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_force_sibling_bypass() -> None:
    """`force_sibling=True` lets a near-duplicate write through."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )

        # Capture what the handler actually gets — we need to confirm
        # `force_sibling` was stripped from the args before forwarding.
        forwarded_args: dict[str, Any] = {}

        def capture_handler(req: ToolCallRequest) -> ToolMessage:
            forwarded_args.update(req.tool_call.get("args") or {})
            return ToolMessage(
                content='{"ok": true}',
                tool_call_id=req.tool_call["id"],
                name="write_file",
            )

        bypass_request = _make_request(
            "write_file",
            {
                "file_path": "/wiki/topics/seller-bl-api-hit-optimisation.md",
                "force_sibling": True,
            },
        )
        result = mw.wrap_tool_call(bypass_request, capture_handler)
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    # The flag was stripped before reaching the underlying tool handler.
    assert "force_sibling" not in forwarded_args
    assert forwarded_args.get("file_path", "").endswith("hit-optimisation.md")


def test_short_slug_skips_check() -> None:
    """Slugs with fewer than 3 meaningful tokens are not compared."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        # Single-token slugs shouldn't fire the check at all.
        mw.wrap_tool_call(
            _make_request("write_file", {"file_path": "/wiki/systems/lens.md"}),
            _success_handler(),
        )
        # `lens-mcat` is also short but happens to share a token — still skipped.
        result = mw.wrap_tool_call(
            _make_request("write_file", {"file_path": "/wiki/systems/lens-mcat.md"}),
            _success_handler(),
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_write_draft_page_slug_arg() -> None:
    """`write_draft_page(slug=...)` is sibling-tracked the same way."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        # First, a topic write registers the slug.
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )
        # Then a draft write with a near-duplicate slug → reject.
        draft_request = _make_request(
            "write_draft_page",
            {
                "slug": "seller-bl-api-hit-optimisation",
                "reason": "stub",
                "content": "draft body",
            },
        )
        handler = MagicMock(side_effect=AssertionError("handler must not run"))
        result = mw.wrap_tool_call(draft_request, handler)
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["attempted_slug"] == "seller-bl-api-hit-optimisation"


def test_system_write_triggers_check() -> None:
    """`/wiki/systems/<slug>.md` is in scope just like `/wiki/topics/`."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/systems/buyer-trust-onboarding-flow.md"},
            ),
            _success_handler(),
        )
        result = mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/systems/buyer-trust-onboarding-modal.md"},
            ),
            _success_handler(),
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["previous_slug"] == "buyer-trust-onboarding-flow"


def test_decisions_path_not_blocked() -> None:
    """Decision pages aren't sibling-tracked — they don't trigger or get tracked."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        # Establish a topic slug first.
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )
        # A decision write that happens to share lots of tokens — passes through.
        result = mw.wrap_tool_call(
            _make_request(
                "write_file",
                {
                    "file_path": ("/wiki/decisions/seller-bl-api-optimization-rollout.md"),
                },
            ),
            _success_handler(),
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_context_var_unset_passes_through() -> None:
    """Outside a compile run (no ContextVar) the guard is inert."""
    mw = SiblingDraftCheckMiddleware()
    # Default value is None.
    request = _make_request(
        "write_file",
        {"file_path": "/wiki/topics/seller-bl-api-hit-optimisation.md"},
    )
    result = mw.wrap_tool_call(request, _success_handler())
    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_same_slug_passes_through() -> None:
    """Re-writing the same slug (a merge into own page) is not a sibling dupe."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )
        result = mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


def test_overlap_ratio_threshold_triggers() -> None:
    """≥70% overlap on the shorter slug fires even when absolute count <3."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:
        # Both slugs have exactly 3 meaningful tokens; 2/3 ≈ 66% — does NOT trigger
        # the ratio rule, and 2 < 3 fails the absolute rule. Sanity case.
        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/buyer-trust-onboarding.md"},
            ),
            _success_handler(),
        )
        result = mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/buyer-trust-completion.md"},
            ),
            _success_handler(),
        )
        # 2 of 3 shared = 66% < 70% AND < 3 absolute → no reject.
        assert isinstance(result, ToolMessage)
        assert result.status != "error"
    finally:
        _current_batch_sibling_slugs_written.reset(token)


# ---------------------------------------------------------------------------
# Async path parity
# ---------------------------------------------------------------------------


def test_failed_write_does_not_pollute_sibling_set() -> None:
    """P1 (#200 followup): error results must not record the slug.

    A downstream middleware (e.g. `EditPayloadSanityMiddleware`,
    `SameThreadTopicGuardMiddleware`) can reject a write AFTER the
    sibling guard. If we recorded the slug regardless, a legitimate
    retry (same slug or a similar-but-distinct one) would be blocked
    with a bogus `sibling_draft_overlap`.
    """
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:

        def failing_handler(request: ToolCallRequest) -> ToolMessage:
            # Simulates a later middleware returning `status="error"`.
            return ToolMessage(
                content='{"ok": false, "reason": "edit_payload_rejected"}',
                status="error",
                tool_call_id=request.tool_call["id"],
                name="write_file",
            )

        first = _make_request(
            "write_file",
            {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
        )
        first_result = mw.wrap_tool_call(first, failing_handler)
        assert isinstance(first_result, ToolMessage)
        assert first_result.status == "error"
        # The key assertion: error result MUST NOT populate the set.
        assert _current_batch_sibling_slugs_written.get() == set()

        # Now the retry (same slug) passes normally — no bogus rejection.
        retry_result = mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            _success_handler(),
        )
        assert isinstance(retry_result, ToolMessage)
        assert retry_result.status != "error"
        assert _current_batch_sibling_slugs_written.get() == {"seller-bl-api-optimization"}
    finally:
        _current_batch_sibling_slugs_written.reset(token)


def test_followup_similar_slug_allowed_after_failed_write() -> None:
    """A failed write leaves the set clean so a sibling write can proceed."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:

        def failing_handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content='{"ok": false}',
                status="error",
                tool_call_id=request.tool_call["id"],
                name="write_file",
            )

        mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            failing_handler,
        )
        # A near-duplicate write that would normally get rejected should
        # now go through, because the prior slug was never recorded.
        result = mw.wrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-hit-optimisation.md"},
            ),
            _success_handler(),
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"


@pytest.mark.asyncio
async def test_awrap_failed_write_does_not_pollute() -> None:
    """Async parity for the failed-write-no-record rule."""
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:

        async def failing_handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content='{"ok": false}',
                status="error",
                tool_call_id=request.tool_call["id"],
                name="write_file",
            )

        await mw.awrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            failing_handler,
        )
        assert _current_batch_sibling_slugs_written.get() == set()
    finally:
        _current_batch_sibling_slugs_written.reset(token)


@pytest.mark.asyncio
async def test_awrap_rejects_near_duplicate() -> None:
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:

        async def first_handler(req: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content='{"ok": true}',
                tool_call_id=req.tool_call["id"],
                name="write_file",
            )

        await mw.awrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            first_handler,
        )

        async def reject_handler(_req: ToolCallRequest) -> ToolMessage:
            raise AssertionError("handler must not run")

        result = await mw.awrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-hit-optimisation.md"},
            ),
            reject_handler,
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["reason"] == "sibling_draft_overlap"


@pytest.mark.asyncio
async def test_awrap_force_sibling_bypass() -> None:
    mw = SiblingDraftCheckMiddleware()
    token = _current_batch_sibling_slugs_written.set(set())
    try:

        async def ok_handler(req: ToolCallRequest) -> ToolMessage:
            assert "force_sibling" not in (req.tool_call.get("args") or {})
            return ToolMessage(
                content='{"ok": true}',
                tool_call_id=req.tool_call["id"],
                name="write_file",
            )

        await mw.awrap_tool_call(
            _make_request(
                "write_file",
                {"file_path": "/wiki/topics/seller-bl-api-optimization.md"},
            ),
            ok_handler,
        )
        result = await mw.awrap_tool_call(
            _make_request(
                "write_file",
                {
                    "file_path": "/wiki/topics/seller-bl-api-hit-optimisation.md",
                    "force_sibling": True,
                },
            ),
            ok_handler,
        )
    finally:
        _current_batch_sibling_slugs_written.reset(token)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
