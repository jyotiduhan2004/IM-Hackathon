-- email_kb catalog — initial slice (messages-only)
--
-- Replaces raw-frontmatter-as-queue. Other tables (users, threads,
-- wiki_pages, external_references, attachments) come in follow-up PRs.
-- See docs/reviews/codex-catalog-review-20260413T080000Z.md for the
-- full target schema and the rationale for shipping this slice first.

CREATE TABLE IF NOT EXISTS messages (
  message_id        TEXT PRIMARY KEY,
  raw_path          TEXT NOT NULL UNIQUE,
  thread_id         TEXT,
  subject           TEXT,
  from_address      TEXT,
  date              TIMESTAMPTZ,

  -- Compile queue state machine
  compile_state     TEXT NOT NULL DEFAULT 'pending'
                    CHECK (compile_state IN ('pending', 'claimed', 'compiled', 'failed')),
  compile_run_id    UUID,
  claimed_at        TIMESTAMPTZ,
  compiled_at       TIMESTAMPTZ,
  compile_attempts  INT NOT NULL DEFAULT 0,
  last_error        TEXT,

  -- Convenience: derived view of compile_state
  is_compiled       BOOLEAN GENERATED ALWAYS AS (compile_state = 'compiled') STORED,

  -- Audit
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Queue scan path: only the live states matter for resume.
CREATE INDEX IF NOT EXISTS messages_compile_queue_idx
  ON messages (compile_state, claimed_at, date)
  WHERE compile_state IN ('pending', 'claimed', 'failed');

-- Thread navigation — cheap to add now, used by the future thread roll-up.
CREATE INDEX IF NOT EXISTS messages_thread_date_idx
  ON messages (thread_id, date);

-- Auto-bump updated_at on UPDATE.
CREATE OR REPLACE FUNCTION email_kb_set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS messages_set_updated_at ON messages;
CREATE TRIGGER messages_set_updated_at
  BEFORE UPDATE ON messages
  FOR EACH ROW EXECUTE FUNCTION email_kb_set_updated_at();


-- ---------------------------------------------------------------------------
-- PR2: users + threads + message_participants
--
-- One row per distinct email address (users), one row per Gmail thread_id
-- (threads), and a many-to-many join (message_participants) capturing the
-- from/to/cc role for every (message, user) pairing. Lets us answer
-- "who emails about X", "list a user's threads", and "what's in a thread"
-- without rescanning raw frontmatter on every query.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
  email             TEXT PRIMARY KEY,
  display_name      TEXT,
  first_seen_at     TIMESTAMPTZ,
  last_seen_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS users_set_updated_at ON users;
CREATE TRIGGER users_set_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION email_kb_set_updated_at();


CREATE TABLE IF NOT EXISTS threads (
  thread_id         TEXT PRIMARY KEY,
  first_message_at  TIMESTAMPTZ,
  last_message_at   TIMESTAMPTZ,
  message_count     INT NOT NULL DEFAULT 0,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS threads_set_updated_at ON threads;
CREATE TRIGGER threads_set_updated_at
  BEFORE UPDATE ON threads
  FOR EACH ROW EXECUTE FUNCTION email_kb_set_updated_at();


CREATE TABLE IF NOT EXISTS message_participants (
  message_id        TEXT NOT NULL
                    REFERENCES messages(message_id) ON DELETE CASCADE,
  user_email        TEXT NOT NULL REFERENCES users(email),
  role              TEXT NOT NULL CHECK (role IN ('from', 'to', 'cc')),
  display_name      TEXT,
  PRIMARY KEY (message_id, user_email, role)
);

-- "What did this user send/receive?" is the dominant access pattern.
CREATE INDEX IF NOT EXISTS message_participants_user_role_idx
  ON message_participants (user_email, role);


-- ---------------------------------------------------------------------------
-- PR5b: compile_runs — one row per `scripts/compile_all.py` invocation.
--
-- Gives run-level observability (cost, counts, status) without the
-- `.watch_state.json`-style sidecar files that Codex flagged.
-- See docs/reviews/codex-priority-review-20260413T090000Z.md §1 PR5.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS compile_runs (
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
CREATE INDEX IF NOT EXISTS compile_runs_started_idx ON compile_runs (started_at DESC);
DROP TRIGGER IF EXISTS compile_runs_set_updated_at ON compile_runs;
CREATE TRIGGER compile_runs_set_updated_at BEFORE UPDATE ON compile_runs
  FOR EACH ROW EXECUTE FUNCTION email_kb_set_updated_at();


-- ---------------------------------------------------------------------------
-- PR5a: ingest_cursors — durable replacement for .watch_state.json. One row
-- per named ingest loop (gmail_history today; more sources later).
-- history_id is whatever resume token the source API exposes (Gmail
-- historyId, or an ISO timestamp for the current poll-by-date watcher).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_cursors (
  cursor_name TEXT PRIMARY KEY,
  history_id  TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS ingest_cursors_set_updated_at ON ingest_cursors;
CREATE TRIGGER ingest_cursors_set_updated_at
  BEFORE UPDATE ON ingest_cursors
  FOR EACH ROW EXECUTE FUNCTION email_kb_set_updated_at();
