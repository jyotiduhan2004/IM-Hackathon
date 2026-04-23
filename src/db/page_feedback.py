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
    """Append one feedback row. Returns the new row id.

    Raises ``RuntimeError`` if the INSERT RETURNING surprises us with zero
    rows — that shouldn't be possible for a row that just landed, so a
    silent ``return 0`` would only mask a driver bug or concurrent DDL.
    The ``Jsonb()`` adapter handles type coercion; no explicit ``::jsonb``
    cast is needed in the SQL.
    """
    row = conn.execute(
        """
        INSERT INTO page_feedback (
          run_id, page_slug, page_version, source, score,
          finding, severity, captured_by, raw_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    if row is None:
        raise RuntimeError("INSERT INTO page_feedback returned no row")
    return int(row["id"])


def list_recent_feedback_for_page(
    conn: psycopg.Connection,
    *,
    page_slug: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the latest row per source for this page, newest first.

    `DISTINCT ON (source)` collapses to one row per source so a page that
    was scored many times by `source='scorer'` doesn't drown out the
    judge/human rows. Sort is stable: source ascending for the DISTINCT
    ON bucketing, then captured_at descending to pick the latest within
    each bucket.

    Default ``limit=10`` because we already expect 5 distinct sources
    (``scorer``, ``judge-newbie``, ``judge-pm``, ``judge-ia``, ``human``)
    and future personas would push that past the old ``limit=3`` — silent
    truncation hides signal from callers aggregating across sources.
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
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return all rows for a scorer/judge run, ordered by page_slug then source.

    Default ``limit=1000`` protects callers from pulling 500+ pages x N
    findings per page into memory without opting in. Bump it explicitly if
    a full scorer run needs reading (the CSV/markdown outputs are the
    intended bulk-export path; this repo call is for UI inspection).
    """
    return conn.execute(
        f"""
        SELECT {_SELECT_COLS}
          FROM page_feedback
         WHERE run_id = %s
         ORDER BY page_slug ASC, source ASC
         LIMIT %s
        """,
        (run_id, limit),
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
