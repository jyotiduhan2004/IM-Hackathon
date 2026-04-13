# Prompt caching — 2026-04-13 verification

**Bottom line**: OpenRouter prompt caching works across all four tested
z-ai / minimax models but is **intermittent** (~35–40% hit rate over 5
sequential identical-prompt calls). Cost rankings stay stable:
`minimax/minimax-m2.7` is the cheapest AND caches on par with the others,
making it the clear best-value default if quality is comparable on our
workload. `z-ai/glm-5.1` also has a sporadic `400: Invalid model name`
error that `z-ai/glm-4.6` does not.

## How this was tested

Four models × five sequential calls, each call using the **real compile
system prompt** (`src/compile/prompts.py::COMPILER_SYSTEM_PROMPT`, ~3000
tokens). Looked for `usage.prompt_tokens_details.cached_tokens > 0` per
response. All calls through the Intermesh LiteLLM proxy
(`https://imllm.intermesh.net/v1/chat/completions`) using key `email-kb-wiki`.

## Real-workload results

| Model | Σ cached / Σ prompt | Cache % | Cost (5 calls) | $/cache%-weighted-call | Notes |
|---|---:|---:|---:|---:|---|
| `z-ai/glm-4.6`         | 6044 / 15183 | **39.8%** | $0.00617 | baseline | current default |
| `z-ai/glm-5`           | 6016 / 15180 | 39.6%     | $0.01376 | 2.23× | caches but per-token pricier |
| `z-ai/glm-5.1`         | 3008 / 9108¹ | 33.0%     | $0.00945 | 1.53× | one call errored 400 |
| `minimax/minimax-m2.7` | 5366 / 14982 | 35.8%     | **$0.00324** | **0.52×** | cheapest + caches |

¹ glm-5.1 call 4 failed with `400 - Invalid model name passed in model=z-ai/glm-5.1`. Transient? Intermittent? Worth flagging to the LiteLLM proxy admin.

### Call-by-call pattern (caching is sticky-but-rotating)

Same prompt, 5 times, `z-ai/glm-4.6`:

| call | prompt | cached | note |
|---:|---:|---:|---|
| 1 | 3036 | 4 | cold |
| 2 | 3036 | 3032 | 99.9% — full cache hit |
| 3 | 3037 | 0 | provider rotated, cold again |
| 4 | 3037 | 2944 | back on a cached edge |
| 5 | 3037 | 64 | partial |

The pattern for `minimax/minimax-m2.7` is even more regular — call 2 and 4
cached, calls 1/3/5 cold. Consistent with OpenRouter's provider-sticky
caching with short TTL. **`extra_body={"provider":{"order":["z-ai"],"allow_fallbacks":false}}`**
would likely push this toward 80%+ by pinning the provider edge.

## What this means for the default model

The synthetic-prompt probe from earlier in this session misled me — two
calls was too short a window to see the sticky-cache warm up. With the real
compile prompt and 5 calls, every tested model caches at roughly the same
rate. The cost delta comes from **per-token pricing**, not cache support.

**Recommended action ordering:**

1. **Keep `z-ai/glm-4.6` as default for now** — it's the incumbent, we have
   quality data on it from the 187+ compiled messages so far, and the cache
   rate matches the alternatives.
2. **Add `minimax/minimax-m2.7` to the short-list for serious eval** —
   2× cheaper than glm-4.6 with equivalent caching. Quality-unknown on our
   workload; needs an A/B run of ~50 emails before switching.
3. **Don't switch to `z-ai/glm-5` or `z-ai/glm-5.1`** without a clear quality
   win — they're more expensive per token at the same cache rate, and
   glm-5.1 has the intermittent 400 error.
4. **Pin the OpenRouter provider** via `extra_body` to prevent rotation and
   increase cache stickiness. Cheap change in `src/compile/compiler.py::_make_chat_model`.

## What ships in this PR

1. **`src/compile/cache_stats.py::CacheStatsCallback`** — LangChain callback
   that accumulates per-turn `input_tokens`, `cache_read`, `output_tokens`.
2. **`run_compilation(cache_stats=...)`** — optional parameter; when passed,
   attaches the callback to the LangGraph config so every agent turn is
   captured.
3. **`scripts/compile_all.py`** — instantiates a fresh `CacheStatsCallback`
   per batch, passes it in, and prints the summary
   (`cache: cached/prompt (cache_pct%) across N turns`) as part of the
   batch-complete line. Also written into the batch log row.
4. **Model default revert** `glm-5.1 → glm-4.6` — see above. The PR that
   made the switch (#38) shipped before this verification.
5. **This findings doc** — so the next person who reconsiders the default
   has the data.

## Follow-up tickets to open separately

- **OpenRouter provider-sticky-routing via `extra_body`** — pin z-ai as sole
  provider on every call to maximize cache stickiness.
- **Quality A/B: minimax-m2.7 vs glm-4.6** — compile the same 50 emails
  with each, diff the output pages. Only switch defaults if m2.7 matches on
  quality.
- **Ticket to LiteLLM proxy admin** — investigate the intermittent
  `400: Invalid model name` on `z-ai/glm-5.1` calls.
- **Per-batch cache-stats dipstick rollup** — once we have a few days of
  batch logs, aggregate into stats.py / dipstick so we see week-over-week
  cache-rate trends.

## Probe scripts (for future reproduction)

- `caching_probe.py` — basic 2-call identity test with/without sticky routing
- `caching_probe_multi.py` — matrix over 5 models, 2 calls each
- `caching_probe_real.py` — 5 sequential calls, real prompt, 2 models
- `caching_real_all.py` — 5 calls × 4 models with real prompt (the data
  driving this doc)

All accept `.env` for `OPENAI_API_KEY` and `LITELLM_BASE_URL`. Run with
`uv run python <script>.py`.
