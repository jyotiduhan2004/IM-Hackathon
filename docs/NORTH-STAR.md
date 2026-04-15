# Email Knowledge Base — North Star

> **This doc describes what the wiki should look like to a reader.**
> The work to build it lives in [`docs/BACKLOG.md`](./BACKLOG.md).
> Active design decisions (while still being ratified) live in [`docs/proposal/NORTH-STAR-DRAFT.md`](./proposal/NORTH-STAR-DRAFT.md).

---

## What it is

**A compiled, curated Wikipedia for IndiaMART — not a directory of emails.**

Pages are about *things* (products, systems, initiatives, decisions), not *events* (threads, emails). The wiki grows as new emails arrive; existing topic pages gain new sections; pages read like encyclopedia entries with progressive disclosure (TL;DR → body → sources).

Audience from day 1: **everyone at IndiaMART**. The 6-month-new-joiner test is the acceptance bar — a joiner 6 months from now, reading the wiki for an hour, should map the IndiaMART ecosystem (products, systems, ownership, recent decisions) without prior context.

## What it is NOT

- Not a personal inbox archive
- Not a filing cabinet (one page per email)
- Not editable by readers (compiler is the sole writer)
- Not a list of people-pages (people exist only as wikilink targets; never in primary nav)
- Not categorized into aspirational buckets that aren't used (no `timelines/`, no `conflicts/`, no `navigation_role`, no required "Ownership History" section)

---

## Structure

```
wiki/
├── home.md             # intro + 8 domain cards + recent activity + search + glossary
├── glossary.md         # auto-generated: IndiaMART acronyms + terms
├── domains/            # 8 compiler-generated hub pages
│   └── <domain>.md     # rollup of everything tagged with this domain
├── topics/             # concept pages (not per-thread) — the bulk of the corpus
├── systems/            # products + platforms (Lens, ISQ, BuyLeads, ...)
├── decisions/          # lazy-created, linked from topics
├── policies/           # org-wide rules (rare)
├── people/             # reference-only, hidden from primary nav
└── changes.md          # auto: last 30 days of compile activity
```

## The 8 domain hubs

1. **Buyer Experience** — BuyerMY, BuyLeads, mobile app, search UX, Lens, WhatsApp buyer
2. **Seller Experience** — AuditMate, Seller IM, seller dashboards, specs, compliance tools
3. **Marketplace & Discovery** — MCAT, ISQ, PhotoSearch, search ranking, categorization, recommendations
4. **Platform Reliability & Infrastructure** — GKE, Mesh PG, DB ops, API frameworks, performance
5. **Trust, Safety & Compliance** — KYC/GST, fraud prevention, moderation, payment protection, TrustSeal
6. **AI Agents & Automation** — CrashAgent, WhatsApp 9696, autonomous assistants
7. **Growth, Monetization & Partnerships** — Export, Ads, affiliates, Google Merchant Center, tenders
8. **Engineering Productivity & Quality** — CI/CD, code quality, testing, dev tools

Domain pages are **compiler-generated rollups** from all topics tagged with that domain. A topic can be tagged into multiple domains (multi-tag). Domains evolve over time — a meta-agent proposes splits / merges / adds based on corpus growth (Phase 2).

---

## Pages are concept pages, not per-thread

A topic is a thing that exists in the world (e.g. "Seller ISQ"). Multiple emails about it all feed into **that one page**. New emails grow the page; they don't spawn new ones.

Sub-initiatives within a topic live as **H2/H3 sections in the parent page** (single file, scrollable) in Phase 1. Phase 2 gains multi-level hierarchy (parent + sub-pages) when topics grow too big.

## Default page template (loose guidance, validator warns, doesn't block)

```markdown
---
title: Seller ISQ
slug: seller-isq
page_type: topic
status: active               # active | superseded | archived
tags: [seller, marketplace]
domain: seller               # primary domain for rollup
updated_at: 2026-04-15T...
superseded_by: null
---

## TL;DR
One paragraph. What is this, current state in 2-3 sentences.

## Background
Synthesis across emails.

## Current state
What's happening now. Sub-initiatives as ## H3 sections if needed.

## Recent changes
- 2026-04-12: scaled buyer-trust pilot 5% → 50% — see [[decision/...]]
- 2026-04-05: launched photo-similarity in 3 categories
(last 3-5 entries; older collapsed)

## Decisions
- [[decision/deprecate-old-bidding]] (2026-03-20)

## Sources
<details><summary>Sources (23)</summary>
(collapsed, rendered by mkdocs_hooks.py)
</details>
```

Compiler aims for this template but may deviate when content warrants. Validator warns on missing required sections (TL;DR, Current state, Sources); does not block.

---

## Status + supersession (first-class)

Every page has `status: active | superseded | archived`. Viewer renders a colored badge at the top.

Supersession rules:
- Compiler sets `status: superseded` + `superseded_by: <new-slug>` when it detects deprecation language ("we're replacing X with Y").
- Changed numbers/dates/rules → update `Current state` AND add to `Recent changes`.
- Never silently delete — preserve lineage.

---

## Home page

```
┌─────────────────────────────────────────────────────────┐
│  Email Knowledge Base — IndiaMART                       │
│  A compiled wikipedia derived from                      │
│  marketplacelaunch@indiamart.com                        │
│                                                         │
│  [ search… ]           [ Glossary ↗ ]                   │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Buyer    │ │ Seller   │ │ Market-  │ │ Platform │   │
│  │ Exp.     │ │ Exp.     │ │ place    │ │ Reliab.  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Trust &  │ │ AI &     │ │ Growth & │ │ Eng.     │   │
│  │ Safety   │ │ Automat. │ │ Monetiz. │ │ Productv.│   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│                                                         │
│  Recent changes                                         │
│  · 2026-04-15: scaled buyer-trust pilot to 50%         │
│  · 2026-04-14: deprecated legacy bidding API           │
│  · 2026-04-12: launched photo-similarity               │
│  ...                                                    │
└─────────────────────────────────────────────────────────┘
```

## What we explicitly don't do

- **No manual edits** to wiki files. Compiler is the only writer. Quality improvements go through prompts + tooling, not markdown edits.
- **No `timelines/` or `conflicts/`** page types (zero pages after 2 weeks — no real use case).
- **No `navigation_role` frontmatter**. Domain hubs are real pages, not marked topics.
- **No required "Ownership History" section**. Phase 2 or cut.
- **No required "How the View Changed" section**. Phase 2 or cut.
- **No per-email entity page proactive creation**. Entities are lazy / wikilink-only / hidden from primary nav.
- **No hand-written domain hubs**. They're compiler rollups.
- **No strict template validation**. Loose guidance + warnings only.

---

## 6-month-new-joiner test (acceptance bar)

Pick 3 random domains. Have a simulated new joiner (blind audit — someone who hasn't seen the wiki) answer 5 realistic questions per domain within 5 min each. Pass rate ≥ 4/5 per domain.

The wiki passes this test when it's doing its job.

---

## Phase ladder (end-state vision)

- **Phase 1 (now)** — the structure above. Readers land on `home.md`, drill into any of the 8 domain hubs, find concept-level topic pages, see status badges, read progressive disclosure (TL;DR → body → sources).
- **Phase 2** — wiki updates in near-real-time (live ingestion). Multiple mailing lists feed a single wiki. Domain meta-agent proposes hub evolution. Topics can have sub-pages when they outgrow single-file. Reports / impact sections appear on topics. Semantic search available.
- **Phase 3** — "Ask this wiki" interface returns cited answers. Manual edits are possible with conflict resolution. Inline citations for sensitive claims. Trust signals per page.

The work to ship each phase lives in [`docs/BACKLOG.md`](./BACKLOG.md).
