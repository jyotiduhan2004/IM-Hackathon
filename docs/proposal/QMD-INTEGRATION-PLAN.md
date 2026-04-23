---
title: qmd integration plan — Phase 1 (resolve_page retrieval)
plan_date: 2026-04-23
status: decided
related:
  - docs/proposal/GBRAIN-QMD-LEARNINGS.md
  - docs/audits/v12-north-star-2026-04-19.md
---

# qmd integration plan — Phase 1 (resolve_page retrieval)

The decision artifact for "put qmd behind resolve_page." Every choice below was ratified in the 2026-04-23 decision conversation; this doc captures the plan, not the rationale (that lives in `GBRAIN-QMD-LEARNINGS.md`).

## Objective (one sentence)

Replace SQL-ILIKE retrieval inside `resolve_page` with qmd's hybrid BM25 + vector + LLM-rerank pipeline, running as a locally-supervised daemon, to eliminate the alphabetical-candidate regression and cut agent retries per compile.

## Success criteria — measurable

**Day-0 spike GO bar** (before any real code)
- On a ~30-query sample drawn from recent Langfuse `resolve_page` observations, qmd's top-5 candidates contain a plausibly-related page for **≥80%** of queries (human-eyeballed; "plausibly related" = defensible as a reasonable first-pass in isolation).
- If <80%, STOP. qmd isn't fitting our domain and we go back to the priority list.

**Post-merge signals to watch** (shipped default-on; manual rollback via `USE_QMD_RESOLVE=0`)
- `is_alphabetical_candidate_list` rate (already instrumented) — target near-zero.
- Calls-per-miss p50 — target drop from ~2-3 to ~1.
- Compile cost per email — target flat or down from $0.156/email (cycle-10 baseline).
- Dedupe-agent merge-candidate rate — target drop (qmd surfacing the right page = fewer duplicate creations).

No pre-declared latency kill threshold. Manual judgement by watching Langfuse.

---

## Phase 0 — Day-0 spike (half-day, kill-cheap)

### Steps

1. **Install qmd locally**
   - `bun install -g @tobilu/qmd` (or `brew install qmd` if that ships)
   - First `qmd query` pre-downloads GGUF models (~2GB) to `~/.cache/qmd/models/`. Time one-shot, expect 5-15 min depending on connection.

2. **Index wiki/ excluding people/** (per-directory collections — qmd 2.1 `collection add` has **no `--exclude` flag**, per the spike audit)
   - `for d in topics systems decisions policies domains; do qmd collection add "$(git rev-parse --show-toplevel)/wiki/$d" --name "wiki-$d"; done`
   - `qmd embed --all`
   - Expect ~2 min for ~400 non-person pages (306 topics + 96 systems + 10 domains + misc). People pages (`wiki/people/`) stay uncollected — canonical path is `create_entities`.

3. **Start daemon for the test run**
   - `qmd mcp --http --daemon --port 8181`
   - Verify via `curl http://localhost:8181/health` (qmd's HTTP liveness endpoint).

4. **Pull real query sample from Langfuse**
   - Query Langfuse for the last ~500 observations with `tool_name = "resolve_page"`.
   - Extract `(query, outcome)` pairs — outcome = hit-slug / miss-then-create-slug / miss-then-skip.
   - Random-sample 30 queries weighted to cover: exact-slug lookups (~40%), prose conceptual (~30%), URL / dotted-host leaks (~10%), short single-token (~10%), multi-word with spaces (~10%).

5. **Evaluate top-5 sensibility**
   - For each sampled query: run `qmd query "<text>" --limit 5`, record top-5 slugs with scores.
   - Also run current `resolve_page("<text>")` via Python REPL, record top-5 candidates.
   - Eyeball each qmd top-5: "sensible" = at least one of the 5 is plausibly on-topic. Record `sensible | not_sensible` per query.
   - Pass: ≥24/30 (80%) sensible on qmd side. Compare to SQL for context.

6. **Record latency**
   - p50 / p95 of `qmd query` calls (expect 5-15s warm).
   - Note cold-start latency (first 2-3 calls after daemon idle).

7. **GO/NO-GO**
   - If ≥80% sensible: go to Phase 1.
   - If <80%: write a one-page summary of where qmd failed, close out, revisit priority list.

### Artifacts from Phase 0

- `docs/audits/qmd-spike-2026-04-23.md` — sample queries, top-5 results, sensibility scores, latency numbers, GO/NO-GO call.

---

## Phase 1 — Week 1 build (only if Phase 0 passes)

### Files to add

- `src/compile/tools/qmd_client.py` — async HTTP client to `http://localhost:8181/mcp/...` endpoints. Thin. Returns candidates in existing `resolve_page` envelope shape for day-1 compat. **Exact-slug short-circuit first**: if `query` matches a known slug exactly (case-insensitive, after `_normalize_query`), skip qmd and return the direct hit. Confirmed necessary from the 2026-04-23 spike — qmd was weak on bare numbers, short code identifiers, and person-email slugs (failure modes #11 and #23); exact-slug lookup covers all three without an HTTP round-trip.
- `src/compile/qmd_daemon.py` — daemon lifecycle helpers: `is_alive() → bool`, `start()`, `stop()`. Uses `pgrep` / `curl /health` for liveness (qmd's canonical liveness endpoint; `/status` does not exist).
- `tests/test_qmd_client.py` — unit tests with mocked HTTP responses.
- `tests/test_qmd_integration.py` — integration tests gated by `QMD_INTEGRATION=1` env var; skipped in default CI, run in nightly.
- `Makefile` additions: `qmd-install`, `qmd-index`, `qmd-sync`, `qmd-start`, `qmd-stop`.

### Files to modify

- `src/compile/tools/raw_access.py:resolve_page` — add `USE_QMD_RESOLVE` env flag check. When on, route through `qmd_client`; preserve existing `_normalize_query` pre-processing. When off or on fallback, current SQL path.
- `scripts/compile_all.py` — (a) daemon auto-start before first batch, (b) post-batch reindex hook: `qmd import --changed-since <batch_start> && qmd embed --stale`.
- `src/observability/langfuse_scores.py` (or existing span-emitter) — add `retriever: qmd | sql-fallback` attribute to the resolve_page span.
- `.env.example` — document `USE_QMD_RESOLVE` and `QMD_DAEMON_PORT` (default 8181).
- `.gitignore` — confirm `~/.cache/qmd/` is out of our tree (it is; lives in home).

### Response envelope decision

Keep existing `{exists, slug, title, page_type, status, confidence, candidates, auto_corrected_from, auto_corrected_to}` envelope day-1 for drop-in compat. Add two new fields: `retriever: qmd | sql-fallback` AND `snippet: str | None` per candidate (qmd already computes these in the MCP response; 3-line addition, zero new infra). Snippet aids agent decision-making on miss-then-candidate-list branches. Agent code unchanged apart from reading the new field where useful.

No new confidence threshold — use qmd's rerank score normalised to 0-1 as `confidence`. Agent decides what to do with it via its existing heuristics. We'll tune the hit/miss cutoff post-trace observation, not now.

### Docstring strategy — MEASUREMENT-FIRST

- **Day-1 docstring stays close to current** (`src/compile/tools/raw_access.py:64-98`) with minimal tweaks:
  - One sentence added: "Hybrid retrieval under the hood; candidates are relevance-ordered, not alphabetical."
  - One sentence added: "People pages are excluded from this index — use `create_entities` for people."
- **No invented WHEN/WHEN-NOT rules beyond what's there today.** No invented budget note. No invented confidence thresholds.
- **Week 2: observe traces, iterate docstring based on observed behavior.** Specifically pull patterns like: how often does the agent retry? What query shapes fail? Where does the agent trust the top-1 vs read down the candidate list? Each pattern becomes one line of docstring.

### Deployment shape

- **qmd runs as a native supervised service.** On my Mac: a LaunchAgent plist that runs `qmd mcp --http --daemon --port 8181` on user login, restart-on-crash. Install via `make qmd-install`.
- **On CI**: mixed strategy. Unit tests mock qmd. A separate `integration` CI job (nightly, and on PRs touching `qmd_client.py` or `compile_all.py`) starts qmd in the runner, indexes a 10-page fixture, runs ~5 end-to-end tests. ~3-5 min added to that job; PR feedback stays fast.
- **Ships as default-on after merge.** No prolonged flag-off window. `USE_QMD_RESOLVE=0` flips it back instantly.

### Fallback behavior

- If qmd daemon is not reachable on port 8181, `qmd_client` returns `{retriever: "sql-fallback"}` and `resolve_page` silently uses the current SQL-ILIKE path.
- Log the fallback to Langfuse span attribute + Python stderr.
- No hard-fail. Compile proceeds.
- Rollback is manual: if the alphabetical-candidate rate stays high, or compile cost jumps, or I don't like how it feels, `USE_QMD_RESOLVE=0`.

### Week-1 Definition of Done

- [ ] `qmd_client.py` + `qmd_daemon.py` shipped with unit tests passing.
- [ ] `scripts/compile_all.py` auto-starts daemon and runs post-batch reindex.
- [ ] Langfuse traces show `retriever` attribute on resolve_page observations.
- [ ] Makefile targets work (`make qmd-install`, `make qmd-index`, `make qmd-sync`).
- [ ] `USE_QMD_RESOLVE=1` smoke of `make compile` on a 5-email test batch succeeds end-to-end.
- [ ] PR merged and deployed default-on (`USE_QMD_RESOLVE=1` by default).

---

## Phase 2 — Week 2 measurement + docstring iteration

### Smoke

- Run `make compile` `--limit 30` with qmd default-on (cycle-11-smoke).
- Compare to cycle-10 smoke baseline on the four tracked signals.

### Trace review (measurement-first docstring)

- Pull all resolve_page observations from the cycle-11 smoke (expect ~60-120 calls across 30 emails).
- Categorize observed patterns:
  - What query shapes did agents send? (prose / slug / URL / mixed)
  - What's the retry distribution? (1 call vs 2+ calls per concept)
  - When agents got a top-5 list, which candidate did they trust? (top-1 always, or did they read down?)
  - Where did agents create a page despite qmd surfacing a reasonable match? (missed-merge signal)
  - Where did qmd fail outright? (no sensible candidate at all)
- For each pattern frequent enough to matter (≥3 occurrences), add ONE docstring line grounded in the observation. No speculative rules.

### Outcome options after Phase 2

- **Win**: signals trend right, docstring iterated, keep going.
- **Neutral**: signals flat, review what's different from spike expectations, targeted tuning (confidence threshold, query expansion on/off, whether to add intent bias).
- **Loss**: signals regress materially, flip `USE_QMD_RESOLVE=0`, write a post-mortem. No recrimination — we designed for cheap rollback.

---

## Phase 3 — deferred, tracked here so we don't forget

### 3a. Add raw/ as a second collection
- Index `raw/**/*.md` (~6,759 emails). ~30 min initial embed.
- Unlocks semantic search over email evidence — directly supports V12-U3 inline footnotes.
- Ship when (a) wiki integration stable, and (b) V12-U3 lands and we need `[^msg-id]` → content lookups.

### 3b. Two-tool split (`find_exact` + `find_similar`)
- User's idea from Round 1: split resolve_page into `find_exact(slug)` for fast exact lookups and `find_similar(query)` for qmd-backed semantic search.
- Revisit after ≥2 weeks of trace data. If traces show a clean bimodal pattern (agents call for exact slugs vs prose queries with predictable distribution), the split is worth the code cost. If queries are mixed-mode, single tool + good ranking is simpler.

### 3c. Backlink-boost in ranking
- Blocked on typed-edge graph (Priority 2 in the broader plan). One `LEFT JOIN` + one `ORDER BY` change in `qmd_client`'s result-ordering layer once `wiki_edges` exists.

---

## Rollback plan — always available

- **Fast rollback**: `USE_QMD_RESOLVE=0` in `.env`, next compile uses SQL-ILIKE. No code change.
- **Revert-merge rollback**: if the shim itself misbehaves structurally, revert the Phase-1 PR. Existing SQL path is preserved.
- **Daemon issues only**: qmd daemon down → silent SQL fallback already handles it. No human action.

---

## Files touched (expected)

### New
- `src/compile/tools/qmd_client.py`
- `src/compile/qmd_daemon.py`
- `tests/test_qmd_client.py`
- `tests/test_qmd_integration.py`
- `docs/audits/qmd-spike-2026-04-23.md` (after Phase 0)
- `ops/launchd/com.indiamart.qmd.plist` (or equivalent systemd unit for Linux dev boxes)

### Modified
- `src/compile/tools/raw_access.py` — route via qmd when flag on, preserve normalize_query
- `scripts/compile_all.py` — daemon auto-start + post-batch reindex hook
- `src/observability/` — retriever attribute on resolve_page span
- `.env.example` — document flags
- `Makefile` — qmd targets

### Untouched (explicitly)
- Existing compile middleware (chronological_scope, same_thread_topic_guard, path_autoheal, entity_write_autoheal, sibling_draft_check) — independent of retrieval.
- Existing prompt (V12-U1-U4 is a separate track).
- Postgres schema (qmd has its own SQLite index in `~/.cache/qmd/`).

---

## Open items — decided NOT to decide today

- **Confidence hit/miss threshold.** Keep qmd's rerank score as-is in `confidence` field. Agent's existing heuristics work. Tune after Phase 2 traces.
- **Docstring WHEN/WHEN-NOT rules beyond day-1 minimums.** Grounded only in observed agent behavior, not speculation.
- **Budget hint in docstring.** Only added if traces show a retry-wasting pattern.
- **Query-expansion tuning (currently always on).** Keep default; revisit if traces show slug-shaped queries getting expanded into noise.
- **Intent parameter for biasing rankings.** Not used day-1. Consider only if person-page exclusion alone isn't enough.
- **Raw/ indexing (phase 3a), two-tool split (3b), backlink-boost (3c).** Each has its own gate.

---

## One-line status tracker

- [x] Phase 0: spike — **GO** (2026-04-23, 85% hit rate on 40 Langfuse queries — sampled seed=42 — see audit at `docs/audits/qmd-spike-2026-04-23.md`)
- [ ] Phase 1: build + merge
- [ ] Phase 2: smoke + measure + docstring-from-traces
- [ ] Phase 3a: raw/ collection — deferred
- [ ] Phase 3b: two-tool split — deferred, observation-gated
- [ ] Phase 3c: backlink-boost — blocked on typed edges
