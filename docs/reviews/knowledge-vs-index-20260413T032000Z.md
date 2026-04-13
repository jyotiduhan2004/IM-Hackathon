# Knowledge vs. Index — Separating Wiki Prose From the Email Catalog

Date: 2026-04-13 · Status: proposal, READ-ONLY investigation

## 1. Current state diagnosis

Today one markdown file owns three things: distilled knowledge (body + wikilinks), provenance (`sources:`, `source_threads:`, `related:`), and render-time metadata (`last_compiled`, `updated_by`, `update_count`, `status`). The first should live in the wiki; the second is an index, not knowledge; the third is instrumentation. Current ratios:

| Page | File size | Lines | Body lines | Frontmatter lines |
|---|---|---|---|---|
| `wiki/entities/himanshu-jain01.md` | 9.7 KB | 215 | ~5 | ~210 |
| `wiki/entities/bharat-agarwal.md` | 7.9 KB | 164 | 7 (lines 159–164) | 157 |
| `wiki/entities/alok-shukla.md` | 7.8 KB | ~200 | small | ~190 |
| `wiki/topics/dspy-gepa-automated-speaker-labelling-pipeline.md` | 6.0 KB | 147 | ~130 | ~15 |
| `wiki/entities/m-site.md` (stub) | 247 B | 14 | 5 | 9 |

Entity pages are roughly 95% provenance, 5% prose. Topic pages are healthy (90% prose, a dozen sources). The pathology is 1:1 coupled to entity degree — popular recipients accumulate hundreds of CC'd threads that say nothing about them personally.

Where provenance is written / read today:

- `src/compile/prompts.py:186-215` — instructs the agent to "Populate sources exhaustively" with "EVERY raw email file where they appear in From, To, CC, or body." Combined with `list_wiki_pages` + `read_file` on 200-line pages, this is the mechanism that kept rewriting 26 KB frontmatter via `edit_file` and stalling LLM runs (see `docs/reviews/edit-tool-research-20260413T031839Z.md`).
- `src/compile/compiler.py:94-128` (`stamp_page_compiled_at`) and `:132-169` (`mark_as_compiled`) — writes `last_compiled`, `updated_by`, `update_count`, `compiled_at` to both raw and wiki frontmatter.
- `mkdocs_hooks.py:162-229` — reads `fm["sources"]`, opens each `raw/<path>`, renders a `<details>` block per source at render time. Also reads `fm["email"]` / greps body for `Email:` line to label From/To/CC/body role (`:62-73`, `:208-216`).
- `scripts/dedupe_sources.py` — collapses same-subject-slug thread replies into one canonical source per thread, writing a parallel `source_threads:` summary. This is a workaround for bloat caused by the ingest keeping one raw file per message instead of per thread.
- `scripts/backfill_stubs.py:40-141` — grep-based reverse index from `raw/`: given a slug like `himanshu-jain`, synthesize candidate emails (`first.last@indiamart.com`, etc.), scan raw files' From/To/CC headers (and body as a weak fallback), return matching raw paths. This is *already* a mention index — it just rebuilds on demand and writes the output back into frontmatter instead of caching the index.
- `scripts/validate_wiki.py:180-216` — enforces that every `[[wikilink]]` resolves to a real page stem. Operates purely on the body text; does not depend on `sources:`.
- `scripts/lint_wiki.py:37` / `:66-117` — `REQUIRED_FRONTMATTER = {title, page_type, status, sources, last_compiled}`. `sources` is mandatory. This is the only real enforcement of the current schema.

Key observation: nothing in the compiler or hooks needs `sources:` to be *inside* the markdown — it's used by exactly one reader (`mkdocs_hooks._render_raw_source`) and one writer (the agent + `backfill_stubs.py`). Moving it out is a local change.

## 2. Proposed separation

Three artefacts, one source of truth for each.

### 2.1 Knowledge layer — `wiki/*.md`

Pure prose + wikilinks. Frontmatter collapses to:

```yaml
---
title: "Human Readable Title"
page_type: topic | entity | system | policy | timeline | conflict
status: current | superseded | contested
email: "first.last@indiamart.com"   # entities only; canonical match key
related:
  - "[[another-page]]"
last_compiled: "2026-04-13T02:30:00Z"
updated_by: "anthropic/claude-opus-4-6"
update_count: 3
---
```

Everything else (`sources`, `source_threads`, `supersedes`, `superseded_by` when applicable) either moves out or stays as-is with sane bounds (supersession chain is knowledge, not provenance). Entity pages go from 210-line YAML blobs to 8 lines.

### 2.2 Email/thread catalog — SQLite

Format: **SQLite** (`.kb/catalog.sqlite`), not JSONL or Parquet.

Why: we already need point lookups by `thread_id`, `message_id`, and email address; ad-hoc `where` queries for analytics; incremental upserts on new ingest; and a stable file that diffs cleanly as one binary (not per-row churn in git). JSONL is fine for append-only archival but every read is a full scan, and Parquet is overkill and columnar-hostile for single-row lookups. SQLite is a file, zero-ops, bundled with Python, and gives us indexes. Keep it out of git (`.gitignore`) — regenerate from `raw/` in seconds.

Schema (one table, one view):

```sql
CREATE TABLE emails (
    path              TEXT PRIMARY KEY,           -- "raw/2026-01-02_foo_abc.md"
    message_id        TEXT NOT NULL,
    thread_id         TEXT NOT NULL,
    in_reply_to       TEXT,
    date              TEXT NOT NULL,              -- ISO8601
    subject           TEXT NOT NULL,
    from_raw          TEXT NOT NULL,              -- "Foo Bar <foo@x.com>"
    from_email        TEXT NOT NULL,              -- normalized lowercase "foo@x.com"
    to_emails_json    TEXT NOT NULL,              -- JSON array of normalized emails
    cc_emails_json    TEXT NOT NULL,
    body_hash         TEXT NOT NULL,              -- sha256 of body_plain
    body_plain        TEXT NOT NULL,              -- first 64 KB; truncate + flag for huge
    ingested_at       TEXT NOT NULL
);
CREATE INDEX emails_thread_idx ON emails(thread_id, date);
CREATE INDEX emails_from_idx   ON emails(from_email);
```

### 2.3 Mention index — derived view

A second table, fully regenerable from `emails`:

```sql
CREATE TABLE mentions (
    email_address   TEXT NOT NULL,    -- lowercase
    raw_path        TEXT NOT NULL REFERENCES emails(path),
    role            TEXT NOT NULL,    -- 'from' | 'to' | 'cc' | 'body'
    date            TEXT NOT NULL,
    thread_id       TEXT NOT NULL,
    PRIMARY KEY (email_address, raw_path, role)
);
CREATE INDEX mentions_email_idx  ON mentions(email_address, date);
CREATE INDEX mentions_thread_idx ON mentions(thread_id);
```

For topics/systems/policies that lack a canonical email, a sibling table keyed by slug:

```sql
CREATE TABLE slug_mentions (
    slug        TEXT NOT NULL,
    raw_path    TEXT NOT NULL REFERENCES emails(path),
    source      TEXT NOT NULL,        -- 'subject' | 'body' | 'tag'
    date        TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    PRIMARY KEY (slug, raw_path, source)
);
```

The compiler becomes the sole writer of `slug_mentions` by emitting a line per wiki-page-touched at compile time ("this thread was about `[[dynamic-smart-rfq-form]]`"). For entities, the mention index is purely mechanical (from/to/cc) — no LLM involvement.

### 2.4 Render-time join — `mkdocs_hooks.py`

Replace the current `sources:` frontmatter consumer with a catalog query. Same `<details>` output, same per-source role tag ("✍️ Sent by", "📋 CC'd"), just different data source.

```python
# mkdocs_hooks.py (sketch)
def on_page_markdown(markdown, *, page, config, files):
    fm = dict(page.meta)
    if fm.get("page_type") == "entity" and fm.get("email"):
        rows = _catalog.fetchall(
            """
            SELECT e.* FROM emails e
            JOIN mentions m ON m.raw_path = e.path
            WHERE m.email_address = ? ORDER BY e.date
            """, (fm["email"].lower(),),
        )
    else:  # topic/system/policy — slug-based
        rows = _catalog.fetchall(
            """
            SELECT e.* FROM emails e
            JOIN slug_mentions s ON s.raw_path = e.path
            WHERE s.slug = ? ORDER BY e.date
            """, (page.file.src_path.removesuffix(".md").split("/")[-1],),
        )
    return markdown + _render_sources(rows, group_by="thread")
```

Group by thread by default: one `<details>` per `thread_id`, containing nested per-message blocks sorted by date. This kills the "same subject 23 times" problem that `dedupe_sources.py` currently papers over.

## 3. Concrete migration path

Five phases, each a self-contained PR, each reversible in one `git revert`.

### Phase 0 — Build the catalog (additive)

- New: `src/catalog/build.py` (reuses `src/ingest/parser.py` for header parsing and `src/utils.extract_frontmatter`).
- New: `scripts/build_catalog.py` with `--incremental` (default: only files newer than `max(ingested_at)` in DB) and `--full-rebuild`.
- New: `.kb/catalog.sqlite` path, gitignored.
- New: test `tests/catalog/test_build.py` — 5 fixture raws, verify row count, normalized emails, thread grouping.
- **Breaks:** nothing. Pure new artefact.
- **Revert:** delete `src/catalog/`, `.kb/`, `scripts/build_catalog.py`.

### Phase 1 — Populate mention index

- Extend `build.py` to write `mentions` and `slug_mentions` rows. For Phase 1, populate `slug_mentions` from the existing wiki pages' `sources:` lists (we know `thread_id` from the raw frontmatter; we know "slug X claims this raw" from the existing `sources:` field). This bootstrap means Phase 2 ships with full coverage before we delete the old `sources:`.
- Test: for a representative page (`himanshu-jain01`), the SQL `SELECT * FROM mentions WHERE email_address = 'himanshu.jain@indiamart.com'` returns the same set of raws currently in `sources:` (minus collapsed dupes) ± known false positives from slug-only name matches. Acceptable delta: < 5% different from the grep-based backfill output.
- **Breaks:** nothing yet. Hook still reads `sources:`.
- **Revert:** drop the two tables; keep `emails`.

### Phase 2 — Teach `mkdocs_hooks.py` to read the catalog

- Add `_catalog` singleton at top of `mkdocs_hooks.py`, opened on first use.
- New rendering path: catalog-first for entities (by `fm["email"]`), catalog-first for topics/systems/policies (by slug), then `thread`-grouped `<details>`.
- **Fallback:** if catalog is missing or query returns zero rows AND `fm["sources"]` is non-empty, use the old renderer. This lets us ship Phase 2 before Phase 3/4 and still see sources on legacy pages.
- Test: `tests/mkdocs/test_sources_render.py` builds a tiny wiki + catalog, asserts the generated HTML has a `<details>` per thread and a role tag per message.
- **Breaks:** pages whose canonical email is wrong or missing — they render with zero sources. Mitigation: validate_wiki check "entity page missing `email:`" promoted to ERROR in Phase 3.
- **Revert:** feature-flag via env var `USE_CATALOG_SOURCES=1`; default off until we flip it.

### Phase 3 — Stop writing `sources:` in new compiles

- Edit `src/compile/prompts.py`: delete the "Populate sources exhaustively" section (`:186-215`), the "Source completeness for entity pages" section (`:210-215`), and the `sources:` line in the frontmatter template (`:54`).
- Add instead: "The sources section is built automatically from the email catalog. Do NOT add `sources:` to frontmatter. Do NOT grep `raw/` for the person's emails. Concentrate on prose."
- Edit `scripts/lint_wiki.py:37`: drop `sources` from `REQUIRED_FRONTMATTER`. Add `email` as required for `page_type == entity` (so catalog queries can resolve them).
- Edit `scripts/validate_wiki.py`: same adjustment; add "entity without `email:` field" as ERROR.
- Edit `src/compile/compiler.py`: no changes needed — `stamp_page_compiled_at` and `mark_as_compiled` already ignore `sources`.
- Test: run the compiler on a fresh batch; assert no new page has `sources:` in its frontmatter.
- **Breaks:** pages created in this window will look different from old pages (no `sources:`). With Phase 2's dual-read hook, both render correctly.
- **Revert:** restore the deleted prompt paragraphs and lint rule.

### Phase 4 — Strip legacy `sources:` from existing pages

- New: `scripts/strip_sources.py`. Walks `wiki/**/*.md`, removes `sources:`, `source_threads:` from frontmatter, leaves `related:` + everything else. Idempotent.
- Run once: `uv run python scripts/strip_sources.py --dry-run` then `--apply`.
- This also shrinks `himanshu-jain01.md` from 9.7 KB / 215 lines to ~400 B / 12 lines, which fixes the `edit_file` stall documented in `docs/reviews/edit-tool-research-20260413T031839Z.md` without needing the shell-escape-hatch workaround.
- **Breaks:** if the catalog is missing, sources disappear from render. Run `make build-catalog` first; verify every entity has its `email:` populated.
- **Revert:** `git revert` the mass-rewrite commit; catalog remains harmless.

Each phase is independently shippable. Phases 0-2 can land without touching the compiler or existing pages at all.

## 4. Open questions — called, not punted

**Inline citations in prose.** Skip them. The existing `<details>`-per-source block below the content is enough context; and every quoted claim in the body can already reference `[[wikilinks]]` for its derivation chain. If we want them later, use MkDocs' built-in footnote plugin (`pymdownx.footnotes` is already enabled, see `mkdocs.yml:50`) with a tiny shortcode `{{ ref("2026-01-14", "abc123") }}` that a hook expands to a footnote pointing at the catalog row. Don't build this in v0 — no one is asking for it.

**Thread grouping vs. message-level.** Default to thread-grouped, reverse-chronological. That's the rendering convention `dedupe_sources.py` was reaching for. Within a thread, sort messages chronologically inside the nested `<details>`. This mirrors how Gmail shows conversations — readers already know that shape.

**Topics/systems without a canonical email.** Two-tier answer: (a) for any page the compiler touches, it emits a `slug_mentions` row tying that `thread_id` to the page's slug — zero ambiguity because the compiler just read those raws and decided they were "about" this page. (b) As an additive enrichment, run a nightly fuzzy match: body-text grep for the page's `title` and for each `[[slug]]` that appears in other pages' prose — populate `slug_mentions(source='body')`. Tier (a) is what unblocks Phase 2; tier (b) is follow-up polish.

**Does this break `validate_wiki`'s wikilink-resolves check?** No. `check_broken_wikilinks` walks body text for `[[target]]` and asserts `target` is a stem of some `wiki/**/*.md` file. Wikilinks are knowledge-to-knowledge — they don't reference raws. Only the `sources:` list moves out.

**Ops story for incremental ingest.** `build_catalog.py --incremental` takes `max(emails.ingested_at)` from the DB, walks `raw/*.md` filtered by file mtime > that, upserts rows. 100 new emails: sub-second. Drive it from the existing `make pipeline` target after `ingest` and before `compile`. The catalog is derived state; if it ever drifts, `--full-rebuild` regenerates it in a few seconds on 6,760 raws.

**What about `source_threads:` summaries?** Delete. The catalog with a `GROUP BY thread_id` gives us the same data at query time, more accurately (real `thread_id`, not a fuzzy subject-slug collision).

**What about `supersedes` / `superseded_by` frontmatter on policies?** Keep in frontmatter. That's knowledge about page lineage, not provenance. It belongs with the prose.

## 5. Quick recommendation — the smallest shippable slice

**Ship Phase 0 + Phase 2 behind a feature flag. That's the proof.**

One PR, two files added (`src/catalog/build.py`, `scripts/build_catalog.py`), one file touched (`mkdocs_hooks.py` gets a second code path guarded by `os.environ.get("USE_CATALOG_SOURCES")`). The existing `sources:` rendering path stays intact. Bootstrap `slug_mentions` by reading current `sources:` lists into the DB — no agent changes, no page rewrites, no lint changes.

Once that's live:
- Open the wiki with the flag off → renders from `sources:` as today.
- Open with `USE_CATALOG_SOURCES=1 mkdocs serve` → renders from catalog, grouped by thread, with correct role labels.

Visually compare the three worst pages (himanshu-jain01, bharat-agarwal, alok-shukla) side by side. If the catalog render is strictly better (fewer dupes, same role tags, cleaner grouping), you have your answer and Phases 1/3/4 become easy follow-ups with confidence. If it's worse, the catalog layer is discardable in one `git revert` and no existing file has lost data.

Do not try to do Phases 3+4 in the same PR. That's the ship-first instinct sliding into big-bang-refactor — the exact failure mode this question came from.
