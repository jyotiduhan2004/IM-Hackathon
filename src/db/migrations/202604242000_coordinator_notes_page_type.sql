-- Add 'coordinator_notes' to wiki_pages page_type CHECK constraint.
--
-- Introduced 2026-04-24 for wiki/merge_candidates.md (and any future
-- coordinator-written queue / log files) so the file gets valid
-- frontmatter instead of being scanned as "broken" by critique's
-- find_touched_pages and pulled into unrelated batch reviews
-- (Codex-identified "poisoned input set" gate-loop, #179).
--
-- Idempotent via DROP CONSTRAINT IF EXISTS.
ALTER TABLE wiki_pages DROP CONSTRAINT IF EXISTS wiki_pages_page_type_check;
ALTER TABLE wiki_pages ADD CONSTRAINT wiki_pages_page_type_check
  CHECK (page_type IN (
    'topic', 'entity', 'system', 'policy',
    'timeline', 'conflict', 'domain', 'glossary',
    'decision', 'person', 'home', 'changes',
    'coordinator_notes'
  ));
