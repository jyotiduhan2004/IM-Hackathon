-- Add 'home' and 'changes' page_types for the top-level landing pages
-- (wiki/home.md, wiki/changes.md). The 'index' value the generators emitted
-- previously was a holdover from the legacy index.md flow; the catalog
-- normalized it to 'glossary' which mis-typed those pages downstream.
-- Idempotent.

ALTER TABLE wiki_pages DROP CONSTRAINT IF EXISTS wiki_pages_page_type_check;
ALTER TABLE wiki_pages ADD CONSTRAINT wiki_pages_page_type_check
  CHECK (page_type IN (
    'topic', 'entity', 'system', 'policy', 'timeline', 'conflict',
    'domain', 'glossary', 'decision', 'person',
    'home', 'changes'
  ));
