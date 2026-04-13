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
