"""Tests for src/db/touched_pages.py.

Covers insert_touch idempotency, lookup by message / page, and the FK
cascade that removes touches when the parent message (or page) is deleted.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import psycopg
from src.db import messages as messages_repo
from src.db import touched_pages as repo
from src.db import wiki_pages as wiki_repo


def _mk_page(db_conn: psycopg.Connection, slug: str, page_type: str = "topic") -> int:
    return wiki_repo.upsert_wiki_page(
        db_conn,
        slug=slug,
        path=f"wiki/{page_type}s/{slug}.md",
        title=slug.title(),
        page_type=page_type,
    )


def _mk_message(db_conn: psycopg.Connection, message_id: str) -> None:
    messages_repo.insert_message(
        db_conn,
        message_id=message_id,
        raw_path=f"raw/{message_id}.md",
        thread_id="t-1",
        subject="s",
        from_address="a@b.c",
        date=datetime(2026, 4, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# insert_touch
# ---------------------------------------------------------------------------


def test_insert_touch_happy_path(db_conn: psycopg.Connection) -> None:
    _mk_message(db_conn, "m1")
    page_id = _mk_page(db_conn, "buylead")
    db_conn.commit()

    ok = repo.insert_touch(db_conn, message_id="m1", page_id=page_id)
    db_conn.commit()
    assert ok is True


def test_insert_touch_conflict_returns_false(db_conn: psycopg.Connection) -> None:
    _mk_message(db_conn, "m1")
    page_id = _mk_page(db_conn, "buylead")
    db_conn.commit()

    first = repo.insert_touch(db_conn, message_id="m1", page_id=page_id)
    second = repo.insert_touch(db_conn, message_id="m1", page_id=page_id)
    db_conn.commit()

    assert first is True
    assert second is False


# ---------------------------------------------------------------------------
# touches_for_message / touches_for_page
# ---------------------------------------------------------------------------


def test_touches_for_message_and_page_lookup(db_conn: psycopg.Connection) -> None:
    _mk_message(db_conn, "m1")
    _mk_message(db_conn, "m2")
    p1 = _mk_page(db_conn, "page-a")
    p2 = _mk_page(db_conn, "page-b")
    db_conn.commit()

    repo.insert_touch(db_conn, message_id="m1", page_id=p1)
    repo.insert_touch(db_conn, message_id="m1", page_id=p2)
    repo.insert_touch(db_conn, message_id="m2", page_id=p1)
    db_conn.commit()

    m1_touches = repo.touches_for_message("m1")
    assert {t["page_id"] for t in m1_touches} == {p1, p2}

    p1_touches = repo.touches_for_page(p1)
    assert {t["message_id"] for t in p1_touches} == {"m1", "m2"}


def test_touches_for_message_empty_for_unknown(db_conn: psycopg.Connection) -> None:
    assert repo.touches_for_message("ghost") == []
    assert repo.touches_for_page(99999) == []


# ---------------------------------------------------------------------------
# FK cascade — deleting the parent cleans up the join rows.
# ---------------------------------------------------------------------------


def test_delete_message_cascades_touches(db_conn: psycopg.Connection) -> None:
    _mk_message(db_conn, "m1")
    p1 = _mk_page(db_conn, "page-a")
    db_conn.commit()
    repo.insert_touch(db_conn, message_id="m1", page_id=p1)
    db_conn.commit()

    assert len(repo.touches_for_page(p1)) == 1

    db_conn.execute("DELETE FROM messages WHERE message_id = %s", ("m1",))
    db_conn.commit()

    assert repo.touches_for_page(p1) == []


def test_delete_page_cascades_touches(db_conn: psycopg.Connection) -> None:
    _mk_message(db_conn, "m1")
    p1 = _mk_page(db_conn, "page-a")
    db_conn.commit()
    repo.insert_touch(db_conn, message_id="m1", page_id=p1)
    db_conn.commit()

    assert len(repo.touches_for_message("m1")) == 1

    db_conn.execute("DELETE FROM wiki_pages WHERE page_id = %s", (p1,))
    db_conn.commit()

    assert repo.touches_for_message("m1") == []
