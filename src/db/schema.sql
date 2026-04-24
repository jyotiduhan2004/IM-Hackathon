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
  -- 'skipped' added on 2026-04-16 via src/db/migrations/202604160000_add_skipped_state.sql
  -- for trivial-filter matches (acks / calendar noise / short tangential replies).
  compile_state     TEXT NOT NULL DEFAULT 'pending'
                    CHECK (compile_state IN ('pending', 'claimed', 'compiled', 'failed', 'skipped')),
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


-- ---------------------------------------------------------------------------
-- PR3: wiki_pages + message_touched_pages
--
-- One row per rendered wiki page (topic / entity / system / policy /
-- timeline / conflict), plus a many-to-many join recording which messages
-- "touched" (contributed to) each page during compile. Lets us answer
-- "which pages did message X land on?" and "which messages does page Y
-- cite?" without grepping frontmatter on every query.
--
-- `canonical_user_email` ties entity pages to a users row; the partial
-- unique index enforces "one entity page per email" while leaving topic /
-- system / policy pages free to share emails (e.g. a topic page about a
-- person's project can coexist with their entity page).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wiki_pages (
  page_id               BIGSERIAL PRIMARY KEY,
  slug                  TEXT NOT NULL UNIQUE,
  path                  TEXT NOT NULL UNIQUE,
  title                 TEXT NOT NULL,
  -- Legacy + North-Star ontology coexist during migration; widened on
  -- 2026-04-16 via src/db/migrations/202604161200_wiki_pages_new_ontology.sql.
  -- See docs/NORTH-STAR.md.
  page_type             TEXT NOT NULL
                        CHECK (page_type IN
                          ('topic', 'entity', 'system', 'policy',
                           'timeline', 'conflict',
                           'domain', 'glossary', 'decision', 'person',
                           'home', 'changes',
                           'coordinator_notes')),
  -- Default flipped from 'current' → 'active' on 2026-04-16 via
  -- src/db/migrations/202604162000_wiki_pages_default_status_active.sql.
  -- CHECK still accepts the legacy triplet so reads of un-migrated rows
  -- keep working; new writes land on the North-Star value.
  status                TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN
                          ('current', 'superseded', 'contested',
                           'active', 'archived')),
  canonical_user_email  TEXT REFERENCES users(email),
  last_compiled_at      TIMESTAMPTZ,
  update_count          INT NOT NULL DEFAULT 0,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "One entity page per email address" — topic / system pages can reuse
-- the same email without conflict.
CREATE UNIQUE INDEX IF NOT EXISTS wiki_pages_entity_email_uidx
  ON wiki_pages (canonical_user_email)
  WHERE page_type = 'entity' AND canonical_user_email IS NOT NULL;

DROP TRIGGER IF EXISTS wiki_pages_set_updated_at ON wiki_pages;
CREATE TRIGGER wiki_pages_set_updated_at
  BEFORE UPDATE ON wiki_pages
  FOR EACH ROW EXECUTE FUNCTION email_kb_set_updated_at();


CREATE TABLE IF NOT EXISTS message_touched_pages (
  message_id        TEXT NOT NULL
                    REFERENCES messages(message_id) ON DELETE CASCADE,
  page_id           BIGINT NOT NULL
                    REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
  compiled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, page_id)
);

-- "Which messages recently touched page X?" — the timeline-on-page lookup.
CREATE INDEX IF NOT EXISTS message_touched_pages_page_idx
  ON message_touched_pages (page_id, compiled_at DESC);


-- ---------------------------------------------------------------------------
-- Per-message model A/B tracking (2026-04-13)
--
-- compile_all.py picks one model per batch from settings.model_pool;
-- finish_message_compile stamps it here so we can join model → outcome.
-- Existing rows pre-A/B get NULL — fine; only future compiles populate.
-- ---------------------------------------------------------------------------

ALTER TABLE messages ADD COLUMN IF NOT EXISTS compile_model TEXT;


-- ---------------------------------------------------------------------------
-- Per-tool-call telemetry (2026-04-13)
--
-- BatchStatsCallback only aggregates tool-call COUNT. This table records one
-- row per tool invocation so we can answer "which tool is slowest / most
-- error-prone / called most often" per run. Written from
-- `src/compile/tool_call_log.py` after every batch; JSONL fallback under
-- `docs/audits/` when the DB is unreachable.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS compile_tool_calls (
  id bigserial PRIMARY KEY,
  run_id uuid REFERENCES compile_runs(run_id) ON DELETE CASCADE,
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

CREATE INDEX IF NOT EXISTS compile_tool_calls_run_id_idx
  ON compile_tool_calls(run_id);
CREATE INDEX IF NOT EXISTS compile_tool_calls_tool_started_idx
  ON compile_tool_calls(tool_name, started_at DESC);


-- ---------------------------------------------------------------------------
-- compile_insights — structured meta-observations emitted by the agent
-- during a compile run. The agent has no other channel to say "this is
-- ambiguous" or "two pages look like they should merge"; this table is
-- that channel. See `src/compile/compiler.py::log_insight`.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS compile_insights (
  id bigserial PRIMARY KEY,
  run_id uuid REFERENCES compile_runs(run_id) ON DELETE CASCADE,
  category text CHECK (category IN (
    'topic_merge_candidate',
    'question_for_human',
    'prompt_ambiguity',
    'tool_gap',
    'supersession_doubt',
    'structure_suggestion',
    -- 'trivial_skip' added 2026-04-16 (PR #126) — non-substantive emails
    -- (one-line confirmations, OOO auto-replies). Drift caught by Cycle 1.
    'trivial_skip',
    -- 'already_captured' added 2026-04-17 (PR #128) — substantive emails
    -- whose content is already merged into the existing topic page from a
    -- prior thread message; agent should log this rather than force an
    -- empty edit. Distinct semantics from 'trivial_skip'.
    'already_captured',
    -- 'insufficient_decision' added 2026-04-23 (V12 audit fix-C) —
    -- substantive email, NOT captured elsewhere, no obvious target
    -- page. Terminal outcome so the email flips to `skipped` instead
    -- of being re-queued; reason lives in `last_error` for human
    -- triage. Pairs with terminal_decision_guard middleware.
    'insufficient_decision'
  )),
  message text NOT NULL,
  email_path text,
  suggested_action text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS compile_insights_run_id_idx ON compile_insights(run_id);
CREATE INDEX IF NOT EXISTS compile_insights_category_created_idx
  ON compile_insights(category, created_at DESC);


-- ---------------------------------------------------------------------------
-- compile_attempts — append-only event log of every model invocation.
-- Replaces the lossy `messages.compile_model` field which gets overwritten
-- by COALESCE on retry. Used by the run-start `_healthy_pool` guard in
-- scripts/compile_all.py to auto-exclude consistently-failing models.
--
-- Rows are written at batch dispatch (attempt start) so orphaned claims
-- (worker died mid-batch) and stale-reclaims stay visible; `outcome` and
-- `finished_at` are NULL while the attempt is in-flight.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS compile_attempts (
  id              bigserial PRIMARY KEY,
  message_id      text NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
  run_id          uuid REFERENCES compile_runs(run_id) ON DELETE CASCADE,
  compile_model   text,
  -- 'skipped' added on 2026-04-16 via src/db/migrations/202604160000_add_skipped_state.sql
  outcome         text CHECK (outcome IN ('compiled', 'failed', 'timeout', 'skipped')),
  error           text,
  attempted_at    timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);

-- Health-stats path (`model_health_stats`) — partial index matches its
-- exact filter so PG can satisfy the GROUP BY without scanning in-flight
-- or model-less rows.
CREATE INDEX IF NOT EXISTS compile_attempts_health_stats_idx
  ON compile_attempts (compile_model, attempted_at DESC)
  WHERE compile_model IS NOT NULL AND finished_at IS NOT NULL;
-- Per-message lookup path + speeds up FK CASCADE on messages delete.
CREATE INDEX IF NOT EXISTS compile_attempts_message_idx
  ON compile_attempts (message_id);
-- FK CASCADE on compile_runs delete + per-run debugging.
CREATE INDEX IF NOT EXISTS compile_attempts_run_idx
  ON compile_attempts (run_id);
