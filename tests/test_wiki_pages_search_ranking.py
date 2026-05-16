"""Ranking tests for `search_pages` — pins real-world miss cases from the
2026-04-17 Langfuse mining window (Apr 13-17).

Langfuse mining showed 191 / 329 (58%) of eligible `resolve_page` misses
returned candidates in monotonic-lowercase order because the previous
`page_id ASC` tiebreaker was effectively alphabetical on slugs inserted
in batches. Exemplar trace: `b6b6e3cca97e48976a0e7841f709b6c3`, query
`"price-widget"` returned `auditmate-unit-price-absurd-checks` first.

These tests pin canonical queries to expected top-N shape so a naive
regression (going back to creation-order tiebreaking) trips the suite.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import psycopg
from src.db import wiki_pages as repo


def _mk_message(
    conn: psycopg.Connection,
    *,
    message_id: str,
    thread_id: str = "T1",
) -> None:
    conn.execute(
        """
        INSERT INTO messages (message_id, raw_path, thread_id, compile_state)
        VALUES (%s, %s, %s, 'compiled')
        """,
        (message_id, f"raw/{message_id}.md", thread_id),
    )


def _touch(
    conn: psycopg.Connection,
    *,
    message_id: str,
    page_id: int,
    compiled_at: datetime | None = None,
) -> None:
    if compiled_at is None:
        conn.execute(
            "INSERT INTO message_touched_pages (message_id, page_id) VALUES (%s, %s)",
            (message_id, page_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO message_touched_pages (message_id, page_id, compiled_at)
            VALUES (%s, %s, %s)
            """,
            (message_id, page_id, compiled_at),
        )


# ---------------------------------------------------------------------------
# Query: "price-widget" — the exemplar from trace b6b6e3cca97e48976a0e7841f709b6c3
# ---------------------------------------------------------------------------


def test_price_widget_prefers_full_substring_over_single_token(
    db_conn: psycopg.Connection,
) -> None:
    """Pages whose slug contains the full `price-widget` substring must rank
    above pages that only match one of the tokens (`price` or `widget`).

    Regression guard: the old ranker grouped all these in the same tier
    and tiebroke on `page_id ASC`, so an alphabetically-early single-
    token match (`auditmate-unit-price-absurd-checks`) could beat a
    full-substring match (`bottom-price-widget-*`).
    """
    # Create pages in insertion order mimicking the real corpus (where
    # `auditmate-*` was inserted earlier, so its page_id is smaller than
    # the `*-price-widget-*` pages).
    auditmate_id = repo.upsert_wiki_page(
        db_conn,
        slug="auditmate-unit-price-absurd-checks",
        path="wiki/topics/auditmate-unit-price-absurd-checks.md",
        title="Unit and Price Absurd Checks in Auditmate",
        page_type="topic",
        status="active",
    )
    bottom_id = repo.upsert_wiki_page(
        db_conn,
        slug="bottom-price-widget-qna-dir-city-mcat-pages",
        path="wiki/topics/bottom-price-widget-qna-dir-city-mcat-pages.md",
        title="Bottom Price Widget QnA on DIR City MCAT Pages",
        page_type="topic",
        status="active",
    )
    seo_id = repo.upsert_wiki_page(
        db_conn,
        slug="seo-price-widget-faq-consolidation-msite-mcat-pages",
        path="wiki/topics/seo-price-widget-faq-consolidation-msite-mcat-pages.md",
        title="SEO Rework: Price Widget + FAQ Consolidation",
        page_type="topic",
        status="active",
    )
    db_conn.commit()

    # Sanity: pages ordered so alphabetical tiebreaking would put
    # `auditmate-*` first (lowest page_id among the substring matches).
    assert auditmate_id < bottom_id < seo_id

    hits = repo.search_pages("price-widget", limit=5)
    top_3_slugs = {h["slug"] for h in hits[:3]}

    # Plan pin: top-3 includes at least one of the *-price-widget-* pages.
    assert any(
        slug in top_3_slugs
        for slug in (
            "bottom-price-widget-qna-dir-city-mcat-pages",
            "seo-price-widget-faq-consolidation-msite-mcat-pages",
        )
    ), f"expected bottom-* or seo-* in top-3; got {top_3_slugs!r}"

    # Plan pin: `auditmate-*` no longer leads.
    assert hits[0]["slug"] != "auditmate-unit-price-absurd-checks", (
        "auditmate-* was the regression exemplar — must not come first"
    )


def test_price_widget_recency_breaks_tie_within_substring_tier(
    db_conn: psycopg.Connection,
) -> None:
    """When two pages match the same tier, the one touched more recently
    wins over the older one — even if its page_id is larger."""
    older_id = repo.upsert_wiki_page(
        db_conn,
        slug="bottom-price-widget-older-page",
        path="wiki/topics/bottom-price-widget-older-page.md",
        title="Bottom Price Widget Older",
        page_type="topic",
        status="active",
    )
    newer_id = repo.upsert_wiki_page(
        db_conn,
        slug="bottom-price-widget-newer-page",
        path="wiki/topics/bottom-price-widget-newer-page.md",
        title="Bottom Price Widget Newer",
        page_type="topic",
        status="active",
    )

    _mk_message(db_conn, message_id="m-old", thread_id="T-old")
    _mk_message(db_conn, message_id="m-new", thread_id="T-new")

    old_ts = datetime.now(UTC) - timedelta(days=30)
    new_ts = datetime.now(UTC) - timedelta(hours=1)
    _touch(db_conn, message_id="m-old", page_id=older_id, compiled_at=old_ts)
    _touch(db_conn, message_id="m-new", page_id=newer_id, compiled_at=new_ts)
    db_conn.commit()

    # Sanity: the NEWER page has a LARGER page_id — so the old
    # `page_id ASC` tiebreaker would put the OLDER page first. Recency
    # must flip that ordering.
    assert older_id < newer_id

    hits = repo.search_pages("price-widget", limit=2)
    assert hits[0]["slug"] == "bottom-price-widget-newer-page", (
        f"recency tiebreaker failed: got {[h['slug'] for h in hits]!r}"
    )


# ---------------------------------------------------------------------------
# Query: "seller-bl" — plan pin: top-3 prefers BL-specific pages
# ---------------------------------------------------------------------------


def test_seller_bl_prefers_bl_specific_pages_in_top_3(
    db_conn: psycopg.Connection,
) -> None:
    """A BL-specific slug (contains `seller-bl`) must out-rank generic
    `seller-*` pages that match only via the `seller` token."""
    # Insert generic seller-* pages FIRST so their page_ids are lower —
    # under the old tiebreaker they'd lead alphabetically.
    generic_ids = [
        repo.upsert_wiki_page(
            db_conn,
            slug=slug,
            path=f"wiki/topics/{slug}.md",
            title=slug.replace("-", " ").title(),
            page_type="topic",
            status="active",
        )
        for slug in (
            "abuse-seller-signals",
            "dspy-gepa-seller-chatbot",
            "seller-performance-dashboard-desktop-lms",
        )
    ]
    bl_ids = [
        repo.upsert_wiki_page(
            db_conn,
            slug=slug,
            path=f"wiki/topics/{slug}.md",
            title=slug.replace("-", " ").title(),
            page_type="topic",
            status="active",
        )
        for slug in (
            "seller-bl-api-hit-optimisation",
            "seller-bl-user-details-verification",
            "seller-bl-api-optimization",
        )
    ]
    db_conn.commit()

    # Sanity: the generic pages come BEFORE the BL-specific ones in
    # insertion order, so page_id ASC would surface them first.
    assert max(generic_ids) < min(bl_ids)

    hits = repo.search_pages("seller-bl", limit=5)
    top_3_slugs = {h["slug"] for h in hits[:3]}

    # Plan pin: top-3 are all `seller-bl-*` (full-substring tier),
    # not the generic `seller-*` token matches.
    assert top_3_slugs == {
        "seller-bl-api-hit-optimisation",
        "seller-bl-user-details-verification",
        "seller-bl-api-optimization",
    }, f"expected the 3 BL-specific slugs in top-3; got {top_3_slugs!r}"


# ---------------------------------------------------------------------------
# Invariants — status preference still holds, full-substring still beats
# ---------------------------------------------------------------------------


def test_current_status_still_beats_superseded_within_tier(
    db_conn: psycopg.Connection,
) -> None:
    """The status preference (current/active over superseded) must still
    apply inside a tier — the recency tiebreaker sorts BELOW status."""
    repo.upsert_wiki_page(
        db_conn,
        slug="my-topic-superseded",
        path="wiki/topics/my-topic-superseded.md",
        title="My Topic A",
        page_type="topic",
        status="superseded",
    )
    repo.upsert_wiki_page(
        db_conn,
        slug="my-topic-active",
        path="wiki/topics/my-topic-active.md",
        title="My Topic B",
        page_type="topic",
        status="active",
    )
    db_conn.commit()

    hits = repo.search_pages("my-topic", limit=2)
    assert hits[0]["slug"] == "my-topic-active", (
        f"active/current must beat superseded; got {[h['slug'] for h in hits]!r}"
    )


def test_full_substring_tier_beats_token_only_tier(
    db_conn: psycopg.Connection,
) -> None:
    """`slug contains full query` (tier 3) must sort ahead of a token-only
    match from the tokenised fallback patterns (tier 4 / ELSE).

    Regression guard: without this tier, both pages fall into the same
    ELSE tier and tiebreak on page_id — which lets an alphabetically-
    early single-token hit beat a mid-slug full-substring hit.
    """
    # page_id-early single-token match (only `price` hits, no `widget`):
    repo.upsert_wiki_page(
        db_conn,
        slug="aaa-price-something",
        path="wiki/topics/aaa-price-something.md",
        title="Aaa Price Something",
        page_type="topic",
        status="active",
    )
    # page_id-later full-substring match:
    repo.upsert_wiki_page(
        db_conn,
        slug="zzz-foo-price-widget-bar",
        path="wiki/topics/zzz-foo-price-widget-bar.md",
        title="Zzz Foo Price Widget Bar",
        page_type="topic",
        status="active",
    )
    db_conn.commit()

    hits = repo.search_pages("price-widget", limit=2)
    assert hits[0]["slug"] == "zzz-foo-price-widget-bar", (
        f"full-substring tier must beat token-only tier; got {[h['slug'] for h in hits]!r}"
    )
