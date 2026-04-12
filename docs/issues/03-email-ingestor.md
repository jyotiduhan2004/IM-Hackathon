# Issue: Build Email Ingestor — Gmail API + Parser → raw/ Markdown

**Labels**: `feature`, `phase-0`

---

## Overview

Build the email ingestion pipeline: connect to Gmail, fetch emails from a mailing list,
parse them, and store each as an immutable markdown file in `raw/`.

## Status: Implemented in Phase 0

Files:
- `src/ingest/gmail.py` — Gmail API client with OAuth, list_messages, get_message, get_attachment
- `src/ingest/parser.py` — Convert Gmail API message → raw markdown with YAML frontmatter
- `src/ingest/attachments.py` — Download attachments, save to raw/attachments/, optional image captioning
- `scripts/ingest_backlog.py` — CLI entry point (Click-based)

## Gmail API setup

1. [Google Cloud Console](https://console.cloud.google.com/) → create project → enable Gmail API
2. Create OAuth 2.0 Client ID (Desktop application type)
3. Download JSON as `credentials.json` in repo root
4. First run opens browser → user consents → `token.json` saved
5. Subsequent runs refresh token automatically

## Usage

```bash
# Pull last 30 days
uv run python scripts/ingest_backlog.py --days 30

# Specific date range
uv run python scripts/ingest_backlog.py --after 2026-01-01 --before 2026-04-01

# Dry run (list without saving)
uv run python scripts/ingest_backlog.py --days 30 --dry-run

# Skip attachment downloads
uv run python scripts/ingest_backlog.py --days 30 --skip-attachments
```

## Gmail API query

Filters by mailing list + date:
```
list:your-list@company.com after:2026/03/11 before:2026/04/12
```

## Data flow

```
Gmail API
  │
  ├── list_messages(list_address, after, before) → [MessageStub(id, thread_id), ...]
  │
  └── for each message:
        ├── get_message(id) → RawMessage (headers, body, attachment refs)
        ├── parse_message(raw) → ParsedEmail (structured)
        ├── save_attachments(client, parsed, raw_dir) → [attachment paths]
        ├── to_raw_markdown(parsed, attachment_paths) → markdown with YAML frontmatter
        └── write to raw/{YYYY-MM-DD}_{slug}_{msg-id-short}.md
```

## Deduplication

Before saving, checks if `raw/` already contains a file for this message_id (via the
`msg-id-short` hash). Duplicates are silently skipped.

## Error handling

- Gmail API rate limits: exponential backoff via the client library
- Invalid email encoding: falls back to errors="replace" on decode
- Missing body: stores with "(no body content)" placeholder
- Attachment download failure: logs warning, continues without that attachment

## Acceptance criteria

- [x] OAuth flow works (browser-based first time, refresh after)
- [x] `--days N` fetches last N days
- [x] Each email → one `.md` file in `raw/` with YAML frontmatter
- [x] Attachments in `raw/attachments/{msg-id-short}/`
- [x] `compiled: false` set on all new raw files
- [x] Duplicates are skipped (running twice is idempotent)
- [ ] Image captioning (wired up but not invoked by default — Phase 1)
- [ ] Thread-aware grouping (Phase 1)
