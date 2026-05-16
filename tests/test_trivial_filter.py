"""Tests for src/ingest/filter_trivial.py — does the classifier pick the
right rows to skip?

Bias is false-negative over false-positive: better to wastefully compile
a trivial email than to silently drop knowledge.
"""

from __future__ import annotations

from src.ingest.filter_trivial import classify


def test_ack_reply_is_trivial() -> None:
    verdict = classify(
        subject="Re: +1 on the proposal",
        body="+1",
        from_addr="alice@indiamart.com",
    )
    assert verdict.is_trivial is True
    assert verdict.reason == "subject_prefix"


def test_substantive_keyword_overrides_skip() -> None:
    # Looks trivial on surface (short body, "Re:" prefix) but mentions
    # an API deprecation — substantive.
    verdict = classify(
        subject="Re: API deprecation plan",
        body="Looks good.",
        from_addr="alice@indiamart.com",
    )
    assert verdict.is_trivial is False
    assert verdict.reason == ""


def test_short_body_is_trivial() -> None:
    verdict = classify(
        subject="Quick note",
        body="Thanks for the update. Let me know when you need anything else.",
        from_addr="alice@indiamart.com",
    )
    assert verdict.is_trivial is True
    assert verdict.reason == "too_short"


def test_noreply_sender_is_trivial() -> None:
    verdict = classify(
        subject="Your daily digest",
        body="Here is your daily summary of activity.",
        from_addr="noreply@indiamart.com",
    )
    assert verdict.is_trivial is True
    assert verdict.reason == "auto_sender"


def test_calendar_invitation_is_trivial() -> None:
    verdict = classify(
        subject="Calendar invitation: Weekly sync",
        body="You have been invited to Weekly sync on Monday at 10am.",
        from_addr="someone@indiamart.com",
    )
    assert verdict.is_trivial is True
    assert verdict.reason == "subject_prefix"


def test_long_substantive_message_is_not_trivial() -> None:
    # 60+ words, no flagged keywords, no trivial subject prefix, human sender.
    body = " ".join(["content"] * 60)
    verdict = classify(
        subject="Weekly progress update",
        body=body,
        from_addr="alice@indiamart.com",
    )
    assert verdict.is_trivial is False
    assert verdict.reason == ""


def test_quoted_reply_alone_is_trivial() -> None:
    # Word-count ignores quoted previous-message lines so short acks
    # with long quoted bodies still get skipped.
    body = "Thanks.\n\n" + "\n".join(f"> quoted line {i}" for i in range(100))
    verdict = classify(
        subject="Update",
        body=body,
        from_addr="alice@indiamart.com",
    )
    assert verdict.is_trivial is True
    assert verdict.reason == "too_short"
