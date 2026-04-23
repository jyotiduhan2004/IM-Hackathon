"""Heuristic scorers for wiki topic pages — pure functions, no IO.

Five cheap, deterministic signals that rank the 300+ topic corpus without
calling an LLM: concept shape (are the H2s thread-subject leakage?),
summary currency (does the lead paragraph describe the present?), source
density (enough inline citations per 150 words?), graph health (how well
connected is the page?), and structural smells (duplicate H2s, empty
sections, email-slug wikilinks, frontmatter+body `Related` duplication).
Each returns a 0-10 integer plus a debug dict the caller can dump to CSV
/ DB.

``structural_smells`` was added 2026-04-23 after a paired smoke on 13
pages showed scorer and LLM judge were near-inversely correlated: the
top-ranked page (9.0) got 4/3/4 from newbie/pm/ia while the bottom
(2.75) got 7/7/7. The four smells it catches (duplicate H2 allowlist,
empty sections, email-slug wikilinks, FM+body `Related` duplication) are
exactly the structural-corruption patterns the first four heuristics
miss behind otherwise good surface signals.

The scorer CLI in ``scripts/score_wiki.py`` is the only intended caller
in app code; tests exercise these functions with synthetic strings.
"""

from __future__ import annotations

import re
from collections import Counter
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
# 1 point from ``concept_shape``; the list is intentionally narrow so
# well-written pages sail through at 10/10. Penalty softened from -2 to -1
# on 2026-04-23 — see ``score_concept_shape`` docstring for the smoke
# finding that motivated it.
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
# One source per ~200 body words. v12-U3 will add inline ``[^msg-abc]``
# footnotes and ``- raw/…`` trailing bullets; both are counted in addition
# to the frontmatter ``sources:`` list. Today only the frontmatter list is
# populated across the corpus, and that's the load-bearing signal.
_WORDS_PER_SOURCE_TARGET = 200

# H2 titles that are legitimately one-per-page — a page with two of these
# almost always means the compiler stapled two batch updates end-to-end
# instead of merging them. Kept tight on purpose: a generic title like
# "Design" might appear twice for good reason ("Design — phase 1", "Design
# — phase 2"), so it's NOT in the list. Every title here is something the
# compiler prompt tells the agent to produce exactly once.
PENALIZED_DUPLICATE_H2: frozenset[str] = frozenset(
    {
        "Related",
        "Testing Bugs",
        "Impact Tracking",
        "Bug Details",
        "Follow-up",
        "Recent Changes",
        "Current State",
        "Overview",
    }
)

# Email-address-shaped wikilinks that leaked from the entity layer into a
# topic page body — ``[[aa-indiamart-com]]``, ``[[neeraj-gmail-com]]``.
# These are reference-only people slugs that shouldn't show up in the
# prose of a concept page; when they do, the page is describing a thread
# conversation instead of the concept. Case-insensitive so Title-Case
# display variants still trigger.
_EMAIL_SLUG_WIKILINK_RE = re.compile(
    r"\[\[[a-z0-9]+(?:-[a-z0-9]+)*-(?:indiamart-com|gmail-com|amazon-com)\]\]",
    re.IGNORECASE,
)
# Soft cap on the email-slug penalty: a page stuffed with 50 such links
# shouldn't zero out the whole heuristic in one shot. -4 is the worst a
# single smell can do; other smells stack on top inside
# ``score_structural_smells``.
_EMAIL_SLUG_PENALTY_CAP = 4


def score_concept_shape(body: str) -> tuple[int, dict[str, Any]]:
    """Penalize narrative / thread-subject H2s.

    Example: a page with ``## Bug Report`` + ``## Final Decision`` scores
    ``10 - 1*2 = 8``. A page with only concept-shaped H2s (``## Current
    state``, ``## How it works``) scores 10. Case-insensitive match — ``##
    testing results`` triggers the penalty too.

    Penalty softened from ``-2`` to ``-1`` per hit on 2026-04-23: the
    13-page paired smoke showed the LLM judge didn't consistently dock
    pages for bad H2 names when the underlying content was fine; the old
    weight over-penalized cosmetic issues that ``structural_smells`` now
    catches more precisely (duplicate H2s, empty sections).
    """
    h2_titles = [m.group(1).strip() for m in _H2_RE.finditer(body)]
    bad_matches = [h for h in h2_titles if h.lower() in _THREAD_SUBJECT_H2_LOWER]
    count_bad = len(bad_matches)
    score = max(0, 10 - count_bad)
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


def score_source_density(
    body: str,
    frontmatter: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Target 1 citation per 200 body words; skip pages too short to evaluate.

    Three source signals combine:
    - frontmatter ``sources:`` YAML list (load-bearing today — every
      compiled topic page populates this)
    - inline footnote refs ``[^msg-abc123]`` in the body (v12-U3 future)
    - trailing ``- raw/…`` bullets in the body (v12-U3 future)

    Bodies under 20 words return score 0 (a stub page shouldn't be
    rewarded just for being empty). Missing frontmatter treated as zero
    sources — callers that have the dict should pass it.
    """
    inline_refs = _MSG_REF_RE.findall(body)
    raw_bullets = _RAW_BULLET_RE.findall(body)
    frontmatter_sources = frontmatter.get("sources") if frontmatter else None
    fm_count = len(frontmatter_sources) if isinstance(frontmatter_sources, list) else 0
    sources = len(inline_refs) + len(raw_bullets) + fm_count
    body_words = len(body.split())
    if body_words < _MIN_BODY_WORDS_FOR_SOURCE_SCORE:
        return 0, {
            "sources": sources,
            "frontmatter_sources": fm_count,
            "inline_refs": len(inline_refs),
            "raw_bullets": len(raw_bullets),
            "body_words": body_words,
            "ratio": 0.0,
        }
    target = max(1.0, body_words / _WORDS_PER_SOURCE_TARGET)
    ratio = sources / target
    score = min(10, round(10 * ratio))
    return score, {
        "sources": sources,
        "frontmatter_sources": fm_count,
        "inline_refs": len(inline_refs),
        "raw_bullets": len(raw_bullets),
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

    Formula: ``max(0, min(10, 5 + incoming - 3 * broken_outgoing))``.
    A clean, isolated page starts at 5 (neutral) — the old ``2*incoming
    - 3*broken`` formula auto-zeroed every page with zero incoming links,
    which on the 2026-04-23 paired smoke dragged otherwise-clean pages
    down to a 2.75 mean while a structurally corrupt page with 10 incoming
    links sat at 9.0. Broken outgoing links still hurt hard (-3 each) —
    they're an unambiguous quality signal.

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
    score = max(0, min(10, 5 + incoming - 3 * broken_outgoing))
    return score, {
        "incoming": incoming,
        "outgoing_count": len(outgoing_slugs),
        "broken_outgoing": broken_outgoing,
    }


def score_structural_smells(
    body: str,
    frontmatter: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    """Penalize structural corruption the other four heuristics miss.

    Four smells, subtractive from a starting score of 10:

    1. **Duplicate allowlisted H2s** (-3 per duplicated title). Titles in
       ``PENALIZED_DUPLICATE_H2`` that appear ≥ 2 times are almost always
       two batch updates stapled together instead of merged. The allowlist
       is tight to avoid false positives on legitimately-repeated generic
       titles like ``## Design``.
    2. **Empty H2 sections** (-2 each). An ``##`` heading followed
       immediately by another ``##`` (or EOF) with no non-whitespace body
       is a placeholder the compiler never filled in.
    3. **Email-slug wikilinks** (-1 per 3 matches, cap -4). Targets like
       ``[[aa-indiamart-com]]`` are reference-only entity slugs leaking
       into topic prose — a signal the page is describing a thread
       conversation instead of the concept.
    4. **Frontmatter + body ``Related`` duplication** (-2, flat). If
       frontmatter has a non-empty ``related:`` YAML list AND the body
       also carries a ``## Related`` H2, the page is maintaining two
       inconsistent related-sets for the same slot.

    Added 2026-04-23 after a 13-page paired smoke showed the top-scored
    page (9.0) got 4/3/4 from the LLM judge personas. The judge was
    flagging duplicate H2s, empty sections, and email-slug wikilinks —
    exactly the patterns the other four heuristics hide behind good
    surface signals. See ``docs/feedback/scorer-2026-04-23.csv`` +
    ``docs/feedback/judge-2026-04-23.csv``.

    Final score clamped to ``[0, 10]``.
    """
    h2_matches = list(_H2_RE.finditer(body))
    title_counts = Counter(m.group(1).strip() for m in h2_matches)
    duplicate_h2 = [t for t, n in title_counts.items() if n >= 2 and t in PENALIZED_DUPLICATE_H2]
    duplicate_penalty = 3 * len(duplicate_h2)

    empty_sections = _empty_h2_sections(body, h2_matches)
    empty_penalty = 2 * len(empty_sections)

    email_slug_hits = len(_EMAIL_SLUG_WIKILINK_RE.findall(body))
    # ``email_slug_hits // 3`` matches the task spec: 3-5 hits → 1, 6-8 →
    # 2, 9-11 → 3, 12+ → 4 (cap). Cleaner than a branch ladder.
    email_slug_penalty = min(_EMAIL_SLUG_PENALTY_CAP, email_slug_hits // 3)

    fm_related = frontmatter.get("related") if frontmatter else None
    has_fm_related = isinstance(fm_related, list) and len(fm_related) > 0
    has_fm_and_body_related = has_fm_related and "Related" in title_counts
    related_dup_penalty = 2 if has_fm_and_body_related else 0

    raw_score = 10 - duplicate_penalty - empty_penalty - email_slug_penalty - related_dup_penalty
    score = max(0, min(10, raw_score))
    return score, {
        "duplicate_h2": duplicate_h2,
        "empty_h2_sections": empty_sections,
        "email_slug_hits": email_slug_hits,
        "has_fm_and_body_related": has_fm_and_body_related,
    }


def _empty_h2_sections(body: str, h2_matches: list[re.Match[str]]) -> list[str]:
    """Titles of H2 sections with no non-whitespace content before the next H2 / EOF.

    Reuses the caller's pre-computed ``_H2_RE.finditer`` results — every
    call site in this module already walks the H2 matches, so taking them
    as a parameter avoids a second full scan of the body string.
    """
    empty: list[str] = []
    for i, match in enumerate(h2_matches):
        section_start = match.end()
        section_end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(body)
        if not body[section_start:section_end].strip():
            empty.append(match.group(1).strip())
    return empty


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
