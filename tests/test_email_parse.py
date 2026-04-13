"""Tests for src/ingest/email_parse.parse_email_address.

Covers the four real-world frontmatter shapes plus malformed input — the
function MUST never raise (callers ingest YAML that may have anything
in those fields) and MUST normalize the email to lowercase so we don't
end up with both `Amit@indiamart.com` and `amit@indiamart.com` in users.
"""

from __future__ import annotations

import pytest
from src.ingest.email_parse import parse_email_address


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # Most common case: display name + bracketed email.
        (
            "Amit Jain <amit@indiamart.com>",
            ("Amit Jain", "amit@indiamart.com"),
        ),
        # Bare email — no display name.
        ("amit@indiamart.com", (None, "amit@indiamart.com")),
        # Quoted display name with a comma — parseaddr unquotes for us.
        (
            '"Last, First" <first.last@example.com>',
            ("Last, First", "first.last@example.com"),
        ),
        # Mixed-case email gets normalized down — the users PK is case-sensitive.
        (
            "Amit Jain <Amit@INDIAMART.com>",
            ("Amit Jain", "amit@indiamart.com"),
        ),
        # Whitespace-only display name -> None.
        ("   <amit@indiamart.com>", (None, "amit@indiamart.com")),
    ],
)
def test_parse_email_address_well_formed(header: str, expected: tuple[str | None, str]) -> None:
    assert parse_email_address(header) == expected


@pytest.mark.parametrize(
    "header",
    [
        "",
        "not-an-email",
        "Amit Jain",
        "<>",
        # Mismatched quotes — parseaddr returns junk; we coerce to (None, "").
        '"Amit <amit@indiamart.com',
    ],
)
def test_parse_email_address_malformed_returns_empty(header: str) -> None:
    name, email = parse_email_address(header)
    assert name is None
    assert email == ""
