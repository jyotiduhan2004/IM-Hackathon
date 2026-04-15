"""Draft-page critique heuristics.

Supporting module for `validate_page_draft` in `src.compile.compiler`.
Each rule function takes the draft body (and, where relevant, the
frontmatter / existing wiki state) and returns a warning dict or None.

All checks are intentionally cheap and local: they catch the specific
failure modes the compiler produces most often — filing-cabinet page
with no TL;DR, email-quote dumping, person-page with nothing but a
name-drop, duplicate title landing on top of an existing page. Deep
semantic checks (is this a concept page vs. an email list?) are
Tier-2 `check_my_work` territory, not this module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.utils import extract_frontmatter

# Matches `tl;dr` or `tldr` as a standalone token (case-insensitive). Covers
# both the heading case (`## TL;DR`) and any in-prose mention. The `\b`
# boundary guards against false positives like "Product Teardown:".
_TLDR_TOKEN_RE = re.compile(r"\btl;?dr\b", re.IGNORECASE)

# H2 heading matcher; used to find the *first* H2 for the TL;DR check.
_FIRST_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Blockquote line: optional leading whitespace then `> ` (GFM style).
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?")

# Sentence splitter — conservative, intentionally crude. The person-page
# heuristic only needs "is this more than a name drop", not a parse tree.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+")


def check_missing_tldr(body: str) -> dict[str, str] | None:
    """Warn if neither the first H2 nor the first 500 chars name TL;DR.

    The primary signal is "first H2 heading is TL;DR". Some pages put
    an intro paragraph before the heading, so we ALSO accept a TL;DR
    inside the first 500 characters as a secondary signal.
    """
    first = _FIRST_H2_RE.search(body)
    if first and _TLDR_TOKEN_RE.search(first.group(1)):
        return None
    if _TLDR_TOKEN_RE.search(body[:500]):
        return None
    return {
        "rule": "missing_tldr",
        "severity": "warning",
        "message": "No TL;DR — reader sees no one-line summary before details.",
    }


def check_over_quoting(body: str) -> dict[str, str] | None:
    """Warn when >30% of non-empty lines are blockquotes (email paste-in).

    Filing-cabinet pages tend to quote enormous email threads instead of
    synthesizing. 30% is the observed threshold where pages read more
    like archives than wiki entries.
    """
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return None
    blockquote_lines = sum(1 for line in lines if _BLOCKQUOTE_RE.match(line))
    ratio = blockquote_lines / len(lines)
    if ratio <= 0.30:
        return None
    return {
        "rule": "over_quoting",
        "severity": "warning",
        "message": (
            f"{blockquote_lines}/{len(lines)} lines ({int(ratio * 100)}%) are "
            "blockquotes — page is dumping email text instead of synthesizing."
        ),
    }


def check_person_page_heuristic(body: str, frontmatter: dict[str, Any]) -> dict[str, str] | None:
    """Warn when a person/entity page has only a name-drop worth of prose.

    Triggered by `page_type: person` or `page_type: entity`. "Multi-sentence"
    means ≥2 sentences in the body excluding headings, wikilinks-only lines,
    and blockquotes. This flags CC-list stubs that should be rejected
    upstream by the `create_entity` evidence gate.
    """
    page_type = str(frontmatter.get("page_type") or "").lower()
    if page_type not in ("person", "entity"):
        return None

    prose_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if _BLOCKQUOTE_RE.match(stripped):
            continue
        # Skip lines that are just a list of wikilinks.
        if re.fullmatch(r"(\[\[[^\]]+\]\][\s,·|]*)+", stripped):
            continue
        prose_lines.append(stripped)

    if not prose_lines:
        return {
            "rule": "person_page_heuristic",
            "severity": "blocker",
            "message": (
                "person/entity page has no prose — only headings, wikilinks, "
                "or quotes. Bare name-drop pages should not be created."
            ),
        }

    joined = " ".join(prose_lines)
    # Count sentences conservatively — split on terminators, drop empties.
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(joined) if s.strip()]
    # A trailing period on the last sentence leaves it in the list; that's fine.
    if len(sentences) < 2:
        return {
            "rule": "person_page_heuristic",
            "severity": "blocker",
            "message": (
                "person/entity page has fewer than 2 substantive sentences — "
                "looks like a CC-list mention, not a wiki page."
            ),
        }
    return None


def check_likely_duplicate(
    slug: str, frontmatter: dict[str, Any], wiki_dir: str | Path
) -> dict[str, str] | None:
    """Warn when another wiki page already has the same title (case-insensitive).

    Reads the existing pages directly off disk — this is a pre-write
    sanity check, so it has to work without a catalog round-trip.
    Matches by lowercased title across ALL category directories.
    """
    title_raw = frontmatter.get("title")
    if not isinstance(title_raw, str) or not title_raw.strip():
        return None
    target = title_raw.strip().lower()
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return None

    categories = ("topics", "entities", "systems", "policies", "timelines", "conflicts")
    for category in categories:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in cat_dir.glob("*.md"):
            if md_file.stem == slug:
                continue  # Self — overwriting our own page is fine.
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = extract_frontmatter(content)
            existing_title = fm.get("title")
            if isinstance(existing_title, str) and existing_title.strip().lower() == target:
                return {
                    "rule": "likely_duplicate",
                    "severity": "warning",
                    "message": (
                        f"Another page ({category}/{md_file.stem}) already has "
                        f"title {title_raw!r}. Merge or pick a distinct title."
                    ),
                }
    return None
