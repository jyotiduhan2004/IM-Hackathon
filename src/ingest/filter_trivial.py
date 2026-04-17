"""Return whether an email is trivial (can skip compile).

Trivial = likely to produce no lasting knowledge. Acks ("+1", "thanks"),
calendar invitations, declined/accepted meeting notices, automated
notifications, and short tangential replies. The compile path is the
expensive one (one LLM call per email), so filtering these up front
cuts cost and queue churn.

Bias: prefer false-negatives over false-positives. Missing a trivial
email wastes a cheap compile; wrongly skipping a substantive email
drops knowledge permanently. Any substantive keyword in subject+body
short-circuits the skip.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.email_quotes import strip_quoted

# Subject prefixes that almost always indicate no new content.
_TRIVIAL_SUBJECT_PREFIXES: tuple[str, ...] = (
    "re: ack",
    "re: +1",
    "re: thanks",
    "re: noted",
    "re: got it",
    "re: confirmed",
    "meeting rescheduled",
    "calendar invitation",
    "declined:",
    "accepted:",
    "tentative:",
)

# Sender local-parts that identify automated/bot traffic.
_AUTOMATED_SENDERS: tuple[str, ...] = (
    "noreply@",
    "no-reply@",
    "calendar@",
    "automated@",
    "notifications@",
)

# Keywords that force the email through compile even if it looks trivial.
_SUBSTANTIVE_KEYWORDS: tuple[str, ...] = (
    "decide",
    "decision",
    "approve",
    "policy",
    "deprecat",
    "launch",
    "rollback",
    "rolled out",
    "sla",
    "metric",
    "rate limit",
    "deploy",
    "http",
    "api",
    "endpoint",
)

_MIN_WORDS = 50


@dataclass(frozen=True)
class TrivialVerdict:
    """Result of classifying an email.

    ``reason`` is stored on the message row via ``last_error`` so operators
    can tell why a row landed in ``skipped`` without re-running the filter.
    """

    is_trivial: bool
    reason: str  # "subject_prefix" | "auto_sender" | "too_short" | ""


def classify(subject: str, body: str, from_addr: str) -> TrivialVerdict:
    """Classify one email. Substantive keywords override skip."""
    haystack = f"{subject} {body}".lower()
    if any(k in haystack for k in _SUBSTANTIVE_KEYWORDS):
        return TrivialVerdict(False, "")

    subject_lower = subject.lower().strip()
    if any(subject_lower.startswith(p) for p in _TRIVIAL_SUBJECT_PREFIXES):
        return TrivialVerdict(True, "subject_prefix")

    from_lower = from_addr.lower()
    if any(sender in from_lower for sender in _AUTOMATED_SENDERS):
        return TrivialVerdict(True, "auto_sender")

    body_only = strip_quoted(body)
    if len(body_only.split()) < _MIN_WORDS:
        return TrivialVerdict(True, "too_short")

    return TrivialVerdict(False, "")


# Backward-compat alias: the helper lives in src.utils.email_quotes now.
# Kept so any legacy `from src.ingest.filter_trivial import _strip_quoted`
# call site (scripts, notebooks) doesn't break. New code should import
# `strip_quoted` from `src.utils.email_quotes` directly.
_strip_quoted = strip_quoted
