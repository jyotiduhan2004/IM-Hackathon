# Deep Agents Learnings — Fixing the Wiki Compiler's Recurring Failure Modes

Context: our wiki compiler at `src/compile/compiler.py` runs on `deepagents==0.5.2`
(installed at `.venv/lib/python3.13/site-packages/deepagents/`) against a 463-page
corpus. It keeps making six repeatable classes of mistakes (duplicates, huge
str_replace payloads, hallucinated quotes, miscategorization, corrupted YAML,
duplicate `## Related` sections). This doc maps each pain point to what
Deep Agents already provides, what's missing, and the smallest set of custom
tools/hooks we should add.

Sources skimmed before writing:

- `deepagents/graph.py` — `create_deep_agent` signature and middleware assembly.
- `deepagents/middleware/filesystem.py` — built-in `read_file`/`write_file`/`edit_file` tools and the automatic large-tool-result eviction.
- `deepagents/middleware/permissions.py` — canonical example of `wrap_tool_call` pre/post hooks.
- `deepagents/middleware/subagents.py` — `SubAgent` typed dict + how the `task` tool dispatches.
- `deepagents/backends/filesystem.py` — `FilesystemBackend.write/edit` internals (no validation layer).
- `langchain/agents/middleware/types.py` — the `before_model`/`after_model`/`wrap_tool_call` hook protocol.
- Anthropic's "[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)" and "[Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)" posts.
- `examples/content-builder-agent/content_writer.py` in `langchain-ai/deepagents` (real-world multi-subagent layout using `FilesystemBackend`).

## What Deep Agents actually gives us that we're not using

### 1. `wrap_tool_call` / `awrap_tool_call` — per-tool pre/post hooks (our biggest miss)

`AgentMiddleware` exposes a pair of hooks that let us intercept every tool call,
inspect/modify args, reject with a synthetic `ToolMessage`, or post-filter the
result. The canonical example in-tree is `_PermissionMiddleware` in
`deepagents/middleware/permissions.py:343-369`:

```python
def wrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage | Command:
    tool_name = request.tool_call["name"]
    args = request.tool_call.get("args", {}) or {}
    denial = self._pre_check(tool_name, request.tool_call["id"], args)
    if denial is not None:
        return denial                     # short-circuit, tool never runs
    result = handler(request)             # actually call the tool
    if self._fs_rules and isinstance(result, ToolMessage) and result.artifact:
        result = self._post_filter(result)
    return result
```

This is exactly the guardrail surface we need for pains #2 (huge payloads),
#4 (miscategorization), #5 (corrupted YAML), and #6 (duplicate `## Related`).
We are calling `create_deep_agent(... middleware=None)` today. We should pass
a single `WikiGuardrailsMiddleware` and check/rewrite `edit_file` / `write_file`
calls before the handler runs.

### 2. `subagents=[...]` with a dedicated `SubAgent` spec

`deepagents/middleware/subagents.py:22-88` defines `SubAgent` as a TypedDict
with `name`, `description`, `system_prompt`, optional `tools`, `model`,
`middleware`, `interrupt_on`, `skills`, `permissions`. The main agent spawns
them via the auto-generated `task` tool (with an isolated context window).
Our compiler does everything in one monolithic thread — classifying, deduping,
verifying quotes, writing pages — which bloats context and causes cross-contamination.

We should split out at least two subagents:

- `deduplicator` — owns `find_similar_pages`, `list_wiki_pages`, `read_file`; returns a decision ("update X" vs "create new Y").
- `verifier` — owns `grep`, `read_file`, `verify_quote`; returns a boolean plus evidence for every quote on an entity page.

Example wiring (fits our current `create_compiler`):

```python
return create_deep_agent(
    model=model,
    tools=[...current tools...],
    system_prompt=system_prompt,
    backend=backend,
    middleware=[WikiGuardrailsMiddleware(wiki_dir=wiki_dir)],
    subagents=[
        {
            "name": "deduplicator",
            "description": "Before the main agent writes any new wiki page, "
                           "call this with the candidate slug and page_type; "
                           "returns a decision and the winning existing slug.",
            "system_prompt": DEDUPLICATOR_PROMPT,
            "tools": [find_similar_pages, list_wiki_pages, read_file_stub],
        },
        {
            "name": "verifier",
            "description": "Given a quote and a claimed author, confirm the "
                           "exact substring appears in at least one source email.",
            "system_prompt": VERIFIER_PROMPT,
            "tools": [verify_quote, grep_stub],
        },
    ],
)
```

### 3. `interrupt_on={...}` — cheap human-in-the-loop on dangerous tools

`create_deep_agent(..., interrupt_on={"edit_file": True, "write_file": True})`
pauses before every edit/write and surfaces a LangGraph interrupt. We don't
need a human — we can wire this to a checkpointer + auto-approval script that
applies our guardrails server-side. But for the narrow case of
"the agent is about to write to `wiki/entities/arjun-gaur.md` while
`arjun-gaur-clean.md` already exists", a hard interrupt is cheaper than a
middleware check we forgot to register.

### 4. Built-in large-tool-result eviction (we get it for free, didn't know)

`FilesystemMiddleware.__init__` defaults to `tool_token_limit_before_evict=20000`
(`deepagents/middleware/filesystem.py:581`). When any tool returns a payload
above that, it writes the full payload to
`/large_tool_results/<tool_call_id>` and replaces the ToolMessage with a
head+tail preview plus instructions to `read_file(path, offset=..., limit=...)`
to page through it. See `TOO_LARGE_TOOL_MSG` template
(`filesystem.py:384-393`). Our `list_wiki_pages` returning 463 slugs is
probably *under* this threshold (~10KB) so we're not getting eviction,
but the mechanism is there if we ever return the full source-list from
`find_similar_pages`.

### 5. `_FILESYSTEM_SYSTEM_PROMPT_TEMPLATE` and tool descriptions are overridable

`FilesystemMiddleware(custom_tool_descriptions={"edit_file": "..."})`
(`filesystem.py:617`) replaces the built-in `EDIT_FILE_TOOL_DESCRIPTION`.
We can add our domain warning directly into the tool description the agent
sees on every call: "Never use `edit_file` on YAML frontmatter blocks; call
`update_page_frontmatter` instead." Today we only preach this in
`COMPILER_SYSTEM_PROMPT`, which the model drifts away from.

### 6. `FilesystemBackend` is plain Python — subclass it

`deepagents/backends/filesystem.py:39-85` is a ~700-line class with six
methods (`read`, `write`, `edit`, `ls`, `glob`, `grep`). We can subclass
and override `write` + `edit` to add a "parse my output" dry-run before
flushing to disk. Minimal version:

```python
class ValidatingFilesystemBackend(FilesystemBackend):
    def write(self, file_path, content):
        err = _dry_run_validate(file_path, content)
        if err:
            return WriteResult(error=err)
        return super().write(file_path, content)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        # simulate the replacement in-memory, validate, only then commit
        resolved = self._resolve_path(file_path)
        if resolved.exists():
            current = resolved.read_text(encoding="utf-8")
            candidate = current.replace(old_string, new_string, 1 if not replace_all else -1)
            err = _dry_run_validate(file_path, candidate)
            if err:
                return EditResult(error=err)
        return super().edit(file_path, old_string, new_string, replace_all)
```

This kills pain #5 (corrupted YAML) and #6 (duplicate `## Related`) at
the single source of writes, no prompt engineering required.

## What it doesn't provide and we'd need to build

| Pain | Covered by deepagents? | What we build |
|---|---|---|
| #1 Duplicate pages (`arjun-gaur` vs `arjun-gaur-clean`) | No. `list_wiki_pages` is custom and flat; no fuzzy-match primitive. | `find_similar_pages(slug, threshold)` custom tool + a `deduplicator` subagent. |
| #2 26KB edit_file payloads | Partial. Eviction triggers on *outputs*, not inputs. | `wrap_tool_call` pre-check that rejects `edit_file`/`write_file` when `len(new_string) + len(content) > 8000` and tells the agent to call `update_page_frontmatter` or `append_section` instead. |
| #3 Hallucinated quotes | No. The built-in `grep` tool exists but isn't wired into a verification workflow. | `verify_quote(quote, claimed_author, source_paths)` tool + `verifier` subagent that is called before any entity-page write. |
| #4 Miscategorization (humans in `systems/`) | No. | `classify_page(slug, body)` tool returning `{page_type, confidence, reasoning}`, plus a guardrail in `wrap_tool_call` that blocks `write_file` when the target directory disagrees with the classifier. |
| #5 Corrupted YAML frontmatter | Mostly no. `FilesystemBackend.edit` does blind `str_replace`. | `ValidatingFilesystemBackend` subclass (see above) with a YAML dry-parse in `write`/`edit`. |
| #6 Duplicate `## Related` on updates | No. | `update_related(slug, new_links)` tool — canonical merge that parses, de-dupes, and rewrites the section, replacing ad-hoc `edit_file` calls. |

## Tool design patterns to adopt

Grounded in Anthropic's "[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)" and
"[Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)",
plus the conventions visible in the deepagents filesystem tools:

1. **Every tool returns a structured dict, never a bare string.** Use at
   minimum `{"ok": bool, "result": ..., "error": str|None, "suggestion": str|None}`.
   The `suggestion` field is what Anthropic calls "actionable improvements
   rather than opaque error codes." Our current `mark_as_compiled` returns
   `{"ok": "true", ...}` with a bool-as-string — fix it.

2. **Filter/reduce data at the tool boundary, not in the agent.** Anthropic:
   *"When working with large datasets, agents can filter and transform results
   in code before returning them."* `list_wiki_pages` returning a flat list
   of 463 slugs violates this. It should take `query: str | None` and
   `page_type: Literal[...] | None` and return at most 25 matches.

3. **Every write-side custom tool must validate before mutating.** Prefer
   `update_page_frontmatter(slug, fields)` over letting the agent
   round-trip 210 lines of YAML through `edit_file`. The tool parses,
   merges, validates, and writes — atomic. This is the "code executes,
   model decides" principle from the MCP post.

4. **Use `response_format: Literal["concise","detailed"]` on any tool
   that can be chatty.** Anthropic reports 67% token savings from this
   pattern on Slack tools. `list_uncompiled_emails` already returns
   abbreviated metadata but `list_wiki_pages` doesn't — add a `detail`
   param so the agent can skip filenames when all it needs is counts.

5. **Namespace related tools with a shared prefix.** Anthropic:
   *"Namespacing (grouping related tools under common prefixes) can help
   delineate boundaries between lots of tools."* Our custom tools should
   all start with `wiki_` (e.g. `wiki_find_similar`, `wiki_classify`,
   `wiki_update_related`, `wiki_verify_quote`) so the model picks from a
   clear cluster and doesn't confuse them with the built-in
   `read_file`/`grep`/`glob` tools.

6. **Make destructive calls opt-in, not default.** `write_file` in
   `FilesystemBackend` already refuses to overwrite an existing file
   (`backends/filesystem.py:366-367`) — a default we should honor. All
   mutations to existing pages go through typed update tools, not
   `edit_file`. Reserve `edit_file` for small surgical body edits and
   register it under `interrupt_on` for first 2 weeks of rollout.

7. **When rejecting a tool call in `wrap_tool_call`, include the name of
   the tool the agent should call instead.** Example rejection message:
   *"edit_file on frontmatter is disallowed. Call
   `wiki_update_page_frontmatter(slug='arjun-gaur', fields={...})` instead."*
   This mirrors how `FilesystemMiddleware` tells the agent to use
   `read_file(offset=...)` after truncation.

## Proposed 3-5 custom tools to add

Signatures use Python types; each kills at least one pain point.

### 1. `wiki_find_similar_pages` — kills #1

```python
@tool
def wiki_find_similar_pages(
    slug: str,
    page_type: Literal["topic","entity","system","policy","timeline","conflict"],
    threshold: float = 0.7,
    max_results: int = 5,
) -> dict:
    """
    Returns {"ok": True, "matches": [{"slug": str, "similarity": float,
    "page_type": str, "sample_sources": [str]}], "suggestion": str|None}.

    Before writing a new page, call this. If any match has similarity > 0.85,
    DO NOT create a new page — update the existing one. The 'suggestion' field
    will say which slug to update.
    """
```

Implementation: rapidfuzz (already in the dep tree via langchain?) or
`difflib.SequenceMatcher` on token-split slugs, plus alias-expansion
(strip `-clean`, `-new`, `-v2`, etc.) on every existing slug before compare.

### 2. `wiki_classify_page` — kills #4

```python
@tool
def wiki_classify_page(
    slug: str,
    body_sample: str,
    sources: list[str],
) -> dict:
    """
    Returns {"ok": True, "page_type": str, "confidence": float,
    "reasoning": str, "correct_dir": str}.

    Call before every write_file. If the target directory in your write path
    doesn't match 'correct_dir', fix the path before calling write_file.
    """
```

Rule-based first pass (slug ends in `.com` / `-team` / `-bot` → system;
slug matches `^[a-z]+-[a-z]+$` AND at least one source has
`from: *{slug}@` → entity). Wrap in a middleware that blocks
`write_file` when `file_path.startswith("wiki/")` and the chosen dir
disagrees with this classifier.

### 3. `wiki_update_page_frontmatter` — kills #2 and #5

```python
@tool
def wiki_update_page_frontmatter(
    slug: str,
    page_type: str,
    fields: dict[str, Any],
    merge_sources: bool = True,
) -> dict:
    """
    Canonical, atomic frontmatter update. Reads the page, parses YAML,
    merges/overwrites the named fields (sources are union-merged if
    merge_sources=True), validates the resulting YAML (via yaml.safe_load),
    writes the file. NEVER use edit_file on a frontmatter block.

    Returns {"ok": bool, "fields_written": [str], "error": str|None,
    "suggestion": str|None}.
    """
```

This is the most important new tool. It replaces 90% of `edit_file` calls
we see in logs.

### 4. `wiki_verify_quote` — kills #3

```python
@tool
def wiki_verify_quote(
    quote: str,
    claimed_author_email: str,
    source_paths: list[str],
) -> dict:
    """
    Scans each source file; returns whether the exact substring appears in a
    body block authored by claimed_author_email (not just CC'd).

    Returns {"ok": True, "verified": bool,
    "found_in": [str], "similar_found": [{"source": str, "text": str,
    "similarity": float}], "suggestion": str|None}.

    If verified=False, DO NOT attribute the quote. 'similar_found' may help
    you rewrite the claim accurately.
    """
```

### 5. `wiki_update_related` — kills #6

```python
@tool
def wiki_update_related(
    slug: str,
    add_links: list[str],
    remove_links: list[str] = (),
) -> dict:
    """
    Parse the page's existing '## Related' section, union-merge with add_links,
    drop remove_links, dedupe, sort, write back as a SINGLE canonical section.
    Returns {"ok": bool, "final_links": [str], "error": str|None}.

    Call this instead of appending '## Related' via edit_file.
    """
```

## Smallest shippable change — one tool + one guardrail this week

The highest-frequency failure mode in our logs is **pain #2** (agent passes
26KB of frontmatter through `edit_file`), which cascades into **#5** (YAML
corruption) and **#6** (duplicate sections). One middleware hook + one tool
kills all three.

**Ship:**

1. **`WikiGuardrailsMiddleware` with a single `wrap_tool_call` hook** that
   rejects any `edit_file` or `write_file` call whose payload contains a YAML
   frontmatter block (`content.lstrip().startswith("---\n")`) OR whose
   new_string/content exceeds 4000 characters AND targets a file under `wiki/`.
   Rejection returns a synthetic `ToolMessage` naming the right tool:

   ```text
   Error: large or frontmatter-bearing edits to wiki/ pages are disabled.
   Call wiki_update_page_frontmatter(slug=..., fields=...) for frontmatter
   changes, or wiki_update_related(slug=..., add_links=[...]) for
   Related-section changes. If you truly need a body edit, keep new_string
   under 4000 chars and do not touch the `---` block.
   ```

   File: add `src/compile/middleware.py` with one class; wire via
   `create_deep_agent(middleware=[WikiGuardrailsMiddleware()])`.

2. **`wiki_update_page_frontmatter` tool** (signature above). ~60 lines
   built on the existing `_extract_frontmatter` / `_render_with_frontmatter`
   helpers in `src/utils.py`. Internally re-parses the final YAML with
   `yaml.safe_load` and fails loudly if the dry-run raises.

**Measurable success criterion (one week):**

- Count of `edit_file` calls where `len(new_string) > 4000` against
  `wiki/**` files drops to zero (enforced by the guardrail).
- Count of pages with duplicate `last_compiled:` keys (caught by
  `mkdocs_hooks.py`'s YAML linter): new regressions = 0.
- Count of duplicate `## Related` sections across `wiki/`: drops
  monotonically per batch.

Everything else (subagents, classifier, quote verifier, dedup tool)
comes in a follow-up PR once we've measured this first hook.

## Where to wire each change in our repo

- `src/compile/compiler.py:374-385` (the `create_deep_agent` call): add
  `middleware=[WikiGuardrailsMiddleware(wiki_dir)]`, pass
  `subagents=[...]` when we split out the deduplicator/verifier.
- `src/compile/compiler.py:359` (the `FilesystemBackend` construction):
  swap in `ValidatingFilesystemBackend(root_dir=str(cwd), virtual_mode=True)`
  after the guardrail hook lands.
- `src/compile/prompts.py:COMPILER_SYSTEM_PROMPT`: shorten — move the
  "NEVER use edit_file on frontmatter" lecture into the tool description
  of `edit_file` itself, via
  `FilesystemMiddleware(custom_tool_descriptions={"edit_file": "..."})`.
  The prompt is easier to drift from than a tool description the model
  sees on every call.

## References

- Anthropic, [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Anthropic, [Code execution with MCP: building more efficient AI agents](https://www.anthropic.com/engineering/code-execution-with-mcp)
- Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- LangChain, [Deep Agents v0.5 release notes](https://blog.langchain.com/deep-agents-v0-5/)
- LangChain, [Deep Agents customization docs](https://docs.langchain.com/oss/python/deepagents/customization)
- `langchain.agents.middleware.types.AgentMiddleware.wrap_tool_call` reference
- `langchain-ai/deepagents` `examples/content-builder-agent/` (real-world multi-subagent + skills + `FilesystemBackend`)
