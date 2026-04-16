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

import re

# Content-type pages — citation in any of these counts as "the
# compiler extracted knowledge from this email", per the North-Star
# definition. Entity / person pages don't count: naming an email in a
# person's page is filing-cabinet behaviour, not knowledge extraction.
CONTENT_PAGE_TYPES: tuple[str, ...] = (
    "topic",
    "system",
    "policy",
    "decision",
    "timeline",
    "conflict",
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
