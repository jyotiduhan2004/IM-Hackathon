# Wiki feedback rollups

Human-readable artifacts produced by the heuristic scorer
(`scripts/score_wiki.py`, V12-U0b) and the LLM-judge personas
(`scripts/judge_wiki.py`, V12-U0c). The machine-statable data lives in the
Postgres `page_feedback` table; this directory holds the human digest.

## Why both a DB table and markdown

The scorer/judge write DB rows so downstream tooling can query, rank, and
chart findings over time. They also drop a markdown + CSV rollup here so
a human can open one file and skim the last run without a database
client. The two views are generated from the same code path; the DB is
the source of truth for machine queries, the files are the source of
truth for human review.

If the DB isn't available (migration hasn't been applied, connection
refused, `UndefinedTable` because this is a fresh checkout), the scorer
and judge gracefully fall back to CSV + markdown only — you don't lose
the run, you just lose the historical query surface until the migration
lands.

## File naming

- `scorer-<YYYY-MM-DD>.md` — heuristic scorer markdown digest
- `scorer-<YYYY-MM-DD>.csv` — machine-readable rows for the same run
- `judge-<YYYY-MM-DD>.md` — combined persona (newbie / PM / IA) markdown
- `judge-<YYYY-MM-DD>.csv` — persona findings, one row per finding

Multiple runs on the same day append `-N` (`scorer-2026-04-23-2.md`). The
`.md` and `.csv` for a single run share a run UUID — that UUID also keys
`page_feedback.run_id` so you can join back to DB rows.

## Applying the migration

Run once, before the first scorer or judge invocation that expects DB
writes:

```bash
psql $DATABASE_URL < src/db/migrations/202604231100_create_page_feedback.sql
```

The migration is idempotent (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`) — safe to rerun.

## Schema quick reference

```
page_feedback
  id             bigserial primary key
  run_id         uuid           -- groups rows per scorer/judge invocation
  page_slug      text           -- NOT FK: slug renames shouldn't cascade-delete history
  page_version   text           -- frontmatter last_compiled ISO at capture time
  source         text           -- 'scorer' | 'judge-newbie' | 'judge-pm' | 'judge-ia' | 'human'
  score          numeric        -- nullable; 0-10 heuristic, null for prose
  finding        text           -- short human-readable sentence
  severity       text           -- 'info' | 'warning' | 'blocker' (CHECK)
  captured_at    timestamptz    -- default now()
  captured_by    text           -- 'heuristic' | persona name | user email
  raw_json       jsonb          -- rule-specific payload, default '{}'
```

Indexes: `(page_slug, captured_at DESC)`, `(source, captured_at DESC)`,
`(run_id)`.

## Append-only lifecycle

Every insert is additive. A re-run over the same page inserts a fresh
row; the older row stays for historical context. `list_recent_feedback_for_page`
uses `DISTINCT ON (source)` to collapse to the latest row per source so
callers see one current line per source, not the full history. Ad-hoc
"what did we think about this page last month" questions go through
`list_feedback_by_run` or direct SQL.

## Human review notes

When a reviewer wants to annotate a page's feedback, write a new row
with `source='human'`, `captured_by='<reviewer email>'`, and a concise
`finding`. Use `severity='blocker'` sparingly — it gates downstream
compile/publish steps in V12-U1+.

## Code entry points

- `src/db/page_feedback.py` — psycopg repo module (read/write helpers)
- `src/db/migrations/202604231100_create_page_feedback.sql` — schema
- `scripts/score_wiki.py` (V12-U0b, follow-up) — heuristic writer
- `scripts/judge_wiki.py` (V12-U0c, follow-up) — persona writer
