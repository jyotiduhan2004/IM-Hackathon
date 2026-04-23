"""Repository for page_feedback — append-only scorer/judge/human findings.

One row per (page_slug, source, captured_at). The heuristic scorer, each
LLM-judge persona, and human reviewers all write here; readers compose
the latest-per-source view at query time.

Lifecycle: append-only. No UPDATEs, no DELETEs — newer rows supersede
older ones for "latest" lookups, older rows stay as history. A scorer
re-run over the same page simply inserts a fresh row for
`source='scorer'`; the previous row survives so we can chart score
drift over time.

Slug-keyed, not FK'd: slugs rename/merge as the wiki reorganises and we
don't want cascade-deletes wiping feedback history when a page moves.
`page_version` (frontmatter `last_compiled` ISO) is stored alongside so
readers can tell which compile produced the feedback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

Severity = Literal["info", "warning", "blocker"]

_SELECT_COLS = (
    "id, run_id, page_slug, page_version, source, score, "
    "finding, severity, captured_at, captured_by, raw_json"
)


def insert_feedback(
    conn: psycopg.Connection,
    *,
    run_id: UUID,
    page_slug: str,
    page_version: str,
    source: str,
    score: float | None,
    finding: str,
    severity: Severity,
    captured_by: str,
    raw_json: dict[str, Any],
) -> int:
    """Append one feedback row. Returns the new row id."""
    row = conn.execute(
        """
        INSERT INTO page_feedback (
          run_id, page_slug, page_version, source, score,
          finding, severity, captured_by, raw_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id
        """,
        (
            run_id,
            page_slug,
            page_version,
            source,
            score,
            finding,
            severity,
            captured_by,
            Jsonb(raw_json),
        ),
    ).fetchone()
    return int(row["id"]) if row else 0


def list_recent_feedback_for_page(
    conn: psycopg.Connection,
    *,
    page_slug: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return the latest row per source for this page, newest first.

    `DISTINCT ON (source)` collapses to one row per source so a page that
    was scored many times by `source='scorer'` doesn't drown out the
    judge/human rows. Sort is stable: source ascending for the DISTINCT
    ON bucketing, then captured_at descending to pick the latest within
    each bucket.
    """
    return conn.execute(
        f"""
        SELECT DISTINCT ON (source) {_SELECT_COLS}
          FROM page_feedback
         WHERE page_slug = %s
         ORDER BY source, captured_at DESC
         LIMIT %s
        """,
        (page_slug, limit),
    ).fetchall()


def list_feedback_by_run(
    conn: psycopg.Connection,
    *,
    run_id: UUID,
) -> list[dict[str, Any]]:
    """Return all rows for a scorer/judge run, ordered by page_slug then source."""
    return conn.execute(
        f"""
        SELECT {_SELECT_COLS}
          FROM page_feedback
         WHERE run_id = %s
         ORDER BY page_slug ASC, source ASC
        """,
        (run_id,),
    ).fetchall()


def list_recent_feedback_by_source(
    conn: psycopg.Connection,
    *,
    source: str,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return recent rows from one source, newest first. `since` is a hard floor."""
    return conn.execute(
        f"""
        SELECT {_SELECT_COLS}
          FROM page_feedback
         WHERE source = %s
           AND (%s::timestamptz IS NULL OR captured_at >= %s)
         ORDER BY captured_at DESC
         LIMIT %s
        """,
        (source, since, since, limit),
    ).fetchall()
