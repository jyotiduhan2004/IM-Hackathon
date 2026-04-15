"""Tests for src/db/wiki_pages and src/compile/compiler.resolve_page.

After the 2026-04-15 trace audit, `resolve_page` collapsed from three
optional args to a single `query` arg with shape-based intent detection,
a fallback chain across all three lookup kinds, and a candidates list
on miss so the agent doesn't retry with slug variants. The tests here
cover the tool wrapper (shape detection, catalog-empty signal, miss
with/without candidates) and the DB helpers (lookup_page / search_pages).
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from src.compile import compiler as compiler_mod
from src.db import wiki_pages as repo


def _resolve(query: str) -> dict[str, Any]:
    return compiler_mod.resolve_page.invoke({"query": query})


def _upsert_entity_user(conn: psycopg.Connection, email: str) -> None:
    from src.db import users as users_repo

    users_repo.upsert_user(conn, email=email, display_name=email.split("@")[0])


# ---------------------------------------------------------------------------
# resolve_page (tool)
# ---------------------------------------------------------------------------


def test_resolve_page_empty_query_returns_error() -> None:
    result = _resolve("   ")
    assert result["exists"] is False
    assert result["error"] == "query is empty"
    assert result["candidates"] == []


def test_resolve_page_surfaces_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.db.wiki_pages.count_wiki_pages_by_type", dict)

    result = _resolve("anything")
    assert result["exists"] is False
    assert result["catalog_empty_or_stale"] is True
    assert result["catalog_counts"] == {}
    assert "wiki_pages catalog is empty" in result["error"]


def test_resolve_page_hit_by_slug(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="buylead",
        path="wiki/topics/buylead.md",
        title="BuyLead",
        page_type="topic",
    )
    db_conn.commit()

    result = _resolve("buylead")
    assert result["exists"] is True
    assert result["slug"] == "buylead"
    assert result["page_type"] == "topic"
    assert result["confidence"] == 1.0


def test_resolve_page_hit_by_title(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="affiliate-program",
        path="wiki/systems/affiliate-program.md",
        title="Affiliate Program",
        page_type="system",
    )
    db_conn.commit()

    # Mixed-case query lands on the case-insensitive title lookup after
    # slug-lookup misses (the query contains a space → slug skipped).
    result = _resolve("affiliate program")
    assert result["exists"] is True
    assert result["slug"] == "affiliate-program"
    assert result["confidence"] == 0.9


def test_resolve_page_hit_by_email(db_conn: psycopg.Connection) -> None:
    _upsert_entity_user(db_conn, "alice@example.com")
    repo.upsert_wiki_page(
        db_conn,
        slug="alice-example-com",
        path="wiki/entities/alice-example-com.md",
        title="Alice",
        page_type="entity",
        canonical_user_email="alice@example.com",
    )
    db_conn.commit()

    result = _resolve("alice@example.com")
    assert result["exists"] is True
    assert result["slug"] == "alice-example-com"
    assert result["page_type"] == "entity"


def test_resolve_page_miss_returns_candidates(db_conn: psycopg.Connection) -> None:
    """On a real miss, the tool returns up to 5 substring candidates so
    the agent doesn't have to retry with slug variants."""
    for slug, title in [
        ("whatsapp-rollout-9696", "WhatsApp Rollout 9696"),
        ("whatsapp-onboarding", "WhatsApp Onboarding"),
        ("whatsapp-lead-handoff", "WhatsApp Lead Handoff"),
        ("unrelated-topic", "Unrelated Topic"),
    ]:
        repo.upsert_wiki_page(
            db_conn, slug=slug, path=f"wiki/topics/{slug}.md", title=title, page_type="topic"
        )
    db_conn.commit()

    result = _resolve("whatsapp-thing-that-isnt-a-real-slug")
    assert result["exists"] is False
    candidates = result["candidates"]
    # All 3 whatsapp-* pages appear as candidates; the unrelated one does not.
    returned_slugs = {c["slug"] for c in candidates}
    assert "whatsapp-rollout-9696" in returned_slugs
    assert "whatsapp-onboarding" in returned_slugs
    assert "whatsapp-lead-handoff" in returned_slugs
    assert "unrelated-topic" not in returned_slugs


def test_resolve_page_miss_with_no_close_candidates(
    db_conn: psycopg.Connection,
) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="foo",
        path="wiki/topics/foo.md",
        title="Foo",
        page_type="topic",
    )
    db_conn.commit()

    result = _resolve("zebras-giraffes-hippos")
    assert result["exists"] is False
    assert result["candidates"] == []


def test_resolve_page_fallback_across_shapes(db_conn: psycopg.Connection) -> None:
    """A query that looks like a slug but actually matches a title still
    resolves — the tool tries slug first, then falls through to title."""
    repo.upsert_wiki_page(
        db_conn,
        slug="quarterly-refund-review",
        path="wiki/topics/quarterly-refund-review.md",
        title="refund-audit",  # quirky title that looks like a slug
        page_type="topic",
    )
    db_conn.commit()

    # slug lookup on "refund-audit" misses; title lookup catches it.
    result = _resolve("refund-audit")
    assert result["exists"] is True
    assert result["slug"] == "quarterly-refund-review"


def test_resolve_page_surfaces_superseded_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded pages surface via `status` so the agent can decide to
    create a replacement."""
    monkeypatch.setattr("src.db.wiki_pages.count_wiki_pages_by_type", lambda: {"policy": 1})

    def fake_lookup(**kwargs: Any) -> dict[str, Any] | None:
        if kwargs.get("slug") == "old-policy":
            return {
                "slug": "old-policy",
                "title": "Old Policy",
                "page_type": "policy",
                "path": "wiki/policies/old-policy.md",
                "status": "superseded",
                "confidence": 1.0,
            }
        return None

    monkeypatch.setattr("src.db.wiki_pages.lookup_page", fake_lookup)
    monkeypatch.setattr("src.db.wiki_pages.search_pages", lambda q, limit=5: [])

    result = _resolve("old-policy")
    assert result["exists"] is True
    assert result["status"] == "superseded"


# ---------------------------------------------------------------------------
# search_pages (DB helper)
# ---------------------------------------------------------------------------


def test_search_pages_returns_substring_matches(db_conn: psycopg.Connection) -> None:
    for slug, title in [
        ("buylead-dispatch", "BuyLead Dispatch"),
        ("buylead-scoring", "BuyLead Scoring"),
        ("unrelated", "Unrelated"),
    ]:
        repo.upsert_wiki_page(
            db_conn, slug=slug, path=f"wiki/topics/{slug}.md", title=title, page_type="topic"
        )
    db_conn.commit()

    hits = repo.search_pages("buylead", limit=5)
    slugs = {h["slug"] for h in hits}
    assert {"buylead-dispatch", "buylead-scoring"}.issubset(slugs)
    assert "unrelated" not in slugs


def test_search_pages_orders_exact_matches_first(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="buylead-dispatch",
        path="wiki/topics/buylead-dispatch.md",
        title="BuyLead Dispatch",
        page_type="topic",
    )
    repo.upsert_wiki_page(
        db_conn,
        slug="buylead",
        path="wiki/topics/buylead.md",
        title="BuyLead",
        page_type="topic",
    )
    db_conn.commit()

    hits = repo.search_pages("buylead", limit=2)
    assert hits[0]["slug"] == "buylead"  # exact slug beats substring


def test_search_pages_empty_query_returns_empty() -> None:
    assert repo.search_pages("") == []
    assert repo.search_pages("   ") == []


# ---------------------------------------------------------------------------
# lookup_page (DB helper — unchanged signature kept for internal use)
# ---------------------------------------------------------------------------


def test_lookup_page_slug_wins_over_title(db_conn: psycopg.Connection) -> None:
    """Resolution order: slug beats title even when both are provided."""
    repo.upsert_wiki_page(
        db_conn,
        slug="foo",
        path="wiki/topics/foo.md",
        title="Foo Topic",
        page_type="topic",
    )
    repo.upsert_wiki_page(
        db_conn,
        slug="bar",
        path="wiki/topics/bar.md",
        title="Bar Topic",
        page_type="topic",
    )
    db_conn.commit()

    row = repo.lookup_page(slug="foo", title="Bar Topic")
    assert row is not None
    assert row["slug"] == "foo"
    assert row["confidence"] == 1.0


def test_lookup_page_all_none_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        repo.lookup_page()


def test_lookup_page_returns_none_when_nothing_matches() -> None:
    assert repo.lookup_page(slug="nope") is None


def test_lookup_page_falls_through_to_title(db_conn: psycopg.Connection) -> None:
    """Slug miss + title hit → title match returned with 0.9 confidence."""
    repo.upsert_wiki_page(
        db_conn,
        slug="canonical-slug",
        path="wiki/topics/canonical-slug.md",
        title="My Title",
        page_type="topic",
    )
    db_conn.commit()

    row = repo.lookup_page(slug="missing", title="My Title")
    assert row is not None
    assert row["slug"] == "canonical-slug"
    assert row["confidence"] == 0.9


def test_lookup_page_title_prefers_current_over_superseded(
    db_conn: psycopg.Connection,
) -> None:
    """Duplicate lowercased titles: `current` beats `superseded`, then page_id breaks ties."""
    repo.upsert_wiki_page(
        db_conn,
        slug="old-policy",
        path="wiki/policies/old-policy.md",
        title="Refund Policy",
        page_type="policy",
        status="superseded",
    )
    repo.upsert_wiki_page(
        db_conn,
        slug="new-policy",
        path="wiki/policies/new-policy.md",
        title="Refund Policy",
        page_type="policy",
        status="current",
    )
    db_conn.commit()

    row = repo.lookup_page(title="refund policy")
    assert row is not None
    assert row["slug"] == "new-policy"
    assert row["status"] == "current"


def test_lookup_page_email_only_matches_entity_pages(
    db_conn: psycopg.Connection,
) -> None:
    """canonical_user_email lookup is scoped to entity pages — a topic page
    that happens to carry the same email is ignored."""
    _upsert_entity_user(db_conn, "bob@example.com")
    repo.upsert_wiki_page(
        db_conn,
        slug="bob-retention-project",
        path="wiki/topics/bob-retention-project.md",
        title="Bob Retention Project",
        page_type="topic",
        canonical_user_email="bob@example.com",
    )
    db_conn.commit()

    # No entity page exists for Bob yet → email lookup misses.
    assert repo.lookup_page(canonical_user_email="bob@example.com") is None

    # Add the entity page; now the lookup finds it.
    repo.upsert_wiki_page(
        db_conn,
        slug="bob-example-com",
        path="wiki/entities/bob-example-com.md",
        title="Bob",
        page_type="entity",
        canonical_user_email="bob@example.com",
    )
    db_conn.commit()

    row = repo.lookup_page(canonical_user_email="bob@example.com")
    assert row is not None
    assert row["slug"] == "bob-example-com"
    assert row["confidence"] == 1.0
