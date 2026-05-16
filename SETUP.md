# Duckie — Setup Guide

Set up Duckie on your own machine with your own email data. You'll need your own API keys, Gmail credentials, and a mailing list to ingest from.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **Gmail API credentials** (OAuth2 — [create here](https://console.cloud.google.com/apis/credentials))
- **LLM access** — either:
  - Anthropic API key (for query agent — Claude Sonnet 4)
  - LiteLLM proxy (for compilation — supports 80+ models)
- **PostgreSQL** (for compile state tracking)

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/jyotiduhan2004/IM-Hackathon.git
cd IM-Hackathon
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your own values:

```env
# LLM — pick one:
ANTHROPIC_API_KEY=sk-ant-your-key-here
# OR use LiteLLM proxy:
LITELLM_BASE_URL=http://your-litellm-proxy:4000
OPENAI_API_KEY=your-litellm-key

# Gmail OAuth
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
MAILING_LIST_ADDRESS=your-mailing-list@company.com

# Query agent model
QUERY_LLM_MODEL=anthropic/claude-sonnet-4

# Paths (defaults work)
RAW_DIR=raw
WIKI_DIR=wiki
```

### 3. Set up Gmail OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create an OAuth 2.0 Client ID (Desktop application)
3. Download the JSON and save as `credentials.json` in the project root
4. First run will open a browser for OAuth consent — this creates `token.json`

### 4. Ingest emails

```bash
# Pull emails from your mailing list (last 30 days)
uv run python scripts/ingest_backlog.py --days 30

# Or specify a date range
uv run python scripts/ingest_backlog.py --after 2026-01-01 --before 2026-03-01
```

This creates `.md` files in `raw/` — one per email with YAML frontmatter (subject, from, to, date).

### 5. Compile wiki

```bash
# Compile all unprocessed emails into wiki pages
uv run python scripts/compile_all.py

# Dry run first to see what would be compiled
uv run python scripts/compile_all.py --dry-run
```

This creates structured wiki pages in `wiki/` — organized by concept, not by email thread.

### 6. Build search indexes

```bash
# Build Chroma vector index + wiki_tree.json + wiki_graph.json
uv run python scripts/index_wiki.py
```

### 7. Run the query UI

```bash
# Streamlit UI
uv run streamlit run src/ui/app.py
```

Open http://localhost:8501 — start asking questions.

### 8. Run the MCP server (for Claude Code integration)

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "duckie": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/IM-Hackathon", "python", "/path/to/IM-Hackathon/src/mcp/server.py"]
    }
  }
}
```

Then open Claude Code in any directory — it will auto-discover `ask_duckie()`.

## Project Structure

```
IM-Hackathon/
├── src/
│   ├── ingest/          # Gmail API → raw/ emails
│   ├── agent/           # Compiler agent + 12 middleware
│   ├── coordinator/     # Thread grouping, model pool, preflight
│   ├── query/           # Query agent + 10 tools + 6 skills
│   │   ├── agents/      # main_agent.py (single LLM session)
│   │   ├── tools/       # search, wiki_reader, qmd_search
│   │   └── config.py    # LLM config, paths
│   ├── mcp/             # MCP server (ask_duckie)
│   └── ui/              # Streamlit chat UI
├── skills/
│   ├── query/           # 4 G-Brain query skills
│   └── compile/         # 4 compilation skills
├── scripts/             # Entry point scripts
│   ├── ingest_backlog.py
│   ├── compile_all.py
│   └── index_wiki.py
├── raw/                 # Immutable email .md files (gitignored)
├── wiki/                # Compiled wiki pages (gitignored)
├── .env.example         # Template — copy to .env
└── pyproject.toml       # Dependencies
```

## Pipeline Flow

```
Gmail API → raw/ (emails)
    ↓
Compiler Agent (LLM + 12 middleware + 6 tools)
    ↓
wiki/ (structured pages, one per concept)
    ↓
Index (Chroma + wiki_tree.json + wiki_graph.json)
    ↓
Query Agent (10 tools, 4 skills, Claude Sonnet 4)
    ↓
Streamlit UI  ←or→  MCP Server (Claude Code)
```

## Key Commands

| Task | Command |
|------|---------|
| Install dependencies | `uv sync` |
| Ingest emails | `uv run python scripts/ingest_backlog.py --days 30` |
| Compile wiki | `uv run python scripts/compile_all.py` |
| Build indexes | `uv run python scripts/index_wiki.py` |
| Start UI | `uv run streamlit run src/ui/app.py` |
| Start MCP server | `uv run python src/mcp/server.py` |

## Notes

- **raw/ and wiki/ are gitignored** — you build your own knowledge base from your own emails
- **No data is included** — this is the code only. Your wiki is as good as your email data
- **Compilation uses LLM calls** — budget accordingly (~$0.01-0.05 per email batch)
- **Query uses Claude Sonnet 4** — ~$0.03-0.06 per query via LiteLLM proxy
- **First qmd_search call takes ~15s** (model loading) — subsequent calls are ~1-2s
