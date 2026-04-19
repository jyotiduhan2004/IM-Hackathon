---
audit_date: 2026-04-18
scope: Cycle 10 smoke compile — 4 pages produced by compile_run_id da82773f
method: full-read of each compiled page, schema/lead/voice/citation checks, DB state reconciliation, merge-candidates + people-stub verification
---

# Cycle 10 deep audit — what the compile actually produced

**Run**: `compile_run_id=da82773f-019a-43de-b297-98f64c2817f6`, 2026-04-18T14:23-14:43 IST, `--limit 10 --batch-size 1`, $0.50, on `eb658d8`.

The sibling `cycle-10-smoke-2026-04-18.md` read the log row at surface level. This audit reads the actual pages on disk, looks at the raw emails they compiled from, reconciles against `messages` / `message_touched_pages` / `compile_tool_calls`, and grades quality. There is a substantive gap between the smoke's "green for shipping" framing and what the pages look like when you open them.

## 1. The four pages this run produced

From `message_touched_pages` joined on `messages.compiled_at` during the run window:

| Batch | Slug | Type | Thread | Model |
|-------|------|------|--------|-------|
| 2 | `topics/lens-indiamart-fully-logged-in-user-flow` | topic | `19bb66cef7aae875` | grok-4.1-fast |
| 5 | `topics/seller-bl-api-optimization` | topic | `19bb72fc748876c2` | glm-5 → grok |
| 5 | `topics/seller-bl-api-hit-optimisation` | topic | `19bb72fc748876c2` | glm-5 → grok |
| 7 | `systems/auditor-agent` | system | `19bbc09f5bf3d154` | glm-5 → grok |

Batch 5 wrote two separate pages from one thread — the exact pattern v9-U14 merge-candidate handoff was built to catch (and it did).

## 2. Per-page grades

| Dim | lens-fully-logged-in | seller-bl-api-optimization | seller-bl-api-hit-optimisation | auditor-agent |
|-----|----------------------|-----------------------------|--------------------------------|---------------|
| Schema (8/7 required H2s) | **0/8** — "Bugs identified during validation", "Bug 637878 root cause analysis", "KPIs monitored", "Impact analysis", "Launch Announcement", "Related" | **0/8** — TL;DR, Business Objective, Problem Statement, Solution Implemented, Deployment, Open Items, References, Testing Results, Related | **0/8** — TL;DR, Overview, Changes Made, Launch Details, Next Steps & Follow-up, Related | **7/7** — Summary, Role, Active related topics, Dependencies, Known issues, Related pages, References |
| Lead paragraph (≥2 sentences, pre-H2, present tense) | **Yes** (3 sentences, Wikipedia-style opener) | **No** — opens directly with `## TL;DR`, no prose above | **No** — opens directly with `## TL;DR` | **Yes** (2 sentences, definition-style) |
| Domain frontmatter | `domain: buyer-experience` (plausible; Lens is consumer-side) | `domain: buyer-experience` (**wrong** — seller-side BL page, should be `seller-experience`) | **missing entirely** | `domain: marketplace-discovery` (defensible) |
| Synthesis vs listing (1-5, 5=synthesised) | **2** — large bug tables, test-case excerpts lifted verbatim, event-log voice in places | **3** — two impact subsections with quantified deltas are real synthesis; Open Items is a bullet dump of QA bug IDs | **3** — clean narrative for Changes Made, but Next Steps reads as a to-do list with names attached | **4** — multi-version explanation (1.3 vs 1.1-no-photo), 12 check types enumerated from prompt |
| Citations (`source_threads` / `sources`) | 10 `sources` + 1 `source_thread` → all real raw paths on disk | 0 `sources` list, 1 `source_thread` | 0 `sources` list, 1 `source_thread` | 0 `sources` list, 1 `source_thread` |
| TL;DR | No explicit TL;DR heading, but lead paragraph is a usable 1-line summary | Yes (`## TL;DR`, 1 sentence, quantified) | Yes (`## TL;DR`, 1 sentence, quantified) | Summary section serves this role, 2 sentences |
| Outgoing wikilinks | 33 | 9 | 19 | 16 |
| Missing cross-refs | Links `Lens.IndiaMART` by display name not slug (`[[Lens.IndiaMART]]` vs `[[system/lens-indiamart-com]]`) — broken wikilink | Does not link to its sibling `seller-bl-api-hit-optimisation`, nor to `seller-bl-user-details-verification-api-optimization` | Does not link to its sibling `seller-bl-api-optimization`, nor to the older duplicate | Links to `[[auditmate]]`, `[[agentic-auditor-product-approval-grid]]`, `[[auditor-11-no-photo-version-rollout]]` — good hub behaviour |
| Voice | Mixed — "Announced by [[saivya-gulati]] on 2026-01-13" is event-log; "Impact: 99% reduction" is present tense knowledge | Mostly present tense ("Service now called after 30-minute intervals") | Mostly present tense ("Store the Company Name alongside existing cookies") | Present tense ("The Auditor Agent performs multi-dimensional product validation checks") |

### Per-page one-liners

**lens-indiamart-fully-logged-in-user-flow** — grade **C+**. The page has a Wikipedia-style opener and 10 cited sources, but the body is a launch-postmortem — bug tables, test-case checklists, vote-of-thanks block. Representative line: *"Vote of thanks to [[vikram-varshney-indiamart-com]], [[chourasia-aseem-indiamart-com]], and [[anshu-chauhan-indiamart-com]]."* That belongs in raw/, not in a persistent knowledge page. **Biggest gap**: zero of the 8 required topic H2s ("Summary", "Current state", "Why it matters", "Key decisions", "Recent changes", "Open questions", "Related pages", "References") — validator drift despite v9-U1.

**seller-bl-api-optimization** — grade **C**. Decent synthesis of the two optimisations with quantified impact (99% / 90%). Representative line: *"Store Company Name alongside existing cookies (GLID, First Name, etc.) immediately after authentication, bypassing the need for service calls. Impact: 99% reduction in API calls."* Clean technical narrative. **Biggest gap**: duplicates an already-existing page (`seller-bl-user-details-verification-api-optimization`) that v9-U14 correctly flagged as a merge candidate; wrong domain (`buyer-experience` for a seller-side page); no link to its sibling written in the same batch.

**seller-bl-api-hit-optimisation** — grade **C-**. Near-carbon copy of the sibling page. Representative line: *"Optimised the number of API hits for User Details and User Verification services on Seller BL pages by 99% and 90% respectively by storing Company Name in cookies and increasing the polling interval for verification checks from every page load to every 30 minutes."* Same facts, different words. **Biggest gap**: shouldn't exist. Batch 5 created two topic pages for the same concept in one thread and the reviewer caught it (`merge_candidates+=2`), but the pages still landed in `wiki/topics/` as separate artefacts. No `domain:` at all. v9-U14 is a handoff, not a block — the pages ship and a human has to run `apply_merge_candidate.py`.

**auditor-agent** — grade **B+**. Cleanest of the four by a wide margin. Representative line: *"The Auditor Agent performs multi-dimensional product validation checks to identify quality issues, contradictions, and categorization errors in product listings. It generates a cumulative error score that determines whether products are approved, rejected, or require remediation. The current production version is Auditor 1.3 (live for products with photos), with Auditor 1.1 No Photo Version handling name-only and low photo importance products."* That is wiki-voice: present tense, explains the *what*, points to related pages for detail. Hits all 7 required system H2s, rich hub links. **Biggest gap**: "Known issues — None documented yet." placeholder is honest but the 12-check enumeration reproduces the prompt document verbatim rather than synthesising the error-scoring logic.

## 3. v10 feature verification

- **v9-U1 required 8 H2s** — **NOT CONFIRMED** for topics, **CONFIRMED** for the one system. Three of three topic pages produced this run carry zero of the 8 required H2 headings. The system page (auditor-agent) is the only one that honours the canonical section shape. Evidence: `grep -n '^## '` across the four files shows topic pages using "TL;DR", "Business Objective", "Problem Statement", "Deployment", "Bugs identified during validation", "Launch Announcement", etc. — none of which contain the substring "Current state" / "Why it matters" / "Key decisions" / "Recent changes" / "Open questions" / "Related pages" / "References". The prompt teaches the schema; the validator substring-matches it; but the agent ignored it on topic pages in 3/3 opportunities.
- **v9-U14 merge-candidate handoff** — **CONFIRMED**. `wiki/merge_candidates.md` has a run-scoped block stamped `2026-04-18T14:35:29+00:00 — trace da82773f-…:5` pairing `seller-bl-api-hit-optimisation` against `seller-bl-user-details-verification-api-optimization`. The two flagged pages plus `seller-bl-api-optimization` are substantively the same concept (same thread, same two services, same 99%/90% deltas, same deployment date) — **not** a false positive. The reviewer caught the overlap at batch-exit; the coordinator wrote the queue entry. v9-U14 is doing exactly what it was built for.
- **v10-U2 multi-domain** — **CONFIRMED singular only**. Three pages use `domain: <slug>` singular; one (`seller-bl-api-hit-optimisation`) has no domain at all. Zero use the plural `domains: [..]` list form. No page in this run genuinely spanned two domains, so the singular choice is correct — but the feature's multi-domain path wasn't exercised.
- **v10-U5 glob narrowing** — **AMBIGUOUS / partial regression**. `compile_tool_calls` for this run shows 7 glob calls across 3 batches: 6 timed out with *"Error: glob timed out after 20.0s. Try a more specific pattern or a narrower path."*, 1 returned results. Patterns that timed out: `**/lens-indiamart-fully-logged-in-user-flow.md`, `/wiki/*/*-indiamart*.md`, `/wiki/**/lens-indiamart-fully-logged-in-user-flow.md`, `/wiki/*/lens-indiamart-fully-logged-in-user-flow.md`, `**/seller-bl-api-optimization.md`, `/wiki/**/seller-bl-api-optimization.md`. The one that worked had both `path='/wiki'` and `pattern='**/lens-indiamart*.md'`. U5's narrowing middleware should be rewriting the timing-out patterns; evidence says it's either not firing or not narrowing aggressively enough.
- **v10-U6 concise defaults** — **VALIDATED BY UNIT TESTS, NOT OBSERVABLE IN OUTPUT**. Tool counts show heavy `get_page_summary` (27), `get_thread_context` (18), `resolve_page` (53) use — the Langfuse comment that concise responses don't close the loop matches what I see in tool sequencing (gps/gtc followed by `read_file` to get the actual content) but no output-side signal here.
- **v10-U8 drop auto-stub** — **CONFIRMED**. Zero new `wiki/people/*.md` files created in the run window (disk mtime filter for `20:13` shows 36 people files touched but these were regenerated by the periodic catalog_sync, not newly created by the agent). No garbage slugs (`vishakha-indiamart`, `akash-singh6`) visible in the current 69-file `wiki/people/` directory. Numeric-suffix slugs that exist (`sachin63596`, `uma-negi1`, `aditya-singh7`) all come from legitimate email local-parts, not slug invention. The v10-U8 fix is holding — no CC-only stubs leaked this run.

## 4. Batch 9 anomaly — NOT a regression

Batch 9 row: `| 9 | 1 | 19bbc0e7d4afdd28 | compiled | normalized 0 pages, 0 with validator errors, catalog_synced 6, touches_inserted 0`.

Reconciliation:

1. `messages` state: `message_id='<CAHixcdf…@mail.gmail.com>'`, `compile_state='compiled'`, `compiled_at=2026-04-18 20:11:41+05:30`.
2. `message_touched_pages`: one row exists — `page_id=2443`, `compiled_at=2026-04-17 02:05:31+05:30`. That touch is from a **previous run**, not this one.
3. `page_id=2443` = `wiki/topics/removing-download-brochure-company-pages` (topic, content-type).
4. That page's `sources:` list already includes `raw/2026-01-14_mplaunchim-removal-of-download-brochure-links-for-_14e860b1.md` — the batch 9 email. Cited since 2026-04-15 (per page frontmatter `last_compiled: 2026-04-15T14:30:34Z`).

So: the agent opened the page, read it, saw its own email already cited, and correctly concluded there was nothing to append. The coordinator's "cited in a content-type page" check passed (citation exists from a prior run), and the email flipped `pending → compiled`. No new touch row was inserted this run because `ON CONFLICT (message_id, page_id) DO NOTHING` — the idempotent path did exactly what it should.

**Verdict: not a regression**. The "coordinators verify, LLMs propose" invariant holds — external evidence (page cites raw) exists, state transition is justified. The anomaly is a log-schema artefact: `touches_inserted 0` for a batch that correctly flipped state because the evidence was already there. Mild UX issue — the log row suggests the agent did nothing useful, when in fact it made the right judgement (existing content covers this email). Could be clarified with a "cited_prior: true" flag in the log row.

## 5. Final verdict

Net **slightly positive for a wiki-knowledge-base purpose**, but mostly driven by the single system page — not a blanket result. The `auditor-agent` page is genuinely wiki-quality prose you'd want a new engineer to read; the topic pages land between filing-cabinet and encyclopedia. The two seller-bl pages are a live demonstration of the dedupe problem v9-U14 was built to surface: the agent cannot avoid creating near-duplicates inside a single thread, so the reviewer has to catch it post-hoc. **Single biggest quality gap: required-H2 drift on topic pages.** Three of three topic pages produced this run ignored the prompt-taught canonical schema. v9-U1 aligned prompt and validator vocabulary, but the agent is still templating off thread-subject vocabulary ("Launch Announcement", "Business Objective", "Bugs identified") instead of the abstract canonical shape. The validator isn't failing — either it's permissive on system pages and not topic pages, or it's substring-matching one of the non-canonical heads against something. Worth a focused read of `src/compile/validation.py` against this run's pages.

## 6. Additional v11 candidates

Langfuse-comment triage already covered V11-U1 through V11-U6 (strip coordinator leak, concise payload loop, resolve_page ranking, TL;DR field, check_my_work rename, read_file truncation hint). Based on spot-checks above, three additional candidates the user-facing reviews didn't catch:

1. **V11-U7 — hard-fail the validator on zero-of-N required H2s** (not just warn). Current behaviour lets 3/3 topic pages ship with zero required headings because the validator substring-matches — any non-empty H2 list satisfies "has headings". Change: require at least `N-2` of the canonical list present (or specifically require `## Summary` + `## References` as non-negotiable), and block at `verdict=block` in the reviewer. Motivation: prompt alignment without enforcement is 0% effective on three consecutive topic pages — the agent uses thread-subject vocabulary instead of schema vocabulary every time.
2. **V11-U8 — domain-frontmatter validator that knows `seller-` prefix ≠ `buyer-experience`.** Evidence: `seller-bl-api-optimization` shipped with `domain: buyer-experience`, sibling shipped with no domain at all. Add a slug-prefix → domain sanity rule: topics starting with `seller-` default-map to `seller-experience`, `mcat-` / `isq-` / `categoriz-` to `marketplace-discovery`, etc. Reject or warn on prefix-domain mismatch.
3. **V11-U9 — sibling-aware draft pass in batch workflow.** When the agent creates a second topic page in the same thread, force a mandatory `resolve_page` + `grep` over sibling pages before the second `write_file`, and write a `related:` wikilink into both pages. v9-U14 catches post-hoc; this catches in-batch. Motivation: batch 5 wrote two near-identical pages in the same thread with no cross-link and no intervening resolve — a coordinator-side pre-write check would have either merged them or at least cross-wikilinked them.

---

**Audit raw numbers**: 10 emails attempted, 4 compiled (1 system + 3 topics), 5 trivial-skipped, 1 kept-pending (self-healing on a reply before the parent was captured), 0 failed. 4 unique pages touched (2443 pre-existing, 3355 + 4554 + 4730 + 5004 updated/written this run). 53 `resolve_page` calls, 36 `read_file`, 27 `get_page_summary`, 18 `get_thread_context`, 10 `check_my_work`, 7 `glob` (6 timed out, 1 succeeded). Cost $0.50 for 20 minutes of wall-clock, one novel system page, two dedupe-problem topic pages, and one valid launch-postmortem that belongs closer to raw/ than wiki/. v10-U8 (drop auto-stub) is the cleanest signal in the run — zero garbage people slugs. The schema-alignment work of v9-U1 is not yet reflected in topic-page output.
