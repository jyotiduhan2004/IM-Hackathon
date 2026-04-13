"""Pytest fixtures for the messages catalog tests.

Isolation strategy — schema namespacing (not a separate database):
    The `email_kb_app` role typically lacks CREATEDB privilege on the local
    Postgres, so we can't spin up a throwaway `email_kb_test` database. Instead
    we create a dedicated schema `email_kb_test_schema` inside the production
    DB, recreate the messages table there (using the same DDL as
    src/db/schema.sql), and point every connection at it via `search_path`.

    Production `public.messages` is never touched: the test schema sits in
    front of `public` in the search path, so unqualified `INSERT INTO messages`
    from src/db/messages.py hits the test table. At session teardown we
    `DROP SCHEMA ... CASCADE`.

Why not transaction-rollback isolation:
    src.db.messages functions each open their own connection via
    `with connect() as conn`. A shared outer transaction can't rollback work
    done inside nested `conn.transaction()` blocks from claim_next_message,
    etc. A dedicated namespace is simpler and robust.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://email_kb_app:email_kb@localhost:5432/email_kb"
)
TEST_SCHEMA = "email_kb_test_schema"


def _load_messages_ddl() -> str:
    """Return DDL for the messages table, schema-qualified to TEST_SCHEMA.

    We don't just execute src/db/schema.sql as-is because it creates a
    global trigger function name (email_kb_set_updated_at) that would
    collide with production. We rewrite the whole block to be
    schema-local.
    """
    return f"""
    CREATE TABLE {TEST_SCHEMA}.messages (
      message_id        TEXT PRIMARY KEY,
      raw_path          TEXT NOT NULL UNIQUE,
      thread_id         TEXT,
      subject           TEXT,
      from_address      TEXT,
      date              TIMESTAMPTZ,
      compile_state     TEXT NOT NULL DEFAULT 'pending'
                        CHECK (compile_state IN
                          ('pending', 'claimed', 'compiled', 'failed')),
      compile_run_id    UUID,
      claimed_at        TIMESTAMPTZ,
      compiled_at       TIMESTAMPTZ,
      compile_attempts  INT NOT NULL DEFAULT 0,
      last_error        TEXT,
      is_compiled       BOOLEAN GENERATED ALWAYS AS
                        (compile_state = 'compiled') STORED,
      created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX messages_compile_queue_idx
      ON {TEST_SCHEMA}.messages (compile_state, claimed_at, date)
      WHERE compile_state IN ('pending', 'claimed', 'failed');

    CREATE INDEX messages_thread_date_idx
      ON {TEST_SCHEMA}.messages (thread_id, date);

    CREATE OR REPLACE FUNCTION {TEST_SCHEMA}.set_updated_at()
    RETURNS trigger AS $$
    BEGIN
      NEW.updated_at = now();
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER messages_set_updated_at
      BEFORE UPDATE ON {TEST_SCHEMA}.messages
      FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.set_updated_at();
    """


@pytest.fixture(scope="session", autouse=True)
def _test_schema() -> Iterator[None]:
    """Create the isolated test schema + messages table, drop at teardown."""
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
        admin.execute(_load_messages_ddl())

    try:
        yield
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")


@contextmanager
def _scoped_connect() -> Iterator[psycopg.Connection]:
    """Yield a dict_row connection with search_path pinned to the test schema.

    Matches src.db.connect's public contract — used as the monkeypatch target.

    We pass `options=-c search_path=...` at connect time rather than running
    a `SET` statement after connect. A `SET` in psycopg's default
    (autocommit=False) mode runs inside an implicit transaction that gets
    wiped out when the repo functions open their own `conn.transaction()`
    block, leaving the search_path at the default (public) and silently
    targeting the production table.
    """
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        options=f"-c search_path={TEST_SCHEMA},public",
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _redirect_connect_and_clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Repoint src.db.connect and clear the messages table before each test."""
    import src.db as db_pkg
    import src.db.messages as db_messages

    monkeypatch.setattr(db_pkg, "connect", _scoped_connect)
    monkeypatch.setattr(db_messages, "connect", _scoped_connect)

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"TRUNCATE TABLE {TEST_SCHEMA}.messages")

    yield


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    """Direct connection to the test schema for setup/inspection in tests."""
    with _scoped_connect() as conn:
        yield conn
