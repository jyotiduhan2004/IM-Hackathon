"""Shared wiki category constants.

Two scopes, avoid drift:

1. ``WIKI_CATEGORIES`` — every directory that might exist on disk.
   Consumers: scanners, validators, backfill scripts. Includes legacy
   categories (``entities`` transitioning to ``people``) and retired
   ones (``timelines``, ``conflicts``) because pages on disk still
   need to be scanned until they're migrated away.

2. ``AGENT_VISIBLE_CATEGORIES`` — the subset the compile agent
   browses via ``list_wiki_pages``. Aligned with the prompt's
   ``<page_types>`` contract: 4 visible content types + lazy person.
   Glossary is a single file (``wiki/glossary.md``), not a directory.
   Domains are coordinator-generated hubs, also not agent-authored.
"""

from __future__ import annotations

WIKI_CATEGORIES: tuple[str, ...] = (
    "topics",
    "entities",
    "people",
    "systems",
    "policies",
    "timelines",
    "conflicts",
    "domains",
    "decisions",
)

AGENT_VISIBLE_CATEGORIES: tuple[str, ...] = (
    "topics",
    "systems",
    "policies",
    "decisions",
    "people",
)
