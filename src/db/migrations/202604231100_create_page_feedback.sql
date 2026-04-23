-- page_feedback — append-only feedback store for scorer/judge/human findings.
--
-- V12-U0a infra: shared by the heuristic scorer (V12-U0b) and the LLM-judge
-- personas (V12-U0c). Each scorer/judge invocation groups its rows by
-- `run_id`; human notes get stamped with the user's email under `captured_by`.
--
-- Why slug-keyed, not FK'd to wiki_pages(page_id):
--   Slugs rename/merge as the wiki reorganises. A hard FK would cascade-
--   delete historical feedback when a page is retired or its slug shifts,
--   which is exactly the wrong thing — we want the record of "this page
--   scored 4/10 on 2026-04-23" to survive the rename. `page_version`
--   (frontmatter `last_compiled` ISO) is stored too, so readers can tell
--   which compile produced the feedback even if the page has moved on.
--
-- Lifecycle: append-only. No UPDATEs, no DELETEs. A newer row with the
-- same (page_slug, source) supersedes the older one for "latest" lookups;
-- the older row stays as history.
--
-- Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS page_feedback (
  id              BIGSERIAL PRIMARY KEY,
  run_id          UUID NOT NULL,                       -- groups rows per scorer/judge invocation
  page_slug       TEXT NOT NULL,                       -- NOT FK (slug renames shouldn't cascade-delete history)
  page_version    TEXT NOT NULL,                       -- frontmatter last_compiled ISO (stored, not derived)
  source          TEXT NOT NULL,                       -- 'scorer' | 'judge-newbie' | 'judge-pm' | 'judge-ia' | 'human'
  score           NUMERIC,                             -- nullable; 0-10 heuristic; null for prose findings
  finding         TEXT NOT NULL,
  severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'blocker')),
  captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  captured_by     TEXT NOT NULL,                       -- 'heuristic' | persona name | user email
  raw_json        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS page_feedback_page_slug_idx
  ON page_feedback (page_slug, captured_at DESC);
CREATE INDEX IF NOT EXISTS page_feedback_source_idx
  ON page_feedback (source, captured_at DESC);
CREATE INDEX IF NOT EXISTS page_feedback_run_id_idx
  ON page_feedback (run_id);
