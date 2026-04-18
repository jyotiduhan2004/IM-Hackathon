"""Repository functions for the wiki_pages table.

One row per rendered wiki page. `slug` is the stem of the markdown file;
`path` is the repo-relative path ("wiki/people/alice.md" post v9-U5,
"wiki/entities/..." for unmigrated legacy rows); `page_type` mirrors the
on-disk folder (topic / person / system / ...; `entity` kept as legacy
alias until the shim is removed in #67).

`canonical_user_email` is populated for person pages (and legacy entity
pages) — see the partial unique index in schema.sql. Topic / system
pages leave it NULL.
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


def find_pages_by_slugs(
    conn: psycopg.Connection,
    slugs: list[str],
) -> dict[str, dict[str, Any]]:
    """Batch companion to ``find_by_slug``. Returns ``{slug: row}``.

    Used by scripts that resolve a page per markdown file — the
    per-slug variant opens a fresh connection on every call, so walking
    hundreds of pages costs hundreds of connections. This one takes a
    caller-supplied connection + runs one ``= ANY(%s)`` query per 500-
    slug chunk. Missing slugs are simply absent from the returned dict.
    """
    if not slugs:
        return {}
    unique_slugs = list(dict.fromkeys(slugs))
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(unique_slugs), 500):
        chunk = unique_slugs[i : i + 500]
        # connect() pins row_factory=dict_row; cast so mypy-strict
        # sees the real runtime shape instead of psycopg's tuple stub.
        rows = cast(
            "list[dict[str, Any]]",
            conn.execute(
                """
                SELECT page_id, slug, path, title, page_type, status,
                       canonical_user_email, last_compiled_at, update_count
                  FROM wiki_pages
                 WHERE slug = ANY(%s)
                """,
                (chunk,),
            ).fetchall(),
        )
        for row in rows:
            out[row["slug"]] = row
    return out


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
      4. slug contains the full query as a substring anywhere (so a
         multi-token query like "price-widget" beats single-token hits
         from the tokenised fallback patterns, even when the full
         substring appears mid-slug)
      5. any other match (token-only via the tokenised patterns, or
         title substring)
      6. within-tier: prefer `current`/`active` over superseded, then
         order by touch-count recency (pages actively being written
         sort before stale pages — Langfuse mining 2026-04-17 showed
         58% of resolve_page misses returned alphabetical/creation-
         order candidates because the previous `page_id ASC`
         tiebreaker ≈ alphabetical on batched inserts).

    The within-tier recency sort uses `message_touched_pages`: a LEFT
    JOIN onto the max `compiled_at` per page; pages never touched fall
    to the bottom (NULLS LAST), then `page_id ASC` as a final
    deterministic break.
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
    full_substring = f"%{query}%"
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT wp.slug, wp.title, wp.page_type, wp.path, wp.status
              FROM wiki_pages wp
              LEFT JOIN (
                SELECT page_id,
                       MAX(compiled_at) AS last_touched,
                       COUNT(*) AS touches
                  FROM message_touched_pages
                 GROUP BY page_id
              ) t ON t.page_id = wp.page_id
             WHERE wp.slug ILIKE ANY(%(patterns)s)
                OR wp.title ILIKE ANY(%(patterns)s)
             ORDER BY
               CASE WHEN wp.slug = %(q)s THEN 0
                    WHEN lower(wp.title) = lower(%(q)s) THEN 1
                    WHEN wp.slug ILIKE %(starts_with)s THEN 2
                    WHEN wp.slug ILIKE %(full_substring)s THEN 3
                    ELSE 4 END,
               (wp.status IN ('current', 'active')) DESC,
               t.last_touched DESC NULLS LAST,
               t.touches DESC NULLS LAST,
               wp.page_id ASC
             LIMIT %(limit)s
            """,
            {
                "q": query,
                "patterns": deduped_patterns,
                "starts_with": starts_with,
                "full_substring": full_substring,
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
    """Find a wiki page by slug, title, or canonical person email.

    Resolution order (first match wins):
      1. Exact slug match → confidence 1.0.
      2. Exact title match (case-insensitive) → confidence 0.9.
      3. Exact email match on person pages (legacy `entity` type included
         as a shim, retired in #67) → confidence 1.0.
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
            # "entity" kept as shim; retired in v9-U5 data migration, full
            # removal in #67. Accept both 'entity' (legacy) and 'person'
            # (post-migration canonical) so the resolver still finds any
            # unmigrated stragglers. Sort preference keeps legacy rows first
            # so resolver order stays deterministic while both coexist.
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
