"""Suggested H2 section shapes for topic / system / policy pages.

The compiler agent uses this template as a starting point. The validator
and the post-write critique both read from here so prompt + critique +
validator can never drift apart.

History note: this dict was previously called ``REQUIRED_SECTIONS`` in
``scripts/validate_wiki.py``. v11-U7 reframed the contract from
"validator enforces" to "agent suggests, reviewer judges" — see
``src/agent/critique._check_suggested_h2_sections`` for the warning-
severity rule and ``src/agent/reviewer.REVIEWER_SYSTEM_PROMPT``
(``filing_cabinet`` + ``structure_mismatch``) for the judgement layer.
The rename mirrors that vocabulary shift.

Match semantics (used by both the validator and the critique rule):
substring + case-insensitive — so a heading like ``## Key decisions
made in 2026`` still satisfies the ``Key decisions`` slot.

``ANTI_PATTERN_H2`` is the shared bad-list consumed by both the scorer
(``src.wiki.scoring.score_concept_shape``) and the critique rule
(``src.agent.critique._check_anti_pattern_h2``). It is the union of
the scorer's ``THREAD_SUBJECT_H2`` and the V12 50-compile deep audit
findings (``Business Requirements``, ``Key Stakeholder Feedback``,
``Early Impact Analysis``, ``Leadership Response and Goals``,
``Ticket Reference``). We re-export rather than duplicate so a new
pattern added to either file flows through automatically. Matching is
exact-title (case-insensitive via ``ANTI_PATTERN_H2_LOWER``); the
``decision:`` prefix rule is handled separately by callers.
"""

from __future__ import annotations

from src.wiki.scoring import THREAD_SUBJECT_H2

SUGGESTED_SECTIONS: dict[str, list[str]] = {
    # PR2 (2026-04-28 prompt-review Q7.1, Q7.2): the universal H2 floor
    # drops `## Summary` (lead paragraph IS the summary, per Q7.2) and
    # `## Key decisions` (decisions live on their own pages and are
    # surfaced via wikilinks in `## Recent changes`, per the lazy-
    # decision rule + the new Hard rule banning inline `## Decision: <X>`
    # H2s). The MkDocs hook auto-renders `## References` from inline
    # `[^msg-*]` footnotes (per Q4.2) so writers no longer hand-author
    # that section. The list below mirrors the prompt's universal H2
    # floor in `<page_types>`.
    "topic": [
        "Why it matters",
        "Current state",
        "Recent changes",
        "Open questions",
        "Related",
    ],
    "system": [
        "Role",
        "Active related topics",
        "Dependencies",
        "Known issues",
        "Related",
    ],
    "policy": [
        "Current policy",
        "Who it affects",
        "Effective date",
        "Supersedes",
        "History",
    ],
}


# Audit-finding additions (V12 50-compile deep audit, 2026-04-23 §7 Tier 1):
# H2 titles that survived the scorer's ``THREAD_SUBJECT_H2`` list but were
# still flagged by the narrative-shape review. These describe one email's
# flow (requirements doc, stakeholder feedback roundup, ticket reference),
# not a durable concept — keeping them isolated from the scorer's frozenset
# avoids silently widening the scorer's penalty surface.
_AUDIT_FINDING_H2: frozenset[str] = frozenset(
    [
        "Business Requirements",
        "Key Stakeholder Feedback",
        "Early Impact Analysis",
        "Leadership Response and Goals",
        "Ticket Reference",
    ]
)

# Canonical bad-list for both scorer + critique. Union of the scorer's
# thread-subject H2s (imported so the scorer stays the single source of
# truth for its own data) and the V12 audit-finding additions. ``decision:``
# prefix matching is handled by callers, not by enumerating every suffix.
ANTI_PATTERN_H2: frozenset[str] = THREAD_SUBJECT_H2 | _AUDIT_FINDING_H2
ANTI_PATTERN_H2_LOWER: frozenset[str] = frozenset(h.lower() for h in ANTI_PATTERN_H2)
