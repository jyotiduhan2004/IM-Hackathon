# Email Knowledge Base — Final-State Proposal

> **Status**: active design doc. Captures decisions from the 2026-04-15 Q&A pass.
> **Audience**: project owner + Codex (next reviewer).
> **Checkpoint**: v2 — replaces the Codex-minimalist-vs-richer tradeoff with a committed direction based on user decisions.

---

## Vision

**A compiled, curated Wikipedia for IndiaMART — not a directory of emails.**

Pages are about **things** (products, systems, initiatives, decisions), not **events** (threads, emails). The wiki grows as new emails arrive; existing topic pages gain new sections/updates; pages read like encyclopedia entries with progressive disclosure (TL;DR → body → sources).

Target audience from day one: **everyone at IndiaMART**. The 6-month-new-joiner test is the real acceptance bar — a joiner 6 months from now, reading the wiki for an hour, should map the IndiaMART ecosystem (products, systems, ownership, recent decisions) without prior context.

---

## 1. Structure

```
wiki/
├── home.md             # Intro + 8 domain hubs + recent activity + search + glossary
├── glossary.md         # Auto-generated: IndiaMART acronyms + key terms
├── domains/            # 8 hubs, compiler-generated rollups
│   ├── buyer-experience.md
│   ├── seller-experience.md
│   ├── marketplace-and-discovery.md
│   ├── platform-reliability-and-infrastructure.md
│   ├── trust-safety-and-compliance.md
│   ├── ai-agents-and-automation.md
│   ├── growth-monetization-and-partnerships.md
│   └── engineering-productivity-and-quality.md
├── topics/             # Concept pages (not per-thread) — the bulk of the corpus
├── systems/            # Products + platforms (Lens, ISQ, BuyLeads, ...)
├── decisions/          # Lazy-created, only when a topic links to one
├── policies/           # Org-wide rules (rare)
├── people/             # Reference-only, hidden from primary nav
└── changes.md          # Auto: last 30 days of compile activity
```

Page types we **drop**: `timelines/`, `conflicts/` (both have zero pages despite being in the taxonomy for 2 weeks — no real use case).

---

## 2. Page model

### Topics are **concept pages**, not per-thread

A topic is a thing that exists in the world (e.g. "Seller ISQ"). Multiple emails about it all feed into that one page. New emails grow the page; don't spawn new ones.

Sub-initiatives within a topic are **H2 sections in the parent** for Phase 1 (single file, scrollable). Phase 2 gains multi-level hierarchy (parent page + sub-pages) when topics grow too big.

### Multi-tag

Topics carry `tags: [domain, subdomain, ...]`. A topic can belong to multiple domains (the "25% of topics span 2-3 domains" case from the corpus audit). Tags are permissive — the compiler adds freely; a dedupe agent normalizes later.

### Default template (loose guidance, no hard validation)

```markdown
---
title: Seller ISQ
slug: seller-isq
page_type: topic
status: active                  # active | superseded | archived
tags: [seller, marketplace]
domain: seller                   # primary domain for rollup
updated_at: 2026-04-15T...
---

## TL;DR
One paragraph. What is this, current state in 2-3 sentences.

## Background
What exists, how it got here. Synthesis across emails.

## Current state
What's happening now. Sub-initiatives as ## H3 sections if needed.

## Recent changes
- 2026-04-12: scaled buyer-trust pilot 5% → 50% — see [[decision/...]]
- 2026-04-05: launched photo-similarity in 3 categories
(last 3-5 entries; older overflow collapsed)

## Decisions
- [[decision/deprecate-old-bidding]] (2026-03-20)
- [[decision/cap-buyer-notif-frequency]] (2026-02-14)

## Sources
<details><summary>Sources (23)</summary>
(collapsed, mkdocs_hooks-rendered)
</details>
```

Compiler aims for this template but can deviate when it makes sense. Validator warns on missing sections, doesn't block.

### Status badges (first-class)

Every page has `status: active | superseded | archived`. Viewer renders a colored badge at the top. Compiler sets `superseded` when it detects deprecation language in emails (e.g. "we're replacing X with Y" → sets X's status and a wikilink to Y's page).

---

## 3. Compile pipeline (6 stages, one unified command)

```
make pipeline    →    ingest → compile → dedupe → domain rollup → glossary refresh → status update
```

Each stage:

### Stage 1 — Ingest (exists, no change)
Gmail OAuth → `raw/*.md` + `messages` row per email.

### Stage 2 — Compile (significant changes)
For each batch of claimed emails:
1. **Trivial-message filter** (NEW): cheap pre-pass. Messages <50 substantive words, pure acks, calendar invites, meeting reschedules → `compile_state=trivial`, skipped. Addresses ~30-50% of mailing-list traffic.
2. **Topic identification** (permissive): agent creates or grows topics liberally. Uses `resolve_page` + `find_new_sources` to check existing pages first, but may create new ones when uncertain.
3. **Decision extraction** (lazy + active): while updating a topic page, the agent scans the email for decision-shaped content (e.g. "scaling from 5% to 50%", "deprecating X", "approved Y for rollout"). When it finds one, it creates a `decisions/...` page and wikilinks from the topic. Bar: meaningful change, not trivial ack.
4. **People pages** (lazy, reference-only): only create when a topic page wikilinks to a person. Hidden from primary nav.
5. **Status update** (NEW): when the agent sees "we replaced X with Y", it marks X `status: superseded` and adds a `superseded_by: y-slug` wikilink.

### Stage 3 — Dedupe (NEW)
Separate agent. Scans all topics. Proposes merges for near-duplicates (cosine similarity + slug-match + tag-overlap heuristics). Merges via `wiki_merge_pages` tool (new). Normalizes tags. Runs after each compile batch so the corpus stays clean.

### Stage 4 — Domain rollup (NEW)
Separate agent. Regenerates each of the 8 domain pages from all topics tagged with that domain. Output per domain hub:
- Definition + scope (1 paragraph)
- "Most active topics" (top N by recent updates)
- "Key systems" (linked systems in this domain)
- "Recent decisions" (last 10 decision wikilinks)
- Navigable index (all topics in the domain)

Regenerates on every `make pipeline` run so domains stay fresh.

### Stage 5 — Glossary refresh (NEW)
Separate agent. Scans the corpus for acronyms + jargon. Writes 1-2 sentence definitions per term, each linked to the topic/system where it's most discussed. Regenerates `wiki/glossary.md` on every run.

### Stage 6 — Status sweep (NEW)
Scans pages for supersession language the main compile missed. Validates `superseded_by` wikilinks exist. Updates `changes.md` with the last 30 days of meaningful updates.

### Periodic — Domain meta-agent (Phase 2)
Runs weekly or per-N-emails. Reviews the corpus and proposes:
- Add a new domain (if a cluster of topics doesn't fit the 8)
- Split a domain (if one is growing disproportionately)
- Merge two domains (if they have near-identical topic sets)
Outputs a suggestion doc for the owner to approve; no auto-changes.

---

## 4. What stays read-only

No manual edits to `wiki/*.md`. The compiler is the only writer. To improve quality, we change prompts or tooling, not markdown files. This keeps the architecture simple (no edit-vs-regen conflict detection).

**Glossary is a documented exception** — it's auto-generated by the compiler, so no human maintenance.

---

## 5. Phase ladder

### Phase 1 — Ship the new architecture (current)
**Bar**: company-wide readers can use the wiki without the owner present. 6-month-new-joiner test passes on a sampled set of domains.

Work items (mapped to PRs below):
- **PR-A**: Doc consolidation — rewrite strategy docs to match direction
- **PR-B**: Structural compiler changes
  - Trivial-message filter
  - Topic-as-concept prompt rule (check existing pages before creating)
  - Decision extraction during topic compile (lazy + active)
  - People pages: lazy, demoted from primary nav
  - Status field + supersession detection
  - Drop `timelines/` + `conflicts/` from schema/validator/prompts
- **PR-C**: Post-compile agents
  - Dedupe agent + `wiki_merge_pages` tool
  - Domain rollup agent (8 hubs)
  - Glossary auto-generation agent
  - Status sweep + changes.md generator
- **PR-D**: Viewer + landing pages
  - Real `home.md` (intro + domain cards + activity + search + glossary)
  - Real `topics/index.md`, `systems/index.md`
  - Status badge rendering in the Material theme
  - Glossary card + search bar on home
- **PR-E**: Unified pipeline command (`make pipeline`) + orchestration

### Phase 2 — Live + multi-user ergonomics (3-6 months out)
- Live ingestion (Gmail watch + Pub/Sub)
- Multiple mailing lists (share catalog, one cursor per list)
- Domain meta-agent (propose hub evolution)
- Multi-level topic hierarchy (parent + sub-pages)
- Reports / impact sections on topics (quantitative follow-up)
- Search upgrade (MkDocs lunr → semantic if needed)

### Phase 3 — Askable + writable (6-12 months out)
- "Ask this wiki" interface (Postgres + LLM, cited)
- Manual edit workflow (markdown PRs; edit-vs-regen conflict resolution)
- Inline citations for sensitive claims
- Trust signals (page freshness, source reliability)

---

## 6. Phase 1 acceptance checklist

- [ ] `home.md` is a real landing page with 8 domain cards + recent activity + search + glossary
- [ ] All 8 domain hub pages exist and update on every `make pipeline`
- [ ] `glossary.md` exists with 30+ terms, auto-updated
- [ ] `status:` field on every topic/system/decision with viewer badge
- [ ] Entity pages are not in primary nav; exist only as wikilink targets
- [ ] `timelines/` and `conflicts/` schema + folders removed
- [ ] Trivial messages are skipped (verified against a sample batch)
- [ ] Dedupe agent merges a known pair of duplicate topics end-to-end
- [ ] 6-month-new-joiner test: pick 3 random domains, ask a simulated new joiner (blind audit) if they can answer 5 questions in the domain within 5 min each. Pass rate ≥ 4/5 per domain.

---

## 7. What we explicitly drop / archive

- `docs/issues/09-internal-wiki-structure.md` — folded into this doc
- `docs/issues/11-user-personas-and-knowledge-flows.md` — the personas are useful research; the `navigation_role` / "Ownership History" / "How the View Changed" concepts are NOT shipping (their implementation cost isn't worth it before Phase 2). Archive as user research.
- `docs/reviews/plan-24h-*.md`, `overnight-plan-*.md`, the 5 persona audits (keep the synthesis) — all archive.
- `BACKLOG.md` — trim from 2,359 to ≤300 lines; archive the rest.
- `02-data-model.md` — rewrite as a 1-page reference matching the new 4-type taxonomy.
- `03-email-ingestor.md`, `04-wiki-compiler.md` — rewrite as Phase-0 completion records; drop stale tool lists.
- `CLAUDE.md` — update current toolbelt; remove the NEVER list; replace with positive description.

---

## 8. Open questions still being asked

The Q&A pass continues. Remaining likely topics: source rendering verbosity, compile-output quality checks (PR #73 `check_my_work` direction), people page lifecycle (archive cadence), tag constraints (vocabulary or free).

---

## Appendix A — Decisions so far (Q&A log)

1. Audience: company-wide, day 1 ✓
2. Live ingestion: defer; manual re-runs fine ✓
3. Mailing lists: single (`marketplacelaunch@indiamart.com`) for now ✓
4. 6-month-new-joiner test: real acceptance bar ✓
5. Codex collaboration: out of this proposal's scope ✓
6. Entity pages: hidden from primary nav, reference-only ✓
7. Page types: drop `timeline` + `conflict`; 4 visible (topic, system, policy, glossary) + `decisions/` + `people/` reference ✓
8. Structure: domain-first + decisions + tags + reference people ✓
9. Edit model: read-only / compiler-only ✓
10. Domain hubs: 8 validated against corpus (see §1); compiler-generated rollups with multi-tag ✓
11. Topics are concept pages, not per-thread ✓
12. Sub-initiatives: sections in parent (Phase 1); multi-level (Phase 2) ✓
13. Decisions trigger: lazy (only when topic links to one), but compiler actively scans emails for decisions when updating topics ✓
14. Topic dedupe: permissive compile + separate dedupe agent ✓
15. Glossary: auto-generated by compiler ✓
16. Page template: loose guidance, no hard validation ✓
17. Home page: intro + 8 domain cards + recent activity + search + glossary card ✓
18. Status field: active/superseded/archived with viewer badge ✓
19. Orchestration: one unified `make pipeline` command ✓
20. Trivial-message filter: yes ✓

Next questions: source rendering, compile-output critique (PR #73), people lifecycle.
