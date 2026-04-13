"""Postgres connection helpers — sync psycopg, single-process model.

The compile pipeline is single-process and not latency-sensitive at the
DB layer, so a simple connect-per-unit-of-work is enough. Add a pool
when the FastAPI service starts hitting these paths.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from src.config import settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a connection scoped to a single unit of work.

    Defaults to dict_row so callers get dict[str, Any] rows — easier to
    return from agent tools that need JSON-shaped output.
    """
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()
