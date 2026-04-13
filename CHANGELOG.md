# Changelog

All notable changes to this project. Format loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/): dates YYYY-MM-DD,
newest first, grouped under `Added`/`Changed`/`Fixed`/`Removed`/`Docs`.

Where the *why* is non-obvious (incident postmortems, root-cause analyses,
specific bugs the code was written to avoid), we keep a detailed entry
below the summary — see the **Historical incidents** section.

---

## [Unreleased] — 2026-04-13

### Added
- Audit framework: 5 parallel background agents (newbie, PM, IA, factcheck,
  journalist personas) produce independent reports at
  `docs/reviews/audit-persona-*-20260413T040000Z.md`; synthesis at
  `audit-synthesis-20260413T040000Z.md` (commit `246bd7a`).
- Sources rendering shows where the entity appears (From ✍️, To 📬, CC 📋,
  body 💬) in `mkdocs_hooks.py::_render_raw_source` (commit `79fda4e`).
- 15-min per-batch timeout wrapper in `scripts/compile_overnight.sh` to kill
  silent LLM stalls (commit `9794efd`).
- GitHub issue #8: full knowledge-vs-index separation plan (SQLite catalog),
  with phased migration and feature-flag rollout.
- `docs/reviews/knowledge-vs-index-20260413T032000Z.md` — architectural plan
  for separating distilled knowledge (markdown) from email provenance
  (SQLite catalog).
- `docs/reviews/edit-tool-research-20260413T031839Z.md` — diagnosis of why
  edit_file rewrites large payloads.
- `docs/reviews/deepagents-learnings-20260413T050000Z.md` — deepagents
  library audit with concrete middleware patterns we should adopt.
- BACKLOG entries: inline-citation proposal, Anthropic engineering reading
  list, QMD (Tobi Lütke) evaluation as local semantic search layer.

### Changed
- Suffix-dupe regex widened previously to include `-temp`/`-draft`/`-rev`;
  still misses `-clean`, numeric (`\d+$`), and US/UK spelling variants —
  tracked for widening in GitHub issues.

### Fixed
- Bharat Agarwal / Sumit Gore confusion in rendered Sources — annotated
  each source with the page-owner's role on that email (commit `79fda4e`).
- Zombie `compile_all` processes from prior timeouts that ignored SIGKILL
  through `uv run`: cleaned up manually; root-cause kill not yet shipped.
- `scripts/backfill_stubs.py` unused `yaml` import (commit `9130e9d`).

### Known issues (not fixed yet — see GitHub issues)
- 64% of wiki pages are stubs (<100 words body). Compiler auto-creates
  stubs on unresolved `[[wikilinks]]`, hiding broken links.
- 11 near-duplicate page pairs (`-clean` family, numeric suffixes,
  US/UK spelling, cross-category collisions).
- 5 humans in `systems/`, 4 products/teams in `entities/`.
- `samarth` exists in both `entities/` AND `systems/` → ambiguous wikilink.
- `topics/sonarqube-quality-profile-transformation.md` has duplicate
  `last_compiled:` keys — validator doesn't catch it.
- Hallucinated quote attribution (`vikram-varshney.md` attributes words
  not present in any raw source).
- Source coverage gaps: `whatsapp9696-agentic-buyer-chatbot` cites 2 raws;
  raw/ has 30 on that thread.
- No glossary for domain acronyms (PNS/ISQ/CSL/MCAT/BMC/HRS).
- `conflicts/`, `policies/`, `timelines/` advertised but empty.

---

## Historical incidents — detailed postmortems

Entries below document specific bugs and the reasoning that shaped the
current code.

---

## 2026-04-13 — Phase 0 bootstrap + first compile iterations

### Entity page bloat → compile stalls on hot threads

**Issue**: Overnight iteration 5 and iteration 6 both hung at the same
point — a single-email batch from a 10-email SonarQube thread that
mentioned many core IndiaMART engineers. Each hang: no TCP activity, no
budget movement, no file writes, but process alive. After ~28min I
killed manually.

**Root cause**: NOT the email length (~10KB), NOT the thread length
(67KB total). The problem is **accumulated entity pages**. After
hundreds of prior compiles, hot entities like Himanshu Jain (26KB),
Bharat Agarwal (20KB), Neeraj Vardhan Ponnada (19KB), Sayan Samanta
(17KB) have grown massive — mostly bloated `sources:` lists with 100+
entries each.

When the compiler agent processes a new email that touches 2-3 such
entities, it reads each 20KB+ entity page into its context. Plus email,
plus system prompt, plus accumulating tool call history. Effective
context per LLM call can exceed 80-100k tokens. At that size, either
the LiteLLM proxy times out silently or the model becomes pathologically
slow.

**Short-term fix**: skip-mark the hung thread's 10 emails so the loop
moves on. We already have a canonical wiki page
(`sonarqube-quality-profile-transformation.md`) covering that topic.

**Long-term fix (BACKLOG)**:
- Cap entity page size: compressed summary sections, don't append
  forever. Maybe cap sources at most-recent N + link to git history for
  full list.
- Don't load full entity pages into agent context: expose
  `summarize_entity(slug)` tool that returns ≤500 tokens instead of raw
  read. Agent uses summary; full read only on demand.
- Stall timeout in compile_overnight.sh: wrap compile_all in
  `timeout 900` so stalls auto-terminate after 15min.

Noted in docs/BACKLOG.md.

---


### Frontmatter parser broke on `---` inside raw filenames

**Issue**: After TPM bump, 3 wiki pages corrupted mid-compile: frontmatter
contained only `last_compiled`; sources/title/body fragments scattered.

**Root cause**: `content.split('---', 2)` naively matched the `---`
sequence wherever it appeared. A raw email filename like
`_informational---transforming-sonarqube-_3b4ad89f.md` (triple-dash from
subject tag) sat inside the frontmatter's `sources:` field. Split treated
those hyphens as the frontmatter terminator, leaving YAML truncated, and
auto-stamp then preserved only `last_compiled` throwing away everything
else. Chain of bugs.

**Fix**:
- `src/compile/compiler.py`: new `_split_frontmatter()` walks lines and
  treats `---` as delimiter only when it's on its own line (proper YAML
  frontmatter semantics). `_extract_frontmatter` and `_extract_body`
  delegate to it.
- Repaired 3 damaged pages manually — reconstructed frontmatter from body
  fragments (sources, related, title from path, page_type from directory).
  Body content preserved.
- Also deleted one byte-identical duplicate (`google-ads-new.md` was a
  second slug for `google-ads.md`).

**Commit**: `49f2741`.

### TPM rate limits contributed to mid-edit corruption

**Issue**: LiteLLM proxy returned 429s every ~60 seconds. Agent's
`edit_file` sometimes abandoned mid-edit, leaving pages with partial
frontmatter.

**Root cause**: TPM (tokens per minute) cap was 30000 on the key. Each
agent turn sends 15-40k tokens of context. Bursts trivially exceeded
the cap.

**Fix**: User bumped TPM to 3,000,000 (100×). Retry logic and per-batch
timeouts still TBD (captured in BACKLOG).

**Commit**: external (proxy config).

### Coherence drift between docs and code

**Issue**: Background coherence-audit agent flagged: README described
files that didn't exist (`relations.py`, `wiki/search.py`, etc.);
CLAUDE.md page-type table missing `system` category; BACKLOG items
shipped but not marked done; `mail-parser` dep declared but never
imported; several scripts (validate_wiki, snapshot_wiki, budget,
watch_and_compile) undocumented in README.

**Root cause**: Rapid iteration; readme was written before the final
layout stabilized; BACKLOG was append-only, nothing ever struck through.

**Fix**: Rewrote README structure section to match actual filesystem;
added `system` page type to CLAUDE.md; struck through shipped P0 items
in BACKLOG; removed `mail-parser` dep.

**Report**: `docs/reviews/coherence-20260413T023902Z.md`.

---


### Frontmatter corruption during `edit_file` → auto-stamp made it worse

**Issue**: After the 2nd 20-email compile batch, 18 wiki pages had mangled
frontmatter — only `last_compiled` remained; title, page_type, sources, and
body fragments were destroyed.

**Root cause**: Two bugs compounded:
1. Agent's built-in `edit_file` tool (Deep Agents) was clipping YAML during
   string-based edits when the prompt asked it to modify a page's
   frontmatter. The result was unparseable YAML or partial frontmatter.
2. Our `update_wiki_index` auto-stamped `last_compiled` on every page missing
   it. When run against a page with ALREADY-broken frontmatter, it called
   `_extract_frontmatter` → got `{}` (or partial) → added only `last_compiled`
   → rewrote the page losing everything else. It effectively "preserved" the
   corruption.

**Fix**:
- `src/compile/compiler.py`: auto-stamp now checks that frontmatter has
  `title` or `page_type` before overwriting. If the page looks mid-broken,
  leave it alone so we can see the damage.
- `scripts/validate_wiki.py`: new hard validator. Runs after compile.
  Detects: orphan frontmatter (only `last_compiled`), missing required
  fields, page_type vs directory mismatch, duplicate bodies.
- `scripts/compile_all.py`: auto-snapshots `wiki/` to
  `.snapshots/pre-compile-{ts}/` BEFORE compiling. Runs validator
  AFTER. If validation fails, snapshot label is printed so we can
  `restore`.

**What NOT to do again**: Delete broken pages. We did this in the
recovery and lost real compute ($ spent on those pages). Prefer repair —
salvage the body, reconstruct frontmatter from path + context.

**Commits**: `01b8e50` (validator + auto-snapshot), `ae5f0e1` (prompt
hardening).

---

### Duplicate file bodies: `systems/export-indiamart.md` == `systems/tawk-to.md`

**Issue**: Two wiki pages had byte-identical bodies. `export-indiamart.md`
actually described tawk.to.

**Root cause**: Agent ran compile across two emails discussing related but
distinct systems (export.indiamart.com domain vs tawk.to live chat) and
wrote the same content to both slugs. No deduplication guard existed.

**Fix**:
- Manual: deleted the wrong dupe, rewrote `trustseal-buyer-program.md`
  wikilinks to point to the correct `exporters-indiamart`.
- Added `check_duplicate_bodies` lint (sha256 of body minus timestamp).

**Commit**: `ae5f0e1`.

---

### page_type misclassification: humans in systems/, products in entities/

**Issue**: `systems/amarinder-s-dhaliwal.md` was a human (CEO-level exec);
`entities/wazuh-mcp.md` was software.

**Root cause**: Agent's stub-creation heuristic in `lint_wiki.py`
classified unresolved wikilinks as "entity" if they looked like
`first-last` (2-part kebab). `amarinder-s-dhaliwal` has 3 parts so was
bucketed as system. `wazuh-mcp` fit the 2-part pattern and landed in
entities.

**Fix**:
- Manual: moved files between directories, updated `page_type` field.
- Added `check_page_type_mismatch` lint (directory vs frontmatter).
- Prompt: hardened to restate that entities = humans, systems =
  products/platforms/URLs/mailing-lists; if in wrong category, MOVE not
  duplicate.

**Commit**: `ae5f0e1`.

---

### Title Case wikilinks broke 210+ cross-references

**Issue**: First-ever compile wrote `[[Amit Agarwal]]`, `[[BuyerMY]]` style
Title Case wikilinks. None resolved because actual files used kebab-case.

**Root cause**: Prompt said "use `[[wikilinks]]`" without requiring case.

**Fix**:
- Prompt: explicit GOOD/BAD examples for kebab-case; "NEVER Title Case".
- `lint_wiki.py normalize_wikilinks()`: case-insensitive + slugify match
  against existing files, rewrites to canonical kebab.
- `lint_wiki.py create_missing_stubs()`: for unresolved targets, create a
  minimal stub page rather than leaving the link broken.
- Run `make lint-wiki-fix` after each compile.

**Commits**: `235dc74`, `b95f7da`.

---

### Deep Agents virtual filesystem silently swallowed writes

**Issue**: First compile reported "batches complete" but produced 0 wiki
pages on disk. Only the log.md entries and raw `compiled: true` flags
showed activity.

**Root cause**: Deep Agents' default backend is an in-memory virtual
filesystem. The agent's `read_file`/`write_file` tools wrote to ephemeral
state, not disk. Our custom tools (which used real `Path.write_text`) did
work — that's why the flags flipped but pages never appeared.

**Fix**:
- `src/compile/compiler.py`: `create_compiler` now passes
  `backend=FilesystemBackend(root_dir=".", virtual_mode=True)` to
  `create_deep_agent`. `virtual_mode=True` gives path traversal
  guardrails (no `..`, no absolute paths outside root) while persisting
  to real disk.
- Prompt: explicit GOOD/BAD path examples with leading-slash warnings.
- Bumped `recursion_limit` to 150 (agent does ~15-20 tool calls per
  email when working carefully).

**Commit**: `ddd0c5a`.

---

### `init_chat_model` couldn't route `z-ai/glm-5` / LiteLLM proxy models

**Issue**: Compile errored with "Unable to infer model provider for
model='z-ai/glm-5'".

**Root cause**: LangChain's `init_chat_model` has a fixed list of known
provider prefixes; LiteLLM-proxy model IDs like `z-ai/glm-4.6` don't
match any.

**Fix**:
- `src/compile/compiler.py`: new `_make_chat_model()` helper. If
  `LITELLM_BASE_URL` is set, construct `ChatOpenAI(model=name,
  base_url=..., api_key=...)` directly — LiteLLM is OpenAI-compatible, so
  any model the proxy knows works.
- `.env`: used `z-ai/glm-4.6` (not `glm-5` — proxy didn't have that).

**Commit**: `b6368d6`.

---

### Date hallucination (`last_compiled: "2025-01-10..."`)

**Issue**: Wiki pages had random 2025 dates in `last_compiled`, not the
real current time.

**Root cause**: Agent was writing `last_compiled` itself, using its
training-cutoff date rather than the real clock.

**Fix**:
- Prompt: "NEVER write `last_compiled` yourself — use
  `stamp_page_compiled_at` tool".
- New tool `stamp_page_compiled_at(file_path)` that sets the field using
  `datetime.now(UTC).isoformat()`.
- `update_wiki_index` also auto-stamps any missing timestamps as a
  safety net.

**Commit**: `ddd0c5a`.

---

## Format for future entries

```
### Short title

**Issue**: one-paragraph description.

**Root cause**: *why* it broke, not just *what* broke.

**Fix**: what we changed, ideally with commit sha.

**What NOT to do again**: lessons if applicable.
```
