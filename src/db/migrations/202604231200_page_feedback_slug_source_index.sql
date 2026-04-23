-- Composite index for the DISTINCT ON (source) / ORDER BY source, captured_at DESC
-- query in ``list_recent_feedback_for_page``. With page_slug-only indexing the
-- planner still had to sort in memory to resolve the DISTINCT; a (page_slug,
-- source, captured_at DESC) composite lets it walk the index directly.
--
-- The old ``page_feedback_page_slug_idx`` is a redundant prefix of the new
-- composite, so we drop it. No behaviour change for inserts — both indexes
-- cover the same write path.
--
-- Idempotent: CREATE INDEX IF NOT EXISTS + DROP INDEX IF EXISTS.

CREATE INDEX IF NOT EXISTS page_feedback_slug_source_idx
  ON page_feedback (page_slug, source, captured_at DESC);

DROP INDEX IF EXISTS page_feedback_page_slug_idx;
