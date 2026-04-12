# Issue: Build Wiki Compiler — Deep Agents Workflow (raw → wiki)

**Labels**: `feature`, `phase-0`, `agent`

---

## Overview

LLM-powered wiki compiler that reads raw emails and creates/updates interlinked wiki pages.
This is the core of Karpathy's pattern — the "compilation step" that transforms raw sources
into accumulated, cross-referenced knowledge.

Uses [Deep Agents](https://github.com/langchain-ai/deepagents) (built on LangGraph) with
LiteLLM for model-agnostic LLM access.

## Status: Implemented in Phase 0

Files:
- `src/compile/compiler.py` — `create_compiler()` factory, custom tools, `run_compilation()` entry
- `src/compile/prompts.py` — System prompt + classification/supersession prompts
- `scripts/compile_all.py` — CLI entry point, batched compilation

## Why Deep Agents?

The compiler needs:
1. Read files from `raw/` — Deep Agents has `read_file`, `glob`, `grep` built in
2. Read/write files in `wiki/` — built-in `write_file`, `edit_file`
3. Plan which pages to update — built-in planning/todos
4. Delegate sub-tasks — built-in `task` for sub-agents
5. Work with any LLM — model-agnostic via `init_chat_model` (works with LiteLLM)

We add 4 custom tools for email-specific operations:
- `list_uncompiled_emails(raw_dir)` — find raw files with `compiled: false`
- `mark_as_compiled(file_path)` — set `compiled: true` in a raw file
- `update_wiki_index(wiki_dir)` — regenerate `wiki/index.md`
- `append_to_log(entry, wiki_dir)` — append timestamped entry to `wiki/log.md`

## Usage

```bash
# Compile all uncompiled emails
uv run python scripts/compile_all.py

# Use a specific model
uv run python scripts/compile_all.py --model gpt-4o

# Batch size (default 20)
uv run python scripts/compile_all.py --batch-size 10

# List uncompiled without compiling
uv run python scripts/compile_all.py --dry-run
```

## Compilation flow

```
1. List uncompiled emails (compiled: false)
2. Sort chronologically, group by thread_id
3. For each email or thread:
   a. Read raw file(s)
   b. Classify: what topics/entities/policies are mentioned?
   c. For each affected wiki page:
      - glob wiki/**/{page-name}.md to check existence
      - If exists: read, merge new info, write updated version
      - If not: write new page
      - Detect supersession: is old guidance being overridden?
      - Detect conflicts: does this contradict existing content?
   d. mark_as_compiled on each processed raw file
4. update_wiki_index at end of batch
5. append_to_log with summary
```

## Langfuse tracing

Every LLM call in compilation is traced via Langfuse (if keys are set in `.env`):
- Full conversation history
- Tool calls and responses
- Token usage per batch
- Latency per step

Set `LANGFUSE_ENABLED=false` in `.env` to disable.

## Acceptance criteria

- [x] `uv run python scripts/compile_all.py` processes uncompiled emails
- [x] Wiki pages created with YAML frontmatter (title, page_type, status, sources, last_compiled)
- [x] Index.md updated after compilation
- [x] Log.md has entries for compilation events
- [x] All raw files marked `compiled: true` after processing
- [x] Langfuse traces visible when keys are configured
- [x] Running twice with no new emails produces no changes
- [ ] Supersession relations properly expressed (relies on LLM following prompt — verify with tests)
- [ ] Conflict pages created when contradictions exist (verify with tests)
