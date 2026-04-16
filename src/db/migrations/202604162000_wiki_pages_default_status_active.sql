-- Flip wiki_pages.status DEFAULT from 'current' → 'active' so new inserts
-- without an explicit status land on the North-Star value rather than
-- re-introducing the legacy one. Phase 0 runtime hardening: the C1/C2
-- migrations emptied the legacy buckets today, but new writes still
-- inherit the old default until this ALTER runs.
--
-- The CHECK constraint still accepts the full legacy set (current /
-- superseded / contested) so reads of any lingering legacy rows keep
-- working; only the DEFAULT moves. Idempotent.

ALTER TABLE wiki_pages ALTER COLUMN status SET DEFAULT 'active';
