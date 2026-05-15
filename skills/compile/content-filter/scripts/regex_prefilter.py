"""Stage 1 pre-compilation filter: strips obvious junk from raw email content.

Usage:
    python scripts/regex_prefilter.py < email.md
    python -c "from skills.compile.content_filter.scripts.regex_prefilter import prefilter; print(prefilter(text))"
"""

from __future__ import annotations

import re
import sys


_SIGNATURE_PATTERNS = [
    re.compile(r"^-- \s*$", re.MULTILINE),
    re.compile(r"^(Regards|Thanks|Best|Cheers|Warm regards|Kind regards|Best regards|Thanks & Regards|Thank you),?\s*$", re.MULTILINE | re.IGNORECASE),
]

_GREETING_PATTERNS = [
    re.compile(r"^(Hi|Hello|Hey|Dear)\s+(team|all|everyone|folks|guys),?\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Hope|I hope)\s+(you're|you are|this finds|this email finds)\s+.{0,50}$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Good\s+(morning|afternoon|evening)\s*(team|all|everyone)?,?\s*$", re.MULTILINE | re.IGNORECASE),
]

_FORWARDED_MARKER = re.compile(
    r"^-{5,}\s*(Forwarded message|Original Message|Begin forwarded message)\s*-{5,}\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_QUOTED_LINE = re.compile(r"^>+\s?", re.MULTILINE)

_DISCLAIMER_PATTERNS = [
    re.compile(r"(?:DISCLAIMER|CONFIDENTIALITY NOTICE|This email and any).*?(?:\n\n|\Z)", re.DOTALL | re.IGNORECASE),
    re.compile(r"This (e-?mail|message) (is|may be) (intended|confidential).*?(?:\n\n|\Z)", re.DOTALL | re.IGNORECASE),
]

_CC_HEADER = re.compile(r"^(CC|Cc|cc|BCC|Bcc|bcc)\s*:.*$", re.MULTILINE)


def prefilter(text: str) -> str:
    """Strip obvious junk from raw email text. Returns cleaned text."""
    lines = text.split("\n")
    result_lines: list[str] = []
    in_signature = False
    in_forwarded = False
    consecutive_quoted = 0

    for line in lines:
        if in_signature:
            continue

        for pat in _SIGNATURE_PATTERNS:
            if pat.match(line):
                in_signature = True
                break
        if in_signature:
            continue

        if _FORWARDED_MARKER.match(line):
            in_forwarded = True
            continue

        if in_forwarded and _QUOTED_LINE.match(line):
            continue

        if _QUOTED_LINE.match(line):
            consecutive_quoted += 1
            if consecutive_quoted > 3:
                continue
        else:
            consecutive_quoted = 0

        skip = False
        for pat in _GREETING_PATTERNS:
            if pat.match(line):
                skip = True
                break
        if skip:
            continue

        if _CC_HEADER.match(line):
            continue

        result_lines.append(line)

    cleaned = "\n".join(result_lines)

    for pat in _DISCLAIMER_PATTERNS:
        cleaned = pat.sub("", cleaned)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


if __name__ == "__main__":
    text = sys.stdin.read()
    print(prefilter(text))
