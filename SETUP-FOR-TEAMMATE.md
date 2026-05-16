# Email Knowledge Base — Teammate Setup

Hi Japsahaj / Jyoti — this archive is a snapshot of Amit's `email-knowledge-base` project, packaged for you to clone and run locally. Everything you need is inside; this README is the order to do it in.

If you hit anything weird, ping Amit (aa@indiamart.com) — the project is moving fast so docs may lag reality.

## What this project is

A **compiled, curated Wikipedia for IndiaMART** built from the mailing-list email firehose. Raw emails go in, an LLM agent compiles them into topic/system/decision pages in `wiki/`. Read `CLAUDE.md` and `docs/NORTH-STAR.md` for the design direction.

## What you're getting in this archive

| Path | What it is |
|---|---|
| `src/`, `scripts/`, `tests/` | Application code |
| `raw/` | Immutable email archive (~73 MB, real IndiaMART mailing-list content) |
| `wiki/` | Compiled wiki output (the product) |
| `docs/` | Design docs, audits, run snapshots |
| `.git/` | Full git history (repo: `indiamart-ai/email-knowledge-base`) |
| `.env` | API keys for shared services — see "What's pre-filled" below |
| `credentials.json` | Gmail OAuth **client** config (safe to share, identifies the GCP OAuth app) |
| `email_kb_dump.sql` | `pg_dump` of Amit's local Postgres database (89 MB live, ~37 MB raw) |

**Not included** (intentionally):
- `token.json` — that's Amit's personal Gmail OAuth refresh token. You'll mint your own.
- `.venv/`, `.snapshots/`, all caches, mkdocs build output, Claude Code session data.

## Prerequisites

- macOS or Linux
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) — package manager (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Postgres 17+ locally (`brew install postgresql@17 && brew services start postgresql@17`)
- GitHub access to the repo — **you already have it** (Japsahaj as `japsahaj13`, Jyoti as `jyotiduhan2004`).

## Setup steps

### 1. Extract and install

```bash
tar -xzf email-knowledge-base-2026-05-12.tar.gz
cd email-knowledge-base
make setup    # creates .venv, syncs deps via uv
```

### 2. Restore the database

```bash
createdb email_kb
psql email_kb < email_kb_dump.sql
```

The `DATABASE_URL` in `.env` defaults to `postgresql://localhost:5432/email_kb` — adjust the username if your local Postgres needs one.

> **Note on a shared cloud DB (future state):** Amit may migrate the database to the existing `voice-eval-db-development` Cloud SQL instance on `voice-eval-stack-im` so we all hit one Postgres. When that happens you'll just swap the `DATABASE_URL`. Until then, each of us runs a local copy seeded from this dump.

### 3. Gmail OAuth (so you can ingest mail as **yourself**, if you need to)

> **Read step 4 before doing this.** In a shared-DB world only one person should ingest. For local-DB work, do this if you want to keep your local copy fresh.

The included `credentials.json` is the OAuth client. It identifies the GCP OAuth app but does **not** grant access to anyone's mailbox. To wire up your own inbox:

1. Ask Amit to add you as a **test user** on the OAuth consent screen for GCP project `voice-eval-stack-im` (Gmail API → OAuth consent screen → "Test users").
2. Make sure you're subscribed to the IndiaMART product mailing list (the one set in `.env` as `MAILING_LIST_ADDRESS`).
3. Run `make ingest`. It opens a browser, you sign in with **your** `@indiamart.com` account and consent. Google writes a `token.json` to the repo root — that's your personal refresh token. Don't share it.

### 4. Important: Gmail thread IDs are per-mailbox

Worth understanding before you run ingest:

- The `messages` table is keyed on the **RFC 2822 `Message-ID` header** (the global ID set by the mail server). The same mailing-list email arriving in three different mailboxes has the same `Message-ID` for all three of us.
- Gmail's `thread_id`, however, is **per-mailbox**. Your mailbox assigns its own internal ID to a conversation, separate from Amit's.

What this means in practice:

| Setup | Outcome |
|---|---|
| Each of us on a local DB (today) | No issue. You ingest your mailbox into your DB; messages dedupe within your DB; thread IDs are consistent within your DB. |
| Shared cloud DB (future) | Messages will dedupe correctly across ingesters thanks to the `Message-ID` PK. But the `threads` table will be polluted with multiple per-mailbox thread IDs for the same conversation, and `messages.thread_id` will keep whichever value the *first* ingester wrote. |

**Operating rule on a shared DB:** designate **one canonical ingester** (Amit) and have everyone else skip `make ingest` — run `make compile` / `make pipeline` against the shared data. This avoids thread fragmentation. Once the project hardens, we can revisit by treating `threads` as a many-to-many of `(gmail_user, gmail_thread_id) → message_id` — but that's premature today.

### 5. (Optional) LiteLLM proxy

The default `LITELLM_BASE_URL=http://localhost:4000` expects a local LiteLLM proxy. If you don't want to run one, either:
- Set `LITELLM_BASE_URL=` (empty) and let the SDKs call providers directly using `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, or
- Spin up LiteLLM yourself: `pip install litellm[proxy] && litellm --config <your-config.yaml>` ([docs](https://docs.litellm.ai/docs/proxy/quick_start)).

### 6. Run the pipeline

```bash
make pipeline        # full: ingest → compile → dedupe → rollup → glossary → status sweep
# or individually:
make compile         # raw/ → wiki/ (skip ingest if working off the shared DB or this snapshot)
```

### 7. View the wiki

The compiled wiki is just markdown. Two options:
- **Local mkdocs**: `uv run mkdocs serve`
- **Deployed viewer**: https://email-kb-viewer-kntbneg73q-el.a.run.app (Cloud Run, `voice-eval-stack-im` project)

## What's pre-filled in `.env`

Amit's keys are included so you can run end-to-end immediately:

| Variable | Status |
|---|---|
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Amit's keys. **Swap in your own** once you have them — they're billed to his account. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Project-level credentials for `langfuse.intermesh.net`. Shared cost — leave as-is. Ask to be added to the Langfuse project for the UI. |
| `LITELLM_BASE_URL` | Points at `localhost:4000` — see step 5. |
| `GMAIL_CREDENTIALS_PATH`, `GMAIL_TOKEN_PATH` | Paths only — files are local. |
| `MAILING_LIST_ADDRESS` | The IndiaMART list this ingests from. |
| `DATABASE_URL` | Local Postgres. Edit if your local setup differs. |
| `LANGFUSE_ENABLED` | Default `false` — see `.env.example` for the reason. Enable when you want traces. |

## Access status

| Resource | Japsahaj | Jyoti |
|---|---|---|
| GitHub repo `indiamart-ai/email-knowledge-base` | Collaborator (`japsahaj13`) ✓ — currently `read`, ask Amit to bump to `write` if you'll be opening PRs | Collaborator (`jyotiduhan2004`) ✓ — currently `read`, same |
| GCP project `voice-eval-stack-im` | `voiceeval.developer` ✓ | `voiceeval.developer` ✓ |
| Gmail OAuth test user (`voice-eval-stack-im`) | Ask Amit to add | Ask Amit to add |
| Langfuse project on `langfuse.intermesh.net` | Ask Amit to invite | Ask Amit to invite |
| IndiaMART product mailing list | Confirm subscribed | Confirm subscribed |

## Where to start reading

1. **`CLAUDE.md`** — the agent contract: what the system does, page types, tool/coordinator split. Read this first.
2. **`docs/NORTH-STAR.md`** — canonical direction.
3. **`docs/proposal/NORTH-STAR-DRAFT.md`** — active design detail.
4. **`README.md`** — repo-level overview.
5. **`AGENTS.md`** — agent operations.
6. **`docs/BACKLOG.md`** — what's planned.

## Conventions

- Python 3.12+, strict typing
- `uv` for deps (`uv add foo`, never `pip install`)
- Ruff (line length 100, double quotes) + mypy strict
- `make check` before pushing
- `raw/` is immutable — never edit
- The agent maintains `wiki/` — humans don't edit pages directly

Welcome aboard.
