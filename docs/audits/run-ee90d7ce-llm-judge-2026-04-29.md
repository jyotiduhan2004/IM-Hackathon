# LLM Judge — Run `ee90d7ce` (2026-04-29)

Wider-sample LLM-judge audit over 8 wiki pages (5 topics + 3 systems) — first time the judge has been pointed at system pages. Run is on commit `a52672a` (PR #284's References-backfill fix landed).

- Personas: newbie, pm, ia (3 personas × 8 pages = 24 scores)
- Model: `openai/anthropic/claude-sonnet-4-6` (default)
- Cost incurred: **~$2.40** (8 × 3 × ~$0.10 estimate; no page exceeded $0.30, so we did not stop early)
- Patch used: `/tmp/judge_wiki_with_systems.py` — extended `_enumerate_topic_slugs` and `_load_page` to also look under `wiki/systems/`, plus a new `--output-dir` flag. Repo is unchanged.
- Outputs: `/tmp/run-ee90d7ce-judge/judge-2026-04-29.{csv,md}`

## Sample + heuristic context

Five topics (top-2 / mid-1 / bottom-2 from the heuristic scorer) and three of the four available system pages.

| slug | type | heuristic mean | newbie | pm | ia | judge mean |
|---|---|---|---|---|---|---|
| contextual-blni-feedback | topic | 8.6 | 8 | 8 | 7 | 7.67 |
| business-credentials-filter-search-api | topic | 8.4 | 7 | 8 | 7 | 7.33 |
| msite-search-product-card-redesign-ab-test | topic | 7.4 | 8 | 8 | 8 | **8.00** |
| dir-desktop-mcat-price-widget-qna-faq-schema-poc | topic | 6.6 | 7 | 8 | 6 | 7.00 |
| whatsapp-photo-buylead-optimization | topic | 6.4 | 8 | 8 | 6 | 7.33 |
| auditmate | system | n/a | 7 | 7 | 7 | 7.00 |
| buyermy | system | n/a | 6 | 5 | 7 | 6.00 |
| photosearch | system | n/a | 4 | 4 | 6 | **4.67** |

- **Overall judge mean (n=8): 6.88 / 10**
- Topics judge mean (n=5): 7.47
- Systems judge mean (n=3): 5.89 — systems are clearly weaker than topics in this sample.

## Reproducibility — heuristic vs judge correlation

Restricting to the 5 topics where the heuristic scorer has data:

- run-5928c151 (n=5): **r = −0.758**
- run-8fa45533 (n=5): **r = +0.518**
- run-ee90d7ce (n=5): **r = +0.401**

The signal is still weak and direction-unstable across runs at n=5. With this run agreeing in direction with run-8fa45533 (positive r ≈ 0.4–0.5), the overwhelmingly negative correlation in run-5928c151 looks like small-n noise rather than a real anti-correlation. Verdict: **cautiously consistent, but n=5 per-run is too small to call**. The heuristic and the judge appear to be measuring loosely related quantities; correlation will not stabilize until we run the judge on n ≥ 20 in one batch.

Of note: `msite-search-product-card-redesign-ab-test` was the **highest** judge score (8.00) but only mid-tier on the heuristic (7.4) — a clear example of the heuristic missing well-written pages. Conversely, the heuristic-top page (`contextual-blni-feedback`, 8.6) was only third in judge ranking.

## Topics vs systems — first-time system-page judgment

Systems scored notably lower than topics (5.89 vs 7.47 mean). Persona pattern:

| persona | topics mean (n=5) | systems mean (n=3) | delta |
|---|---|---|---|
| newbie | 7.60 | 5.67 | −1.93 |
| pm | 8.00 | 5.33 | −2.67 |
| ia | 6.80 | 6.67 | −0.13 |

PM and Newbie penalize systems much harder than IA does. The recurring complaint across all three system pages: **no DRI / owner / team named in body**, **no rollout-status or stage label**, **no metrics or SLA**, and **architecture is implied but never stated**. IA persona is more forgiving because it scores graph health (slugs resolve, frontmatter is well-formed) — and the system pages do have rich source lists and Related sections, so they pass the IA bar.

**System pages are *less* consistent across personas, not more.** Persona spread (max-min across newbie/pm/ia) on systems: avg 1.33 (range 0–2). On topics: avg 1.20 (range 0–2). Roughly the same, but `buyermy` and `photosearch` show full-2-point spreads driven by IA scoring high on graph-form while PM/Newbie penalize the missing ownership/metrics. The systems pages are essentially **"well-linked but content-thin"** — exactly the failure mode v9-U1 / v10-U2 was meant to address for topic pages, now showing up uncorrected on systems.

## Three prior weakness patterns — reproduce or attenuate?

1. **Stale dates / "active" pages with months-old facts** — **REPRODUCED**, severely. Multiple judge bullets call this out:
   - `auditmate` PM: *"Performance data is dated 2026-01-07 and the page was last compiled 2026-04-23 — nearly four months of drift with no updated latency or throughput numbers."*
   - `dir-desktop-mcat-price-widget` PM: *"The rollout target of '2026-02-end' is now in the past relative to the audit date (2026-04-13) with no update — the page reads as stale and I cannot tell if rollout happened or stalled."*
   - `whatsapp-photo-buylead` Newbie: *"Status says 'active' but the thread was closed 2026-02-12 and monitoring concluded — the page reads more like a completed initiative than an active one."*
   - `business-credentials-filter-search-api` PM: *"Bug fix target date (2026-02-10 for Ticket #642522) is in the past relative to any current read; no resolution status recorded."*

2. **Entity-slug fragmentation (email-suffix vs name-canonical)** — **REPRODUCED**, less severe. Auditmate IA: *"chittresh-lohani-indiamart-com, deepak-yadav-indiamart-com … use email-style slugs that may not match the canonical entity slug pattern used elsewhere."* `whatsapp-photo-buylead` IA: *"`[[sahil-sharma2-indiamart-com]]` … the `-indiamart-com` suffix pattern is inconsistent with how other entity links in this corpus resolve (some entities use that suffix, some don't — no single convention is enforced)."* This isn't `naman-jain1-` vs `naman-jain-` per se, but it's the same family of fragmentation. The 47abaa3 bulk-cleanup is incomplete.

3. **Missing system-layer wikilinks** — **REPRODUCED**, every page. Several explicit hits:
   - `business-credentials-filter-search-api` IA: *"No wikilinks to system pages (e.g., [[marketplace-launch]], [[buyermy]], or a search-api system page) despite the topic being tightly coupled to the Search API system — the topic floats without anchoring to any system node."*
   - `whatsapp-photo-buylead` IA: *"No link to `[[whatsapp9696]]` or `[[whatsapp]]` … the page describes a change to the WhatsApp 9696 bot but never wikilinks to the system page, breaking the topic→system edge."*
   - `dir-desktop-mcat-price-widget` IA: *"Only one system link … breaks the graph edge to the systems/testlink.md node that exists in the corpus."*

## F1 fix validation — citation confidence

PR #284's References backfill **did not visibly raise the judge's confidence in claims**. Every page in the sample carries dense per-claim footnotes (the judges note this approvingly), but the qualitative reasoning still flags **temporal staleness** rather than **untraceable provenance**. Representative quote (`contextual-blni-feedback` Newbie, score 8): *"Recent-changes log with dated entries and raw-file footnotes lets me cross-check freshness against raw/ in under a minute."* — citations are working as designed.

The judge's lingering distrust is no longer "I can't find the source" but "the source is two months old and the page hasn't been updated since." This is a **win**: F1 collapses the citation-trust failure mode, and the next failure mode (stale-claim drift) is now visibly the dominant complaint. **Verdict: F1 fix validated — citation provenance is no longer a top-3 weakness; staleness is.** This shifts the v11→v12 priority from "make claims verifiable" to "stamp claim-level dates and detect drift."

## Five qualitative quotes

- **Strong topic** — `msite-search-product-card-redesign-ab-test` IA, score 8: *"Every factual claim is footnoted to a specific raw source thread with a dated filename, making claim-verification straightforward … Design rationale cites UX principles (Gutenberg Diagram, Gestalt Law of Proximity) with enough specificity to be verifiable against source threads."*
- **Weak topic** — `whatsapp-photo-buylead-optimization` IA, score 6: *"The `related` frontmatter uses non-standard slug prefixes (`system/whatsapp-9696-bot`, `topic/buylead-whatsapp-display`) with directory-prefix notation that mkdocs-roamlinks stem-matching will NOT resolve — these should be bare slugs … as written they are effectively dead links."*
- **Strong system** — `auditmate` Newbie, score 7: *"Audit attributes table is comprehensive and well-structured; a new engineer can see exactly what checks are in scope vs out of scope … Known gaps table with dated footnotes and raw-file references is excellent — traceable to source emails, not just assertions."*
- **Weak system** — `photosearch` PM, score 4: *"No owner or DRI identified anywhere — 27 source files and no 'Owner:' field … No rollout or launch status — is this system in GA, limited rollout, or beta? … No metrics whatsoever — no query volume, success rate, latency, or conversion impact."*
- **F1 / citation impact** — `contextual-blni-feedback` Newbie, score 8: *"Bug tracker section is unusually complete: bug IDs, platform, assignee, and current status (open/resolved/deferred) all in one place — saves a Jira lookup … Recent-changes log with dated entries and raw-file footnotes lets me cross-check freshness against raw/ in under a minute."*

## Cost

**~$2.40** — 8 pages × 3 personas × ~$0.10/call (estimate from `estimate_cost`). No single page exceeded the $0.30 stop threshold; the full 24-row run completed.
