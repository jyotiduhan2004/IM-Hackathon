# Smoke run 99a267f4 — quality audit (2026-04-28)

**Run id:** `99a267f4-55f1-4fad-bca9-e23589bfab01`
**Kicked:** 2026-04-28 ~01:56 IST · `--limit 25 --batch-size 5 --batch-timeout 900`
**State at audit time:** ~1h in, 31/134 emails compiled across 4 models, 0
hard failures.

This is the first clean post-Wave-1 + post-PR-#238 run measuring whether
the previously-troubled glm-5 / glm-5.1 models actually produce good
content (not just zero-failure). 12 wiki pages spot-checked across 3
parallel deep audits.

## Headline

| Model | Sample | Mean quality | Hard fails | Notes |
|-------|--------|--------------|-----------:|-------|
| z-ai/glm-5      | 4 of 14 | **4.5 / 5** | 0 | Strong synthesis; supersession handled in `<details>` block |
| z-ai/glm-5.1    | 4 of 17 | **4.75 / 5** | 0 | On par with grok/kimi/minimax; proactive sibling-page cross-linking |
| skip decisions  | 9 of 9  | 6 correct + 1 clear MISTAKE + 2 borderline | — | Question-delta caught existing-page case; missed the answer-arrival case |

Quality is genuine, not just "didn't crash". The 0% recursion-fail
signal corresponds to real content extraction.

## Findings (by severity)

### F1 — `already_captured` skip when email is the *answer* to an open question (HIGH)

**Case:** msg `55ff6c5f` (GST repositioning, 2026-01-30, grok-4.1-fast).

- Jan 26: Devesh Agarwal asks "await last week impact" on the GST
  repositioning launch.
- Jan 30: Aditi replies with the requested 18-24 Jan window — selection
  +521% (vs prior 3x), conversion -48.53%, overall page conv +2.27%.
- grok skipped Jan 30 as `already_captured` because the wiki page
  `wiki/topics/repositioning-of-gst-registration-annual-turnover-filters.md`
  already existed.
- Result: the wiki has only the Jan 23 metrics window. DA's Jan 26 ask
  remains formally open on the page, but the answer is gone.

**Pattern:** same class as bug #181 (`Question-delta exception`). That
fix says *"don't skip if the email contains an open question"*. Symmetric
gap: *"don't skip if the email is the answer to an open question already
in the wiki"*.

**Borderline cases (same pattern):**
- msg `26a265cf` (Ads-PDP, 2026-01-27, grok) answers DA/AB's "do we
  know the volume of ads users doing search?" with 3.9M PV / 1.9M user
  data; wiki only has prose summary "~50K weekly searches".
- msg `070a5aa4` (Ads-PDP, 2026-01-29, grok) re-states the same 3.9M /
  1.9M denominator; same gap.

### F2 — `updated_by` frontmatter not re-stamped on rewrite (MEDIUM)

Two pages found with stale `updated_by`:
- `wiki/topics/mcat-search-hybrid-audit-process.md:8` —
  `updated_by: minimax/minimax-m2.7` despite glm-5.1 rewrite confirmed
  by mtime 02:14-02:17.
- `wiki/topics/buyer-feedback-on-bl-purchase-seller-introductions-via-whatsapp.md:28`
  — same shape, stale stamp from a prior run.

This is a coordinator-side stamp that's missing on edit-only paths.
Per CLAUDE.md the coordinator owns clock-driven fields; this one is
slipping through.

### F3 — Split-identity wikilinks (MEDIUM)

`wiki/topics/hrs-marked-free-buyers-bl-posting-restriction.md` lines
36-50 reference the same person as both `[[mohak-saxena]]` (legacy
slug) and `[[mohak-saxena-indiamart-com]]` (canonical email-derived
slug). Both target pages exist but represent the same identity.
Violates the "always use email-canonical entity slugs" rule.

### F4 — Section ordering bug (LOW)

`wiki/topics/lms-saved-replies-api-migration-gke.md:80` — `## TL;DR`
heading appears AFTER `## References`, not at the top. Looks like an
edit_file error rather than systematic.

### F5 — Pre-existing broken wikilink (LOW, not a regression)

`wiki/topics/ai-driven-pdp-experiment.md:11` — `[[marketplace-launch]]`
points to a page that doesn't exist (`wiki/topics/marketplace-launch.md`
is absent). Left in place during glm-5.1 re-compile. This pre-dates the
audit; flagged so it doesn't get baked in further.

## What's working

- **Synthesis** is genuine, not paraphrase. glm-5 reconstructed an
  A/B/C variant table from raw numbers and surfaced a same-thread
  decision reversal in a `<details>` "Initial decision confusion"
  block. That's exactly the supersession-handling pattern.
- **Question-delta exception** is firing as intended — kimi correctly
  skipped the cache-rule thread because Jan 30 + Feb 3 follow-ups are
  already cited on the existing page via `[^msg-*]` footnotes.
- **Cross-linking**: glm-5.1 proactively linked sibling Gladmin
  migration pages (PNS spam, bulk-blacklist, bulk-PNS-upload) on a
  brand-new compile without being told.
- **Numbers and dates are faithful** — sample-by-sample, recomputed
  P95 percents and weekly windows match raw email content. No
  hallucinations found in 12 spot-checked pages.

## Action stack

| # | Action | Severity | This PR? |
|---|--------|----------|---------|
| F1 | Prompt: extend Question-delta to cover answer arrival | HIGH | **yes** |
| F2 | Coordinator: re-stamp `updated_by` on edit-only writes | MEDIUM | follow-up task |
| F3 | One-off: rewrite `mohak-saxena` → email-canonical slug on the HRS page | MEDIUM | manual fix |
| F4 | One-off: lift `## TL;DR` to top of `lms-saved-replies-api-migration-gke.md` | LOW | manual fix |
| F5 | Decide whether `marketplace-launch` should be auto-stubbed or wikilinks scrubbed | LOW | future |

## Sample list (12 pages spot-checked)

**glm-5 (4):** `foreign-bl-expiry-email-bounce`,
`buyer-feedback-on-bl-purchase-seller-introductions-via-whatsapp`,
`lms-saved-replies-api-migration-gke`,
`hrs-marked-free-buyers-bl-posting-restriction`.

**glm-5.1 (4):** `mcat-search-hybrid-audit-process`,
`remove-bulk-pns-screen-migration-react-nodejs`,
`ai-driven-pdp-experiment`, `foreign-bl-expiry-email-bounce`.

**Skip decisions (9):** 3 cache-rule (kimi), 3 GST (grok), 3 Ads-PDP
(grok). One clear mistake + 2 borderline; details in F1.
