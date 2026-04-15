---
title: Systemic wiki quality review
date: 2026-04-13T03:11:02Z
scope: wiki/ (463 pages across entities, topics, systems)
mode: read-only diagnostic
---

# Systemic wiki quality review

**TL;DR.** The wiki does not have a "size problem" — it has a **frontmatter
bloat** problem and a **content-shallowness** problem. 98% of pages are under
10 KB; the 8 pages that aren't are entity pages drowning in duplicate
`sources:` entries (same email thread listed 20–34 times with different hashes).
Meanwhile, **235 of 463 pages (51%) have bodies under 500 bytes** — mostly
entity stubs with 1–3 bullet points. Lint warnings are negligible (12 total).
Cross-page references resolve cleanly (0 broken wikilinks in bodies).

The two axes of the problem are independent, but both originate in the
compile step: it appends every raw email to `sources:` without deduping by
thread, and it regenerates thin bodies for every name it sees without
checking whether the entity actually deserves a page.

---

## 1. Size distribution

| Category   | N   | p50   | p90   | p99    | max    | >10 KB | >20 KB |
| ---------- | --- | ----- | ----- | ------ | ------ | ------ | ------ |
| entities   | 311 | 652   | 2,436 | 16,790 | 26,413 | 8      | 1      |
| systems    |  57 | 587   | 1,876 |  4,665 |  7,331 | 0      | 0      |
| topics     |  95 | 2,903 | 4,975 |  6,197 |  6,569 | 0      | 0      |
| policies / timelines / conflicts | 0 | — | — | — | — | — | — |

Across all pages, **8 exceed 10 KB and only 1 exceeds 20 KB.** Topics have the
healthiest distribution (tight p50–p99 spread). Entities have a **long right
tail driven entirely by the sources list**, not the body.

### Body-only (excludes frontmatter)

| Category | p50 body | p90 body | p99 body | max body |
| -------- | -------- | -------- | -------- | -------- |
| entities | 269      | 996      | 2,317    | 4,873    |
| systems  | 239      | 1,043    | 3,665    | 3,806    |
| topics   | 2,527    | 4,553    | 5,903    | 6,075    |

Entity bodies are **10× smaller than topic bodies at p50**, yet entity files
are where the size outliers live — confirmation that bloat is in frontmatter.

---

## 2. Top 10 bloated pages

| Rank | Path                                             | Size   | Body  | Sources | Unique subj | Dup% |
| ---- | ------------------------------------------------ | -----: | ----: | ------: | ----------: | ---: |
| 1    | `wiki/entities/himanshu-jain01.md`               | 26,413 | 1,043 |     312 |          47 |  85% |
| 2    | `wiki/entities/bharat-agarwal.md`                | 19,879 |   157 |     246 |          48 |  80% |
| 3    | `wiki/entities/neeraj-vardhan-ponnada.md`        | 19,448 | 1,214 |     225 |          32 |  86% |
| 4    | `wiki/entities/sayan-samanta.md`                 | 16,790 |   693 |     197 |          42 |  79% |
| 5    | `wiki/entities/yashwant-chandra.md`              | 16,521 |   178 |     203 |          25 |  88% |
| 6    | `wiki/entities/alok-shukla.md`                   | 13,528 |   625 |     158 |          50 |  68% |
| 7    | `wiki/entities/julee-kumari.md`                  | 12,616 |   898 |     144 |          40 |  72% |
| 8    | `wiki/entities/ishu-garg.md`                     | 10,304 |   137 |     126 |          29 |  77% |
| 9    | `wiki/entities/ramavtar-pareek.md`               |  8,697 |   156 |     104 |          15 |  86% |
| 10   | `wiki/entities/sandeep-kumar.md`                 |  8,091 | 1,099 |      83 |          17 |  80% |

Every single one of the top 10 is an entity; every single one has **≥68%
duplicate subjects**. The body sizes (157, 178, 137, 156 …) are tiny
compared to the file size — these are frontmatter-only bloat.

### Source-duplication breakdown (top 3)

**`himanshu-jain01`** — 312 sources, 47 unique (85% dup).
Top repeated subjects:
- `mplaunchim-indiamart-premium-buyer-subscription-tr` × **26**
- `mplaunchimpre-launchcentral-smart-orchestrator-api` × **23**
- `mplaunchim-indiamart-buyer-payment-protection-plan` × **22**
- `mplaunchim-launch-migration-of-buylead-fulfillment` × **18**
- `mplaunchim-addition-of-productbl-images-in-astbuy-` × **17**

**`bharat-agarwal`** — 246 sources, 48 unique (80% dup). Body is 3 lines
("Email: …" plus two Related bullets). The file is 19.8 KB to say "this
person exists".

**`neeraj-vardhan-ponnada`** — 225 sources, 32 unique (86% dup). Top subject
repeats 20 times.

### Worst 15 entities by duplication ratio (from 18 entities with >20 sources)

| Page                       | Sources | Unique | Dup% | Top subject × N                                              |
| -------------------------- | ------: | -----: | ---: | ------------------------------------------------------------ |
| `yashwant-chandra`         |     203 |     25 |  88% | `mplaunchim-re-launching-tara-20-ai-powered-buylead` × 23    |
| `neeraj-vardhan-ponnada`   |     225 |     32 |  86% | `mplaunchim-from-idea-to-code-in-ai-time-ai-driven-` × 20    |
| `ramavtar-pareek`          |     104 |     15 |  86% | `mplaunchim-im-search-ak-key-page-threshold-update-` × 34    |
| `himanshu-jain01`          |     312 |     47 |  85% | `mplaunchim-indiamart-premium-buyer-subscription-tr` × 26    |
| `yashwant-singh`           |      51 |      8 |  84% | `informationalmplaunchim-api-knowledge-agent-is-liv` × 15    |
| `lucky-agarwal`            |      84 |     15 |  82% | `mplaunchim-indiamart-ios-app---buylead-webview` × 25        |
| `soumyajeet-sen`           |      95 |     17 |  82% | `mplaunchim-app-install-journey-poc-in-whatsapp` × 24        |
| `bharat-agarwal`           |     246 |     48 |  80% | `mplaunchim-indiamart-premium-buyer-subscription-tr` × 25    |
| `sandeep-kumar`            |      83 |     17 |  80% | `informationalmplaunchim-api-knowledge-agent-is-liv` × 18    |
| `sayan-samanta`            |     197 |     42 |  79% | `mplaunchim-ai-powered-mobile-ui-redesign-for-enhan` × 13    |
| `ishu-garg`                |     126 |     29 |  77% | `mplaunchimtechnical-launching-improved-background-` × 16    |
| `niraj-katiyar`            |      54 |     13 |  76% | `informationalmplaunchimtechnical-leads-rejection-a` × 9     |
| `aditi-garg`               |      35 |      9 |  74% | `mplaunchim-im-search-best-matching-sellers-introdu` × 15    |
| `kriti-nagar`              |      35 |      9 |  74% | `mplaunchim-im-search-best-matching-sellers-introdu` × 15    |
| `julee-kumari`             |     144 |     40 |  72% | `mplaunchim-pdp-page-wrapper-api-optimized-pdp-page` × 11    |

---

## 3. Content redundancy across large entity pages

Line-level and word-level uniqueness for the 5 largest entity bodies:

| Page                     | Body lines | Unique lines | Body words | Unique words | Dup-line% |
| ------------------------ | ---------: | -----------: | ---------: | -----------: | --------: |
| `himanshu-jain01`        |         23 |           23 |        125 |           76 |        0% |
| `bharat-agarwal`         |          3 |            3 |         17 |           10 |        0% |
| `neeraj-vardhan-ponnada` |         16 |           16 |        164 |          117 |        0% |
| `sayan-samanta`          |         10 |           10 |         98 |           68 |        0% |
| `yashwant-chandra`       |          2 |            2 |         22 |           18 |        0% |

**No exact body-line duplication** — the "Contributions" redundancy the user
described is semantic, not textual. Bullets like "Involved in multiple
buyer-focused initiatives including: Buyer NPS feedback, Buyer fulfilment
feedback, Buyer My interface, Track order, PPP, …" each list the same 6–10
programs in slightly different phrasings. The compile step rewords the same
content per-entity instead of linking to a single canonical program page.

Note also the shape: `bharat-agarwal` has 3 body lines supported by 246
source emails. That is not a knowledge page; it is a directory entry with a
megabyte of citations glued on.

---

## 4. Lint state

`uv run python scripts/lint_wiki.py` → **0 errors, 1 warning, 11 info
(12 total)**. All 11 infos are "missing_index_entry" (pages that exist but
aren't linked from `wiki/index.md`) and the 1 warning is an orphan page
(`kundan-kumargiri`). Lint is not the pressure point; the compiler is
producing well-formed but substantively thin output.

Additional issues the current linter does not catch (found in this review):

- **Byte-identical body pairs** left over from a rename:
  `entities/samarth.md` ≡ `entities/samarth-temp.md`, and
  `entities/sandeep-garg.md` ≡ `entities/sandeep-garg-temp.md`. The
  `check_duplicate_bodies` check already exists — it fires if run, but the
  `-temp` versions are still checked into the tree.
- **Source duplication** by normalized subject (no check exists).
- **Thin-body entities** (no check exists).

---

## 5. Pages that are stubs disguised as content

| Bucket                       | entities | systems | topics |
| ---------------------------- | -------: | ------: | -----: |
| Body < 300 B                 |      167 |      32 |      0 |
| Body < 500 B                 |      228 |      37 |      0 |
| Body < 1 KB                  |      280 |      48 |      5 |

**228 of 311 entity pages (73%)** have bodies shorter than 500 bytes but
non-empty `sources:` or `related:`. Worst: `entities/rahul-singh-dehradun.md`
has 73 bytes of body, 317 bytes of frontmatter, 2 sources. These pages exist
only because a name appeared in an email; they carry no information that
couldn't live as a row in a directory table.

---

## 6. Cross-page references

**Top 15 incoming links** (to existing pages):

| Page                                               | Incoming |
| -------------------------------------------------- | -------: |
| `marketplace-launch`                               |      129 |
| `whatsapp`                                         |       21 |
| `buyermy`                                          |       20 |
| `whatsapp9696`                                     |       20 |
| `neeraj-agrawal`                                   |       19 |
| `lms-bmc-access-restriction-fraud-hrs-users`       |       15 |
| `auditmate-history-feature`                        |       15 |
| `vikram-varshney`                                  |       15 |
| `replacing-and-removing-expired-bl-alert-notifs`   |       14 |
| `ai-powered-mobile-ui-redesign-bicycles`           |       14 |

**Broken wikilinks: 0.** Every `[[target]]` resolves to an existing page.
This is largely because `lint_wiki.py --fix` auto-creates stubs for unknown
targets — which inflates the "thin body" count, so the two problems are
linked: every previously-broken link became a stub, and the stub inventory
is now 73% of entities.

---

## 7. Common failure patterns

1. **Thread fan-out into sources** (biggest bloat driver). Each reply on a
   mailing list arrives as a separate raw file with a different random hash
   suffix; the compiler appends all of them. Result: one thread with 30
   replies adds 30 source entries. Top offender:
   `mplaunchim-im-search-ak-key-page-threshold-update-` appears **34 times**
   in `ramavtar-pareek.md`.
2. **Entity-per-name, regardless of signal.** Anyone cc'd on any email gets
   a page; most end up as 1-source stubs. 228 of 311 entity pages are
   sub-500-byte stubs with no real content.
3. **Contribution sections re-list the same programs per person.** The same
   6–10 launch names ("PPP", "Know Your Seller", "Smart RFQ", …) appear
   across dozens of entity pages, each written in slightly different words.
   No extraction to a single canonical program page.
4. **Residual `-temp` rename artifacts** (2 pairs) duplicate two entities
   verbatim.
5. **Compile step lacks a "coalesce by thread" primitive.** Source lists
   grow linearly with email volume instead of logarithmically with thread
   count.

---

## 8. Suggested automated fixes

| # | Pattern                                  | Where to fix                                  | Fix                                                                                                                                                                                |
| - | ---------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | Duplicate sources by normalized subject  | **Compile-time** (`scripts/compile_all.py`)   | Before writing `sources:`, collapse entries whose filename share the same normalized subject (strip date prefix, `Re:/Fwd:`, trailing `_[0-9a-f]{8}`); keep `{subject}` with a `count` field or earliest/latest date only. Would shrink `himanshu-jain01.md` from 312 to 47 sources. |
| 2 | Per-page source cap                      | Compile-time                                  | Hard-cap sources at e.g. 25 per page; if more, write a sibling `sources/` file or truncate with "… and N more threads".                                                            |
| 3 | Minimum-body threshold for entity pages  | Compile-time                                  | Require ≥2 distinct thread subjects and ≥300 body B to materialize an entity page. Otherwise route the name into a `wiki/entities/_directory.md` index row.                         |
| 4 | Thin-body detector                       | **`lint_wiki.py`** (new check)                | New check `thin_body`: body < 500 B **and** frontmatter > 3× body size → warning, with a `--fix` option that folds the page into the shared directory page.                         |
| 5 | Subject-duplication detector             | `lint_wiki.py` (new check)                    | For any entity page with >20 sources, warn if >50% of sources share a normalized subject.                                                                                          |
| 6 | Residual `-temp` duplicates              | Post-processing one-shot                      | Run `check_duplicate_bodies` (already implemented); delete the `-temp` copies. Also add a rule: refuse to commit a page whose stem ends in `-temp`.                                 |
| 7 | Contribution section canonicalization    | Compile-time                                  | When an entity's contributions reference a program that already has a topic page, emit a single `[[topic-slug]]` wikilink instead of re-describing it. Today the same program is reworded across ~20 entity pages. |
| 8 | Entity directory page                    | Compile-time                                  | Emit one `wiki/entities/_directory.md` with a table (name, email, #threads, primary topic) — so the thin stubs can collapse into rows instead of files.                            |
| 9 | Orphan + missing-index cleanups          | `lint_wiki.py --fix` (exists)                 | Already auto-fixable; just run it.                                                                                                                                                 |

### Cheapest wins, in order

1. **Dedupe sources by normalized subject** at compile time (fix #1). Shrinks
   the top-10 bloated pages by ~80% with zero loss of information.
2. **Remove `-temp` duplicates** (fix #6). 2 files, verbatim duplicates.
3. **Add thin-body lint check** (fix #4). Surfaces the 228 entity stubs so a
   directory-style migration (fix #8) can be planned.
4. **Raise entity materialization threshold** (fix #3). Stops the bleeding
   going forward.

---

*Read-only review; no files modified. Analysis script at `/tmp/wiki_analyze.py`.*
