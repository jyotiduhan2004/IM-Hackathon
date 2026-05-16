"""Email-header parsing helpers — RFC 2822 address strings.

The Gmail frontmatter stores `from`/`to`/`cc` as raw header strings:
    "Amit Jain <amit@indiamart.com>"
    "amit@indiamart.com"
    '"Last, First" <first.last@example.com>'

We need to split those into (display_name, email) pairs for the users
and message_participants tables. `email.utils.parseaddr` handles the
RFC 2822 cases for us; we just normalize the result.
"""

from __future__ import annotations

from email.utils import parseaddr


def parse_email_address(header_value: str) -> tuple[str | None, str]:
    """Split an RFC 2822 address into (display_name, email).

    Returns (None, "") for unparseable / empty input — callers should skip
    those rather than insert empty-string emails.

    Examples:
        "Amit Jain <amit@indiamart.com>" -> ("Amit Jain", "amit@indiamart.com")
        "amit@indiamart.com"             -> (None, "amit@indiamart.com")
        '"Last, First" <a@b.c>'          -> ("Last, First", "a@b.c")
        ""                               -> (None, "")
    """
    if not header_value:
        return None, ""

    name, email = parseaddr(header_value)
    name = name.strip() or None
    email = email.strip().lower()

    # parseaddr is lenient: a malformed string like '"Amit <a@b.c' returns
    # the whole salvaged-but-broken value as the email. Reject anything
    # that doesn't structurally look like a single address.
    if (
        not email
        or "@" not in email
        or any(c.isspace() for c in email)
        or "<" in email
        or ">" in email
    ):
        return None, ""

    return name, email
