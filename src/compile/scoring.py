"""Heuristic scorers for wiki topic pages — pure functions, no IO.

Four cheap, deterministic signals that rank the 300+ topic corpus without
calling an LLM: concept shape (are the H2s thread-subject leakage?),
summary currency (does the lead paragraph describe the present?), source
density (enough inline citations per 150 words?), and graph health (how
well connected is the page?). Each returns a 0-10 integer plus a debug
dict the caller can dump to CSV / DB.

The scorer CLI in ``scripts/score_wiki.py`` is the only intended caller
in app code; tests exercise these functions with synthetic strings.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.utils import extract_body
from src.utils.wikilinks import WIKILINK_RE
from src.utils.wikilinks import parse_wikilink_target

# Own this regex locally instead of importing ``_H2_RE`` from critique.py —
# cross-module private imports silently break when the owner refactors.
# Both modules having their own copy keeps the dependency graph honest.
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# H2 section titles that look like they were copy-pasted from the thread
# subject instead of synthesized into a concept page. Every hit here loses
# 2 points from ``concept_shape``; the list is intentionally narrow so
# well-written pages sail through at 10/10.
#
# Matching is case-insensitive at lookup time — ``_THREAD_SUBJECT_H2_LOWER``
# is built once from this canonical title-case list so ``## testing
# results`` or ``## BUG REPORT`` both trigger the penalty.
THREAD_SUBJECT_H2: frozenset[str] = frozenset(
    [
        "Launch Announcement",
        "Bug Report",
        "Testing Results",
        "Business Objective",
        "Final Decision",
        "QA Results",
        "Release Notes",
        "Announcement",
        "Issue",
        "Thread Summary",
        "Email Summary",
        "Discussion",
    ]
)
_THREAD_SUBJECT_H2_LOWER: frozenset[str] = frozenset(h.lower() for h in THREAD_SUBJECT_H2)

# Past-tense / narrative tokens that imply the summary is recounting history
# instead of stating current truth. Trailing spaces are load-bearing: they
# prevent false matches on "was" inside "because", "later" inside "related",
# etc. Each hit costs 2 points.
BAD_TOKENS: tuple[str, ...] = (
    "was ",
    "then ",
    "originally ",
    "initially ",
    "later ",
    "became ",
    "we tried ",
    "then we ",
    "previously ",
)

# Present-tense / ownership tokens that imply the summary is describing the
# current state. Each hit adds 1 point. Trailing spaces again guard against
# substring false matches.
#
# Note: a bare ``"is "`` was explicitly dropped — ``str.count("is ")`` fires
# inside ``analysis ``, ``basis ``, ``crisis ``, ``This `` (Th-I-S-space),
# etc., silently inflating ``summary_currency`` on 300+ pages. ``is
# responsible`` stays because the full phrase is specific enough.
GOOD_TOKENS: tuple[str, ...] = (
    "provides ",
    "handles ",
    "owns ",
    "is responsible",
    "covers ",
    "manages ",
    "enables ",
)

# Generated hub pages that link to most topics — counting their outbound
# wikilinks as "incoming" for the topic pages inflates ``graph_health``
# across the whole corpus. Filtered out in
# ``build_wikilink_incoming_index`` so only authored topic/system/policy
# pages contribute to the incoming count.
_GENERATED_HUB_STEMS: frozenset[str] = frozenset({"index", "home", "changes"})
_GENERATED_HUB_PARENTS: frozenset[str] = frozenset({"domains"})

_MSG_REF_RE = re.compile(r"\[\^msg-[a-z0-9\-]+\]")
_RAW_BULLET_RE = re.compile(r"^\s*-\s+raw/", re.MULTILINE)
_MIN_BODY_WORDS_FOR_SOURCE_SCORE = 20
_WORDS_PER_SOURCE_TARGET = 150


def score_concept_shape(body: str) -> tuple[int, dict[str, Any]]:
    """Penalize narrative / thread-subject H2s.

    Example: a page with ``## Bug Report`` + ``## Final Decision`` scores
    ``10 - 2*2 = 6``. A page with only concept-shaped H2s (``## Current
    state``, ``## How it works``) scores 10. Case-insensitive match — ``##
    testing results`` triggers the penalty too.
    """
    h2_titles = [m.group(1).strip() for m in _H2_RE.finditer(body)]
    bad_matches = [h for h in h2_titles if h.lower() in _THREAD_SUBJECT_H2_LOWER]
    count_bad = len(bad_matches)
    score = max(0, 10 - 2 * count_bad)
    return score, {
        "h2_titles": h2_titles,
        "bad_matches": bad_matches,
        "count_bad": count_bad,
    }


def score_summary_currency(body: str) -> tuple[int, dict[str, Any]]:
    """Reward present-tense lead paragraphs; penalize narrative ones.

    ``body`` is already frontmatter-stripped (caller uses ``extract_body``).
    The first non-empty paragraph after any leading H1 line is inspected.
    Score clamps to [0, 10]; neutral pages (zero hits either way) score 5.
    """
    first_paragraph = _first_paragraph(body)
    lowered = first_paragraph.lower()
    bad_count = sum(lowered.count(tok) for tok in BAD_TOKENS)
    good_count = sum(lowered.count(tok) for tok in GOOD_TOKENS)
    score = max(0, min(10, 5 + good_count - 2 * bad_count))
    return score, {
        "first_paragraph": first_paragraph,
        "bad_count": bad_count,
        "good_count": good_count,
    }


def score_source_density(body: str) -> tuple[int, dict[str, Any]]:
    """Target 1 citation per 150 words; skip pages too short to evaluate.

    Counts inline footnote refs (``[^msg-abc123]``) plus trailing bullets
    that begin ``- raw/``. Bodies under 20 words return score 0 (a stub
    page shouldn't be rewarded just for being empty).
    """
    inline_refs = _MSG_REF_RE.findall(body)
    raw_bullets = _RAW_BULLET_RE.findall(body)
    sources = len(inline_refs) + len(raw_bullets)
    body_words = len(body.split())
    if body_words < _MIN_BODY_WORDS_FOR_SOURCE_SCORE:
        return 0, {
            "sources": sources,
            "body_words": body_words,
            "ratio": 0.0,
        }
    target = max(1.0, body_words / _WORDS_PER_SOURCE_TARGET)
    ratio = sources / target
    score = min(10, round(10 * ratio))
    return score, {
        "sources": sources,
        "body_words": body_words,
        "ratio": round(ratio, 3),
    }


def score_graph_health(
    slug: str,
    body: str,
    wikilink_index: dict[str, int],
    known_slugs: set[str],
) -> tuple[int, dict[str, Any]]:
    """Reward incoming wikilinks; heavily penalize broken outgoing ones.

    ``wikilink_index`` is a corpus-scan result (slug → incoming count)
    produced once by ``build_wikilink_incoming_index``. ``known_slugs``
    is the universe of existing page slugs. Self-links are excluded by
    the index builder, not here.
    """
    incoming = wikilink_index.get(slug, 0)
    outgoing_slugs = [
        _slug_only(parse_wikilink_target(m.group(1))) for m in WIKILINK_RE.finditer(body)
    ]
    outgoing_slugs = [s for s in outgoing_slugs if s]
    broken_outgoing = sum(1 for s in outgoing_slugs if s not in known_slugs)
    score = max(0, min(10, 2 * incoming - 3 * broken_outgoing))
    return score, {
        "incoming": incoming,
        "outgoing_count": len(outgoing_slugs),
        "broken_outgoing": broken_outgoing,
    }


def build_wikilink_incoming_index(wiki_dir: Path) -> tuple[dict[str, int], set[str]]:
    """Scan every ``wiki/**/*.md`` once; return (incoming-count, known-slugs).

    Self-links are excluded from the incoming count — a page linking to
    itself shouldn't inflate its own ``graph_health`` score. Outbound links
    from generated hub pages (``index.md``, ``home.md``, ``changes.md``,
    anything under ``wiki/domains/``) are ignored: those pages link to
    most topics by construction, so treating them as first-class sources
    would inflate ``graph_health`` uniformly across the corpus. Authored
    topic/system/policy pages are the signal we want.

    ``known_slugs`` includes every page on disk (hub pages included) so
    ``graph_health`` can still flag broken outgoing links against the full
    corpus, not just topics.
    """
    incoming: dict[str, int] = {}
    known_slugs: set[str] = set()
    # Bodies are cached so we read each file once, register the slug, then
    # replay the link scan after every slug is known. Two separate rglobs
    # would walk the tree twice for no reason.
    bodies: list[tuple[str, str]] = []
    for path in wiki_dir.rglob("*.md"):
        if not path.is_file():
            continue
        known_slugs.add(path.stem)
        if _is_generated_hub(path, wiki_dir):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        bodies.append((path.stem, extract_body(content)))
    for page_slug, body in bodies:
        for match in WIKILINK_RE.finditer(body):
            target_slug = _slug_only(parse_wikilink_target(match.group(1)))
            if not target_slug or target_slug == page_slug:
                continue
            incoming[target_slug] = incoming.get(target_slug, 0) + 1
    return incoming, known_slugs


def _is_generated_hub(path: Path, wiki_dir: Path) -> bool:
    """True for auto-generated hub pages whose links shouldn't count as signal.

    Stems ``index`` / ``home`` / ``changes`` anywhere in the tree qualify,
    as does any file under ``wiki/domains/``. Matching is by stem + by a
    single ancestor directory name to keep the rule cheap and obvious.
    """
    if path.stem in _GENERATED_HUB_STEMS:
        return True
    try:
        rel_parts = path.relative_to(wiki_dir).parts
    except ValueError:
        rel_parts = path.parts
    return any(part in _GENERATED_HUB_PARENTS for part in rel_parts[:-1])


def _first_paragraph(body: str) -> str:
    """Return the first non-empty, non-heading paragraph after any H1.

    The summary is what shows up before the first H2 — skip optional H1,
    skip blank lines, then grab up to the next blank line or heading.
    """
    lines = body.splitlines()
    # Skip leading blanks + a single H1 line if present.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# ") and not lines[i].startswith("## "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    # Collect paragraph lines until blank or heading.
    buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            break
        if line.startswith("#"):
            break
        buf.append(line)
        i += 1
    return " ".join(line.strip() for line in buf).strip()


def _slug_only(target: str) -> str:
    """Strip any ``page-type/`` prefix from a wikilink target.

    Mirrors ``src.compile.critique`` / ``compiler`` / ``legacy_page_hint`` —
    they all reach for ``rsplit("/", 1)[-1]`` on wikilink slugs.
    """
    return target.rsplit("/", 1)[-1]
