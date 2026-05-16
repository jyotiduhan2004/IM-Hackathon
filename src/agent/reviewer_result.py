"""Reviewer-result parsing — read the agent's final state for verdicts and silent-fail detection.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). Parses
`agent.ainvoke(...)` return values to surface reviewer-flagged merge
candidates and to detect the LiteLLM 200-empty silent-fail mode.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class SilentModelFailError(RuntimeError):
    """Raised when the agent's only model response is an empty ChatCompletion.

    The LiteLLM proxy occasionally returns HTTP 200 with
    ``completion_tokens=0 prompt_tokens=0 content=""`` on certain
    model requests (observed on minimax/minimax-m2.7-20260318, Cycle 5).
    The agent sees an empty AI message, terminates with no tool calls,
    and the coordinator records a spurious ``outcome='failed'`` with
    ``error='not cited in wiki'`` — indistinguishable from a genuine
    agent failure.

    `compile_all.py` treats this as an infrastructure error: retry the
    batch with a different model from the pool, same as the LiteLLM
    401/400 path. See docs/audits/cycle-5-case-bug-j-minimax-silent-fail.md.
    """


def _check_silent_fail(result: dict[str, Any], *, model: str | None = None) -> None:
    """Raise SilentModelFailError if the agent's final state is the
    zero-token empty-content shape produced by the LiteLLM proxy on
    malfunctioning model requests.
    """
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return

    ai_messages = [m for m in messages if _message_is_ai(m)]
    if len(ai_messages) != 1:
        return

    ai = ai_messages[0]
    content = _message_content(ai)
    tool_calls = _message_tool_calls(ai)
    if content or tool_calls:
        return

    token_total = _message_total_tokens(ai)
    if token_total != 0:
        return

    raise SilentModelFailError(
        f"LiteLLM returned 200-empty on model={model!r} "
        "(completion_tokens=0 prompt_tokens=0 content=''). "
        "Retry with a different model."
    )


_VERDICT_KEY_RE = re.compile(r'"verdict"\s*:\s*"(pass|revise|block)"', re.IGNORECASE)


def _extract_merge_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return reviewer-flagged merge candidate pairs from an agent result.

    When the main agent calls ``task(subagent_type="reviewer", ...)`` the
    reviewer's final ``ReviewReport`` JSON is returned as the content of a
    ``ToolMessage`` in the agent state (see
    ``deepagents.middleware.subagents._return_command_with_state_update``).

    Scans every message for JSON objects that carry a reviewer ``verdict``
    and a non-empty ``merge_candidates`` list, and returns one pair per
    candidate::

        [{"slug_a": "bl-notif", "slug_b": "bl-sms", "note": <summary>}]

    ``slug_a`` is the page the reviewer was reading (first finding's
    ``slug``, empty when none); ``slug_b`` is the flagged candidate.

    Returns ``[]`` when there are no messages, no reviewer ran, or every
    report had ``merge_candidates=[]``. Malformed JSON is skipped — parser
    errors must never crash a compile run.
    """
    import json

    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return []

    decoder = json.JSONDecoder()
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for msg in messages:
        text = _message_any_text(msg)
        if not text:
            continue
        for match in _VERDICT_KEY_RE.finditer(text):
            # Walk left for the ENCLOSING `{` (not the nearest — a nested
            # object would break json.raw_decode since `"verdict"` may sit
            # at the top level while an inner `{` is closer). Count braces
            # so a ``{"x": {"y":1}, "verdict": ...}`` matches the outer.
            start = _find_enclosing_brace(text, match.start())
            if start < 0:
                continue
            try:
                payload, _end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("verdict") not in {"pass", "revise", "block"}:
                continue
            candidates = payload.get("merge_candidates")
            if not isinstance(candidates, list) or not candidates:
                continue
            slug_a = _reviewed_slug(payload)
            note = _reviewer_note(payload)
            for c in candidates:
                slug_b = c.strip() if isinstance(c, str) else ""
                if not slug_b:
                    continue
                key = tuple(sorted([slug_a, slug_b]))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append({"slug_a": slug_a, "slug_b": slug_b, "note": note})

    return pairs


def _find_enclosing_brace(text: str, pos: int) -> int:
    """Return the index of the ``{`` that encloses ``pos``, or -1.

    Walks left counting ``}``/``{`` depth so a match inside a nested JSON
    object finds the outer container, not a sibling's. Literal braces
    inside string values would break this count, but reviewer JSON never
    emits those — worst case is ``raw_decode`` raising and the pair being
    dropped (best-effort is fine).
    """
    depth = 0
    for i in range(pos, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return i
            depth -= 1
    return -1


def _message_any_text(msg: Any) -> str:
    """Return the text content of any message (AI, Tool, Human, ...).

    Handles both dict-shaped and LangChain BaseMessage instances. List-
    of-block content (Anthropic format) is joined by ``text`` fields.
    """
    content = (
        msg.get("content", "") if isinstance(msg, dict) else (getattr(msg, "content", "") or "")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


def _reviewed_slug(payload: dict[str, Any]) -> str:
    """Return the first non-empty finding ``slug`` in blockers → warnings.

    ReviewReport has no ``target`` field — the reviewer places the
    offending slug on each finding. Empty when no findings attached.
    """
    for key in ("blockers", "warnings"):
        findings = payload.get(key)
        if not isinstance(findings, list):
            continue
        for f in findings:
            if isinstance(f, dict):
                slug = f.get("slug")
                if isinstance(slug, str) and slug.strip():
                    return slug.strip()
    return ""


def _reviewer_note(payload: dict[str, Any]) -> str:
    """Compose a <=200-char reviewer note for the merge queue.

    Prefers ``summary``; falls back to the first blocker/warning message.
    """
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()[:200]
    for key in ("blockers", "warnings"):
        findings = payload.get(key)
        if isinstance(findings, list):
            for f in findings:
                if isinstance(f, dict):
                    msg = f.get("message")
                    if isinstance(msg, str) and msg.strip():
                        return msg.strip()[:200]
    return ""


def _message_is_ai(msg: Any) -> bool:
    if isinstance(msg, dict):
        return msg.get("type") == "ai" or msg.get("role") == "assistant"
    return msg.__class__.__name__ == "AIMessage"


def _message_content(msg: Any) -> str:
    c = msg.get("content", "") if isinstance(msg, dict) else (getattr(msg, "content", "") or "")
    return c.strip() if isinstance(c, str) else ""


def _message_tool_calls(msg: Any) -> list[Any]:
    if isinstance(msg, dict):
        calls = msg.get("tool_calls") or (msg.get("additional_kwargs") or {}).get("tool_calls")
    else:
        calls = getattr(msg, "tool_calls", None) or getattr(msg, "additional_kwargs", {}).get(
            "tool_calls"
        )
    return calls if isinstance(calls, list) else []


def _message_total_tokens(msg: Any) -> int | None:
    if isinstance(msg, dict):
        meta = msg.get("response_metadata") or {}
    else:
        meta = getattr(msg, "response_metadata", {}) or {}
    usage = meta.get("token_usage") if isinstance(meta, dict) else None
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    return int(total) if isinstance(total, (int, float)) else None
