"""Repository functions for the wiki_pages table.

One row per rendered wiki page. `slug` is the stem of the markdown file;
`path` is the repo-relative path ("wiki/entities/alice.md"); `page_type`
mirrors the on-disk folder (topic / entity / system / ...).

`canonical_user_email` is populated only for entity pages — see the
partial unique index in schema.sql. For topic / system pages we just
leave it NULL.
"""

from __future__ import annotations

from typing import Any

import psycopg

from src.db import connect


def upsert_wiki_page(
    conn: psycopg.Connection,
    *,
    slug: str,
    path: str,
    title: str,
    page_type: str,
    status: str = "current",
    canonical_user_email: str | None = None,
) -> int:
    """Insert or refresh a wiki_pages row. Returns the page_id.

    Uses slug as the conflict key — on re-running the backfill we overwrite
    title/path/page_type/status/canonical_user_email with the latest values
    from disk. update_count / last_compiled_at stay untouched; those are
    bumped by the compiler when a message actually touches the page.
    """
    cur = conn.execute(
        """
        INSERT INTO wiki_pages (
          slug, path, title, page_type, status, canonical_user_email
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE
          SET title = EXCLUDED.title,
              path = EXCLUDED.path,
              page_type = EXCLUDED.page_type,
              status = EXCLUDED.status,
              canonical_user_email = EXCLUDED.canonical_user_email
        RETURNING page_id
        """,
        (slug, path, title, page_type, status, canonical_user_email),
    )
    row = cur.fetchone()
    assert row is not None, "INSERT ... RETURNING must yield a row"
    return int(row["page_id"])


def find_by_slug(slug: str) -> dict[str, Any] | None:
    """Lookup a wiki_pages row by slug. Returns None when unknown."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT page_id, slug, path, title, page_type, status,
                   canonical_user_email, last_compiled_at, update_count
              FROM wiki_pages
             WHERE slug = %s
            """,
            (slug,),
        ).fetchone()


def count_wiki_pages_by_type() -> dict[str, int]:
    """Distribution of wiki pages by page_type — used by backfill smoke check."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT page_type, count(*)::int AS n FROM wiki_pages GROUP BY 1"
        ).fetchall()
    return {r["page_type"]: r["n"] for r in rows}
