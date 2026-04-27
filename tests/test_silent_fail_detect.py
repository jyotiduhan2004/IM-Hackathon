"""Tests for silent-fail detection (Bug J).

LiteLLM proxy occasionally returns HTTP 200 with an empty payload on
minimax/minimax-m2.7-20260318. Agent terminates with zero work, the
coordinator marks the attempt failed with "not cited in wiki" —
indistinguishable from a real agent failure.

Detection: inspect the agent's final `result['messages']` for the
shape "1 AI message, empty content, no tool calls, total_tokens=0"
and raise SilentModelFailError so the retry loop can pick another
model.
"""

from __future__ import annotations

from typing import Any

import pytest
from src.compile.compiler import SilentModelFailError
from src.compile.compiler import _check_silent_fail


def _user(content: str = "compile this") -> dict[str, Any]:
    return {"role": "user", "content": content, "type": "human"}


def _ai(
    content: str = "",
    tool_calls: list[Any] | None = None,
    total_tokens: int | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "role": "assistant",
        "type": "ai",
        "content": content,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if total_tokens is not None:
        msg["response_metadata"] = {"token_usage": {"total_tokens": total_tokens}}
    return msg


class TestSilentFailDetection:
    def test_empty_content_zero_tokens_no_tool_calls_raises(self) -> None:
        result = {"messages": [_user(), _ai(content="", total_tokens=0)]}
        with pytest.raises(SilentModelFailError) as exc_info:
            _check_silent_fail(result, model="minimax/m2.7")
        assert "minimax/m2.7" in str(exc_info.value)
        assert "200-empty" in str(exc_info.value)

    def test_non_empty_content_passes(self) -> None:
        result = {
            "messages": [
                _user(),
                _ai(content="I'll start by reading the email.", total_tokens=0),
            ]
        }
        # No-op — content is non-empty so NOT a silent fail.
        _check_silent_fail(result, model="minimax/m2.7")

    def test_tool_calls_present_passes(self) -> None:
        result = {
            "messages": [
                _user(),
                _ai(
                    content="",
                    tool_calls=[{"name": "read_file", "args": {"file_path": "raw/x.md"}}],
                    total_tokens=0,
                ),
            ]
        }
        _check_silent_fail(result, model="minimax/m2.7")

    def test_nonzero_tokens_passes(self) -> None:
        # Legitimate agent that chose to be terse. Total_tokens > 0 means
        # the proxy DID reach the model — not infra failure.
        result = {"messages": [_user(), _ai(content="", total_tokens=42)]}
        _check_silent_fail(result, model="minimax/m2.7")

    def test_multiple_ai_messages_pass(self) -> None:
        # If the agent had multiple turns (even one of them empty), it's
        # not the "one empty message total" shape — proxy reached the
        # model at least once.
        result = {
            "messages": [
                _user(),
                _ai(content="thinking...", total_tokens=10),
                _ai(content="", total_tokens=0),
            ]
        }
        _check_silent_fail(result, model="minimax/m2.7")

    def test_missing_token_usage_passes(self) -> None:
        # When response_metadata has no token_usage, we don't have
        # explicit proof this was a proxy-empty. Be conservative; don't
        # raise.
        result = {"messages": [_user(), _ai(content="")]}
        _check_silent_fail(result, model="minimax/m2.7")

    def test_non_dict_result_tolerated(self) -> None:
        # Defensive: weird result shapes don't crash the detector.
        _check_silent_fail({}, model="minimax/m2.7")
        _check_silent_fail({"messages": None}, model="minimax/m2.7")  # type: ignore[arg-type]


class TestRetryLoopIntegration:
    """Verify compile_all's retry path treats SilentModelFailError the
    same as LiteLLM 401/400 — drop the model from pool + retry."""

    def test_is_model_unavailable_error_catches_silent_fail(self) -> None:
        from scripts.compile_all import _is_model_unavailable_error

        assert _is_model_unavailable_error(SilentModelFailError("minimax 200-empty")) is True

    def test_is_model_unavailable_error_still_catches_litellm_401(self) -> None:
        from scripts.compile_all import _is_model_unavailable_error

        assert _is_model_unavailable_error(Exception("team not allowed to access model")) is True
        assert _is_model_unavailable_error(Exception("Invalid model name")) is True

    def test_is_model_unavailable_error_catches_bare_401_auth(self) -> None:
        # Bug K — Cycle 6 glm-5 at 162s died with
        # ``Error code: 401 - {'error': {'message': 'Authentication Error'...``
        # and slipped past the original string match.
        from scripts.compile_all import _is_model_unavailable_error

        exc = Exception(
            "Error code: 401 - {'error': {'message': 'Authentication Error, "
            "API key provided', 'type': 'invalid_request_error'}}"
        )
        assert _is_model_unavailable_error(exc) is True

    def test_is_model_unavailable_error_catches_403_forbidden(self) -> None:
        from scripts.compile_all import _is_model_unavailable_error

        assert (
            _is_model_unavailable_error(
                Exception("Error code: 403 - {'error': {'message': 'Forbidden'}}")
            )
            is True
        )

    def test_is_model_unavailable_error_ignores_free_text_401(self) -> None:
        # False-positive hedge: matcher requires the structured "Error
        # code: 401" prefix, not any mention of 401.
        from scripts.compile_all import _is_model_unavailable_error

        assert (
            _is_model_unavailable_error(Exception("response: HTTP 401 came back from upstream API"))
            is False
        )

    def test_unrelated_exception_does_not_trigger_retry(self) -> None:
        from scripts.compile_all import _is_model_unavailable_error

        assert _is_model_unavailable_error(ValueError("some bug")) is False

    def test_is_model_unavailable_error_catches_html_502(self) -> None:
        """2026-04-24 smoke: glm-5.1 returned 5x ``<title>502 Server
        Error</title>`` HTML responses from the proxy. Each one took
        ~570 s before failing; the batch was marked failed instead of
        retrying with a healthy pool model. Make these route to retry.
        """
        from scripts.compile_all import _is_model_unavailable_error

        html_502 = (
            "<html><head>\n"
            '<meta http-equiv="content-type" content="text/html;charset=utf-8">\n'
            "<title>502 Server Error</title>\n</head>\n"
            "<body text=#000000 bgcolor=#ffffff>\n<h1>Error: Server Error</h1>\n"
        )
        assert _is_model_unavailable_error(Exception(html_502)) is True

    def test_is_model_unavailable_error_catches_html_5xx_variants(self) -> None:
        from scripts.compile_all import _is_model_unavailable_error

        assert (
            _is_model_unavailable_error(Exception("<title>503 Service Unavailable</title>")) is True
        )
        assert _is_model_unavailable_error(Exception("<title>504 Gateway Timeout</title>")) is True
        # Cloudflare / nginx use ``502 Bad Gateway`` (distinct from
        # Google Frontend's ``502 Server Error``).
        assert _is_model_unavailable_error(Exception("<title>502 Bad Gateway</title>")) is True
        # nginx 504 page uses hyphenated ``Gateway Time-out`` (not
        # ``Timeout``). Found in stock nginx error pages — distinct
        # from Cloudflare/Google Frontend's spelling.
        assert _is_model_unavailable_error(Exception("<title>504 Gateway Time-out</title>")) is True

    def test_is_model_unavailable_error_catches_structured_5xx(self) -> None:
        from scripts.compile_all import _is_model_unavailable_error

        assert _is_model_unavailable_error(Exception("Error code: 502")) is True
        assert _is_model_unavailable_error(Exception("Error code: 503")) is True
        assert _is_model_unavailable_error(Exception("Error code: 504")) is True

    def test_is_model_unavailable_error_ignores_free_text_502(self) -> None:
        """Same hedge as the 401 case: a wiki page that mentions "502" in
        prose must not trigger pool retry.
        """
        from scripts.compile_all import _is_model_unavailable_error

        assert (
            _is_model_unavailable_error(Exception("the upstream returned a 502 once last week"))
            is False
        )
