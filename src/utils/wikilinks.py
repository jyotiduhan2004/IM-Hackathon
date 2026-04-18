"""Shared wikilink parsing — one regex, one normalizer.

`[[topic/foo|Display#section]]` shows up in wiki bodies with three
decorations: pipe for display text, hash for intra-page anchor, both.
Callers only ever want the bare slug. Prior bugs: critique and validator
did `link.split("|")[0].strip()` which left `#anchor` attached, so
`[[topic/foo#history]]` got mis-flagged as broken.
"""

from __future__ import annotations

import re

WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\[\]]*?)?(?:#[^\[\]]*?)?\]\]")


def parse_wikilink_target(raw: str) -> str:
    """Return the bare slug from the inside of a `[[...]]`.

    `raw` is the content between the brackets (e.g. `"topic/foo|Display#section"`);
    the return value strips both the `|display` and `#anchor` tails.
    Idempotent: safe to call on already-extracted group 1 from WIKILINK_RE.
    """
    return raw.split("|", 1)[0].split("#", 1)[0].strip()
