"""Repository functions for the wiki_pages table.

One row per rendered wiki page. `slug` is the stem of the markdown file;
`path` is the repo-relative path ("wiki/entities/alice.md"); `page_type`
mirrors the on-disk folder (topic / entity / system / ...).

`canonical_user_email` is populated for entity and person pages — see
the partial unique index in schema.sql. During the C1 migration both
categories coexist; `person` is the target, `entity` is the legacy
alias. For topic / system pages we just leave it NULL.
"""

from __future__ import annotations

from typing import Any
from typing import cast

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


def search_pages(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Substring search over slug + title.

    Used by `resolve_page` to return candidates when the exact lookup
    misses — so the agent sees "you tried X, did you mean one of these
    five?" instead of blankly missing and retrying with variants.

    Ordering prioritises:
      1. exact slug match (redundant with lookup_page, but kept so the
         fallback chain is self-contained)
      2. case-insensitive exact title match
      3. slug starts-with the query
      4. any substring match on slug or title
      5. page_id tiebreaker for determinism across test runs

    `current` status beats `superseded`/`contested` at the same rank.
    """
    import re

    query = query.strip()
    if not query:
        return []
    # Tokenize so "whatsapp-lead-handoff" matches pages containing
    # "whatsapp" even when the exact full string doesn't appear
    # anywhere. Full-string substring stays the primary signal; tokens
    # are a fallback that widens recall on multi-word queries.
    tokens = [t for t in re.split(r"[-\s_]+", query) if len(t) >= 3]
    patterns = [f"%{query}%"] + [f"%{t}%" for t in tokens]
    # De-duplicate while preserving order so the exact-substring pattern
    # stays first (Postgres ILIKE ANY uses the array as a set of ORs).
    seen: set[str] = set()
    deduped_patterns: list[str] = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            deduped_patterns.append(p)
    starts_with = f"{query}%"
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT slug, title, page_type, path, status
              FROM wiki_pages
             WHERE slug ILIKE ANY(%(patterns)s)
                OR title ILIKE ANY(%(patterns)s)
             ORDER BY
               CASE WHEN slug = %(q)s THEN 0
                    WHEN lower(title) = lower(%(q)s) THEN 1
                    WHEN slug ILIKE %(starts_with)s THEN 2
                    ELSE 3 END,
               (status = 'current') DESC,
               page_id ASC
             LIMIT %(limit)s
            """,
            {
                "q": query,
                "patterns": deduped_patterns,
                "starts_with": starts_with,
                "limit": limit,
            },
        ).fetchall()
    return list(rows)


def lookup_page(
    *,
    slug: str | None = None,
    title: str | None = None,
    canonical_user_email: str | None = None,
) -> dict[str, Any] | None:
    """Find a wiki page by slug, title, or canonical entity email.

    Resolution order (first match wins):
      1. Exact slug match → confidence 1.0.
      2. Exact title match (case-insensitive) → confidence 0.9.
      3. Exact email match on entity pages → confidence 1.0.
      4. None if nothing matches.

    Raises ValueError if no lookup key is supplied.

    Returns:
        Dict with keys {slug, title, page_type, path, status, confidence} or None.
    """
    if slug is None and title is None and canonical_user_email is None:
        raise ValueError("lookup_page requires at least one of: slug, title, canonical_user_email")

    def _stamp(row: Any, confidence: float) -> dict[str, Any] | None:
        if row is None:
            return None
        row["confidence"] = confidence
        return row  # type: ignore[no-any-return]

    with connect() as conn:
        if slug is not None:
            hit = _stamp(
                conn.execute(
                    """
                    SELECT slug, title, page_type, path, status
                      FROM wiki_pages
                     WHERE slug = %s
                    """,
                    (slug,),
                ).fetchone(),
                1.0,
            )
            if hit is not None:
                return hit

        if title is not None:
            hit = _stamp(
                conn.execute(
                    """
                    SELECT slug, title, page_type, path, status
                      FROM wiki_pages
                     WHERE lower(title) = lower(%s)
                     ORDER BY (status = 'current') DESC,
                              last_compiled_at DESC NULLS LAST,
                              page_id ASC
                     LIMIT 1
                    """,
                    (title,),
                ).fetchone(),
                0.9,
            )
            if hit is not None:
                return hit

        if canonical_user_email is not None:
            # Accept both 'entity' (legacy) and 'person' (C1 migration) so
            # the resolver finds people-directory pages during the transition.
            # Prefer 'entity' matches so resolver order stays deterministic
            # while both categories coexist.
            hit = _stamp(
                conn.execute(
                    """
                    SELECT slug, title, page_type, path, status
                      FROM wiki_pages
                     WHERE page_type IN ('entity', 'person')
                       AND canonical_user_email = %s
                     ORDER BY (page_type = 'entity') DESC,
                              page_id ASC
                     LIMIT 1
                    """,
                    (canonical_user_email,),
                ).fetchone(),
                1.0,
            )
            if hit is not None:
                return hit

    return None


def count_wiki_pages_by_type() -> dict[str, int]:
    """Distribution of wiki pages by page_type — used by backfill smoke check."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT page_type, count(*)::int AS n FROM wiki_pages GROUP BY 1"
        ).fetchall()
    return {r["page_type"]: r["n"] for r in rows}


def find_active_topic_for_thread(thread_id: str) -> str | None:
    """Return the slug of an active topic already linked to `thread_id`, or None.

    Catalog-truth join: `message_touched_pages` → `messages` → `wiki_pages`,
    filtered to `page_type='topic'` and `status IN ('active', 'current')`.
    Legacy `current` is kept so unmigrated rows still register. Returns
    the first match — callers only need "exists or not".

    Used by `same_thread_topic_guard` middleware to detect the Codex
    2026-04-17 fragmentation pattern (one thread → two topic pages).
    """
    with connect() as conn:
        # connect() pins row_factory=dict_row; cast so mypy-strict sees
        # the real runtime shape instead of psycopg's generic tuple stub.
        row = cast(
            "dict[str, Any] | None",
            conn.execute(
                """
                SELECT DISTINCT wp.slug
                  FROM message_touched_pages mtp
                  JOIN messages m ON m.message_id = mtp.message_id
                  JOIN wiki_pages wp ON wp.page_id = mtp.page_id
                 WHERE m.thread_id = %s
                   AND wp.page_type = 'topic'
                   AND wp.status IN ('active', 'current')
                 LIMIT 1
                """,
                (thread_id,),
            ).fetchone(),
        )
    if row is None:
        return None
    return str(row["slug"])
