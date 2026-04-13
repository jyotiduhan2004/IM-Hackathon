"""Tests for src/db/wiki_pages.py.

Covers upsert idempotency, metadata refresh on conflict, find_by_slug,
count_wiki_pages_by_type, and the partial unique index that enforces
"one entity page per email address".
"""

from __future__ import annotations

import psycopg
import pytest
from src.db import users as users_repo
from src.db import wiki_pages as repo


def _upsert(
    conn: psycopg.Connection,
    *,
    slug: str,
    page_type: str = "topic",
    title: str | None = None,
    path: str | None = None,
    status: str = "current",
    canonical_user_email: str | None = None,
) -> int:
    return repo.upsert_wiki_page(
        conn,
        slug=slug,
        path=path or f"wiki/{page_type}s/{slug}.md",
        title=title or slug.replace("-", " ").title(),
        page_type=page_type,
        status=status,
        canonical_user_email=canonical_user_email,
    )


# ---------------------------------------------------------------------------
# upsert_wiki_page
# ---------------------------------------------------------------------------


def test_upsert_wiki_page_inserts_and_returns_id(db_conn: psycopg.Connection) -> None:
    page_id = _upsert(db_conn, slug="buylead")
    db_conn.commit()
    assert isinstance(page_id, int)
    assert page_id > 0


def test_upsert_wiki_page_idempotent_same_id(db_conn: psycopg.Connection) -> None:
    first = _upsert(db_conn, slug="buylead", title="BuyLead")
    second = _upsert(db_conn, slug="buylead", title="BuyLead")
    db_conn.commit()
    assert first == second


def test_upsert_wiki_page_refreshes_metadata_on_conflict(
    db_conn: psycopg.Connection,
) -> None:
    page_id = _upsert(db_conn, slug="buylead", title="Old Title", status="current")
    # Second upsert with new title + new path + new status.
    _upsert(
        db_conn,
        slug="buylead",
        title="New Title",
        path="wiki/topics/buylead.md",
        status="superseded",
    )
    db_conn.commit()

    row = repo.find_by_slug("buylead")
    assert row is not None
    assert row["page_id"] == page_id
    assert row["title"] == "New Title"
    assert row["path"] == "wiki/topics/buylead.md"
    assert row["status"] == "superseded"


# ---------------------------------------------------------------------------
# find_by_slug
# ---------------------------------------------------------------------------


def test_find_by_slug_returns_none_for_unknown(db_conn: psycopg.Connection) -> None:
    assert repo.find_by_slug("does-not-exist") is None


def test_find_by_slug_returns_row_with_defaults(db_conn: psycopg.Connection) -> None:
    _upsert(db_conn, slug="affiliate-program", page_type="system")
    db_conn.commit()

    row = repo.find_by_slug("affiliate-program")
    assert row is not None
    assert row["slug"] == "affiliate-program"
    assert row["page_type"] == "system"
    assert row["status"] == "current"
    assert row["update_count"] == 0
    assert row["last_compiled_at"] is None
    assert row["canonical_user_email"] is None


# ---------------------------------------------------------------------------
# count_wiki_pages_by_type
# ---------------------------------------------------------------------------


def test_count_wiki_pages_by_type_groups_correctly(
    db_conn: psycopg.Connection,
) -> None:
    _upsert(db_conn, slug="t1", page_type="topic")
    _upsert(db_conn, slug="t2", page_type="topic")
    _upsert(db_conn, slug="s1", page_type="system")
    _upsert(db_conn, slug="c1", page_type="conflict")
    db_conn.commit()

    counts = repo.count_wiki_pages_by_type()
    assert counts == {"topic": 2, "system": 1, "conflict": 1}


def test_count_wiki_pages_by_type_empty(db_conn: psycopg.Connection) -> None:
    # Schema cleaned between tests — an empty table just returns {}.
    assert repo.count_wiki_pages_by_type() == {}


# ---------------------------------------------------------------------------
# page_type CHECK constraint
# ---------------------------------------------------------------------------


def test_upsert_rejects_invalid_page_type(db_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _upsert(db_conn, slug="oops", page_type="bogus")


def test_upsert_rejects_invalid_status(db_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _upsert(db_conn, slug="oops", status="draft")


# ---------------------------------------------------------------------------
# Partial unique index: one entity page per canonical_user_email.
# ---------------------------------------------------------------------------


def test_two_entity_pages_same_email_violates_unique(
    db_conn: psycopg.Connection,
) -> None:
    users_repo.upsert_user(db_conn, email="alice@example.com", display_name="Alice")
    db_conn.commit()

    _upsert(
        db_conn,
        slug="alice",
        page_type="entity",
        canonical_user_email="alice@example.com",
    )
    db_conn.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        _upsert(
            db_conn,
            slug="alice-dupe",
            page_type="entity",
            canonical_user_email="alice@example.com",
        )


def test_entity_plus_non_entity_share_email_ok(db_conn: psycopg.Connection) -> None:
    """Partial index excludes non-entity rows — same email on a topic + an entity
    coexist happily."""
    users_repo.upsert_user(db_conn, email="bob@example.com", display_name="Bob")
    db_conn.commit()

    _upsert(
        db_conn,
        slug="bob",
        page_type="entity",
        canonical_user_email="bob@example.com",
    )
    _upsert(
        db_conn,
        slug="bob-retention-project",
        page_type="topic",
        canonical_user_email="bob@example.com",
    )
    db_conn.commit()

    # Both should exist.
    assert repo.find_by_slug("bob") is not None
    assert repo.find_by_slug("bob-retention-project") is not None


def test_two_non_entity_pages_share_email_ok(db_conn: psycopg.Connection) -> None:
    """Partial index keys only on entity rows — weird, but should not fail."""
    users_repo.upsert_user(db_conn, email="carol@example.com", display_name="Carol")
    db_conn.commit()

    _upsert(
        db_conn,
        slug="carol-a",
        page_type="topic",
        canonical_user_email="carol@example.com",
    )
    _upsert(
        db_conn,
        slug="carol-b",
        page_type="system",
        canonical_user_email="carol@example.com",
    )
    db_conn.commit()

    assert repo.find_by_slug("carol-a") is not None
    assert repo.find_by_slug("carol-b") is not None


def test_many_entity_pages_with_null_email_ok(db_conn: psycopg.Connection) -> None:
    """NULL canonical_user_email is excluded from the partial unique index."""
    _upsert(db_conn, slug="e1", page_type="entity", canonical_user_email=None)
    _upsert(db_conn, slug="e2", page_type="entity", canonical_user_email=None)
    db_conn.commit()

    assert repo.find_by_slug("e1") is not None
    assert repo.find_by_slug("e2") is not None


def test_entity_email_fk_rejects_unknown_user(db_conn: psycopg.Connection) -> None:
    """canonical_user_email has a FK to users.email — unknown email fails."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _upsert(
            db_conn,
            slug="ghost",
            page_type="entity",
            canonical_user_email="nobody@example.com",
        )
