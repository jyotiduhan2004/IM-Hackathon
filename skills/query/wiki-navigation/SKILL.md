---
name: wiki-navigation
description: "How to navigate IndiaMART's wiki structure. Load when exploring the wiki, following links, or understanding page organization."
---

## Wiki Structure

### Categories (5 content types)
- `topics/` — 434 pages. Concept pages about features, initiatives, launches. THE BULK of the wiki.
- `systems/` — 110 pages. Products and platforms (e.g., PhotoSearch, GLAdmin, WhatsApp 9696).
- `people/` — 628 pages. Person pages with email, "Appears in" section. Reference only.
- `policies/` — Mostly empty (1 index page). Policies are lazy-created.
- `decisions/` — Mostly empty (1 index page). Decisions are lazy-created.

### Domain Hubs (8 domains)
Hub pages at `domains/{name}.md` list ALL pages in that domain. **Read these first** for domain-scoped questions.

| Domain | Pages | Focus |
|--------|-------|-------|
| buyer-experience | 83 | BuyLeads, buyer chat, search UX |
| seller-experience | 83 | Seller tools, compliance, dashboard |
| marketplace-discovery | 83 | MCAT, ISQ, photo search, ranking |
| platform-reliability | 43 | GKE, DB ops, API framework |
| trust-safety | 27 | KYC, GST, fraud, moderation |
| ai-automation | 16 | CrashAgent, chatbots, AI tools |
| growth-monetization | 43 | Ads, exports, affiliates, tenders |
| engineering-productivity | 26 | CI/CD, code quality, testing |
| (uncategorized) | 139 | No domain assigned |

### Special Pages
- `index.md` — Home page with domain overview and top pages per domain
- `changes.md` — Activity feed of recently updated pages
- `glossary.md` — 110+ acronyms (auto-generated, may be inaccurate)
- `log.md` — Compile audit trail

### Page Format
Every page has YAML frontmatter:
```yaml
title: "Page Title"
page_type: topic|system|person|domain
status: active|superseded|archived
domain: buyer-experience
owner: '[[person-slug]]'
source_threads: ["thread-id"]
related: ["[[topic/other-page]]"]
```

Body has: lead paragraph, then ## sections (Why it matters, Current state, Recent changes, etc.)

### Wikilink Format
- `[[topic/seller-isq]]` — link to topic page
- `[[systems/photosearch]]` — link to system page
- `[[people/amit-agarwal-indiamart-com]]` — link to person page
- `[[domains/ai-automation]]` — link to domain hub

### Coverage
This wiki covers **January to mid-February 2026** IndiaMART launch emails. Topics outside this window may not exist.
