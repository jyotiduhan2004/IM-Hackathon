## 1. Does the proposed schema hold up?

No. The direction is right: move provenance and compile state out of markdown. The draft is wrong in three places: it bakes in lossy one-to-one relations, uses mutable paths as foreign keys, and treats processed state as a boolean when the real problem is crash-safe claiming.

That direction still matches the repo. Today the compiler scans raw frontmatter to find uncompiled messages (`src/compile/compiler.py:24-59`), writes `compiled: true` back into raw files (`src/compile/compiler.py:132-169`), and the prompt explicitly tells the agent to do that (`src/compile/prompts.py:8-10`, `src/compile/prompts.py:41-45`). It also still forces exhaustive `sources:` population for entity pages (`src/compile/prompts.py:185-215`), while MkDocs renders sources directly from frontmatter (`mkdocs_hooks.py:200-229`). That is the right thing to delete.

Exact fixes:

- Drop `threads.topic_wiki_slug`. One thread can touch many page types, not one topic. The prompt already tells the compiler to update multiple affected pages per email (`src/compile/prompts.py:31-45`).
- Drop `users.wiki_slug`. The canonical mapping belongs on the page, not the user row. `wiki_pages.email` should become `canonical_user_email`; otherwise you create a bidirectional drift problem on page rename.
- Make `wiki_pages.slug` globally unique. Current wikilinks resolve by bare stem across the whole wiki (`scripts/validate_wiki.py:367-403`), and the audit already found a real cross-category collision at `samarth` (`docs/reviews/audit-synthesis-20260413T040000Z.md:19-21`).
- Do not key `message_touched_pages` by `page_path`. Paths are mutable presentation details. Add a stable `page_id`.
- Do not store `is_fresh`. Freshness is derived from timestamps and newer source availability. A stored boolean will rot immediately, the same way `wiki/index.md` page counts already drifted (`docs/reviews/audit-synthesis-20260413T040000Z.md:67-68`).
- Add a real queue shape to `messages`; a boolean `is_compiled` is not enough. Section 3.

```sql
ALTER TABLE threads
  DROP COLUMN topic_wiki_slug;

ALTER TABLE users
  DROP COLUMN wiki_slug;

ALTER TABLE wiki_pages
  RENAME COLUMN email TO canonical_user_email;

ALTER TABLE wiki_pages
  ADD COLUMN page_id BIGSERIAL;

ALTER TABLE wiki_pages
  ADD CONSTRAINT wiki_pages_pk PRIMARY KEY (page_id);

ALTER TABLE wiki_pages
  ALTER COLUMN slug SET NOT NULL,
  ALTER COLUMN title SET NOT NULL,
  ALTER COLUMN page_type SET NOT NULL,
  ALTER COLUMN status SET NOT NULL,
  ADD CONSTRAINT wiki_pages_slug_unique UNIQUE (slug),
  ADD CONSTRAINT wiki_pages_path_unique UNIQUE (path),
  ADD CONSTRAINT wiki_pages_canonical_user_fk
    FOREIGN KEY (canonical_user_email) REFERENCES users(email);

CREATE UNIQUE INDEX wiki_pages_entity_email_uidx
  ON wiki_pages (canonical_user_email)
  WHERE page_type = 'entity' AND canonical_user_email IS NOT NULL;

ALTER TABLE message_touched_pages
  DROP COLUMN page_path,
  ADD COLUMN page_id BIGINT NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
  ADD CONSTRAINT message_touched_pages_pk PRIMARY KEY (message_id, page_id);

CREATE INDEX messages_thread_date_idx
  ON messages (thread_id, date);

CREATE INDEX message_participants_message_idx
  ON message_participants (message_id);

CREATE INDEX message_participants_user_role_idx
  ON message_participants (user_email, role, message_id);

ALTER TABLE wiki_pages
  DROP COLUMN is_fresh;

CREATE MATERIALIZED VIEW thread_pages_mv AS
SELECT
  m.thread_id,
  p.page_id,
  p.slug,
  p.page_type,
  COUNT(*) AS touch_count,
  MAX(mtp.compiled_at) AS last_compiled_at
FROM message_touched_pages mtp
JOIN messages m ON m.message_id = mtp.message_id
JOIN wiki_pages p ON p.page_id = mtp.page_id
GROUP BY m.thread_id, p.page_id, p.slug, p.page_type;

CREATE INDEX thread_pages_mv_thread_idx
  ON thread_pages_mv (thread_id);
```

One more correction: keep page identity close to the page. `mkdocs_hooks.py` currently has to regex the body for `Email:` because entity pages do not reliably carry canonical email in metadata (`mkdocs_hooks.py:204-216`). That is backwards. Put `canonical_user_email` on `wiki_pages` and stop scraping it from prose.

## 2. External references (URLs, Google Docs, images, attachments).

Yes. URLs should be their own table. `messages.url_count` is fine as a cheap denormalized counter, but not as the model. With 6,231/6,759 raws containing URLs, repeated links are entities in their own right.

Use two tables:

```sql
CREATE TABLE external_references (
  external_reference_id BIGSERIAL PRIMARY KEY,
  canonical_key TEXT NOT NULL UNIQUE,
  canonical_url TEXT NOT NULL,
  reference_type TEXT NOT NULL CHECK (
    reference_type IN ('url', 'google_doc', 'google_sheet', 'google_slide', 'drive_file')
  ),
  host TEXT GENERATED ALWAYS AS (
    lower(split_part(regexp_replace(canonical_url, '^https?://', ''), '/', 1))
  ) STORED,
  google_file_id TEXT,
  metadata_title TEXT,
  metadata_owner_email TEXT,
  metadata_status TEXT NOT NULL DEFAULT 'unfetched' CHECK (
    metadata_status IN ('unfetched', 'fetched', 'forbidden', 'not_found', 'error')
  ),
  metadata_fetched_at TIMESTAMPTZ
);

CREATE TABLE message_external_references (
  message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
  external_reference_id BIGINT NOT NULL REFERENCES external_references(external_reference_id) ON DELETE CASCADE,
  raw_url TEXT NOT NULL,
  position_index INT NOT NULL,
  context_snippet TEXT,
  PRIMARY KEY (message_id, external_reference_id, position_index)
);

CREATE INDEX message_external_references_ref_idx
  ON message_external_references (external_reference_id, message_id);
```

Google Docs: extract the file ID at ingest every time. Do not stop there. Fetch lightweight metadata asynchronously: title, owner email if available, permission outcome. Do not block ingest on that API call. Doc ID alone is not enough once the same doc shows up across threads; title and owner are what make the reference legible to a human.

Repeated dashboard URL: yes, collapse to one `external_references` row keyed by a canonical URL or canonical file ID. Keep every per-message occurrence in `message_external_references`. Do not explode the same Looker/Grafana/Docs link into 300 rows.

Attachments: worth modeling, not worth re-ingesting before ship. The parser already writes `has_attachments`, `attachment_files`, and `inline_images` into raw frontmatter (`src/ingest/parser.py:136-173`), and there is already a download helper (`src/ingest/attachments.py:17-72`). Sampled raws show `has_attachments: true` with `attachment_files: []` (`raw/2026-01-01_mplaunchim-introducing-feedback-system-for-ai-call_8a6097e7.md:30-31`), which matches the user probe. That is an ingest bug, not a schema excuse.

Model attachments separately, not as URLs:

```sql
CREATE TABLE attachments (
  attachment_id BIGSERIAL PRIMARY KEY,
  storage_path TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  mime_type TEXT,
  size_bytes BIGINT,
  sha256 TEXT,
  caption_text TEXT
);

CREATE TABLE message_attachments (
  message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
  attachment_id BIGINT NOT NULL REFERENCES attachments(attachment_id) ON DELETE CASCADE,
  ordinal INT NOT NULL,
  is_inline BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (message_id, attachment_id)
);
```

## 3. Processed state.

Move it to the DB. Do not mass-edit raws again just to remove the old flag. Backfill once, then stop trusting frontmatter.

Current state is explicit in code: `list_uncompiled_emails()` filters on `compiled` in raw frontmatter (`src/compile/compiler.py:45-59`), `mark_as_compiled()` writes `compiled` and `compiled_at` back into raw markdown (`src/compile/compiler.py:153-168`), and the prompt instructs the agent to do that (`src/compile/prompts.py:8-10`, `src/compile/prompts.py:41-45`). Replace that with a state machine:

```sql
ALTER TABLE messages
  DROP COLUMN is_compiled,
  ADD COLUMN compile_state TEXT NOT NULL DEFAULT 'pending' CHECK (
    compile_state IN ('pending', 'claimed', 'compiled', 'failed')
  ),
  ADD COLUMN compile_run_id UUID,
  ADD COLUMN claimed_at TIMESTAMPTZ,
  ADD COLUMN compiled_at TIMESTAMPTZ,
  ADD COLUMN compile_attempts INT NOT NULL DEFAULT 0,
  ADD COLUMN last_error TEXT;

ALTER TABLE messages
  ADD COLUMN is_compiled BOOLEAN GENERATED ALWAYS AS (compile_state = 'compiled') STORED;

CREATE INDEX messages_compile_queue_idx
  ON messages (compile_state, claimed_at, date)
  WHERE compile_state IN ('pending', 'claimed', 'failed');
```

Migration:

1. Backfill `messages` from `raw/*.md`. Map `compiled: true` to `compile_state='compiled'`; otherwise `pending`. Copy `compiled_at` if present.
2. Change `list_uncompiled_emails` to read from Postgres, ordered by `date`, not by raw scan.
3. Replace `mark_as_compiled` with two DB operations: `claim_next_message` and `finish_message_compile`.
4. Leave legacy `compiled:` in raw files as dead legacy state. Do not rewrite 6,759 raws just to erase it.

Claim query:

```sql
WITH next AS (
  SELECT message_id
  FROM messages
  WHERE compile_state IN ('pending', 'failed')
     OR (compile_state = 'claimed' AND claimed_at < now() - interval '30 minutes')
  ORDER BY date, message_id
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE messages m
SET compile_state = 'claimed',
    compile_run_id = $1,
    claimed_at = now(),
    compile_attempts = compile_attempts + 1
FROM next
WHERE m.message_id = next.message_id
RETURNING m.message_id, m.raw_path;
```

Finish in one transaction: insert/upsert `message_touched_pages`, then `UPDATE messages SET compile_state='compiled', compiled_at=now()`.

Idempotency guarantee: exactly-once queue state, at-least-once wiki projection. That is the honest answer. If the compile crashes mid-message after writing markdown but before the finish transaction, the message stays `claimed`; the next run requeues it after the stale-claim timeout. That can re-apply the same message to the same page. If you want exactly-once end-to-end, the page content has to become transactional too. A boolean in Postgres does not buy that.

## 4. Local Postgres vs SQLite given 1-2 day GCP timeline.

Confirm Postgres. I would not confirm the full schema, but I would confirm the engine.

Issue #8 was right to choose SQLite for a read-only provenance catalog. This is no longer just a catalog. The moment you move compile claiming out of raw frontmatter and plan Cloud SQL within the week, Postgres wins on partial indexes, generated columns, materialized views, and `FOR UPDATE SKIP LOCKED`. SQLite can limp through that. Postgres is cleaner and avoids a second migration in five days.

Use the smallest local setup possible:

```yaml
services: {db: {image: postgres:16, environment: {POSTGRES_USER: kb, POSTGRES_PASSWORD: kb, POSTGRES_DB: kb}, ports: ["5432:5432"], volumes: ["pgdata:/var/lib/postgresql/data"], healthcheck: {test: ["CMD-SHELL", "pg_isready -U kb -d kb"], interval: 5s, timeout: 5s, retries: 20}}}
volumes: {pgdata: {}}
```

Do not spend time on Docker networks, init scripts, or PgBouncer now.

## 5. Smallest thing that works.

Ship one table: `messages`.

Not the full schema. Not users, not wiki pages, not URL refs. Just a real `messages` table in Postgres with queue fields, backfilled from raw markdown, and wired into `list_uncompiled_emails` and `mark_as_compiled` replacements. That unblocks compile resume immediately and gives you a stable base for the rest.

Why this slice: the current failure boundary is raw-frontmatter queue state, not missing user normalization. The repo’s real incidents are compile stalls, frontmatter corruption, and bad write loops (`docs/incidents/2026-04-13-phase0-bootstrap.md:15-49`, `docs/incidents/2026-04-13-phase0-bootstrap.md:118-151`). A DB-backed message queue fixes resume. It does not pretend to fix everything else.

The single first PR should do exactly three things:

- Create and backfill `messages`.
- Switch queue reads/writes from raw frontmatter to Postgres.
- Leave wiki markdown rendering and provenance alone for one more PR.

Anything larger is schedule risk.

## 6. Dead ends / traps.

- `threads.topic_wiki_slug`: you will regret this first. Consequence: every multi-page thread forces an arbitrary “main topic” and loses the actual page graph.
- `users.wiki_slug` plus `wiki_pages.email`: bidirectional identity is drift bait. Consequence: one rename or merge leaves two “canonical” links and no source of truth.
- `page_path` as FK in `message_touched_pages`: path is not identity. Consequence: slug cleanup and recategorization become referential-update chores instead of page edits.
- Stored `is_fresh`: do not do this. Consequence: it becomes another stale field, same class of problem as the already stale `index.md` counts (`docs/reviews/audit-synthesis-20260413T040000Z.md:67-68`).
- Boolean `is_compiled` as the queue: this is the biggest design lie in the draft. Consequence: after a mid-message crash you cannot tell “not started” from “partially applied.”
- Synchronous Google metadata fetch in ingest: tempting, wrong. Consequence: Gmail ingest becomes permission-sensitive and slow for the 6,231 URL-bearing messages instead of remaining a reliable append pipeline.
- Assuming slug uniqueness only by `path`: current wiki rules do not namespace wikilinks by directory (`scripts/validate_wiki.py:367-403`). Consequence: you preserve `samarth`-style ambiguity instead of killing it.
