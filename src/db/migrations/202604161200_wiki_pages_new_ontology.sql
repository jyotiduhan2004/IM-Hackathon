-- Widen wiki_pages CHECKs to accept both legacy and the North-Star 4+2
-- ontology (docs/NORTH-STAR.md). Both coexist during migration; a
-- follow-up renames legacy rows. Idempotent.

ALTER TABLE wiki_pages DROP CONSTRAINT IF EXISTS wiki_pages_page_type_check;
ALTER TABLE wiki_pages ADD CONSTRAINT wiki_pages_page_type_check
  CHECK (page_type IN (
    'topic', 'entity', 'system', 'policy', 'timeline', 'conflict',
    'domain', 'glossary', 'decision', 'person'
  ));

ALTER TABLE wiki_pages DROP CONSTRAINT IF EXISTS wiki_pages_status_check;
ALTER TABLE wiki_pages ADD CONSTRAINT wiki_pages_status_check
  CHECK (status IN (
    'current', 'superseded', 'contested',
    'active', 'archived'
  ));
