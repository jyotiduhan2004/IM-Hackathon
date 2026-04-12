# Issue: CLAUDE.md — Agent Schema for LLM Wiki Operations

**Labels**: `documentation`, `phase-0`, `agent`

---

## Status: Implemented in Phase 0

The `CLAUDE.md` file at repo root is the schema document that tells any LLM agent how to
operate on this knowledge base. It's the Karpathy pattern's "third layer" — the governance
schema that sits alongside raw/ and wiki/.

See [CLAUDE.md](../../CLAUDE.md) for the full content.

## What it covers

- Tech stack and constraints
- Directory structure (raw/, wiki/, src/)
- Commands (make ingest, make compile, make lint-wiki)
- Page types and when to use each
- Status values (current, superseded, contested)
- Operations: INGEST, COMPILE, QUERY, LINT
- Wiki page format (YAML frontmatter requirements)
- Supersession rules
- Conflict rules
- Cross-referencing conventions
- Hard rules (what the LLM must NEVER do)
- Code conventions

## Symlink

`AGENTS.md` is a symlink to `CLAUDE.md` so tools that read either will find it.

## Acceptance criteria

- [x] CLAUDE.md at repo root
- [x] AGENTS.md symlinked to CLAUDE.md
- [x] Covers all operations
- [x] Defines all page types with examples
- [x] Documents supersession and conflict rules
- [x] Includes naming conventions
- [x] Lists commands for common operations
