# Edit-tool research for the email-knowledge-base compiler

Date: 2026-04-13
Scope: READ-ONLY research; recommends changes for the Deep Agents-based compiler at `src/compile/compiler.py`.

## Symptom recap
Compiler agent edits single lines in 26 KB pages by emitting near-full files.
Output budget (~6k) makes each edit marginal, and bloated pages stall the run.

## How the leading agents edit

**Claude Code / Anthropic `text_editor` (`str_replace`).** Exact
`old_string`/`new_string` replacer; must be unique or pass `replace_all`. The
LLM emits only the hunk. Uniqueness + must-read-first enforced server-side.
Cheapest token-wise: ~20-80 tokens per 1-line change. Deep Agents' `edit_file`
is a direct clone (see
`.venv/.../deepagents/backends/filesystem.py:385-442` ->
`perform_string_replacement`). Our tool is already surgical; the bug is the
model choosing to rewrite.

**Cursor (Fast Apply / speculative edits).** Big model emits a sketch with
`// ... existing code ...` markers; a cheap 70B fine-tune on Fireworks merges
it at ~1000 tok/s via speculative decoding (the original file IS the draft).
Cursor found full rewrite beats aider diffs under 400 lines because diffs
hurt quality more than they save tokens.

**OpenAI Codex CLI (`apply_patch` / V4A).** Envelope format
(`*** Begin Patch / *** Update File / @@ context / - old / + new`), no line
numbers; GPT-5 post-trained for it. CLI also exposes `local_shell` behind
approval gates; `apply_patch` is preferred.

**Aider (SEARCH/REPLACE + udiff).** S/R blocks and `diff -U0`. udiff lifted
GPT-4-Turbo pass rate 26% -> 59% by reducing "lazy" skipping. Aider also
notes full-rewrite often wins for files <400 lines.

**Morph Fast Apply.** Standalone 7B apply model, OpenAI-compatible API,
$0.80/M input, MCP that drops into Claude Code/Cursor. POST original + lazy
edit + instruction, get merged file at ~10.5k tok/s, 98% accuracy. 200 free
req/month.

**Deep Agents `edit_file` (ours).** Confirmed via
`filesystem.py:385-442` + `utils.py:335`: already str-replace, does NOT
rewrite. The LLM is sending whole-file `old_string`/`new_string` because
nothing in prompts discourages it and bloated pages push it into
"just-regenerate" mode.

## Why we're bleeding tokens anyway
1. Prompts / tool descriptions don't strongly nudge minimal hunks.
2. On a 26 KB bloated page the model can't cheaply locate a unique
   `old_string`, so it panics and sends a big block.
3. There is no `bash` escape hatch for mechanical ops (dedupe sources,
   truncate a section, regex-replace a stale date).
4. `write_file` is available and refuses overwrites (line 366-367), but the
   agent can `edit_file` with the whole file and succeed — same cost.

## Recommendation (ship-first, one PR)

**Do NOT add a new apply-model or diff format. Our tool is fine; we need
guardrails and one escape hatch.** Estimated effort: 0.5-1 day.

1. **Enable Deep Agents' `execute` tool** (shell) scoped to `wiki/` only.
   `FilesystemMiddleware` already ships `execute`; it's just not exposed
   in our `compiler.py`. Whitelist: `sed -i`, `awk`, `rg`, `wc`, `head`,
   `tail`, `python -c`. Deny network + any write outside `wiki/`. Use the
   existing `virtual_mode=True` root to enforce. (File: `src/compile/compiler.py`
   around line 341-359.)
2. **Cap `edit_file` payload.** Pre-tool-call middleware: if
   `len(old_string) + len(new_string) > 4096` chars, reject with a message
   steering the model to `execute` (`sed`) or a smaller hunk. Deep Agents
   supports tool middleware — implement as a `patch_tool_calls` hook.
3. **Prompt surgery.** In `src/compile/prompts.py`, add a 2-line rule:
   "For edits >200 lines, use `execute` with `sed`/`awk`, not `edit_file`.
   Never pass >50 lines into `old_string`." And: "If an entity page
   exceeds 20 KB, split or prune before continuing — do not rewrite it."
4. **Page-size circuit breaker.** If a wiki page crosses, say, 30 KB, the
   compiler should bail that page out into a compaction sub-task instead
   of letting the agent repeatedly rewrite it. Cheap check in the
   compiler loop.

### What NOT to do right now
- Don't integrate Morph / Fast Apply yet. It's a $-per-call dependency for
  a problem that is actually our prompt + input size. Revisit if, after
  the above, we still see >1k-token edits on healthy-sized pages.
- Don't introduce udiff/SEARCH-REPLACE formats. Claude models (we use
  them) are trained on `str_replace`, not on aider formats. Switching
  formats would regress quality.
- Don't ship a raw `bash` tool without path confinement — the Claude Code
  team has a documented bypass issue (#31292) where `sed -i` trivially
  evades `disallowedTools: [Write, Edit]`. Our risk is lower (we run
  locally, single repo) but keep the whitelist + root confinement.

## OSS we could drop in (if #1-4 isn't enough)
- **Morph MCP server** (`morphllm.com/mcp`): ~1 hour to wire, $0.80/M,
  one more moving piece.
- **Aider's editblock coder** (`aider/coders/editblock_coder.py`): steal
  the S/R prompt wording, not the tool.
- **`apply_patch` reference impl** (`github.com/openai/codex` -> crate
  `apply-patch`): if we ever switch to GPT-5 as the compiler model, this
  is the format to adopt.

## Code refs
- Our compiler: `/Users/amtagrwl/git/email-knowledge-base/src/compile/compiler.py:341-359`
- Our prompts: `/Users/amtagrwl/git/email-knowledge-base/src/compile/prompts.py`
- Deep Agents edit impl: `/Users/amtagrwl/git/email-knowledge-base/.venv/lib/python3.13/site-packages/deepagents/backends/filesystem.py:385-442`
- `perform_string_replacement`: `/Users/amtagrwl/git/email-knowledge-base/.venv/lib/python3.13/site-packages/deepagents/backends/utils.py:335`

## Sources
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool
- https://cursor.com/blog/instant-apply
- https://fireworks.ai/blog/cursor
- https://www.morphllm.com/fast-apply-model
- https://www.morphllm.com/mcp
- https://github.com/openai/codex/blob/main/codex-rs/apply-patch/apply_patch_tool_instructions.md
- https://developers.openai.com/api/docs/guides/tools-apply-patch
- https://aider.chat/docs/more/edit-formats.html
- https://aider.chat/docs/unified-diffs.html
- https://github.com/paul-gauthier/aider/issues/625
- https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/filesystem.py
- https://github.com/anthropics/claude-code/issues/31292
- https://code.claude.com/docs/en/sandboxing
