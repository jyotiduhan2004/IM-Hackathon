# Blind-audit synthesis — 2026-04-13

Five independent agents audited the wiki without any prior context about its
architecture or compiler. Each had a different persona: new employee, PM
status-hunter, information-architecture reviewer, fact-faithfulness auditor,
business journalist. Reports in `docs/reviews/audit-persona-*-20260413T040000Z.md`.

This file consolidates what they agreed on, ranked by impact × ease.

## Cross-cutting themes (at least 2 of 5 agents named it)

### 1. Duplicate-page proliferation — **every agent flagged it**

The suffix-dupe detector I wrote only catches `-new|-v\d+|-copy|-latest|-updated|-temp|-draft|-rev\d*`. Actual duplicate families in the wild are much wider:

- `-clean` suffix: `arjun-gaur` / `arjun-gaur-clean`, `saurabh-gupta` / `saurabh-gupta-clean`
- Numeric suffix: `alok-kumar` / `alok-kumar2`, `deepak-jain` / `deepak-jain1`, `whatsapp` / `whatsapp9696`, `sahil-sharma` / `sahil-sharma2`
- US/UK spelling: `dspy-gepa-automated-speaker-labeling` / `-labelling-pipeline`
- Same email, different slug: `durga-muddala` / `durga-suresh-muddala`, `buyers-helpteam` / `buyershelpteam`
- Cross-category collision: `samarth` exists in both `entities/` and `systems/` — `[[samarth]]` is ambiguous
- Stub vs canonical: `sahil-sharma.md` is a stub, real content lives at `sahil-sharma2.md`

IA audit counted **11 near-duplicate pairs**.

### 2. Stubs dominate the wiki — **64% of pages are stubs**

295 / 463 pages have body under 100 words (IA audit count). A large share are auto-created by the compiler when it encounters an unresolved `[[wikilink]]` — that behavior *hides* broken links by manufacturing placeholder pages, inflating page count without adding knowledge.

Named stub offenders:
- `wiki/systems/buylead.md` — the company's core product concept, as a stub
- `[[sahil-sharma]]` (stub) pulls link traffic away from `sahil-sharma2.md` (real)
- 20 entity stubs + 17 system stubs identified by IA audit

### 3. Category mislabeling — **3 of 5 agents named it**

- **5 humans stranded in `systems/`**: `alok-kumar2`, `bolisetty-shravan-kumar`, `deepak-yadav01`, `mohammad-kashif-khan`, `samarth`
- **4 products/teams stranded in `entities/`**: `m-site`, `mobile-team`, `lens-indiamart`, `pr-agent`
- **Most-linked page in the wiki** is `systems/marketplace-launch.md` (134 inbound) — but it's actually a mailing list, miscategorized despite being a central hub

### 4. Source coverage gaps — **newbie + factcheck audit**

- `topics/whatsapp9696-agentic-buyer-chatbot.md` cites 2 raw emails; raw/ has **30** on the same thread. Two months of updates missing.
- Vikram Varshney entity is missing the raw where he's the actual subject.
- `sources:` list often doesn't reflect what's available in raw/. Compile is a snapshot in time; raw accumulates.

### 5. Factual degradation on entity pages — **factcheck audit; 1 of 10 claims hallucinated**

- Hallucinated quote + speaker attribution: `entities/vikram-varshney.md:42` has "Key issue is low feedback count. Please keep a close tab on it" attributed to Vikram on Photosearch. Not in any source. Vikram is only in CC + acknowledgments.
- Source misattribution: CC'd on X, but the Mar 20 source listed is actually about Y (same-day-different-topic collision).
- Date error: Dinesh quote dated April 6 in wiki but actually Mar 26 in source (quoted sub-message inside a later thread).
- Truncated list: 4 of 5 items preserved on an analysis checks list.
- Silent data normalization: verbatim country list deduplicated ("Vietnam, India, Vietnam, Romania" → "Vietnam, India, Romania").

Pattern: **entity pages degrade more than topic pages.** Topic pages paraphrasing one launch email are trustworthy. Entity pages synthesizing across threads hallucinate more.

### 6. File / YAML corruption — **newbie + journalist**

- `topics/sonarqube-quality-profile-transformation.md` has **two `last_compiled:` keys and a split `sources:`** block — validator should fail on this and doesn't.
- Copy-paste tails on `entities/neeraj-agrawal.md` and `entities/saurabh-gupta.md` (edit_file rewrite artefact).
- `wiki/log.md` (2026-04-13T02:05Z) already recorded "18 broken pages deleted — frontmatter corruption from agent edit_file."

### 7. Missing infrastructure — **newbie + PM**

- No glossary for domain acronyms: PNS, ISQ, CSL, MCAT, BMC, HRS, etc. Newbie read them as pervasive and uninterpretable.
- Frontmatter has `status: current` but no `owner:`, `stage:`, `target_date:` — biggest gap for PM use.
- No rollup/hub pages for cross-cutting themes. WhatsApp work lives across 12 pages with no master index.
- `conflicts/`, `policies/`, `timelines/` directories are empty but advertised to the compiler as valid categories → dead nav slots.
- `wiki/index.md` claims 461 pages; actual is 463. Stale count.

### 8. `## Related` append-not-merge — **IA audit**

- Duplicated `## Related` sections on several topic pages. Updater is appending rather than merging.

## Prioritized actions

### Tier 1 — Ship today, no LLM needed (~2 hours)

1. **Widen suffix-dupe detector** in `scripts/validate_wiki.py` and `scripts/merge_suffix_dupes.py`. Add: `-clean`, numeric suffixes (`\d+$`), single-char variants. Re-run merge. Expected to collapse the 11 pairs IA audit found.
2. **Add YAML integrity check** to `validate_wiki.py`: parse frontmatter as YAML, fail on duplicate keys. Would have caught the `sonarqube-quality-profile-transformation.md` double `last_compiled`.
3. **Add content-integrity check** to `validate_wiki.py`: duplicate `## Related`, `## Sources`, or any repeated H2 within one page fails.
4. **Move miscategorized pages**: 5 humans systems/→entities/, 4 products entities/→systems/. Script it; include wikilink rewrite.
5. **Resolve `samarth` collision**: pick canonical (which one has real content?), redirect the other.
6. **Rename `sahil-sharma` → `sahil-sharma2` as canonical** (or vice versa); delete stub; rewrite wikilinks.
7. **Clean copy-paste tails** on `neeraj-agrawal.md`, `saurabh-gupta.md`.
8. **Drop the auto-stub-on-unresolved-wikilink behavior.** Compiler should either create a *real* page or leave the link as plain text. Stubs-as-placeholders hide problems.

### Tier 2 — Ship this week, no LLM (~half day)

9. **Kick off Issue #8 Phase 0** (build SQLite catalog from raw/). Fixes source-coverage gaps structurally. Unblocks the render-cleanup path.
10. **Delete empty `conflicts/`, `policies/`, `timelines/` directories** OR generate a seed page in each so the category isn't a dead slot.
11. **Auto-regenerate `index.md`** from the actual wiki tree with correct counts. Small script.
12. **Create `wiki/glossary.md`** — domain acronyms (PNS, ISQ, CSL, MCAT, BMC, HRS, BL, etc.). Can seed from raw emails without LLM.
13. **Add `owner:`, `stage:`, `target_date:` to the compiler prompt** as optional topic-page fields. Takes effect on next LLM run.

### Tier 3 — Needs LLM budget back

14. **Recompile hallucination victims**: `vikram-varshney` (quote attribution) and any entity page cited in the factcheck audit as wrong-on-dates-or-quotes.
15. **Strengthen compiler prompt**: "Don't attribute quotes to CC'd people. Verify the quote actually appears in the source body, not just because the name is in the thread."
16. **Add `## Related` merge logic** to the edit_file update path (or regenerate Related at render-time from backlinks).

### Tier 4 — Structural (issue #8 follow-up)

17. **Ship Phase 2** of the catalog plan (render sources from SQLite join, behind feature flag).
18. **Ship Phase 4** (strip frontmatter `sources:` from existing pages).
19. **Topic rollup pages** (e.g. "all WhatsApp work") — auto-generatable from the catalog's `slug_mentions` table once #8 lands.

## Recommendation

**Do Tier 1 today while LLM budget is frozen.** Every item is mechanical, reversible, and fixes a specific pattern at least 2 of 5 agents independently called out. Expected outcome: ~11 duplicate pages collapsed, 9 miscategorized pages moved, YAML corruption caught automatically going forward, and the auto-stub noise removed.

Tier 2 should wait for user review — those are design decisions (delete empty categories? add owner fields?) that deserve a thumbs-up.

Don't start Tier 3 until LLM budget is back. Don't start Tier 4 until issue #8 is reviewed and approved.
