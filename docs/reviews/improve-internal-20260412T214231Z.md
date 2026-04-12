# Improvement Plan — 2026-04-12T21:42Z

Scope: what to fix next, grounded in CHANGELOG, two audits, coherence review, overnight plan, dipstick reports, and recent commits (`9c19bf6` → `c11e146`). Repo is mid-Phase-0 iteration; 2947/2948 raw emails are already flagged `compiled: true` (dipstick cap of 254 wiki pages suggests most of that is pre-audit state — worth confirming).

---

## Top 5 improvements

### 1. Second-pass stub-filler (0-source entity/system pages)

Both audits (pass-1 rec #1, pass-2 rec #1/#6) flagged the same failure: `lucky-agarwal`, `amarinder-s-dhaliwal`, `wazuh-mcp` exist with `sources: []` yet are referenced from topic pages. `src/compile/prompts.py:204-210` tells the agent to grep — it doesn't do it reliably. Dipstick shows 36 lint warnings / 16 info per run; most are stubs.

- Change: new tool in `src/compile/compiler.py` — `backfill_entity_sources(entity_slug)` that globs `raw/*.md`, greps for the person's email or name, rewrites `sources:`.
- New script `scripts/backfill_stubs.py` that enumerates `wiki/entities/*.md` + `wiki/systems/*.md` with `sources: []` and fires that tool for each.
- Gate: fail `validate_wiki.py` if any entity/system has empty `sources:` (hard error, not warning).
- Why: no amount of prompt pressure has fixed this across 3 compiler iterations. Move the retry loop out of the LLM.

### 2. Bug/ticket table preservation

Both audits flagged the exact same content loss: `ios-performance-fix-login-flow-v13-6-6.md` dropped 7 test cases + 2 bug IDs; `dynamic-smart-rfq-form.md` dropped 12 bug IDs; `auditmate.md` dropped Ticket 646247 + 4 open bugs. Pass-2 rec #2, prompt at `prompts.py:167-178`.

- Change: in `src/ingest/parser.py`, detect markdown tables in email body and tag them in raw frontmatter as `has_tables: true` with a `table_signatures` list of column headers.
- In prompt, add rule: "If `has_tables: true` on a source, the resulting wiki page MUST contain at least as many table rows as the source." Add a post-compile check in `scripts/validate_wiki.py` that asserts this row-count invariant.
- Why: the prompt currently says "preserve tables verbatim" but has no verification; this is the single highest-value content gap in every audit.

### 3. Pre-compile dead-wikilink and duplicate-body gates

Pass-2 rec #4 found `[[buylead]]` resolves to nothing; pass-1 found `export-indiamart` == `tawk-to` byte-identical bodies. `scripts/lint_wiki.py` already has `check_duplicate_bodies` and wikilink checks, but they run as warnings, not failures.

- Change: `scripts/validate_wiki.py` — promote `broken_wikilink` and `duplicate_body` from warning to error (non-zero exit). Hook into `scripts/compile_all.py:213-228` so the run fails loudly.
- Why: dipstick `test-baseline.md` shows the validator already failed (2 pages missing frontmatter) but compile continued; and `smoke.md` shows 36 warnings the run happily walked past. Gates without teeth don't catch regressions.

### 4. Per-batch timeout and retry for `compile_parallel.py`

Overnight plan section 1 explicitly accepts "no per-batch timeout" as a risk. BACKLOG P1 #7 ("Batch stall at 15/22") is still open. At concurrency 4, one hung `agent.invoke` blocks 25% of throughput indefinitely.

- Change: in `scripts/compile_parallel.py:117-135`, wrap `asyncio.to_thread(agent.invoke, ...)` with `asyncio.wait_for(..., timeout=600)`. On timeout: log thread_id, do NOT mark as compiled, continue. On `httpx.ReadTimeout` / 429 / 5xx: retry once with 30s backoff.
- Also add to `compile_all.py` sequential path for parity.
- Why: prerequisite for running the 2947-email full backlog unattended. Without it, any one bad request stalls the whole overnight run.

### 5. Documentation truth-up

Coherence review §1 and §4 documented ~8 drift points, most still live in README.md and CLAUDE.md. Also BACKLOG §"Performance: parallelize compilation" is marked DRAFTED but duplicated down at line 82.

- `README.md:186-245` — delete `src/compile/relations.py`, `src/wiki/index.py`, `src/wiki/search.py`, `src/wiki/lint.py`, `src/api/server.py` from the Project Layout. They don't exist. Diagram at `README.md:120-136` also references `relations.py` — drop it.
- `CLAUDE.md:64-71` — add `system` to the page-type table (it's the sixth type in `prompts.py` and `compiler.py`).
- `README.md` — add Quick Start entry for `make pipeline`, `make snapshot`, `validate_wiki.py`; mention `LITELLM_BASE_URL` env var.
- `pyproject.toml:13` — remove `mail-parser>=3.15.0` (dead dep per coherence §3).
- BACKLOG — strike through P0 #1/#2/#3/#5, the duplicate "wikilink casing" section (lines 211-221), and the duplicate "date hallucination" section (lines 195-208).
- Why: auditors and the agent itself read these docs. False claims about `relations.py` and the missing `system` type directly caused the entity-misclassification bug that two audits flagged.

---

## Drift / contradictions still present

- **Langfuse posture**: README:180 and `07-phased-delivery.md:22` both sell it as shipped/on. `.env.example` default is `LANGFUSE_ENABLED=false`. BACKLOG§"Observability" recommends deferring until Phase 2. Pick one posture and reconcile.
- **Phase 0 DoD**: `07-phased-delivery.md:38` says lint must report clean. Audits confirm it does not. Either lower the bar (warnings allowed) or add the blocking gates in improvement #3.
- **BACKLOG§"Performance: parallelize"** is marked DRAFTED at top (line 73) but the same idea repeats un-struck-through at line 82 ("[original content below]") — contradictory status.
- **Dipstick `test-baseline.md` shows validator failed** with missing frontmatter on two pages (`ashish-verma.md`, `julee-kumari.md`); not mentioned anywhere else. Is this repaired or still broken in `wiki/`?
- **Thread-aware batching**: BACKLOG§"Thread-aware compilation — not yet implemented" (line 377) is stale — `compile_all.py:38-70` and `compile_parallel.py:55-85` both group by `thread_id`. Strike or rewrite.

---

## Hidden gaps / risks not yet discussed

- **LiteLLM proxy budget exhaustion mid-run**: `src/budget.py` snapshots before/after, but nothing checks mid-run. At concurrency 4 with a 402-return path, failed batches leave emails un-flipped but wiki pages half-written. Add a `fetch_budget()` check every N batches; abort if `remaining < safety_floor`.
- **Langfuse single point of failure**: If `LANGFUSE_ENABLED=true` and the self-hosted instance (`https://langfuse.intermesh.net`) is down, the callback handler will either swallow errors or block. Confirm graceful degradation — a tracing outage must not kill a 3-hour compile.
- **Snapshot storage growth**: `compile_all.py:123-130` writes a full `.snapshots/pre-compile-<ts>/wiki/` copy every run. With 254+ pages per run and hourly testing, `.snapshots/` will balloon. No retention policy anywhere. Add `scripts/snapshot_wiki.py prune --keep N`.
- **OAuth token rotation**: `token.json` lives in repo root, refreshed by `google-auth`. If refresh fails silently during a Phase 1 live-run, ingestion stops with no alerting. `src/ingest/gmail.py` needs a health-check endpoint or a startup assertion.
- **`credentials.json` and `token.json` in repo root**: I see them in `ls` output; confirm `.gitignore` covers both. Leaking either is a security incident.
- **Per-thread race on shared entities**: `compile_parallel.py:13` says "LAST write wins for shared pages" — but no file lock, no CAS. If two batches both open `wiki/entities/amit-agarwal.md`, read, merge, write, the later write overwrites the earlier merge. Not eventual consistency — data loss. Accept risk for now, but add a post-run diff-against-all-raw check.
- **Gmail API quota**: 2947 emails in `raw/` each required a `get_message` call. No backoff visible in `src/ingest/gmail.py`; a backlog of 10k+ could trip quota mid-fetch.
- **`watch_and_compile.py` drift**: exists, zero docs, unused. Either wire into `Makefile` with a label ("experimental") or delete.

---

## What's working well — don't touch

- **CHANGELOG.md discipline** — commit-linked, root-cause + "what NOT to do again" format. Keep.
- **Auto-snapshot + validator wrapper** around `compile_all.py` — caught real corruption once (CHANGELOG `01b8e50`).
- **`src/compile/prompts.py`** — every hard rule traces to a specific past failure. This is the highest-signal file in the repo.
- **Thread-grouped batching** (`compile_all.py:38-70`) — both audits confirm multi-email merges work correctly.
- **Dipstick report format** (`docs/runs/smoke.md`) — lightweight, reusable, gives throughput + integrity + spot checks in one view.
- **`_split_frontmatter` fix** (`compiler.py`, CHANGELOG entry 1) and `_make_chat_model` LiteLLM routing — both are subtle correctness fixes that should stay.
- **Audit-then-fix cadence** — two audits in 24h driving two prompt-hardening commits is the healthiest loop in this repo. Keep scheduling audits after every significant compile run.
