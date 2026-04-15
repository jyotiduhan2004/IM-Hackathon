# Email Knowledge Base — North Star

> The single source of truth for what this project is building.
> Active design detail lives in [`docs/proposal/NORTH-STAR-DRAFT.md`](./proposal/NORTH-STAR-DRAFT.md) until the proposal is ratified.

---

## What it is

**A compiled, curated Wikipedia for IndiaMART — not a directory of emails.**

Pages are about things (products, systems, initiatives, decisions), not events (threads, emails). The wiki grows as new emails arrive; existing topic pages gain new sections; pages read like encyclopedia entries with progressive disclosure (TL;DR → body → sources).

Audience from day 1: **everyone at IndiaMART**. The 6-month-new-joiner test is the acceptance bar.

## What it is NOT

- Not a personal inbox archive
- Not a filing cabinet (one page per email)
- Not live (backlog mode; manual re-runs for now)
- Not editable by readers (compiler is the sole writer)
- Not searchable beyond MkDocs lunr (Phase 2)
- Not a chatbot interface (Phase 3)

## The structure

```
wiki/
├── home.md         # intro + 8 domain cards + recent activity + search + glossary
├── glossary.md     # auto-generated: IndiaMART acronyms + terms
├── domains/        # 8 compiler-generated hub pages
├── topics/         # concept pages (not per-thread)
├── systems/        # products + platforms
├── decisions/      # lazy-created, linked from topics
├── policies/       # rare
├── people/         # reference-only, hidden from primary nav
└── changes.md      # auto from compile log
```

Dropped: `timelines/`, `conflicts/` (0 pages after 2 weeks — no use case).

## The 8 domains

1. **Buyer Experience**
2. **Seller Experience**
3. **Marketplace & Discovery**
4. **Platform Reliability & Infrastructure**
5. **Trust, Safety & Compliance**
6. **AI Agents & Automation**
7. **Growth, Monetization & Partnerships**
8. **Engineering Productivity & Quality**

Topics are multi-tagged; domain pages are rollup summaries regenerated on every pipeline run.

## The pipeline (one unified command)

```
make pipeline
→ ingest → compile → dedupe → domain rollup → glossary refresh → status sweep
```

Each stage:
- **Ingest**: Gmail OAuth → raw markdown + Postgres row
- **Compile**: trivial-filter → topic identification (permissive) → decision extraction (lazy) → people pages (lazy, reference-only) → status detection → synthesis self-review pass
- **Dedupe**: separate agent merges near-duplicate topics
- **Domain rollup**: regenerates each of the 8 hub pages
- **Glossary refresh**: extracts acronyms; writes definitions
- **Status sweep**: updates `changes.md` + validates supersession wikilinks

## Phase ladder

- **Phase 1 (current)**: ship the architecture above
- **Phase 2**: live ingestion (Gmail watch), multi-list, domain meta-agent, multi-level topic hierarchy, reports/impact sections
- **Phase 3**: askable interface, manual edits with conflict resolution, inline citations, trust signals

## Detailed design

See [`docs/proposal/NORTH-STAR-DRAFT.md`](./proposal/NORTH-STAR-DRAFT.md) for:
- Page templates
- Decision flow / extraction rules
- All 21 design decisions with rationale
- PR-level work breakdown (PR-A through PR-E)
- Acceptance checklist

## What was deprecated in the 2026-04-15 consolidation

- 6 competing north-star statements → this single doc
- `timelines/` + `conflicts/` page types → dropped
- `navigation_role` frontmatter → not shipping
- "Ownership History" required section → Phase 2 or cut
- "How the View Changed" required section → Phase 2 or cut
- Entity page domination → entities demoted to reference-only
- Hand-written domain hubs → compiler-generated rollups
- Strict template validation → loose guidance

See [`docs/archive/README.md`](./archive/README.md) for the historical record.
