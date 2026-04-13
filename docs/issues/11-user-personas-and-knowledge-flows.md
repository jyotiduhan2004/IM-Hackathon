# User Personas and Knowledge Flows

This document translates the current wiki into actual user jobs:

- how someone learns the major products first
- how they drill into minor experiments
- how they understand who owns something now vs earlier
- how they understand how opinion changed over time, and why

It is based on a live audit of the current viewer and sample pages on April 13, 2026.

## What the live audit showed

### What is already working

- Some topic pages are already strong. `CrashAgent`, `Seller Specs Scaleup`, and
  `Personalised Buyer Dashboard - Msite` read like useful internal wiki pages.
- Source traceability is good. Readers can drill down from a compiled page to the
  underlying email.
- Full-text search works and can find a topic quickly if the user already knows
  the exact term.

### What is not working yet

- The home page is still a flat catalog, not a guided wiki.
- Entity pages are too ledger-like. They answer "what emails mentioned this
  person?" more than "what does this person own / influence?".
- System pages are inconsistent. Some are useful, some are extremely thin, and
  some are obvious stubs.
- Ownership is not consistently modeled as `current owner`, `previous owner`,
  `ownership changed on`, and `why ownership changed`.
- The wiki currently has no real `policies/`, `timelines/`, or `conflicts/`
  content, so changes in position are not surfaced as first-class knowledge.
- Readers can land on duplicates, weak slugs, and misclassified pages.

## The real user jobs

Most people are not trying to "browse markdown". They are trying to answer one of
these questions:

1. What are the major products / systems / programs the company runs?
2. What small experiments or recent launches are happening around them?
3. Who owns this now?
4. Who owned it earlier?
5. What changed in our view of this topic?
6. Why did that view change?

The wiki should be optimized for those jobs.

## Personas

### 1. Leadership / Strategy

This user wants breadth first, then depth.

Primary questions:

- What are the major product lines and strategic programs?
- Which areas are active right now?
- Which experiments are gaining momentum?
- Where is ownership clear, unclear, or changing?
- What has leadership opinion shifted on recently?

Needs:

- domain hubs
- major-product landing pages
- active bets / recent changes rollups
- current owner + previous owner
- rationale for changes, not just factual chronology

Failure mode today:

- They hit a giant flat index, then a mix of strong pages, stubs, and person-ledger pages.

### 2. Product Manager / Program Manager

This user wants current state plus adjacent context.

Primary questions:

- What is the current shape of this product or initiative?
- Which experiments sit under it?
- What launched, what is pending, and what got dropped?
- Who is implementing, sponsoring, or reviewing it?
- What assumptions changed after feedback, launch, or metrics?

Needs:

- product pages with nested experiments
- clear sections for current state, open questions, rollout, and ownership
- links to related experiments
- change log with reasons

Failure mode today:

- Topic pages can answer part of this, but discovery is manual and ownership history is weak.

### 3. Engineer / Operator

This user wants implementation context and provenance.

Primary questions:

- What is this system?
- Why was this decision made?
- What were the guardrails?
- What changed after failures or feedback?
- Which emails or threads justify the current implementation?

Needs:

- strong topic/system pages
- technical context
- source drilldown
- explicit "decision changes" and "why"

Failure mode today:

- Good on source access, inconsistent on historical reasoning and system quality.

### 4. New Joiner / Cross-Functional Reader

This user does not know the slugs or the mailing-list language.

Primary questions:

- What are the important things I should know in this area?
- How do the product, the system, and the experiments relate?
- Who are the main people involved?
- What terms or acronyms mean what?

Needs:

- domain hubs
- guided navigation
- plain-language summaries
- glossary / aliases
- "start here" entry points

Failure mode today:

- Search only works if the user already knows what to search for.

## Recommended browsing model

The right shape is breadth-first navigation with drilldown.

```mermaid
flowchart TD
    A["Home"] --> B["Domains"]
    B --> C["Major Product / Platform"]
    C --> D["Sub-area / Capability"]
    D --> E["Experiment / Launch / Decision"]
    C --> F["Ownership"]
    C --> G["Change History"]
    E --> H["Sources"]
```

### What "proper use" should look like

An actual user should not begin with raw search unless they already know the term.

The primary path should be:

1. Start from a domain hub such as `Marketplace`, `Buyer`, `Seller`, `AI Agents`,
   `Search`, `Trust`, `Infra`, or `Growth`.
2. Open a major product/platform page.
3. See:
   - what it is
   - why it matters
   - current owner
   - previous owner(s)
   - major active topics / launches / experiments
   - recent important changes
4. Drill into a topic or experiment page only when needed.
5. Drill into sources only when trust or detail requires it.

That is the difference between a wiki and a ledger.

## Major products vs minor experiments

This needs a deliberate hierarchy.

### Major product / platform pages

These should be durable and curated. They answer:

- what this thing is
- where it fits in the business
- what teams use or own it
- what the current strategic direction is
- what experiments or launches sit under it

Examples:

- `BuyerMY`
- `Marketplace Launch`
- `WhatsApp9696`
- `LEAP`
- `Photosearch`
- `CrashAgent` if it grows into a durable program rather than a one-off launch topic

### Minor experiment / launch pages

These should be narrower and time-bound. They answer:

- what changed
- who proposed it
- what was tested or launched
- what happened after launch
- what was learned
- whether it graduated, stalled, or got superseded

Examples:

- A/B test pages
- launch announcements
- rollout experiments
- prompt / agent / workflow POCs

### Relationship between them

Each experiment should roll up into a more durable parent area.

```mermaid
flowchart LR
    A["Major product / platform"] --> B["Capability"]
    B --> C["Launch"]
    B --> D["Experiment"]
    B --> E["Policy / Guardrail"]
    C --> F["Decision change"]
    D --> F
    E --> F
```

## Ownership now vs earlier

Ownership has to be explicit, not inferred from who appeared on email.

Every major product, system, and durable topic should expose:

- `Current owner`
- `Current team`
- `Prior owner(s)`
- `Ownership changed`
- `Why ownership changed`
- `Key stakeholders`

### Ownership model

```mermaid
flowchart TD
    A["Canonical page"] --> B["Current owner"]
    A --> C["Current team"]
    A --> D["Ownership history"]
    D --> E["Previous owner"]
    D --> F["Change date"]
    D --> G["Reason for change"]
    D --> H["Supporting sources"]
```

### Important rule

Do not use entity pages as the primary source of ownership truth.

Entity pages should support discovery:

- what this person is most associated with
- what they currently own
- what they previously owned

But the canonical truth about ownership belongs on the product/system/topic page.

## How opinion changes over time

This is the missing layer right now.

The current wiki preserves facts better than it preserves changing interpretation.
That is why it can feel "basic" even when the source coverage is strong.

Opinion change should be modeled as:

1. prior position
2. current position
3. what changed
4. why it changed
5. confidence in the new position
6. evidence supporting the change

### What counts as an opinion change

- "we should launch this" -> "we should hold rollout"
- "this architecture is enough" -> "this will not scale"
- "this metric looked good" -> "feedback shows this hurts users"
- "this team owns it" -> "ownership should move"
- "this rule is okay" -> "this creates risk and needs a blocker"

### Why opinion changes

These reasons should be normalized and captured explicitly:

- new metrics or launch outcomes
- customer or user feedback
- implementation complexity
- operational failures / incidents
- leadership direction
- resource or org changes
- policy / compliance / risk concerns
- dependency changes
- better alternative discovered

### Required page structure for opinion change

Every durable topic/system page should eventually support this section:

```markdown
## Current View

Short statement of the present position.

## How the View Changed

| Date | Previous view | Current view | Why it changed | Source |
| --- | --- | --- | --- | --- |
| 2026-04-10 | Scale this immediately | Keep single-repo for now | Multi-repo support not ready | raw/... |

## Decision Drivers

- Launch results
- User feedback
- Infra constraints

## Open Questions

- What evidence would change the recommendation again?
```

This is where the wiki becomes strategic rather than merely archival.

## What the current product is missing

The live audit suggests these missing capabilities are the main blockers:

### 1. Domain hubs

Without them, users cannot move from major areas to minor topics naturally.

### 2. Ownership as first-class knowledge

Today it is sometimes implied by a `Team` section, but not modeled consistently.

### 3. Change-of-view sections

There are no real timelines, conflict pages, or policy histories yet. That means
the wiki is weak at showing "how we got here".

### 4. Rollups for active experiments

A major product page should surface:

- active launches
- recent experiments
- superseded experiments
- open decisions

### 5. Better entity pages

Entity pages should summarize current role in the knowledge graph, not act as a
running email participation log.

## Recommended page templates

### Major product / platform page

```markdown
# BuyerMY

## Summary
## Why It Matters
## Current State
## Current Owner
## Ownership History
## Active Experiments
## Important Recent Changes
## Risks / Open Questions
## Related Topics
## Sources
```

### Experiment / launch page

```markdown
# Dynamic Smart RFQ Form

## Summary
## Parent Area
## Goal
## What Changed
## Rollout / Status
## Current Owner
## Why This Direction Was Chosen
## What Changed Since Initial Proposal
## Related Experiments
## Sources
```

### Entity page

```markdown
# Amit Agarwal

## Summary
## Current Areas of Involvement
## Current Ownership
## Previous Ownership
## Key Strategic Decisions Influenced
## Related Products / Systems
## Sources
```

## Acceptance scenarios

The wiki is materially better only if these scenarios work.

### Scenario A: Leadership maps the area

Goal:

- "Show me the major buyer-side products and the important active experiments under each."

Pass condition:

- This can be answered by browsing from Home -> Domain -> Product page without needing slug knowledge.

### Scenario B: PM understands ownership

Goal:

- "Who owns BuyerMY now, who owned it earlier, and what changed?"

Pass condition:

- One canonical page answers this directly, with dates and source-backed rationale.

### Scenario C: Engineer understands why the team changed direction

Goal:

- "Why did the team move from position A to position B?"

Pass condition:

- The page includes a structured change-of-view section and links to the source emails.

### Scenario D: New joiner learns the landscape

Goal:

- "I know nothing about this area. Help me understand the major things, then the experiments."

Pass condition:

- Domain hubs, glossary links, and curated rollups are sufficient without raw search.

## Immediate implementation implications

This analysis implies a few concrete changes:

1. The home page should become a domain map, not a full file index.
2. Major product/system pages need stronger templates than current system stubs.
3. Ownership must move into canonical topic/system pages.
4. Entity pages should become supporting summaries, not activity ledgers.
5. Timelines / conflicts / policy history need real population, not empty directories.
6. Opinion change should be captured as a first-class section with explicit reasons.

## Bottom line

If this wiki is meant to help humans reason about the company, then it has to
preserve three things at the same time:

- current state
- historical change
- rationale

Today it preserves evidence reasonably well and summary unevenly well. The next
step is to preserve interpretation: what changed in thinking, and why.
