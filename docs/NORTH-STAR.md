# Email Knowledge Base — North Star

> **This doc describes what the wiki should look like to a reader.**
> The work to build it lives in [`docs/BACKLOG.md`](./BACKLOG.md).
> Active design decisions (while still being ratified) live in [`docs/proposal/NORTH-STAR-DRAFT.md`](./proposal/NORTH-STAR-DRAFT.md).

---

## What it is

**A compiled, curated Wikipedia for IndiaMART — not a directory of emails.**

Pages are about *things* (products, systems, initiatives, decisions), not *events* (threads, emails). The wiki grows as new emails arrive; existing topic pages gain new sections; pages read like encyclopedia entries with progressive disclosure (lead paragraph → body → references).

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

Lead paragraph (≥2 sentences before the first H2): what this is, current state.
The lead IS the summary — no separate `## TL;DR` section.

## Why it matters
Why this exists / who it affects.

## Current state
What's happening now. Sub-initiatives as ### H3 sections if needed.

## Recent changes
- 2026-04-12: scaled buyer-trust pilot 5% → 50% — see [[decision/...]]
- 2026-04-05: launched photo-similarity in 3 categories
Keep the last 3-5 entries inline; collapse older into `<details>` blocks under `Recent changes`.

## Open questions
- What's still unresolved.

## Related
- [[decision/deprecate-old-bidding]]
- [[system/lens]]

## References
<details><summary>References (23)</summary>
(auto-rendered by mkdocs_hooks.py from inline footnotes)
</details>
```

Compiler aims for this template but may deviate when content warrants. The `## References` H2 is reserved for the auto-rendered footnote block; do not write a `## Sources` H2 by hand.

### Universal H2 floor

Topic, system, and policy pages share a common H2 floor: `## Why it matters` → `## Current state` (or `## Current policy` for policies) → `## Recent changes` → `## Open questions` → `## Related` → `## References`. Type-specific sections (e.g. `## Effective date`, `## Dependencies`) are optional flavor. The compile agent owns H3 structure within each H2 and may add sections its content needs.

### Wikilinks and slug form

Wikilinks use the form `[[page-type/slug]]` (e.g. `[[topic/seller-isq]]`, `[[system/lens]]`, `[[decision/cap-notif-frequency]]`). Slugs are kebab-case identifiers. For people, the slug is the kebab-case form of the email address (e.g. `aa-indiamart-com` for `aa@indiamart.com`) — identity is the email, not the display name.

Each topic page links 3-8 adjacent concepts via `related:` frontmatter.

### ISO 8601 dates everywhere

Use ISO 8601 (`YYYY-MM-DD`) for dates in frontmatter, in `## Recent changes` bullets, and in body prose where the date is the focus. Full timestamps (`2026-04-15T07:00:00Z`) are reserved for machine-stamped fields like `last_compiled`.

---

## Terminal outcomes (every email gets one)

The compile agent reaches exactly one terminal outcome per email:

1. **Edit or create a content page** (topic / system / policy / decision) — the email contributed something synthesizable.
2. **`log_insight("trivial_skip")`** — pure ack, calendar invite, one-liner, <50 substantive words.
3. **`log_insight("already_captured")`** — the content is already on an existing page; nothing new to add.
4. **`log_insight("insufficient_decision")`** — decision-shaped language but lacks the meaningful-change bar (no scale change, deprecation, policy shift).

Two patterns extend an existing page rather than skip — both are flavors of outcome (1):

- **Question-delta** — an existing page raises a question the email answers → resolve the bullet in `## Open questions` and update `## Current state` or `## Recent changes`.
- **Answer-delta** — the email contradicts or refines an existing answer → update `## Current state` and append to `## Recent changes` with the date and source.

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

- **Phase 1 (now)** — the structure above. Readers land on `home.md`, drill into any of the 8 domain hubs, find concept-level topic pages, see status badges, read progressive disclosure (lead paragraph → body → references).
- **Phase 2** — wiki updates in near-real-time (live ingestion). Multiple mailing lists feed a single wiki. Domain meta-agent proposes hub evolution. Topics can have sub-pages when they outgrow single-file. Reports / impact sections appear on topics. Semantic search available.
- **Phase 3** — "Ask this wiki" interface returns cited answers. Manual edits are possible with conflict resolution. Inline citations for sensitive claims. Trust signals per page.

The work to ship each phase lives in [`docs/BACKLOG.md`](./BACKLOG.md).
