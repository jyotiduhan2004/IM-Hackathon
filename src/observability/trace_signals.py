"""Shared constants for per-trace signal extraction.

Used by ``scripts/trace_scorecard.py``, ``scripts/nightly_trace_audit.py``,
``scripts/audit_50_traces.py``, and ``src/observability/langfuse_scores.py``
so every pipeline agrees on what counts as a content-type page, an
auto-correct, a reviewer verdict, an early ``write_todos`` call, or a
D1 ``check_my_work`` gate rejection.

Lives under ``src/`` (not ``scripts/``) because src must not depend on
scripts — the import direction stays one-way. Scripts import these
constants; everyone agrees.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from typing import Any

# Content-type pages — citation in any of these counts as "the
# compiler extracted knowledge from this email", per the North-Star
# definition. Entity / person pages don't count: naming an email in a
# person's page is filing-cabinet behaviour, not knowledge extraction.
# `timeline` and `conflict` were dropped in the 2026-04-15 consolidation
# (see CLAUDE.md) so they're no longer part of the content-type set.
CONTENT_PAGE_TYPES: tuple[str, ...] = (
    "topic",
    "system",
    "policy",
    "decision",
)

# PathAutoHealMiddleware (Tier A) appends this annotation to a tool
# message when it rewrites a file path. Matches only the key — the
# middleware-owned punctuation is allowed to drift without breaking the
# scorecard.
AUTO_CORRECT_PAT: re.Pattern[str] = re.compile(r"auto_corrected_from", re.IGNORECASE)

# Reviewer subagent (Tier A) returns a structured ReviewReport. We
# match the literal verdict value alongside the key so stray "verdict"
# prose (e.g. inside the system prompt printed in a trace) doesn't
# fire.
REVIEWER_VERDICT_PAT: re.Pattern[str] = re.compile(
    r"""['"]verdict['"]\s*:\s*['"](pass|revise|block)['"]""",
    re.IGNORECASE,
)

REVIEWER_VERDICTS: tuple[str, ...] = ("pass", "revise", "block")

# CheckMyWorkGate middleware (D1) emits a synthetic ToolMessage when
# ``check_my_work`` is called before any successful content-page write.
# Pattern is loose: matches the prefix only, so middleware-owned
# phrasing tweaks don't break the score. Returns 0 hits when D1 hasn't
# landed yet (no rejections in any trace).
GATE_REJECT_PAT: re.Pattern[str] = re.compile(
    r"check_my_work.{0,40}only after.{0,40}successfully.{0,40}edited|"
    r"Rejected.{0,40}check_my_work",
    re.IGNORECASE,
)

# A ``write_todos`` call within the first N tool calls counts as
# adoption of the early-planning prompt nudge.
TODOS_EARLY_WINDOW: int = 3

# U7: `log_insight` categories the agent uses to declare a valid no-op
# on an email — "nothing worth filing" (trivial_skip) or "already on
# an existing topic page" (already_captured). The audit metric
# denominator excludes these so correct skips don't look like synthesis
# failures. Kept in sync with `_VALID_INSIGHT_CATEGORIES` in
# `src/agent/tools/insights.py`.
NOOP_INSIGHT_CATEGORIES: tuple[str, ...] = ("trivial_skip", "already_captured")


# U13 — per-observation predicates surfacing the three regressions the
# user called out in 2026-04-17 trace comments:
#
# 1. `resolve_page` returning an alphabetical candidate list on a miss —
#    a fuzzy-search collapse symptom. The fix lives in a different unit;
#    we just need to see the symptom in dashboards.
# 2. `glob` timing out (deepagents filesystem middleware emits a plain
#    "Error: glob timed out after Ns..." string). Per-obs visibility
#    beats the trace-level average when only one glob in ten times out.
# 3. `reviewer` subagent returning `merge_candidates` the main agent
#    ignores — silent merge debt. Surface the count per reviewer run
#    so dashboards can slice by reviewer call, not just per trace.
#
# Each helper takes the raw `output` string from an observation. None
# require a network / DB hit; each is a pure function the per-obs
# emitter can call in a tight loop.


def _parse_tool_output_json(output_str: str) -> Any | None:
    """Parse a ToolMessage content string into a Python object, or None.

    LangChain stringifies dict/list tool results via
    ``json.dumps(content, ensure_ascii=False)`` (see
    ``langchain_core.tools.base._stringify``) so the output is normally
    valid JSON. When the full string fails to parse (AI messages
    occasionally prefix the structured JSON with a prose preamble), walk
    back from the final ``}`` using brace-balance counting to find the
    outermost trailing object and retry.
    """
    if not output_str:
        return None
    try:
        return json.loads(output_str)
    except (ValueError, TypeError):
        pass
    trailing = _extract_trailing_json_object(output_str)
    if trailing is None:
        return None
    try:
        return json.loads(trailing)
    except (ValueError, TypeError):
        return None


def _extract_trailing_json_object(text: str) -> str | None:
    """Return the last balanced ``{...}`` span in ``text``, or None.

    Walks right-to-left counting braces so nested objects (a common
    ReviewReport shape with ``"warnings": [{...}]``) don't trip the
    match. Skips braces inside JSON string literals via a simple
    in-string toggle — good enough for well-formed AI JSON, which is
    the only input shape this feeds.

    KNOWN LIMITATION: the right-to-left brace counter can be fooled by
    string values that contain literal ``{`` or ``}`` characters (e.g.
    ``{"key": "fmt: {val}"}``) — a stray brace inside a string will
    either over- or under-count depth. In practice this never happens
    for the reviewer ``ReviewReport`` shape this function is called
    against: ``merge_candidates`` is ``list[str]`` of wiki slugs (no
    braces), ``warnings`` is a list of dicts/strings with plain prose,
    and ``summary`` is short prose. Safe in the current call site —
    widen the parser only if a future payload adds free-form strings
    that could embed JSON punctuation.
    """
    end = text.rfind("}")
    if end < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(end, -1, -1):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            # Right-to-left can't tell escape-vs-literal perfectly, so
            # set a one-char guard. Rare enough we don't lose much.
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                return text[i : end + 1]
    return None


def is_alphabetical_candidate_list(output_str: str) -> bool:
    """True when `resolve_page` missed with ≥3 alphabetically-sorted candidates.

    Signals the fuzzy-search regression the user flagged on 2026-04-17:
    ``resolve_page`` returning candidates in `sorted()` order instead of
    by relevance, a symptom of the catalog falling back to the "show me
    anything that starts with the same letter" path.

    Shape expected:
    ``{"exists": False, "candidates": [{"slug": "..."}, ...]}``.
    False for hits, missing-candidates payloads, or lists shorter than
    three — two alphabetical slugs is coincidence, three is a pattern.
    """
    payload = _parse_tool_output_json(output_str)
    if not isinstance(payload, dict):
        return False
    if payload.get("exists") is not False:
        return False
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 3:
        return False
    slugs: list[str] = []
    for c in candidates:
        if not isinstance(c, dict):
            return False
        slug = c.get("slug")
        if not isinstance(slug, str):
            return False
        slugs.append(slug.lower())
    return all(a <= b for a, b in pairwise(slugs))


def is_glob_timeout(output_str: str) -> bool:
    """True when deepagents' `glob` tool timed out.

    Matches the literal prefix the filesystem middleware emits:
    ``"Error: glob timed out after 30s. Try a more specific pattern..."``.
    Loose prefix-only match so SDK tweaks to the backoff suffix don't
    silently break the signal.
    """
    if not output_str:
        return False
    return output_str.startswith("Error: glob timed out")


def extract_reviewer_merge_count(output_str: str) -> int:
    """Return len(merge_candidates) from a reviewer AGENT observation's output.

    The reviewer subagent (``src/agent/reviewer.py``) returns a
    ``ReviewReport`` with a ``merge_candidates: list[str]`` field. The
    final AI message in the subagent's trace carries the serialized
    report — either as the whole message body or appended after prose
    (handled transparently by :func:`_parse_tool_output_json`). Returns
    0 on any parse failure or when the field is absent — we never want a
    malformed trace to crash score emission.
    """
    payload = _parse_tool_output_json(output_str)
    if not isinstance(payload, dict):
        return 0
    candidates = payload.get("merge_candidates")
    if not isinstance(candidates, list):
        return 0
    return len(candidates)
