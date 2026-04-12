# Overnight Compile Plan — 2026-04-13 (447 uncompiled / $17.89 / ~8h)

## TL;DR

Run `compile_parallel.py --concurrency 4` under `caffeinate`. Skip mid-run audits. Budget projects ~$3.58; you have 5x slack. Wall-clock target ~3h, leaving 5h reserve.

## 1. Throughput — go parallel at c=4

Sequential is 11.2h — won't finish. TPM is no longer the cap.

- **Concurrency: 4.** ~4x speedup → ~2.8h for 447. Deep Agents does ~15-20 tool calls per email, so c>=8 bursts back into TPM limits despite the bump and amplifies shared-entity write collisions on hot pages (`amit-agarwal.md` appears in dozens of threads).
- **Batch size: 5** (default). Thread-grouped, so most batches are 1-3 emails.
- **Risks**: (a) last-write-wins on shared entity pages — the script documents this; `make lint-wiki-fix` reconciles. (b) Cross-thread supersession is lost — acceptable for one-day backlog. (c) No per-batch timeout in `_run_batch_async` — a hung worker stalls 1/4 of throughput, not the run. Accept it.

## 2. Quality gates — audit at the END, not mid-run

- `validate_wiki.py` (hard check: orphan frontmatter, dup bodies, page_type vs dir) already runs after `compile_all.py` — `compile_parallel.py` does NOT call it. **Run it manually post-run.**
- Running the audit agent every N batches is waste — burns budget, and omission/role-drop issues can't be fixed without a re-compile anyway.
- **Drift canary without audit**: in a side terminal, every 30 min check `grep -c "compiled: true" raw/*.md` (expect +60-80) and `ls wiki/entities | wc -l` (expect +5-10). A jump >30 entities/30min = hallucination run — kill and restore.

## 3. Budget — fine, don't switch models

- Observed $0.004-0.008/email. 447 x $0.008 = **$3.58 worst case**. Reserve: $14.31.
- **Don't swap models.** glm-4.6 is 2-5x cheaper via LiteLLM than gpt-4.1-mini / sonnet-4 / gemini-2.5-flash, and today's bug fixes (virtual FS, casing, dates, dedup) are tuned to *this* model. Re-validating a new model at 2am is how you wake up to a corrupted wiki.
- If LiteLLM UI shows spend >$10 partway, kill — that's retry storms, not work.

## 4. Failure modes

- **Compiler crash**: `mark_as_compiled` flips `compiled: true` per email. Re-run picks up only `false`. Clean resume.
- **Corrupted pages**: `snapshot_wiki.py restore <label>`. (Parallel script does NOT auto-snapshot — step 1 below captures one manually.)
- **Budget exhaustion**: LiteLLM returns 402 → batches fail → unflipped emails wait for retry. No data loss.
- **Machine sleep**: kills asyncio. Use `caffeinate -i`.
- **Manual only**: (a) body-dupe reconciliation (`export-indiamart` / `tawk-to` style) — validator flags, you decide. (b) entity/system page_type swaps if validator fails.

## 5. Execution sequence

```bash
cd /Users/amtagrwl/git/email-knowledge-base

# [T+0, 2 min] Preflight
uv run python scripts/snapshot_wiki.py save
uv run python scripts/compile_parallel.py --dry-run
uv run python scripts/validate_wiki.py

# [T+2min, ~3h] Launch
caffeinate -i uv run python scripts/compile_parallel.py \
  --concurrency 4 --batch-size 5 \
  2>&1 | tee .logs/compile-$(date -u +%Y%m%dT%H%M%SZ).log

# [T+30min, 60min, 90min] Sanity
grep -c "compiled: true" raw/*.md
grep -c "ERROR in batch" .logs/compile-*.log

# [T+~3h] Post-run
make lint-wiki-fix
uv run python scripts/validate_wiki.py
# On failure: uv run python scripts/snapshot_wiki.py restore <label>

# [morning] Audit a 20-page sample (same template as audit-20260412T200427Z.md)
```

## 6. Skip tonight

From BACKLOG.md — DO NOT touch:

- Email-based entity IDs (P1) — rewrites every page.
- Per-batch timeout/retry logic — not tonight's bottleneck.
- Responses API, Langfuse, attachments, Google Groups, datastore, thread-state, schema versioning — post-backlog.
- Orphan back-link reverse pass — let lint auto-stub, reconcile in morning.
- Non-person entity taxonomy — validator catches the worst cases.

Morning work (30 min): audit sample, reconcile any body dupes, fix page_type mismatches by hand.

Report path: `/Users/amtagrwl/git/email-knowledge-base/docs/reviews/overnight-plan-20260412T210837Z.md`
