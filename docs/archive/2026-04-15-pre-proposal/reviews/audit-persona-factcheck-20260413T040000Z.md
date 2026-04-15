# Wiki Fact-Faithfulness Audit — 2026-04-13

Auditor: Claude (fact-faithfulness review agent)
Scope: `/Users/amtagrwl/git/email-knowledge-base/wiki/` pages verified against their `sources:` in `/Users/amtagrwl/git/email-knowledge-base/raw/`

## Verdict Counts

- Supported: 5
- Partially supported (right idea, wrong detail): 3
- Not found in cited sources: 1
- Contradicted by source: 1
- Total claims checked: 10

Pattern: the wiki's LLM-generated prose is largely faithful where it paraphrases launch announcements, but degrades when it tries to attribute quotes to specific people, normalize lists, or invent metadata-like qualifiers ("Stakeholder", "Primary challenge", role labels). The most dangerous class of error is attaching real material to the *wrong* source file (claim 2) and fabricating a quote not in any source (claim 3).

---

## Claim-by-Claim

### 1. Auditor 1.1 analysis checks — only 4 listed (source has 5)

- Claim: `wiki/topics/auditor-11-no-photo-version-rollout.md:39-44` — "The No Photo Version analyzes products without taking photos into context, performing only these checks: 1. Title vs Category 2. Title vs Specification 3. Error Within Product Title 4. Category_contradiction..." (4 items).
- Source: `raw/2026-01-02_mplaunchim-auditor-11-no-photo-version-rollout-for_adef625b.md:93-102` lists 5 checks; check #5 is "Error Within Product Search Query".
- Verdict: WARNING Partially supported. The list is truncated. The fifth check was silently dropped.

### 2. Vikram Varshney "CC'd on city-based filters ... (March 20, 2026)"

- Claim: `wiki/entities/vikram-varshney.md:38` — "CC'd on city-based filters on lens results page testing feedback (March 20, 2026)."
- Source in the page's `sources:` dated 2026-03-20 is `raw/2026-03-20_mplaunchim-leap-launch-poc-using-mapping-agent-to-_b3900022.md` — subject "LEAP Launch — POC | Using Mapping Agent to Correctly Map BuyLeads with Used, Second-Hand or Refurbished Product Requirements". This thread is about LEAP mapping agent for BuyLeads, not about city-based filters on Lens. Grep for "city-based" / "city based" in that source returns 0 matches. City-based-filters-on-lens has its own separate raw files (e.g., `raw/2026-03-20_mplaunchim-lensindiamart-city-based-filters-on-len_eaaaef8e.md`) which are *not* listed in this entity's sources.
- Verdict: CONTRADICTED. Misattribution — wrong source file glued to the claim. Either the citation is wrong, or the sentence is hallucinated. Most likely: the date-matching logic grabbed a raw file from the same day and fabricated a plausible-sounding summary.

### 3. Vikram Varshney quote "Key issue is low feedback count. Please keep a close tab on it"

- Claim: `wiki/entities/vikram-varshney.md:42` — Vikram "Identified the primary challenge: 'Key issue is low feedback count. Please keep a close tab on it'" on the Photosearch Star Rating launch.
- Source: `raw/2026-01-02_mplaunchim-photosearch-new-star-rating-feedback-po_c6f372e1.md`. I read the entire body; Vikram Varshney appears only in the `cc:` list and in the author's acknowledgments ("Special thanks to @Aseem Shekhar and @Vikram Varshney for their constant guidance and support"). He does not speak. Grep for "low feedback count", "feedback count", "close tab" — all zero matches.
- Verdict: NOT FOUND. Hallucinated quote and speaker attribution.

### 4. Dinesh Agarwal "That's very little coverage of calibration, Not sufficient" (April 6, 2026)

- Claim: `wiki/entities/dinesh-agarwal.md:53-56` — Buyer Specs Scale Up Phase 2.0 (April 6, 2026): Dinesh gave feedback "That's very little coverage of calibration, Not sufficient".
- Source: `raw/2026-04-06_mplaunchim-launch-buyer-specs-scale-up-phase-20-pm_7093b3f6.md:98-102` — the quote originates from **Dinesh Agarwal's email dated Thu, Mar 26, 2026 at 5:23 PM**, not April 6. The April 6 source is Amarinder's short reply ("await the corresponding quality report") which itself quotes Saurabh Rai who quotes Dinesh's earlier Mar 26 message.
- Verdict: WARNING Partially supported. Quote is real and belongs to Dinesh, but the date "April 6" is wrong by ~11 days. The entity page should say Mar 26.

### 5. Dinesh Agarwal "Very very good, Let's try to get 10/10" (Jan 3, 2026)

- Claim: `wiki/entities/dinesh-agarwal.md:80-83` — Phase 2 Code Revamp: Dinesh gave feedback "Very very good, Let's try to get 10/10".
- Source: `raw/2026-01-03_mplaunchim-phase-2-code-revamp-additional-26-docum_18ce8fef.md:32-33` — Dinesh's email, dated 2026-01-03T00:02:00+05:30. Exact text "Very very good / Let's try to get 10/10".
- Verdict: SUPPORTED.

### 6. Dinesh Agarwal "How do test false positive or false negative" on GST Blocker (Apr 6, 2026)

- Claim: `wiki/entities/dinesh-agarwal.md:48-51` — On Intent-Based GST Blocker (April 6, 2026), Dinesh raised "How do test false positive or false negative".
- Source: `raw/2026-04-06_mplaunchim-intent-based-gst-blocker-across-platfor_24e4dedf.md:36-37` — Dinesh email dated 2026-04-06T23:52:56+05:30: "Very good / How do test false positive or false negative".
- Verdict: SUPPORTED.

### 7. Complaint Agent v2 — "Live as of December 31, 2025" and Bug 635878

- Claim: `wiki/topics/complaint-agent-v2-whatsapp-9696.md:63-74` — "Target: 50% of paid sellers (GLIDs ending with 1, 5, 6, 7, and 8)"; "Status: Live as of December 31, 2025"; "Bug 635878 ... Priority: Medium ... Status: To Do".
- Source: `raw/2026-01-02_mplaunchim-complaint-agent-for-paid-sellers-on-wha_abd01702.md` — Saswat's original launch email dated Wed, Dec 31, 2025 at 4:10 PM with "now live for 50% of paid sellers (GLIDs ending with 1, 5, 6, 7, and 8)". Bug 635878 confirmed in `raw/..._16247837.md:45-49` with priority Medium, status To Do, assignee Saswat Sarangi.
- Verdict: SUPPORTED.

### 8. Dynamic Dispositions — "Countries: Vietnam, India, Romania"

- Claim: `wiki/topics/dynamic-dispositions-on-buyer-nps-feedback-screen.md:99` — "Countries: Vietnam, India, Romania".
- Source: `raw/2026-01-02_mplaunchim-launch-dynamic-dispositions-on-buyer-np_4f955e25.md:62-66` — Sunny Sachdeva's test report lists countries as "Vietnam, India, Vietnam, Romania" (Vietnam appears twice, probably a typo in the original email but reproduced verbatim in the raw). Wiki silently de-duplicated.
- Verdict: WARNING Partially supported. Low-impact normalization, but it does alter the quoted test data.

### 9. Dinesh Agarwal "Will await going live again" (Jan 2, 2026)

- Claim: `wiki/topics/dynamic-dispositions-on-buyer-nps-feedback-screen.md:83` — "Leadership Response: Dinesh Agarwal acknowledged the revert and stated 'Will await going live again' on Jan 2, 2026."
- Source: `raw/2026-01-02_mplaunchim-launch-dynamic-dispositions-on-buyer-np_7d9fea96.md:22,38` — Dinesh's email dated 2026-01-02T02:24:26+05:30, body "Will await going live again".
- Verdict: SUPPORTED.

### 10. ASTBUY Day-1 feedback rate "6.17% → 7.38%"

- Claim: `wiki/topics/astbuy-product-images-whatsapp.md:90` — "Feedback rate increased from 6.17% -> 7.38%, indicating higher buyer interaction with enriched WhatsApp messages."
- Source: `raw/2026-01-08_mplaunchim-addition-of-productbl-images-in-astbuy-_bfa4e65e.md:59` — Pragnya Vemulapalli's Day-1 update (quoted from Jan 7) says "Feedback rate increased from *6.17% → 7.38%*, indicating higher buyer interaction with the enriched WhatsApp messages." Exact match.
- Verdict: SUPPORTED.

---

## Cross-Cutting Observations

### Dates drift into wrong years/weeks

- Claim 4 places a Mar 26 quote on April 6 (same page as the launch announcement). This is a systemic risk: when a thread's top-level `date:` is April 6 but the body quotes a March 26 sub-message, the LLM is tagging the whole page's events with the top-level date.

### Hallucinated attributions

- Claim 3 fabricated a quote and attributed it to someone who never spoke in the thread.
- The entity pages in general attach prose like "Identified the primary challenge", "Leadership oversight", "Project accountable" to people whose actual contribution in the source is just being on a CC line. Verify these labels before trusting any entity page's responsibility claims.

### Mis-matched `sources:` lists

- Claim 2 is the most concerning: the `sources:` list points at a file that does not contain the content the prose claims. Presumably a date-based join (same day, same person in CC list) collided with a different unrelated topic. This could easily repeat elsewhere — recommend a spot-check of every entity page whose `related:` spans unrelated topics.

### Truncated lists

- Claim 1 dropped one of five checks. The summarizer seems to stop at four — possibly a token-budget or formatting artifact. Worth grepping other topic pages for numbered lists that might have been clipped.

### De-duplication of quoted raw data

- Claim 8 silently cleaned a duplicated "Vietnam" in test data. If this wiki is used as evidence for actual testing coverage, altering the raw test-report data (even to "fix" a typo) is a trust violation.

### What held up well

- Ticket IDs (635878, 633510, 635522, 651190, etc.) and bug assignments were consistently correct where checked.
- Metrics quoted as exact numbers with `%` signs mostly tracked (6.17% → 7.38%, 22,460 MCATs, etc.) as long as they appear verbatim in the source.
- Dinesh Agarwal's laconic one-liners ("Good", "Very good", "Let's try to get 10/10", "Will await going live again", "How do test false positive or false negative") are faithfully reproduced and correctly attributed — presumably because they are short and distinctive enough that the LLM just copies them.

## Trust Recommendation

Treat any "entity page" sentence that (a) attributes a quote to a person, (b) assigns a role label ("Project accountable", "Stakeholder", "Primary challenge identifier"), or (c) dates an event should be independently checked against the cited raw file before being relied on. Topic pages that paraphrase a single launch email are generally trustworthy for "what happened"; they degrade where they synthesize across multiple threads.

The `sources:` field cannot be assumed to be accurate — at least one page (vikram-varshney.md) cites a raw file whose content has no relationship to the prose it supposedly supports.
