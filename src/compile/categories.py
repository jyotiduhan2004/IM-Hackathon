"""Shared wiki category constants.

`compiler.py` and `validation.py` both need the canonical category list
when scanning `wiki/<category>/*.md`. Defined here to avoid drift —
adding a category in one and not the other silently broke duplicate-title
checks before this module existed.
"""

from __future__ import annotations

WIKI_CATEGORIES: tuple[str, ...] = (
    "topics",
    "entities",
    "systems",
    "policies",
    "timelines",
    "conflicts",
)
