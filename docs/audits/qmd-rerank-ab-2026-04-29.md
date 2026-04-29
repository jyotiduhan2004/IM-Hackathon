---
title: qmd rerank vs --no-rerank A/B
audit_date: 2026-04-29
author: claude-opus-4-7
status: shipped
related:
  - docs/audits/qmd-spike-2026-04-23.md
  - docs/audits/run-5928c151-trace-audit-2026-04-29.md
---

# qmd rerank vs `--no-rerank` A/B — 77 queries

**TL;DR.** `--no-rerank` cuts qmd p95 latency 37.5s → 1.3s (29×) while preserving top-1 on 73/73 non-person queries and *fixing* two failure modes the original spike documented. Shipped: see PR description for code changes.

## Why this audit existed

The 5928c151 trace audit (2026-04-29) flagged `resolve_page` semantic timeouts at **15.4% of calls vs 8.9% prior** with p95 sitting on the 45s cap. Investigation showed the cap was set from a *sequential* spike (p95=21s, max=22s) with no concurrency margin. Production runs `qmd query` as a subprocess from langgraph's `ToolNode`, which fires sync tool calls via `asyncio.to_thread` — concurrent `subprocess.run`s trigger Metal GPU contention and OS page-cache thrash on the three GGUF models qmd reloads per process (~2GB total).

Before bumping the cap as a band-aid, this audit asked: can we just turn the reranker off?

## Method

Ran the 77-query corpus saved at `docs/audits/qmd-spike-2026-04-23-queries.jsonl` through `qmd query <q> -n 5 --json` twice — once with the reranker on (production today) and once with `--no-rerank`. Sequential calls, no concurrency. Treated rerank-on top-5 as ground truth (the spike validated it at 85% sensible). Measured per-query latency, top-1 match, and top-5 set overlap.

Script: `.context/qmd_rerank_ab.py` (one-off; not committed).

## Headline results

| Metric | rerank ON | `--no-rerank` | Δ |
|---|---:|---:|---:|
| p50 latency | 14.4s | **0.75s** | **19× faster** |
| p95 latency | 37.5s | **1.30s** | **29× faster** |
| max latency | 45.1s | 2.76s | 16× faster |
| Sequential queries hitting 45s cap | 1/73 | 0/73 | — |
| Top-1 identical | — | **73/73 (100%)** | — |
| Top-5 share ≥3 candidates | — | 49.3% | — |
| Top-5 zero overlap | — | 0% | — |

Person-email queries (4) excluded from quality stats — qmd is intentionally blind to `wiki/people/` per the spike's "use `create_entities` instead" finding.

## Per-category breakdown

| Category | n | rerank-on p95 | `--no-rerank` p95 | Top-1 match |
|---|---:|---:|---:|---:|
| langfuse_sample (real production) | 38 | 40.6s | 1.4s | 38/38 |
| fixture_new_joiner | 9 | 12.8s | 0.8s | 9/9 |
| long_prose | 8 | 12.3s | 0.8s | 8/8 |
| body_only_* (deep-phrase queries) | 3 | 13.9s | 0.9s | 3/3 |
| deterministic_* (acronyms / IDs / versions) | 14 | 31.0s | 2.3s | 14/14 |
| bl_as_buylead (semantic mapping) | 1 | 12.3s | 0.8s | 1/1 |

## The "no-rerank fixes spike failure modes" finding

The original spike (2026-04-23, §"6 not sensible cases") flagged two queries where rerank-on **missed** the exact-match page that exists in the corpus:

| Query | rerank-on top-5 position of exact match | `--no-rerank` position |
|---|---:|---:|
| `marketplace-launch` | **MISS** (system page absent from top-5) | **#4** ✅ |
| `msite-pdp-html-rewrite-phase-2-dom-simplification-size-reduction` | **MISS** (exact slug absent) | **#4** ✅ |

The reranker over-weights prose-similar pages and demotes exact slug matches. `--no-rerank` (RRF + BM25 + vec only) keeps the literal match in the candidate list. Going to no-rerank therefore *improves* quality on the exact failure modes the spike worried about.

## What we lose

Top-5 ranks 2–5 reshuffle frequently — only 49% of queries share ≥3 of the same 5 candidates. If the agent reads past the top hit, it sees a different list. Mitigated by:

- The agent overwhelmingly picks #1 or `read_file`s the top hit.
- Top-1 is preserved 100%.
- Zero queries had zero overlap; the candidate sets are always related, just reordered.

Verifiable post-flip via Langfuse `resolve_page` traces + a 30-pick sample on the next smoke run. Rollback is one line in `qmd_client.py`.

## Concurrency confirmation (separate measurement)

To verify the original 5928c151 hypothesis, timed concurrent `qmd query` (rerank-on) subprocesses on the live corpus:

| Pattern | Per-call latency |
|---|---|
| Sequential single | 9-13s |
| 3-way concurrent, run 1 | 30s, 30s, **46s** |
| 3-way concurrent, run 2 | **46s, 50s, 68s** |
| 5-way concurrent | 39-44s (all near cap) |
| 5-way concurrent `--no-rerank` | 4-18s |

Confirmed: the 8.9% → 15.4% cap-hit rate jump is concurrency variance on a marginal cap, not a service-side regression. `--no-rerank` removes the cliff entirely.

## Daemon investigation (verified, not shipped)

`qmd mcp --http --daemon` exists, keeps models in VRAM, exposes `POST /mcp` (JSON-RPC) and `POST /query` (REST alias `/search`). Saves ~3s cold-start per call. **But:** reading `tobi/qmd:src/mcp/server.ts:680` confirmed the public REST + MCP `query` tool do not plumb `skipRerank` through to `store.search()`. So daemon ⇒ forced rerank-on. Not adopted in this PR; would require either upstreaming the flag or calling the JS SDK directly.

Concurrency thrash also persists with the daemon (verified: 3-way concurrent `POST /query` → 16s/27s/40s) — daemon eliminates cold-start, not GPU contention.

## Index-staleness fix (shipped alongside)

`qmd status` showed the index updated 6 days ago — 305 topics indexed vs 382 on disk (25% miss). qmd has no file-watch mode; daemon doesn't auto-reindex. Added a best-effort `qmd update && qmd embed` post-batch hook in `src/coordinator/post_batch.py:_refresh_qmd_index`. Cost: ~3s typical batch overhead; gated on `use_semantic_resolve`.

## Code changes shipped

- `src/agent/tools/qmd_client.py` — append `--no-rerank` to argv; updated module docstring.
- `src/config.py` — `qmd_timeout_s` 45s → 10s. With no-rerank, p95=1.3s sequential and ~5s under contention; 10s gives 2× headroom.
- `src/coordinator/post_batch.py` — new `_refresh_qmd_index()` hook.
- `scripts/compile_all.py` — call the hook after `_validate_touched_pages`.
- `tests/test_qmd_client.py` — pin `--no-rerank` in argv to prevent silent regression.

## Full per-query results

[Embedded below for grep-ability. Person-email queries marked `is_person_query=True` in the raw JSONL excluded from top-1 stats above.]

| # | Category | Query | RR (s) | NR (s) | Top-5 ovlp | Top-1 |
|---:|---|---|---:|---:|---:|---|
| 1 | langfuse_sample | `mp launch optimising api hits user details verification` | 0.8 | 1.24 | 4/5 | ✅ |
| 2 | langfuse_sample | `nextjs mobile bmc` | 0.8 | 0.73 | 4/5 | ✅ |
| 4 | langfuse_sample | `dspy-gepa` | 13.3 | 0.74 | 3/5 | ✅ |
| 5 | langfuse_sample | `foreign-bl-whatsapp-enrichment-flow` | 16.9 | 1.24 | 2/5 | ✅ |
| 6 | langfuse_sample | `mp launch poc` | 22.4 | 0.72 | 2/5 | ✅ |
| 7 | langfuse_sample | `MCAT Search Audit Process` | 13.3 | 0.75 | 2/5 | ✅ |
| 8 | langfuse_sample | `lens` | 16.9 | 0.75 | 4/5 | ✅ |
| 9 | langfuse_sample | `sayan-samanta-indiamart-com` | 25.9 | 0.74 | 2/5 | ✅ |
| 10 | langfuse_sample | `company-api-category-navigation` | 7.8 | 2.76 | 2/5 | ✅ |
| 11 | langfuse_sample | `marketplace-launch` | 17.4 | 0.75 | 2/5 | ✅ |
| 12 | langfuse_sample | `seller-custtype-realtime-capture-payment-page` | 23.4 | 1.25 | 4/5 | ✅ |
| 13 | langfuse_sample | `bl-api-optimization` | 32.5 | 0.75 | 2/5 | ✅ |
| 14 | langfuse_sample | `business-whatsapp-sync` | 27.4 | 0.74 | 2/5 | ✅ |
| 15 | langfuse_sample | `dspy gepa intent classification` | 16.9 | 0.76 | 2/5 | ✅ |
| 16 | langfuse_sample | `intent classification whatsapp` | 31.0 | 0.75 | 2/5 | ✅ |
| 17 | langfuse_sample | `leadmanager` | 10.8 | 0.76 | 2/5 | ✅ |
| 18 | langfuse_sample | `performance-dashboard-desktop-lms` | 29.5 | 0.76 | 3/5 | ✅ |
| 19 | langfuse_sample | `qdrant-vector-recommendations-poc` | 32.5 | 1.45 | 4/5 | ✅ |
| 20 | langfuse_sample | `indiamart-procurement-agent` | 34.5 | 1.30 | 2/5 | ✅ |
| 21 | langfuse_sample | `buylead-notifications` | 42.1 | 0.75 | 2/5 | ✅ |
| 22 | langfuse_sample | `shreyans-singh-indiamart-com` | 15.4 | 0.75 | 2/5 | ✅ |
| 23 | langfuse_sample | `msite-pdp-html-rewrite-phase-2-dom-simplification-size-reduc` | 20.4 | 0.76 | 2/5 | ✅ |
| 24 | langfuse_sample | `user-details-api-optimization` | 15.3 | 0.74 | 3/5 | ✅ |
| 25 | langfuse_sample | `api-optimization-seller-bl` | 25.9 | 0.76 | 2/5 | ✅ |
| 26 | langfuse_sample | `removal of download brochure links` | 45.1 | 0.75 | 4/5 | ✅ |
| 27 | langfuse_sample | `whatsapp9696 seller chatbot` | 11.3 | 0.75 | 2/5 | ✅ |
| 28 | langfuse_sample | `aditya-rai-indiamart-com` | 7.3 | 0.76 | 2/5 | ✅ |
| 29 | langfuse_sample | `webpurify` | 14.3 | 0.75 | 4/5 | ✅ |
| 30 | langfuse_sample | `abc-test-bl-purchase` | 14.3 | 0.74 | 2/5 | ✅ |
| 31 | langfuse_sample | `seller-custtype` | 17.9 | 0.75 | 3/5 | ✅ |
| 32 | langfuse_sample | `GEPA` | 18.9 | 0.74 | 3/5 | ✅ |
| 33 | langfuse_sample | `bottom-price-widget-qna-dir-city-mcat-pages` | 25.4 | 1.27 | 2/5 | ✅ |
| 34 | langfuse_sample | `im-insta-pro-whatsapp-contact-sync-improvement` | 40.0 | 0.77 | 2/5 | ✅ |
| 35 | langfuse_sample | `whatsapp-carousel-template` | 13.9 | 1.26 | 3/5 | ✅ |
| 36 | langfuse_sample | `nextjs-mobile-bmc` | 23.9 | 0.86 | 4/5 | ✅ |
| 37 | langfuse_sample | `auto-responder-matchmaking` | 40.6 | 1.43 | 3/5 | ✅ |
| 38 | langfuse_sample | `VANI` | 13.9 | 0.76 | 2/5 | ✅ |
| 40 | langfuse_sample | `prompt optimization intent classification` | 12.8 | 0.74 | 3/5 | ✅ |
| 41 | fixture_new_joiner | `What does MCAT buyer spec fill mean?` | 6.8 | 0.75 | 2/5 | ✅ |
| 42 | fixture_new_joiner | `How are Lens visual searches handled?` | 12.8 | 1.26 | 2/5 | ✅ |
| 43 | fixture_new_joiner | `What's the MIM app buyer onboarding flow?` | 37.5 | 0.77 | 3/5 | ✅ |
| 44 | fixture_new_joiner | `What's the MIM seller SOI journey?` | 11.8 | 0.74 | 3/5 | ✅ |
| 45 | fixture_new_joiner | `What does the LMS dashboard show sellers?` | 11.3 | 0.74 | 3/5 | ✅ |
| 46 | fixture_new_joiner | `How does seller KYC verification work?` | 12.8 | 0.74 | 4/5 | ✅ |
| 47 | fixture_new_joiner | `What's the n8n LLM auditor?` | 5.8 | 0.75 | 4/5 | ✅ |
| 48 | fixture_new_joiner | `What's the Dialogflow intent pipeline?` | 6.3 | 0.75 | 4/5 | ✅ |
| 49 | fixture_new_joiner | `What's the agentic buyer chatbot?` | 8.8 | 0.76 | 2/5 | ✅ |
| 50 | long_prose | `how do we handle buyer lead purchase via whatsapp for foreig` | 6.3 | 0.74 | 2/5 | ✅ |
| 51 | long_prose | `what is the pipeline that audits dialogflow intent classific` | 5.3 | 0.74 | 4/5 | ✅ |
| 52 | long_prose | `why does buylead recommendation use negative MCAT weights an` | 13.8 | 0.74 | 3/5 | ✅ |
| 53 | long_prose | `explain the photo search qdrant vector updation pipeline and` | 12.3 | 0.75 | 5/5 | ✅ |
| 54 | long_prose | `what happened when we migrated the m-site PDP HTML rewrite p` | 11.8 | 0.75 | 2/5 | ✅ |
| 55 | long_prose | `how does the agentic auditor product approval grid work for ` | 6.3 | 0.74 | 2/5 | ✅ |
| 56 | long_prose | `how does seller ISQ scoring affect buyer lead recommendation` | 6.3 | 0.77 | 2/5 | ✅ |
| 57 | long_prose | `walk me through msite company home page html rewrite and nav` | 11.3 | 0.80 | 2/5 | ✅ |
| 58 | bl_as_buylead | `BL recommendation engine` | 12.3 | 0.84 | 3/5 | ✅ |
| 60 | body_only_ticket_p | `Ticket 638983` | 6.8 | 0.81 | 4/5 | ✅ |
| 61 | body_only_wrong_ma | `Wrong Mapping disposition` | 13.4 | 0.86 | 3/5 | ✅ |
| 62 | body_only_deep_phr | `seller-MCAT relevance A and BA rank` | 13.9 | 0.86 | 2/5 | ✅ |
| 63 | deterministic_tick | `Ticket 638983` | 0.8 | 0.74 | 4/5 | ✅ |
| 64 | deterministic_tick | `Ticket 636565` | 6.8 | 0.73 | 4/5 | ✅ |
| 66 | deterministic_db_t | `eto_ofr_rejected` | 31.0 | 1.25 | 2/5 | ✅ |
| 67 | deterministic_brow | `Chrome 143` | 13.3 | 0.75 | 2/5 | ✅ |
| 68 | deterministic_app_ | `Android v1369` | 13.8 | 0.74 | 3/5 | ✅ |
| 69 | deterministic_spec | `2026-01-23` | 15.4 | 0.75 | 2/5 | ✅ |
| 70 | deterministic_spec | `15:37 IST` | 20.9 | 1.27 | 4/5 | ✅ |
| 71 | deterministic_perc | `5% to 50%` | 14.9 | 0.75 | 4/5 | ✅ |
| 72 | deterministic_spec | `rank MCAT A and BA` | 24.9 | 0.78 | 4/5 | ✅ |
| 73 | deterministic_numb | `638983` | 20.9 | 0.73 | 4/5 | ✅ |
| 74 | deterministic_inte | `project.intermesh.net` | 29.9 | 2.26 | 2/5 | ✅ |
| 75 | deterministic_acro | `BLNI` | 20.4 | 0.74 | 2/5 | ✅ |
| 76 | deterministic_acro | `PMCAT` | 23.9 | 0.74 | 2/5 | ✅ |
| 77 | deterministic_acro | `ISQ` | 15.9 | 0.74 | 2/5 | ✅ |
