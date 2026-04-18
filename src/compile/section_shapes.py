"""Suggested H2 section shapes for topic / system / policy pages.

The compiler agent uses this template as a starting point. The validator
and the post-write critique both read from here so prompt + critique +
validator can never drift apart.

History note: this dict was previously called ``REQUIRED_SECTIONS`` in
``scripts/validate_wiki.py``. v11-U7 reframed the contract from
"validator enforces" to "agent suggests, reviewer judges" — see
``src/compile/critique._check_suggested_h2_sections`` for the warning-
severity rule and ``src/compile/reviewer.REVIEWER_SYSTEM_PROMPT``
(``filing_cabinet`` + ``structure_mismatch``) for the judgement layer.
The rename mirrors that vocabulary shift.

Match semantics (used by both the validator and the critique rule):
substring + case-insensitive — so a heading like ``## Key decisions
made in 2026`` still satisfies the ``Key decisions`` slot.
"""

from __future__ import annotations

SUGGESTED_SECTIONS: dict[str, list[str]] = {
    "topic": [
        "Summary",
        "Current state",
        "Why it matters",
        "Key decisions",
        "Recent changes",
        "Open questions",
        "Related pages",
        "References",
    ],
    "system": [
        "Summary",
        "Role",
        "Active related topics",
        "Dependencies",
        "Known issues",
        "Related pages",
        "References",
    ],
    "policy": [
        "Current policy",
        "Who it affects",
        "Effective date",
        "Supersedes",
        "History",
        "References",
    ],
}
