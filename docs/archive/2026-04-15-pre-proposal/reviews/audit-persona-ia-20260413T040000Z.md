# IA Audit — Wikilink Graph Coherence & Connectivity

Auditor persona: information-architecture. Scope: `wiki/` only (plus `raw/` for claim-verification). Resolver model assumed: `mkdocs-roamlinks`, which matches `[[slug]]` against a page stem anywhere in the docs tree, case-insensitively. One slug = one target unless two `.md` files share a stem (collision).

## 1. Random walk (10 hops from `wiki/index.md`)

Arbitrary starting page: `topics/central-smart-orchestrator-api.md`.

| Hop | Page | Notes |
|----|------|-------|
| 0 | `topics/central-smart-orchestrator-api.md` | Seed. |
| 1 | `systems/webpurify.md` | Resolves. Legacy "superseded" third-party tool. |
| 2 | back to `central-smart-orchestrator-api` | **2-cycle.** `webpurify` has exactly one outbound link and it points home. |
| 3 | `systems/alok-kumar2.md` | **Category spillover + stub.** This is a person (Alok Kumar) auto-placed in `systems/` because the compiler slugged him `alok-kumar2` and stubbed him under systems. Entity `alok-kumar` already exists. Only outbound is back to source → cycle. |
| 4 | `entities/sunil-parolia.md` | Resolves. Rich back-references. |
| 5 | `topics/msite-pdp-automation-ai-cypress.md` | Resolves. **Minor smell:** two `## Related` headings, indicating the updater appended rather than merged. |
| 6 | `systems/cypress.md` | Resolves. |
| 7 | `systems/testlink.md` | Resolves. |
| 8 | `topics/lms-save-feedback-api-migration-gke.md` | Resolves. |
| 9 | `entities/alok-shukla.md` | Resolves. 49-source entity, rich. |
| 10 | `topics/feedback-system-for-ai-call-summary-notes.md` → `systems/marketplace-launch.md` | Dense hub. |

All 10 hops resolved to real `.md` files — **no dead links** in the walk. Two pitfalls surfaced on the way: (a) `webpurify ↔ central-smart-orchestrator-api` is a tight 2-cycle with no alternative exits on the `webpurify` side; (b) `alok-kumar2.md` is a person masquerading as a system.

## 2. Entity spot-check (5 random)

Random seed `20260413`, sampled from `wiki/entities/`:

| Page | Unique outbound | Non-index inbound | From-entities | From-topics | From-systems | Dead |
|------|----|----|----|----|----|----|
| `neeraj-agrawal` | 25 | 20 | 2 | 17 | 1 | 0 |
| `saurabh-gupta-clean` | 3 | **0** | 0 | 0 | 0 | 0 |
| `rohith-menon` | 2 | **0** | 0 | 0 | 0 | 0 |
| `navendra-singh` | 8 | 6 | 0 | 6 | 0 | 0 |
| `tanya-agrawal` | 2 | 2 | 1 | 1 | 0 | 0 |

All outbound wikilinks resolve. But `saurabh-gupta-clean` and `rohith-menon` are effective orphans — linked **only** from `index.md`. The `-clean` suffix is a tell: it's a dedup attempt that left the legacy `saurabh-gupta.md` alone, so the graph still routes to the old page. Entity-to-entity back-reference coverage is weak in general (Neeraj gets 2 from his ~200 peers).

## 3. Topic spot-check (5 random)

| Page | Unique outbound | Non-index inbound | From-entities | From-topics | From-systems | Dead |
|------|----|----|----|----|----|----|
| `buyer-fulfilment-feedback-did-not-post-requirement-disposition` | 7 | 5 | 5 | 0 | 0 | 0 |
| `mandatory-otp-for-enquiry-form-on-pdp` | 1 | 1 | 1 | 0 | 0 | 0 |
| `export-csl-tracking` | 6 | 4 | 3 | 0 | 1 | 0 |
| `search-relevance-category-filters-city-searches` | 13 | 5 | 5 | 0 | 0 | 0 |
| `export-pii-masking` | 5 | 6 | 3 | 1 | 2 | 0 |

All resolve. Topics are strongly linked **from** entities but rarely **to** other topics — only 1 of the 5 sampled topics has a topic-to-topic inbound. Topic clusters (search, WhatsApp, LMS) aren't cross-wired.

## 4. Counts

- **Total wiki pages:** 463 (the `index.md` header claims 461 — it undercounts by 2; likely excludes `index.md`, `log.md`).
- **Per-directory:** `entities/` 307 · `topics/` 96 · `systems/` 58 · `policies/` 0 · `timelines/` 0 · `conflicts/` 0 · plus `index.md`, `log.md`. Policies/timelines/conflicts are empty directories.
- **Stub / leaf pages (body < 100 words):** **295** (≈ 64%). Includes all 37 auto-created "Stub page auto-created because…" placeholders (20 in `entities/`, 17 in `systems/`), plus many short entity pages that have only frontmatter sources plus 1–2 sentences.
- **Orphans (zero incoming, literal):** 0 — `index.md` links to all 461 listed pages, so nothing is strictly orphaned.
- **Effective orphans (no incoming from any page other than `index.md`/`log.md`):** **96** — `entities/` 91, `systems/` 3 (`buyershelpteam`, `fcpdata`, `tech-security-team`), `topics/` 2 (`buyer-payment-protection-banner-reply-email`, `dspy-gepa-automated-speaker-labelling-pipeline`). The two orphan topics are both near-duplicates or obsolete versions (see §5).
- **Over-connected hubs:**
  - By **unique** outbound (>20 distinct targets): 1 non-index page — `entities/neeraj-agrawal.md` at 25.
  - By **total** outbound wikilink occurrences (>20), excluding `index.md` (461) and `log.md` (76): 12 pages. Top hubs: `entities/neeraj-agrawal.md` (54), `entities/mohak-saxena.md` (35), `topics/lms-save-feedback-api-migration-gke.md` (34), `topics/phase-2-code-revamp-additional-26-document-size-reduction.md` (32), `entities/amit-agarwal.md` (28).
- **Most-linked pages (authority / inbound):** `systems/marketplace-launch.md` (134), `systems/buyermy.md` (22), `entities/neeraj-agrawal.md` (22), `systems/whatsapp.md` (21), `systems/whatsapp9696.md` (20).
- **Dead wikilinks:** **0** across the whole corpus. Every `[[slug]]` resolves, because the compiler auto-creates stub pages for unknown references. That guarantees a zero-dead-link metric but launders structural gaps into "stub pollution."

### Category internal consistency

`page_type` frontmatter always matches directory (0 mismatches). But **directory placement of the *concept*** disagrees with the directory's purpose in many places:

- `systems/` contains 5 pages that are clearly *people* (stubs auto-created from misslugged entity references): `alok-kumar2.md` (dup of `entities/alok-kumar`), `bolisetty-shravan-kumar.md`, `deepak-yadav01.md` (dup of `entities/deepak-yadav`), `mohammad-kashif-khan.md`, `samarth.md` (dup of `entities/samarth`).
- `entities/` contains pages that are *teams or systems*: `m-site.md` (a product), `mobile-team.md`, `lens-indiamart.md` (a product), `pr-agent.md` (a tool). All four are auto-stubs.
- `topics/` contains 2 short names that look like product/tool nouns — `crashagent`, `trustpulse` — but those are reasonable once you read them (they describe initiatives). Not a real miscategorization.

## 5. Surprises & pitfalls

1. **Two pages for the same entity, same email address.** `entities/durga-muddala.md` and `entities/durga-suresh-muddala.md` both declare `durga.muddala@indiamart.com`. Both are live, link-reachable. Also: `entities/samarth.md` (real entity, has email, 1 source) vs `systems/samarth.md` (auto-stub). The two `samarth` pages share a filename stem — **this is the only true slug collision** in the corpus. A `[[samarth]]` wikilink is ambiguous and will resolve to whichever path the plugin's directory scan hits first.
2. **Near-duplicate entity slugs from inconsistent normalization.** 11 name pairs differ only by a numeric or `-clean` suffix, suggesting multiple compile passes merged imperfectly: `alok-kumar` / `alok-kumar2`, `deepak-jain` / `deepak-jain1`, `deepak-yadav` / `deepak-yadav01`, `himanshu-jain` / `himanshu-jain01`, `sahil-sharma` / `sahil-sharma2`, `sukanya-sharma` / `sukanya-sharma-clean`, `saurabh-gupta` / `saurabh-gupta-clean`, `sweta-negi` / `sweta-negi1`, `arjun-gaur` / `arjun-gaur-clean`, plus `samarth` (cross-dir), plus `buyers-helpteam` / `buyershelpteam` (same title "Buyers HelpTeam", same email `buyershelpteam@indiamart.com`, both in `systems/`).
3. **Two pages for the same topic, US vs UK spelling.** `topics/dspy-gepa-automated-speaker-labeling.md` and `topics/dspy-gepa-automated-speaker-labelling-pipeline.md` cover the same initiative. Both are live; the "labelling" one is an effective orphan (only `index.md` links in). Same cluster: the `-labeling` page cites `central-audio-transcription-api`; the `-labelling-pipeline` page cites `whatsapp`, `ai-intermesh-net`. Neither links to the other.
4. **`Lens.IndiaMART.md` uses non-kebab slug.** It's a stub under `systems/` with dot-case filename (`Lens.IndiaMART`) and title "Lens.Indiamart." Combined with a separate `entities/lens-indiamart.md` (stub) and no real page for the product, this concept is represented by two empty placeholders in two directories.
5. **`index.md` and `log.md` dominate link topology.** `index.md` has 461 outbound wikilinks; `log.md` has 76. These two pages alone keep the "dead link" and "orphan" counters at zero and make every page appear reachable. If you recompute centrality with them excluded, 96 pages drop to effective-orphan status — 20% of the corpus is not actually cross-referenced by written prose.
6. **Tight cycles around low-content pages.** `webpurify ↔ central-smart-orchestrator-api`, `cypress ↔ testlink ↔ msite-pdp-automation-ai-cypress`, `webpurify`-style "sole outbound back home" appears on every auto-stub (14 stubs have exactly one out-link and it's the page that triggered their creation). These give the graph local 2-cycles without real informational exits.
7. **`## Related` sections duplicated.** `topics/msite-pdp-automation-ai-cypress.md` has two consecutive `## Related` headings, suggesting a merge step is appending to already-related content. `entities/durga-suresh-muddala.md` lists `[[marketplace-launch]]` twice in the same block. Low-quality but authoritative-looking content.
8. **Empty categories.** `wiki/policies/`, `wiki/timelines/`, `wiki/conflicts/` directories exist but are empty. The IA planning vocabulary has been declared and not populated — plausible future-scaffold, but today it contributes noise in templates that link to those sections.

## Summary

The wiki is **very well-connected by index inflation but thinly cross-linked by content**. Zero dead wikilinks is a lower bound set by the auto-stub compiler, not evidence of health: 37 stubs with two sentences each, and 96 pages with only `index.md` as inbound, tell the real story. The two biggest coherence issues to fix are (a) the `entities/` vs `systems/` spillover caused by misslugged people becoming system stubs, and (b) the ~12 identity-duplicate slug pairs (same person / same topic spelled twice). Both are mechanical and dedup-scriptable.
