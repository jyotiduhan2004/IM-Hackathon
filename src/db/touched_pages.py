"""Repository functions for message_touched_pages — (message, page) join.

Records which messages contributed to which wiki pages during compile.
Composite primary key (message_id, page_id) makes ON CONFLICT DO NOTHING
the natural idempotency guarantee — re-compiling a message onto the same
page keeps a single (message, page) row, not a flurry of duplicates.
"""

from __future__ import annotations

from typing import Any

import psycopg

from src.db import connect


def insert_touch(
    conn: psycopg.Connection,
    *,
    message_id: str,
    page_id: int,
) -> bool:
    """Insert one (message_id, page_id) touch. Returns True when actually inserted.

    Idempotent: re-running over the same pair is a no-op. `compiled_at`
    defaults to `now()` and stays on the first insert — we don't refresh it
    on conflict, so the row records the *first* time this message touched
    this page.
    """
    cur = conn.execute(
        """
        INSERT INTO message_touched_pages (message_id, page_id)
        VALUES (%s, %s)
        ON CONFLICT (message_id, page_id) DO NOTHING
        RETURNING message_id
        """,
        (message_id, page_id),
    )
    return cur.fetchone() is not None


def touches_for_message(message_id: str) -> list[dict[str, Any]]:
    """Pages this message touched, most recent first."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT message_id, page_id, compiled_at
              FROM message_touched_pages
             WHERE message_id = %s
             ORDER BY compiled_at DESC, page_id ASC
            """,
            (message_id,),
        ).fetchall()


def touches_for_page(page_id: int) -> list[dict[str, Any]]:
    """Messages that touched this page, most recent first."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT message_id, page_id, compiled_at
              FROM message_touched_pages
             WHERE page_id = %s
             ORDER BY compiled_at DESC, message_id ASC
            """,
            (page_id,),
        ).fetchall()


def get_sources_for_slug(slug: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return email sources that touched this wiki page, newest first.

    Joins `message_touched_pages` → `messages` via `wiki_pages.slug` so the
    viewer can render a "Sources" block without reading the `sources:` list
    from the page's frontmatter. Each row carries the fields the hook needs
    to render a collapsible block: `raw_path`, `subject`, `date`,
    `from_address`. Newest-first ordering matches the entity-page "show
    recent" convention.

    Unknown slug → empty list (not an error). Caller decides whether to
    fall back to frontmatter.
    """
    with connect() as conn:
        return conn.execute(
            """
            SELECT m.raw_path,
                   m.subject,
                   m.date,
                   m.from_address
              FROM message_touched_pages mtp
              JOIN messages m ON m.message_id = mtp.message_id
              JOIN wiki_pages wp ON wp.page_id = mtp.page_id
             WHERE wp.slug = %s
             ORDER BY m.date DESC NULLS LAST, m.message_id ASC
             LIMIT %s
            """,
            (slug, limit),
        ).fetchall()
