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
from types import ModuleType

import psycopg
import pytest
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests._script_loader import load_script  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://email_kb_app:email_kb@localhost:5432/email_kb"
)
TEST_SCHEMA = "email_kb_test_schema"


def _load_messages_ddl() -> str:
    """Return DDL for the full catalog (messages + users + threads +
    message_participants + compile_runs + ingest_cursors + wiki_pages +
    message_touched_pages), schema-qualified to TEST_SCHEMA.

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
                          ('pending', 'claimed', 'compiled', 'failed', 'skipped')),
      compile_run_id    UUID,
      claimed_at        TIMESTAMPTZ,
      compiled_at       TIMESTAMPTZ,
      compile_attempts  INT NOT NULL DEFAULT 0,
      last_error        TEXT,
      compile_model     TEXT,
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

    CREATE TABLE {TEST_SCHEMA}.users (
      email             TEXT PRIMARY KEY,
      display_name      TEXT,
      first_seen_at     TIMESTAMPTZ,
      last_seen_at      TIMESTAMPTZ,
      created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TRIGGER users_set_updated_at
      BEFORE UPDATE ON {TEST_SCHEMA}.users
      FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.set_updated_at();

    CREATE TABLE {TEST_SCHEMA}.threads (
      thread_id         TEXT PRIMARY KEY,
      first_message_at  TIMESTAMPTZ,
      last_message_at   TIMESTAMPTZ,
      message_count     INT NOT NULL DEFAULT 0,
      created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TRIGGER threads_set_updated_at
      BEFORE UPDATE ON {TEST_SCHEMA}.threads
      FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.set_updated_at();

    CREATE TABLE {TEST_SCHEMA}.message_participants (
      message_id        TEXT NOT NULL
                        REFERENCES {TEST_SCHEMA}.messages(message_id)
                        ON DELETE CASCADE,
      user_email        TEXT NOT NULL
                        REFERENCES {TEST_SCHEMA}.users(email),
      role              TEXT NOT NULL CHECK (role IN ('from', 'to', 'cc')),
      display_name      TEXT,
      PRIMARY KEY (message_id, user_email, role)
    );

    CREATE INDEX message_participants_user_role_idx
      ON {TEST_SCHEMA}.message_participants (user_email, role);

    CREATE TABLE {TEST_SCHEMA}.compile_runs (
      run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      finished_at TIMESTAMPTZ,
      model TEXT,
      status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed','killed')),
      emails_processed INT NOT NULL DEFAULT 0,
      emails_failed INT NOT NULL DEFAULT 0,
      cost_cents INT,
      notes TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX compile_runs_started_idx
      ON {TEST_SCHEMA}.compile_runs (started_at DESC);

    CREATE TRIGGER compile_runs_set_updated_at
      BEFORE UPDATE ON {TEST_SCHEMA}.compile_runs
      FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.set_updated_at();

    CREATE TABLE {TEST_SCHEMA}.ingest_cursors (
      cursor_name TEXT PRIMARY KEY,
      history_id  TEXT NOT NULL,
      updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TRIGGER ingest_cursors_set_updated_at
      BEFORE UPDATE ON {TEST_SCHEMA}.ingest_cursors
      FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.set_updated_at();

    CREATE TABLE {TEST_SCHEMA}.wiki_pages (
      page_id               BIGSERIAL PRIMARY KEY,
      slug                  TEXT NOT NULL UNIQUE,
      path                  TEXT NOT NULL UNIQUE,
      title                 TEXT NOT NULL,
      -- Mirrors post-migration shape in src/db/schema.sql.
      page_type             TEXT NOT NULL
                            CHECK (page_type IN
                              ('topic', 'entity', 'system', 'policy',
                               'timeline', 'conflict',
                               'domain', 'glossary', 'decision', 'person',
                               'home', 'changes')),
      status                TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN
                              ('current', 'superseded', 'contested',
                               'active', 'archived')),
      canonical_user_email  TEXT REFERENCES {TEST_SCHEMA}.users(email),
      last_compiled_at      TIMESTAMPTZ,
      update_count          INT NOT NULL DEFAULT 0,
      created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE UNIQUE INDEX wiki_pages_entity_email_uidx
      ON {TEST_SCHEMA}.wiki_pages (canonical_user_email)
      WHERE page_type = 'entity' AND canonical_user_email IS NOT NULL;

    CREATE TRIGGER wiki_pages_set_updated_at
      BEFORE UPDATE ON {TEST_SCHEMA}.wiki_pages
      FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.set_updated_at();

    CREATE TABLE {TEST_SCHEMA}.message_touched_pages (
      message_id        TEXT NOT NULL
                        REFERENCES {TEST_SCHEMA}.messages(message_id)
                        ON DELETE CASCADE,
      page_id           BIGINT NOT NULL
                        REFERENCES {TEST_SCHEMA}.wiki_pages(page_id)
                        ON DELETE CASCADE,
      compiled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (message_id, page_id)
    );

    CREATE INDEX message_touched_pages_page_idx
      ON {TEST_SCHEMA}.message_touched_pages (page_id, compiled_at DESC);

    CREATE TABLE {TEST_SCHEMA}.compile_tool_calls (
      id bigserial PRIMARY KEY,
      run_id uuid REFERENCES {TEST_SCHEMA}.compile_runs(run_id) ON DELETE CASCADE,
      tool_name text NOT NULL,
      inputs_json jsonb,
      output_preview varchar(500),
      output_bytes int,
      latency_ms int,
      status text CHECK (status IN ('ok', 'error', 'abandoned')),
      error_message text,
      started_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz
    );

    CREATE INDEX compile_tool_calls_run_id_idx
      ON {TEST_SCHEMA}.compile_tool_calls(run_id);
    CREATE INDEX compile_tool_calls_tool_started_idx
      ON {TEST_SCHEMA}.compile_tool_calls(tool_name, started_at DESC);

    CREATE TABLE {TEST_SCHEMA}.compile_insights (
      id bigserial PRIMARY KEY,
      run_id uuid REFERENCES {TEST_SCHEMA}.compile_runs(run_id) ON DELETE CASCADE,
      category text CHECK (category IN (
        'topic_merge_candidate',
        'question_for_human',
        'prompt_ambiguity',
        'tool_gap',
        'supersession_doubt',
        'structure_suggestion',
        'trivial_skip',
        'already_captured',
        'insufficient_decision'
      )),
      message text NOT NULL,
      email_path text,
      suggested_action text,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE INDEX compile_insights_run_id_idx
      ON {TEST_SCHEMA}.compile_insights(run_id);
    CREATE INDEX compile_insights_category_created_idx
      ON {TEST_SCHEMA}.compile_insights(category, created_at DESC);

    CREATE TABLE {TEST_SCHEMA}.compile_attempts (
      id              bigserial PRIMARY KEY,
      message_id      text NOT NULL
                      REFERENCES {TEST_SCHEMA}.messages(message_id)
                      ON DELETE CASCADE,
      run_id          uuid
                      REFERENCES {TEST_SCHEMA}.compile_runs(run_id)
                      ON DELETE CASCADE,
      compile_model   text,
      outcome         text CHECK (outcome IN ('compiled', 'failed', 'timeout', 'skipped')),
      error           text,
      attempted_at    timestamptz NOT NULL DEFAULT now(),
      finished_at     timestamptz
    );

    CREATE INDEX compile_attempts_health_stats_idx
      ON {TEST_SCHEMA}.compile_attempts (compile_model, attempted_at DESC)
      WHERE compile_model IS NOT NULL AND finished_at IS NOT NULL;
    CREATE INDEX compile_attempts_message_idx
      ON {TEST_SCHEMA}.compile_attempts (message_id);
    CREATE INDEX compile_attempts_run_idx
      ON {TEST_SCHEMA}.compile_attempts (run_id);

    CREATE TABLE {TEST_SCHEMA}.page_feedback (
      id              BIGSERIAL PRIMARY KEY,
      run_id          UUID NOT NULL,
      page_slug       TEXT NOT NULL,
      page_version    TEXT NOT NULL,
      source          TEXT NOT NULL,
      score           NUMERIC,
      finding         TEXT NOT NULL,
      severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'blocker')),
      captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
      captured_by     TEXT NOT NULL,
      raw_json        JSONB NOT NULL DEFAULT '{{}}'::jsonb
    );

    -- Composite index matches the 202604231200 migration: it supports the
    -- DISTINCT ON (source) / ORDER BY source, captured_at DESC query in
    -- list_recent_feedback_for_page. The old page_slug-only index was a
    -- redundant prefix and got dropped in prod.
    CREATE INDEX page_feedback_slug_source_idx
      ON {TEST_SCHEMA}.page_feedback (page_slug, source, captured_at DESC);
    CREATE INDEX page_feedback_source_idx
      ON {TEST_SCHEMA}.page_feedback (source, captured_at DESC);
    CREATE INDEX page_feedback_run_id_idx
      ON {TEST_SCHEMA}.page_feedback (run_id);
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
    """Repoint src.db.connect and clear all catalog tables before each test.

    Also resets pydantic-settings-driven flags (qmd, etc.) to their
    declared defaults so tests don't see the developer's .env values
    (USE_SEMANTIC_RESOLVE=1 etc.).
    """
    from src.config import settings as _settings

    monkeypatch.setattr(_settings, "use_semantic_resolve", False)
    monkeypatch.setattr(_settings, "qmd_timeout_s", 45)

    import src.db as db_pkg
    import src.db.compile_runs as db_compile_runs
    import src.db.cursors as db_cursors
    import src.db.insights as db_insights
    import src.db.messages as db_messages
    import src.db.participants as db_participants
    import src.db.threads as db_threads
    import src.db.tool_call_log as db_tool_call_log
    import src.db.touched_pages as db_touched_pages
    import src.db.users as db_users
    import src.db.wiki_pages as db_wiki_pages

    monkeypatch.setattr(db_pkg, "connect", _scoped_connect)
    monkeypatch.setattr(db_messages, "connect", _scoped_connect)
    monkeypatch.setattr(db_users, "connect", _scoped_connect)
    monkeypatch.setattr(db_threads, "connect", _scoped_connect)
    monkeypatch.setattr(db_participants, "connect", _scoped_connect)
    monkeypatch.setattr(db_compile_runs, "connect", _scoped_connect)
    monkeypatch.setattr(db_cursors, "connect", _scoped_connect)
    monkeypatch.setattr(db_wiki_pages, "connect", _scoped_connect)
    monkeypatch.setattr(db_touched_pages, "connect", _scoped_connect)
    monkeypatch.setattr(db_tool_call_log, "connect", _scoped_connect)
    monkeypatch.setattr(db_insights, "connect", _scoped_connect)

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        # message_participants + message_touched_pages + compile_attempts
        # → messages via FK; truncate together so we don't have to think
        # about delete order. compile_runs and ingest_cursors have no FK
        # back to messages so they get their own truncates. compile_attempts
        # also FKs compile_runs, so it's named in both statements below so
        # Postgres doesn't complain about dangling dependencies either way.
        conn.execute(
            f"TRUNCATE TABLE {TEST_SCHEMA}.message_touched_pages, "
            f"{TEST_SCHEMA}.message_participants, "
            f"{TEST_SCHEMA}.compile_attempts, "
            f"{TEST_SCHEMA}.wiki_pages, "
            f"{TEST_SCHEMA}.messages, {TEST_SCHEMA}.users, "
            f"{TEST_SCHEMA}.threads CASCADE"
        )
        # compile_tool_calls + compile_insights + compile_attempts all have
        # FKs to compile_runs — Postgres requires all dependent tables in
        # one TRUNCATE statement. compile_attempts is also named in the
        # earlier messages-rooted TRUNCATE; either order keeps the FK happy.
        conn.execute(
            f"TRUNCATE TABLE "
            f"{TEST_SCHEMA}.compile_tool_calls, "
            f"{TEST_SCHEMA}.compile_insights, "
            f"{TEST_SCHEMA}.compile_attempts, "
            f"{TEST_SCHEMA}.compile_runs"
        )
        conn.execute(f"TRUNCATE TABLE {TEST_SCHEMA}.ingest_cursors")
        # page_feedback has no FK to any other table — slug-keyed on purpose
        # (see src/db/page_feedback.py) — so it gets its own truncate.
        conn.execute(f"TRUNCATE TABLE {TEST_SCHEMA}.page_feedback")

    yield


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    """Direct connection to the test schema for setup/inspection in tests."""
    with _scoped_connect() as conn:
        yield conn


@pytest.fixture
def compile_all_module() -> ModuleType:
    """scripts/compile_all.py loaded as a module for white-box testing."""
    return load_script("compile_all")


@pytest.fixture
def mini_wiki(tmp_path: Path) -> Path:
    """Empty wiki tree pre-seeded with the full set of category subdirs.

    Includes every category any test has needed (topics, entities, people,
    systems, policies, timelines, conflicts) so a single fixture serves the
    whole `test_format_wiki` + `test_validate_wiki_*` family without
    per-test overrides. Extra empty dirs are irrelevant to validators that
    only scan specific categories.
    """
    wiki = tmp_path / "wiki"
    for cat in ("topics", "entities", "people", "systems", "policies", "timelines", "conflicts"):
        (wiki / cat).mkdir(parents=True)
    return wiki
