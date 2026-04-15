-- Add 'skipped' to messages.compile_state enum so trivial emails (acks,
-- calendar noise, short tangential replies) can bypass the expensive
-- compile path without blocking the queue. See src/ingest/filter_trivial.py
-- for the classifier and scripts/backfill_trivial.py for the one-shot
-- backfill of existing pending rows.

ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_compile_state_check;
ALTER TABLE messages ADD CONSTRAINT messages_compile_state_check
  CHECK (compile_state IN ('pending', 'claimed', 'compiled', 'failed', 'skipped'));

-- compile_attempts.outcome may get 'skipped' rows in the future so the
-- per-model health stats can distinguish "model failed" from "we never
-- even tried". Extend the CHECK if it exists; the table is newish so
-- a fresh setup doesn't need this branch.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.table_constraints
             WHERE table_name = 'compile_attempts'
               AND constraint_name = 'compile_attempts_outcome_check') THEN
    ALTER TABLE compile_attempts DROP CONSTRAINT compile_attempts_outcome_check;
    ALTER TABLE compile_attempts ADD CONSTRAINT compile_attempts_outcome_check
      CHECK (outcome IN ('compiled', 'failed', 'timeout', 'skipped'));
  END IF;
END $$;

COMMENT ON COLUMN messages.compile_state IS
  'pending | claimed | compiled | failed | skipped. skipped = trivial filter match, not to be retried.';
