"""Shared helper for stripping quoted-reply and forwarded-message blocks from email bodies.

Used by ingest (trivial-ack filter) and compile (thread-context summarization).
Keeping one copy avoids the two consumers drifting — e.g. compile inventing a
slightly different marker list from ingest and silently under-stripping long
reply chains.
"""

from __future__ import annotations


def strip_quoted(body: str) -> str:
    """Drop quoted-reply and forwarded-message blocks from an email body.

    - Lines starting with `>` (after optional leading whitespace) are skipped.
    - Everything from the first `Original Message` or `----- Forwarded` marker
      onwards is truncated.

    Without this, a two-word ack with a 500-word quoted previous message
    trivially clears word-count thresholds; and an agent reading a long
    thread sees the same content duplicated N times across replies.
    """
    out: list[str] = []
    for line in body.splitlines():
        if line.lstrip().startswith(">"):
            continue
        lower = line.lower()
        if "original message" in lower or "----- forwarded" in lower:
            break
        out.append(line)
    return "\n".join(out)
