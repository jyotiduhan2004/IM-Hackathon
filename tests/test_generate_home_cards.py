"""Tests for the v10-U3 home page domain-card layout.

Complements `tests/test_landing_generators.py::test_generate_home_*` by
exercising the richer per-domain card surface: top-3 recency ordering,
total counts, singular-vs-plural domain frontmatter, and the
Uncategorized fallback for pages missing a `domain:` field.
"""

from __future__ import annotations

import os
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import yaml
from src.compile.compiler import _DOMAINS
from src.compile.landing import _generate_home


def _iso(ts: datetime) -> str:
    """Render a UTC ISO8601 string matching `last_compiled` frontmatter shape."""
    return ts.astimezone(UTC).isoformat()


def _write_page(
    path: Path,
    frontmatter: dict[str, object],
    body: str,
    mtime: datetime | None = None,
) -> None:
    """Write a wiki page; optionally pin fs mtime to a deterministic instant.

    Pinning mtime matters for the mtime-fallback test — otherwise pages
    created in the same tick sort by filename order, which hides the
    bug we're trying to guard against.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
    path.write_text(f"---\n{fm_text}\n---\n\n{body}", encoding="utf-8")
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(path, (ts, ts))


def _seed_three_domain_corpus(wiki: Path, base: datetime) -> None:
    """10 pages: 4 in buyer-experience, 3 in trust-safety, 3 in marketplace.

    Staggered `last_compiled` so the per-domain top-3 ordering is
    unambiguous — day 0 is newest, day 10 is oldest.
    """

    def ts(days_ago: int) -> str:
        return _iso(base - timedelta(days=days_ago))

    buyer = [
        ("buymer-rollout", 0),
        ("buyer-whatsapp", 2),
        ("buylead-tuning", 5),
        ("buyer-search", 9),
    ]
    for slug, days in buyer:
        _write_page(
            wiki / "topics" / f"{slug}.md",
            {
                "title": slug.replace("-", " ").title(),
                "page_type": "topic",
                "domain": "buyer-experience",
                "last_compiled": ts(days),
            },
            "Buyer-side change details.\n",
        )

    trust = [
        ("kyc-refresh", 1),
        ("fraud-guardrails", 4),
        ("gst-onboarding", 7),
    ]
    for slug, days in trust:
        _write_page(
            wiki / "topics" / f"{slug}.md",
            {
                "title": slug.replace("-", " ").title(),
                "page_type": "topic",
                "domain": "trust-safety",
                "last_compiled": ts(days),
            },
            "Trust update.\n",
        )

    marketplace = [
        ("photosearch-v2", 3),
        ("mcat-split", 6),
        ("ranking-rewrite", 8),
    ]
    for slug, days in marketplace:
        _write_page(
            wiki / "topics" / f"{slug}.md",
            {
                "title": slug.replace("-", " ").title(),
                "page_type": "topic",
                "domain": "marketplace-discovery",
                "last_compiled": ts(days),
            },
            "Marketplace update.\n",
        )


def test_generate_home_emits_card_per_canonical_domain(mini_wiki: Path) -> None:
    base = datetime(2026, 4, 15, tzinfo=UTC)
    _seed_three_domain_corpus(mini_wiki, base)

    path = _generate_home(mini_wiki)
    content = path.read_text()

    # Every canonical domain gets a card header, even when empty.
    for slug, title, _keywords in _DOMAINS:
        assert f"## [{title}](domains/{slug}.md)" in content


def test_generate_home_top_three_desc_last_compiled(mini_wiki: Path) -> None:
    base = datetime(2026, 4, 15, tzinfo=UTC)
    _seed_three_domain_corpus(mini_wiki, base)

    content = _generate_home(mini_wiki).read_text()
    buyer_card = _extract_card(content, "Buyer Experience")

    # Top-3 newest-first; oldest buyer page (days=9) must be excluded.
    assert "[[topics/buymer-rollout]]" in buyer_card
    assert "[[topics/buyer-whatsapp]]" in buyer_card
    assert "[[topics/buylead-tuning]]" in buyer_card
    assert "[[topics/buyer-search]]" not in buyer_card

    # Entries appear newest-first — index ordering proves the sort.
    i0 = buyer_card.index("[[topics/buymer-rollout]]")
    i1 = buyer_card.index("[[topics/buyer-whatsapp]]")
    i2 = buyer_card.index("[[topics/buylead-tuning]]")
    assert i0 < i1 < i2


def test_generate_home_total_count_per_domain(mini_wiki: Path) -> None:
    base = datetime(2026, 4, 15, tzinfo=UTC)
    _seed_three_domain_corpus(mini_wiki, base)

    content = _generate_home(mini_wiki).read_text()
    assert "4 pages total" in _extract_card(content, "Buyer Experience")
    assert "3 pages total" in _extract_card(content, "Trust, Safety & Compliance")
    assert "3 pages total" in _extract_card(content, "Marketplace & Discovery")
    # Empty domain says zero pages, not empty string.
    assert "0 pages total" in _extract_card(content, "AI Agents & Automation")


def test_generate_home_handles_plural_domains_list(mini_wiki: Path) -> None:
    # v10-U2 multi-domain schema: page appears in every listed domain.
    _write_page(
        mini_wiki / "topics" / "cross-cutting.md",
        {
            "title": "Cross cutting",
            "page_type": "topic",
            "domains": ["buyer-experience", "trust-safety"],
            "last_compiled": _iso(datetime(2026, 4, 14, tzinfo=UTC)),
        },
        "Spans buyer trust.\n",
    )

    content = _generate_home(mini_wiki).read_text()
    assert "[[topics/cross-cutting]]" in _extract_card(content, "Buyer Experience")
    assert "[[topics/cross-cutting]]" in _extract_card(content, "Trust, Safety & Compliance")


def test_generate_home_uncategorized_bucket_catches_missing_domain(
    mini_wiki: Path,
) -> None:
    # Page without domain / domains / tags / keyword-matching body → Uncategorized.
    _write_page(
        mini_wiki / "topics" / "orphan.md",
        {
            "title": "Random orphan",
            "page_type": "topic",
            "last_compiled": _iso(datetime(2026, 4, 10, tzinfo=UTC)),
        },
        "Nothing about any domain keywords here.\n",
    )

    content = _generate_home(mini_wiki).read_text()
    uncategorized = _extract_card(content, "Uncategorized")
    assert "[[topics/orphan]]" in uncategorized
    assert "1 page total" in uncategorized


def test_generate_home_uncategorized_hidden_when_empty(mini_wiki: Path) -> None:
    _write_page(
        mini_wiki / "topics" / "a.md",
        {
            "title": "A",
            "page_type": "topic",
            "domain": "buyer-experience",
            "last_compiled": _iso(datetime(2026, 4, 15, tzinfo=UTC)),
        },
        "Buyer.\n",
    )
    content = _generate_home(mini_wiki).read_text()
    # Neither the old linked form nor the new plain form should appear
    # when there's nothing to bucket — card is emitted only if non-empty.
    assert "## [Uncategorized]" not in content
    assert "## Uncategorized\n" not in content


def test_generate_home_uncategorized_has_no_dead_link(mini_wiki: Path) -> None:
    """P1-1: the Uncategorized card must not link to a non-existent hub page."""
    _write_page(
        mini_wiki / "topics" / "orphan.md",
        {
            "title": "Orphan",
            "page_type": "topic",
            "last_compiled": _iso(datetime(2026, 4, 10, tzinfo=UTC)),
        },
        "No domain keywords anywhere.\n",
    )
    content = _generate_home(mini_wiki).read_text()
    # Card header is plain, not a markdown link — no `domains/uncategorized.md` target exists.
    assert "## Uncategorized\n" in content
    assert "[Uncategorized](domains/uncategorized.md)" not in content
    assert "domains/uncategorized.md" not in content


def test_generate_home_falls_back_to_mtime_when_last_compiled_missing(
    mini_wiki: Path,
) -> None:
    base = datetime(2026, 4, 15, tzinfo=UTC)
    # No last_compiled — rank via fs mtime. `newer` has higher mtime.
    _write_page(
        mini_wiki / "topics" / "older.md",
        {"title": "Older", "page_type": "topic", "domain": "ai-automation"},
        "Older page.\n",
        mtime=base - timedelta(days=5),
    )
    _write_page(
        mini_wiki / "topics" / "newer.md",
        {"title": "Newer", "page_type": "topic", "domain": "ai-automation"},
        "Newer page.\n",
        mtime=base - timedelta(days=1),
    )

    content = _generate_home(mini_wiki).read_text()
    card = _extract_card(content, "AI Agents & Automation")
    assert card.index("[[topics/newer]]") < card.index("[[topics/older]]")


def _extract_card(content: str, domain_title: str) -> str:
    """Return the markdown slice between this card's H2 and the next H2.

    Helper over `content.split` — the home page has multiple H2s
    (Explore by domain, each card, Recent changes) so raw splits are
    fragile. We anchor on the exact card heading and stop at the next
    `\\n## ` boundary.

    Accepts both the linked form `## [Title](domains/<slug>.md)` used
    by canonical domains and the plain form `## Title` used by the
    Uncategorized bucket (no hub page, so no link — see v10-U3 P1-1).
    """
    linked = f"## [{domain_title}]"
    plain = f"## {domain_title}"
    start = content.index(linked) if linked in content else content.index(f"{plain}\n")
    remainder = content[start:]
    next_h2 = remainder.find("\n## ", 1)
    if next_h2 == -1:
        return remainder
    return remainder[:next_h2]
