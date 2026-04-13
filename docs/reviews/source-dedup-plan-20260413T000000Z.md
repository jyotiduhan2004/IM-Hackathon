# Source Deduplication Plan — Collapse Thread Replies in Wiki `sources:`

Date: 2026-04-13 · Status: proposal, READ-ONLY investigation

## 1. Observed redundancy

`wiki/entities/himanshu-jain01.md`: 312 `sources:` entries, 365 total lines.
Biggest repeats on that one page:

| subject slug | count |
| --- | --- |
| `mplaunchimpre-launchcentral-smart-orchestrator-api` | 23 |
| `mplaunchim-launch-migration-of-buylead-fulfillment` | 18 |
| `mplaunchim-addition-of-productbl-images-in-astbuy-` | 17 |
| `mplaunchim-m-site-ab-test-to-improve-buyer-trust` | 6 |

Spot-checked: all 17 `addition-of-productbl-images-in-astbuy` raw files share `thread_id: 19b91e6980de1a70`. Subject slug is a safe fallback if `thread_id` is missing, but **`thread_id` is the true key**.

Other bloated pages (lines): `bharat-agarwal.md` 265, `neeraj-vardhan-ponnada.md` 262, `sayan-samanta.md` 230, `yashwant-chandra.md` 217.

## 2. Raw frontmatter signal available

Each `raw/*.md` already carries: `message_id`, `thread_id`, `subject`, `from`, `date`, `in_reply_to`. We have everything we need for thread-level collapse with zero re-ingest.

## 3. Proposed `sources:` schema (thread-collapsed)

```yaml
sources:
  - thread_id: 19b91e6980de1a70
    subject: "[MPLaunch@IM] Addition of ProductBL Images in AstBuy"
    message_count: 17
    date_range: 2026-01-06..2026-01-20
    first_message: raw/2026-01-06_mplaunchim-addition-of-productbl-images-in-astbuy-_bdd40198.md
    latest_message: raw/2026-01-20_mplaunchim-addition-of-productbl-images-in-astbuy-_6c63b29e.md
  - thread_id: 19b6eaead1fb5ef7
    subject: "[MPLaunch@IM] Launch: Dynamic Dispositions on Buyer NPS Feedback Screen"
    message_count: 4
    date_range: 2026-01-02..2026-01-14
    first_message: raw/2026-01-02_mplaunchim-launch-dynamic-dispositions-on-buyer-np_432432f0.md
    latest_message: raw/2026-01-14_mplaunchim-launch-dynamic-dispositions-on-buyer-np_7e2d9f4b.md
```

Keep legacy string form supported (backward-compat): if `isinstance(src, str)` → treat as single-message thread.

## 4. Migration plan

**a. One-shot rewriter** `scripts/dedupe_sources.py` (new):
1. For each `wiki/**/*.md`, parse frontmatter with `src/utils.py:extract_frontmatter`.
2. Resolve each `raw/...` path → read `thread_id`, `subject`, `date`.
3. Group by `thread_id` (fallback: subject-slug from filename when `thread_id` absent).
4. Emit collapsed `sources:` (sorted by latest_message date desc). Loses zero info — every raw file is still reachable via `first_message`/`latest_message` and the MkDocs hook can link an index.
5. Re-render via `render_with_frontmatter`. Dry-run mode prints per-page size delta.

**b. MkDocs hook** `mkdocs_hooks.py:on_page_markdown`:
- Teach the loop at line 182 to accept dict sources. One `<details>` per thread, summary = `📧 {subject} ({message_count} messages, {date_range})`, body = render `latest_message` inline plus a short list of all message paths for drill-down. Keep string fallback for legacy data.
- `_page_metadata_banner` (line 128): count messages as `sum(s.get("message_count",1) for s in sources)` so "Sources: 312" banner stays honest.

**c. Compiler prompt** `src/compile/prompts.py:54`:
- Replace the string-only YAML example with the dict shape above.
- Reinforce at line 219 ("Group by thread_id when possible") that `sources:` itself must also be thread-grouped — one entry per `thread_id`, not per message.
- Update the exhaustiveness rule at line 184-193: "enumerate all threads" instead of "all messages".

**d. Validator** (optional, small): fail CI if any `sources:` entry uses the legacy string form after migration cutover, or if two entries share a `thread_id`.

## 5. Expected size reduction

himanshu-jain01.md: 312 message entries. Thread-unique count estimate from the top repeats alone (23+18+17+6 = 64 messages collapse to 4 threads) plus a long tail. Realistic landing: **~80-110 thread entries**, i.e. **65-75% reduction** on sources block, page drops from 365 lines to ~150.

Other 200+ line pages scale similarly (they hit the same MPLaunch mailing list).

## 6. Worth it vs. cap-to-20?

Cap-to-20 hides information (same thread's 23 replies → 20 survive arbitrarily, 3 lost; no way to tell a user "you have 23 messages in this thread"). Thread-collapse is **lossless**, keeps the banner count truthful, and makes the rendered `<details>` far more useful (one block per topic, not 23 near-duplicates). Effort is one ~150-line script + ~30 lines of hook/prompt changes. Ship thread-collapse; no cap needed.

## 7. Rollout order

1. Land rewriter + hook support in one PR, run rewriter against wiki/, verify MkDocs build.
2. Land prompt change in a follow-up so new compilations emit the new shape from the start.
3. Delete legacy string-form support after one full recompile cycle.

Path: `/Users/amtagrwl/git/email-knowledge-base/docs/reviews/source-dedup-plan-20260413T000000Z.md`
