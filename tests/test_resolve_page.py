"""Tests for src/db/wiki_pages.lookup_page and src/compile/compiler.resolve_page.

The tool invokes the DB helper, which runs real SQL inside a schema-isolated
test database (see conftest.py). For the tool wrapper we either use real rows
via db_conn or monkey-patch lookup_page — both paths are exercised here so
regressions in either layer surface.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from src.compile import compiler as compiler_mod
from src.db import wiki_pages as repo


def _resolve(**kwargs: Any) -> dict[str, Any]:
    result: dict[str, Any] = compiler_mod.resolve_page.invoke(kwargs)
    return result


def _upsert_entity_user(conn: psycopg.Connection, email: str) -> None:
    from src.db import users as users_repo

    users_repo.upsert_user(conn, email=email, display_name=email.split("@")[0])


# ---------------------------------------------------------------------------
# resolve_page tool
# ---------------------------------------------------------------------------


def test_resolve_page_no_args_returns_error() -> None:
    result = _resolve()
    assert result["exists"] is False
    assert result["slug"] is None
    assert result["confidence"] == 0.0
    assert "provide at least one" in result["error"]


def test_resolve_page_surfaces_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.db.wiki_pages.count_wiki_pages_by_type", dict)

    result = _resolve(slug="anything")
    assert result["exists"] is False
    assert result["catalog_counts"] == {}
    assert "wiki_pages catalog is empty or stale" in result["error"]


def test_resolve_page_hit_by_slug(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="buylead",
        path="wiki/topics/buylead.md",
        title="BuyLead",
        page_type="topic",
    )
    db_conn.commit()

    result = _resolve(slug="buylead")
    assert result == {
        "exists": True,
        "slug": "buylead",
        "title": "BuyLead",
        "page_type": "topic",
        "path": "wiki/topics/buylead.md",
        "status": "current",
        "confidence": 1.0,
    }


def test_resolve_page_miss_returns_exists_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.db.wiki_pages.count_wiki_pages_by_type", lambda: {"topic": 1})
    result = _resolve(slug="does-not-exist")
    assert result["exists"] is False
    assert result["slug"] is None
    assert result["title"] is None
    assert result["page_type"] is None
    assert result["path"] is None
    assert result["status"] is None
    assert result["confidence"] == 0.0
    assert "error" not in result


def test_resolve_page_hit_by_title(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="affiliate-program",
        path="wiki/systems/affiliate-program.md",
        title="Affiliate Program",
        page_type="system",
    )
    db_conn.commit()

    # Case-insensitive match.
    result = _resolve(title="affiliate program")
    assert result["exists"] is True
    assert result["slug"] == "affiliate-program"
    assert result["confidence"] == 0.9


def test_resolve_page_hit_by_canonical_email(db_conn: psycopg.Connection) -> None:
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

    result = _resolve(canonical_user_email="alice@example.com")
    assert result["exists"] is True
    assert result["slug"] == "alice-example-com"
    assert result["page_type"] == "entity"
    assert result["confidence"] == 1.0


def test_resolve_page_uses_mocked_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke-test the tool wrapper in isolation — no DB round-trip."""
    calls: list[dict[str, Any]] = []

    def fake_lookup(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "slug": "foo",
            "title": "Foo",
            "page_type": "topic",
            "path": "wiki/topics/foo.md",
            "status": "current",
            "confidence": 1.0,
        }

    monkeypatch.setattr("src.db.wiki_pages.count_wiki_pages_by_type", lambda: {"topic": 1})
    monkeypatch.setattr("src.db.wiki_pages.lookup_page", fake_lookup)

    result = _resolve(slug="foo")
    assert result["exists"] is True
    assert result["slug"] == "foo"
    assert result["status"] == "current"
    assert calls == [{"slug": "foo", "title": None, "canonical_user_email": None}]


def test_resolve_page_surfaces_superseded_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded pages surface via `status` so the agent can create a replacement."""

    def fake_lookup(**kwargs: Any) -> dict[str, Any]:
        return {
            "slug": "old-policy",
            "title": "Old Policy",
            "page_type": "policy",
            "path": "wiki/policies/old-policy.md",
            "status": "superseded",
            "confidence": 1.0,
        }

    monkeypatch.setattr("src.db.wiki_pages.count_wiki_pages_by_type", lambda: {"policy": 1})
    monkeypatch.setattr("src.db.wiki_pages.lookup_page", fake_lookup)

    result = _resolve(slug="old-policy")
    assert result["exists"] is True
    assert result["status"] == "superseded"


# ---------------------------------------------------------------------------
# lookup_page
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

    # title="bar" would match page "bar", but slug="foo" wins.
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
