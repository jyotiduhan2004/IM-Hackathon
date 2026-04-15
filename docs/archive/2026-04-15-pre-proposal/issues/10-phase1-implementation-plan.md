# Issue: Phase 1 Implementation Plan — Real Internal Wiki

**Labels**: `documentation`, `architecture`, `phase-1`, `implementation-plan`

---

## Executive summary

The current system already ingests email, compiles wiki pages, and serves them
through MkDocs. What it does **not** yet do reliably is produce a polished
internal wiki.

The core problem is structural:

- the wiki is too flat
- navigation is too file-driven
- entity pages are too prominent
- references are too loud
- the compiler still creates reader-visible clutter to recover from ambiguity

This plan turns the current pipeline into a topic-first, reference-backed,
hierarchical internal wiki that developers can implement incrementally.

The recommended approach is:

1. Clarify page taxonomy and reader hierarchy.
2. Move provenance and lookup logic out of the LLM where possible.
3. Make the viewer render a curated hierarchy instead of a filesystem dump.
4. Reduce noisy page creation and visible stub pollution.
5. Add verification gates so the wiki cannot silently regress.

---

## What success looks like

A new reader should be able to:

- land on the home page
- browse by subject area without knowing filenames
- find the canonical page for a product, initiative, or policy
- see what is current, what changed, and what is uncertain
- inspect source evidence without the page becoming an email dump

Developers and operators should be able to:

- reason about where a page belongs
- predict when a new page is created versus an existing page updated
- run automated checks that catch taxonomy drift, duplicates, corruption, and evidence gaps
- roll changes out in small PRs with clear acceptance criteria

---

## Non-goals for Phase 1

Do not treat these as blockers for this phase:

- semantic search
- chat / QA over the wiki
- additional mailing lists beyond the current corpus and same-list year-backfill
- deep folder hierarchies in the repo
- perfect historical cleanup of every legacy page before shipping improvements

Phase 1 is about making the wiki **good enough to deserve automation**.

---

## Core design decisions

### 1. Keep storage shallow, make reading hierarchical

The repository can stay shallow:

- `wiki/topics/`
- `wiki/systems/`
- `wiki/entities/`
- `wiki/policies/`
- `wiki/timelines/`
- `wiki/conflicts/`

But the viewer should become more hierarchical:

- top-level section pages
- cluster / domain landing pages
- canonical content pages beneath those landing pages

### 2. Keep `system` internally, label it better for readers

The internal page type can remain `system`, but the UI should present it as:

- `Products & Platforms`

That is clearer to non-technical readers than `Systems`.

### 3. Treat `topics` as the main product

`Topics` are where changing knowledge lives:

- rollouts
- incidents
- decisions
- migrations
- experiments

`Systems` are durable nouns:

- products
- platforms
- tools
- services
- mailing lists

### 4. Preserve evidence, but layer it

The page should lead with knowledge. Evidence should sit behind metadata,
compact references, and expandable raw details.

### 5. Push deterministic work into tools and coordinators

The compiler agent should synthesize knowledge, not invent identity, canonical
slugs, page merges, or navigation structure ad hoc.

### 6. Use hierarchy for browsing, tags for slicing

This should not be a choice between hierarchy and tags. A large company wiki
needs both.

- **Hierarchy** is for human navigation.
- **Tags / facets** are for filtering, rollups, and alternate cuts.

Hierarchy should answer:

- where do I start
- what cluster does this belong to
- what is the canonical page

Tags should answer:

- which teams touched this
- which channel or source family this came from
- which business area this belongs to
- which lifecycle state it is in

Recommended rule:

- do **not** use tags as the primary information architecture
- do use tags to generate views, rollups, and filters on top of the hierarchy

---

## Target information architecture

### Reader hierarchy

```mermaid
flowchart TD
    A["Home"] --> B["Topics"]
    A --> C["Products & Platforms"]
    A --> D["Policies"]
    A --> E["People"]
    A --> F["Changes"]

    B --> B1["Domain / Cluster landing pages"]
    B1 --> B2["Canonical topic pages"]

    C --> C1["Product area landing pages"]
    C1 --> C2["Canonical system pages"]

    D --> D1["Current policies"]
    D1 --> D2["Policy history / supersession"]

    E --> E1["Major contributors / owners"]
    E1 --> E2["Supporting people pages"]
```

### Hierarchy plus tags

```mermaid
flowchart LR
    A["Canonical page"] --> B["Lives in one primary hierarchy path"]
    A --> C["Has tags / facets"]
    C --> C1["Team"]
    C --> C2["Business area"]
    C --> C3["Channel / source family"]
    C --> C4["Status"]
    C --> C5["Time period"]
```

### Taxonomy decision tree

```mermaid
flowchart TD
    A["New information from email"] --> B{"Is this a human person?"}
    B -- Yes --> C["Entity page"]
    B -- No --> D{"Is this a durable product / tool / platform / service / mailing list?"}
    D -- Yes --> E["System page ('Products & Platforms' in UI)"]
    D -- No --> F{"Is this a rule or operating guideline?"}
    F -- Yes --> G["Policy page"]
    F -- No --> H{"Is chronology itself the main value?"}
    H -- Yes --> I["Timeline page"]
    H -- No --> J{"Are there unresolved competing claims?"}
    J -- Yes --> K["Conflict page"]
    J -- No --> L["Topic page"]
```

### Compile and render flow

```mermaid
flowchart LR
    A["Gmail / raw emails"] --> B["Ingest"]
    B --> C["Postgres catalog + queue"]
    C --> D["Compiler agent"]
    D --> E["Wiki markdown pages"]
    C --> F["Deterministic metadata / touched-page data"]
    E --> G["Viewer render layer"]
    F --> G
    G --> H["Reader-facing wiki"]
```

### Verification loop

```mermaid
flowchart TD
    A["Compile run"] --> B["Hard validation"]
    B --> C["Wiki quality metrics"]
    C --> D["Audit samples / manual QA"]
    D --> E{"Passes release gates?"}
    E -- Yes --> F["Ship / publish"]
    E -- No --> G["Repair / recompile / merge duplicates"]
    G --> A
```

---

## Page definitions

### Topic

A topic answers:

- what is happening
- what changed
- what decisions were made
- what remains open

Examples:

- city-based filters on Lens results page
- complaint agent v2 on WhatsApp 9696
- buyer payment protection banner rollout

### System

A system answers:

- what this thing is
- what role it plays
- which topics are happening around it

Examples:

- Lens
- WhatsApp 9696
- TrustPulse, if treated as a durable product

### Topic vs system rule

- If the page is mostly about **status and change**, it is a `topic`.
- If the page is mostly about **the thing itself**, it is a `system`.

When both are needed:

- one canonical `system` page for the durable thing
- multiple `topic` pages for rollouts, migrations, incidents, or initiatives involving that thing

### Policy

A policy is a rule, approval flow, guideline, or procedure with a current state.

### Entity

An entity is a human person only. The page should summarize meaningful
involvement, not act as an exhaustive CC ledger.

### Timeline

Use only when chronology is the point, not by default.

### Conflict

Use when the system cannot honestly collapse competing claims into one current truth.

---

## Required page templates

### Topic page

Required sections:

1. `Summary`
2. `Current state`
3. `Why it matters`
4. `Key decisions`
5. `Recent changes`
6. `Open questions`
7. `Related pages`
8. `References`

### System page

Required sections:

1. `Summary`
2. `Role / purpose`
3. `Active related topics`
4. `Dependencies / related systems`
5. `Known issues or changes`
6. `Related pages`
7. `References`

### Policy page

Required sections:

1. `Current policy`
2. `Who it affects`
3. `Effective date`
4. `Supersedes / superseded by`
5. `History`
6. `References`

### Entity page

Required sections:

1. `Who this is`
2. `Areas of involvement`
3. `Major related topics`
4. `Major related systems`
5. `Recent material contributions`
6. `References`

### Timeline page

Required sections:

1. `Scope`
2. `Chronology`
3. `Current status`
4. `Related pages`
5. `References`

### Conflict page

Required sections:

1. `Question in dispute`
2. `Position A`
3. `Position B`
4. `Evidence`
5. `Resolution path`
6. `Affected pages`
7. `References`

---

## Workstreams

## Workstream 1 — Taxonomy and templates

### Goal

Make page-type decisions predictable and make all important page types render
consistently.

### Current issues

- `topic` vs `system` is unclear
- people pages and systems are sometimes miscategorized
- policies / timelines / conflicts exist conceptually but are underused
- pages vary too much in structure

### Changes

- codify topic vs system rules in prompts and docs
- add required section templates per page type
- add validator checks for section presence on key page types
- add UI labels that are clearer than raw internal names

### Code areas

- [src/compile/prompts.py](../../src/compile/prompts.py)
- [scripts/validate_wiki.py](../../scripts/validate_wiki.py)
- [mkdocs.yml](../../mkdocs.yml)
- docs

### Deliverables

- stable page-type definitions
- section templates documented and enforced
- UI label change from `Systems` to `Products & Platforms`

### Acceptance criteria

- no new person pages created under `systems/`
- no new obvious products created under `entities/`
- topic and system pages render with predictable section order

---

## Workstream 2 — Viewer hierarchy and navigation

### Goal

Replace sidebar dumps and file-driven navigation with reader-facing structure.

### Current issues

- MkDocs auto-discovers the entire `wiki/` tree
- `index.md` acts as a giant catalog
- stub and duplicate pages leak into visible navigation
- there are no domain landing pages or rollups

### Changes

- define explicit top-level nav in `mkdocs.yml`
- create section landing pages
- create cluster / rollup pages for major domains
- hide stub pages and alias pages from main navigation
- surface canonical pages only
- support tag/facet-driven rollups without replacing the main hierarchy

### Proposed top-level navigation

- `Home`
- `Topics`
- `Products & Platforms`
- `Policies`
- `People`
- `Changes`
- `About`

### Proposed landing pages

- `wiki/home.md`
- `wiki/topics/index.md`
- `wiki/systems/index.md`
- `wiki/policies/index.md`
- `wiki/entities/index.md`
- `wiki/changes/index.md`

### Code areas

- [mkdocs.yml](../../mkdocs.yml)
- [mkdocs_hooks.py](../../mkdocs_hooks.py)
- generated wiki landing pages

### Acceptance criteria

- a reader can navigate to major subject areas without scanning raw filenames
- stub pages do not appear in primary navigation
- topic and system pages are easier to reach than people pages
- tag/facet views exist as secondary entry points, not as the only way to browse

---

## Workstream 3 — Provenance and references

### Goal

Keep evidence strong while reducing page clutter.

### Current issues

- full raw-email blocks are appended to every page
- large source lists dominate many pages
- frontmatter and sources are pulling the reader away from the synthesized content

### Changes

- keep source truth in frontmatter and catalog data for machine use
- render a compact metadata banner at the top
- render a compact `References` section by default
- make raw-email evidence expandable or separately linked
- cap visible source lists on noisy pages, especially entities
- support inline citations for sensitive claims when useful
- build on the existing MkDocs tags plugin for facets rather than inventing a parallel tag system

### Code areas

- [mkdocs_hooks.py](../../mkdocs_hooks.py)
- [scripts/compile_all.py](../../scripts/compile_all.py)
- [src/db/wiki_pages.py](../../src/db/wiki_pages.py)
- [src/db/touched_pages.py](../../src/db/touched_pages.py)

### Acceptance criteria

- topic pages are readable without scrolling through large evidence blocks
- references remain one click away
- page trust improves without increasing clutter

---

## Workstream 4 — Compiler tooling and agent boundaries

### Goal

Reduce LLM guesswork and stop failure-recovery behavior from creating visible wiki damage.

### Current issues

- the agent still has too much responsibility for page discovery and merging
- missing references become stub pages
- duplicate pages and suffix variants still happen
- entity pages bloat because low-signal mentions are treated as material

### Changes

- add canonical page lookup and similar-page search tools
- add alias / redirect support for duplicate or legacy names
- add page-type classification helpers
- add structured page-update helpers for frontmatter and `Related`
- block user-visible stub creation as the default recovery path
- distinguish strong vs weak evidence for entity inclusion
- route unresolved page creation into a hidden review queue rather than reader-facing nav

### Recommended new tools

- `wiki_find_similar_pages`
- `wiki_resolve_page`
- `wiki_classify_page`
- `wiki_update_frontmatter`
- `wiki_update_related`
- `wiki_verify_quote`
- `wiki_merge_pages`
- `wiki_compact_entity_sources`
- `log_insight`

Operator-facing tools such as `get_compile_health` and `get_recent_runs`
should not live in the main compile agent tool belt. They belong in
ops/audit workflows, scripts, or skills.

### Preferred tool shapes

Tool shapes should be:

- narrow
- deterministic
- self-validating
- named by job, not by implementation
- safe to compose

Good pattern:

- `wiki_resolve_page(name="lens", allowed_types=["system", "topic"])`
- returns canonical slug, type, confidence, aliases, and whether the page exists

Bad pattern:

- `search_everything(query)`
- returns an unstructured blob the model has to reinterpret each time

Examples:

#### `wiki_resolve_page`

```python
def wiki_resolve_page(
    name: str,
    allowed_types: list[str] | None = None,
) -> dict:
    """
    Returns:
    {
      "matches": [
        {
          "slug": "lens-indiamart",
          "title": "Lens",
          "page_type": "system",
          "confidence": 0.94,
          "aliases": ["lens", "lens.indiamart"]
        }
      ],
      "canonical": {
        "slug": "lens-indiamart",
        "page_type": "system"
      }
    }
    """
```

#### `wiki_find_similar_pages`

```python
def wiki_find_similar_pages(
    slug: str,
    page_type: str | None = None,
    threshold: float = 0.8,
) -> dict:
    """
    Returns ranked near-duplicates to stop suffix variants and spelling splits.
    """
```

#### `wiki_classify_page`

```python
def wiki_classify_page(
    title: str,
    raw_paths: list[str],
    candidate_slug: str | None = None,
) -> dict:
    """
    Returns:
    {
      "page_type": "topic",
      "reason": "describes a rollout/change rather than a durable product",
      "confidence": 0.88
    }
    """
```

#### `wiki_write_page`

```python
def wiki_write_page(
    slug: str,
    page_type: str,
    frontmatter: dict,
    body: str,
    expected_prior_hash: str | None = None,
) -> dict:
    """
    Writes only after validating:
    - YAML parses
    - page_type matches path
    - required sections exist
    - sources are unique

    On `expected_prior_hash` mismatch, return a structured conflict response
    with the current hash and do not write. Caller should re-read once and retry;
    if it still conflicts, log an insight and stop.
    """
```

#### `wiki_record_touch`

```python
def wiki_record_touch(message_id: str, slug: str) -> dict:
    """
    Deterministically records that a source message contributed to a page.
    This should be implemented as a thin tool over the existing
    `message_touched_pages` join, not as new storage.
    """
```

#### `wiki_get_references`

```python
def wiki_get_references(
    slug: str,
    mode: str = "compact",
    limit: int = 20,
) -> dict:
    """
    Returns compact reader-facing references or full raw evidence links.
    """
```

#### `wiki_list_nav_nodes`

```python
def wiki_list_nav_nodes(section: str) -> dict:
    """
    Returns canonical landing pages, clusters, and visible children for the UI.
    """
```

### Tooling principles from the references

The referenced Anthropic material points in the same direction:

- tools should do one thing clearly
- overlapping tools confuse agents
- tool outputs should be structured and compact
- context should be curated, not maximized

That matches what this repo needs: fewer generic file operations in the main
loop, more wiki-shaped primitives.

### How to give the right filters

The longstanding advice is:

- start from the user's goal, not from the storage shape
- expose the most discriminative filters first
- default to high-signal narrowing
- paginate and sort explicitly
- support broad-to-narrow exploration instead of giant dumps

In practice:

- prefer `find_new_sources(date_from=..., sender=..., subject_contains=..., thread_id=...)`
  over `list_all_sources()`
- prefer `resolve_page(name, allowed_types=[...])` over `list_pages()`
- prefer `find_related_pages(slug, via="shared_sources", limit=20)` over
  `search_everything(query)`

Good filter dimensions for this repo:

- date range
- sender / participant
- thread
- source family
- page type
- status
- recency
- limit / offset

Good defaults:

- sort by recency or relevance
- return a compact summary list first
- require the caller to drill deeper for full bodies
- cap default result counts

This is consistent with the external guidance:

- tools should return just enough context for the current task
- search/filter tools are usually better than dump/list tools
- context should be curated and incremental, not maximized upfront

### Storage-agnostic context tools

The implementation should use the catalog/database aggressively, but the agent-facing
tool contract should usually stay storage-agnostic.

The current schema already supports this:

- `messages` for queue state and source-document metadata
- `users` for canonical participant identity
- `threads` for thread-level grouping
- `message_participants` for from/to/cc structure
- `wiki_pages` for canonical page metadata
- `message_touched_pages` for provenance joins
- `compile_runs` for run-level observability
- `ingest_cursors` for source sync state

Relevant code:

- [src/db/schema.sql](../../src/db/schema.sql)
- [src/db/messages.py](../../src/db/messages.py)
- [src/db/users.py](../../src/db/users.py)
- [src/db/threads.py](../../src/db/threads.py)
- [src/db/participants.py](../../src/db/participants.py)
- [src/db/wiki_pages.py](../../src/db/wiki_pages.py)
- [src/db/touched_pages.py](../../src/db/touched_pages.py)

Recommended rule:

- the agent should almost never query raw files first when a structured catalog
  lookup can answer the question

#### Recommended context tool set

##### `get_source_context`

```python
def get_source_context(source_id: str | None = None, raw_path: str | None = None) -> dict:
    """
    Returns one normalized source with title, sender, date, thread_id,
    participants, status, and compact provenance metadata.
    """
```

Use for:

- locating a source deterministically
- bridging from raw path to canonical source id
- checking context before processing

##### `get_thread_context`

```python
def get_thread_context(thread_id: str, include_sources: bool = True) -> dict:
    """
    Returns thread metadata plus chronologically ordered source summaries.
    """
```

Use for:

- compiling thread-aware topic updates
- understanding how a discussion evolved
- deciding whether a topic page or timeline page is warranted

##### `get_person_context`

```python
def get_person_context(email: str) -> dict:
    """
    Returns canonical person metadata, display names, first_seen_at, last_seen_at,
    and major related topics/systems.
    """
```

Use for:

- resolving people deterministically
- avoiding name-collision logic in the prompt
- showing better entity-page context

##### `find_people_involved`

```python
def find_people_involved(
    source_id: str | None = None,
    thread_id: str | None = None,
    role: str | None = None,
) -> dict:
    """
    Returns structured from/to/cc participants for a message or thread.
    """
```

Use for:

- deciding whether someone is materially involved or just copied
- building high-confidence entity provenance
- separating strong and weak evidence for person pages

##### `resolve_page`

```python
def resolve_page(
    slug: str | None = None,
    title: str | None = None,
    canonical_user_email: str | None = None,
) -> dict:
    """
    Returns canonical page metadata regardless of whether it was resolved
    from catalog rows, aliases, or existing markdown.
    """
```

Use for:

- page existence checks
- entity-page resolution by email
- avoiding duplicate page creation

##### `get_pages_for_source`

```python
def get_pages_for_source(source_id: str) -> dict:
    """
    Returns wiki pages already touched by a given source.
    """
```

Use for:

- provenance
- understanding where a source already landed
- avoiding redundant page updates

##### `get_references_for_page`

```python
def get_references_for_page(page_id: int | None = None, slug: str | None = None) -> dict:
    """
    Returns the sources that contributed to a page, newest first.
    """
```

Use for:

- compact references
- change summaries
- timeline generation

##### `find_related_pages`

```python
def find_related_pages(
    slug: str,
    via: str = "shared_messages",
    limit: int = 20,
) -> dict:
    """
    Returns candidate related pages based on shared touched messages,
    shared thread_ids, shared users, or shared systems.
    """
```

Use for:

- generating `Related` sections deterministically
- building rollups and cluster landing pages
- reducing ad hoc related-link drift

##### `get_compile_health`

```python
def get_compile_health() -> dict:
    """
    Returns compile queue counts, failure counts, stale claims, recent throughput.
    """
```

Use for:

- operator dashboards
- compile health gates
- deciding whether automation is safe

##### `get_recent_runs`

```python
def get_recent_runs(limit: int = 20) -> dict:
    """
    Returns recent compile runs with status, counts, cost, and notes.
    """
```

Use for:

- recent-change awareness
- operational debugging
- feeding `log.md` and release reports

##### `find_new_sources`

```python
def find_new_sources(
    date_from: str | None = None,
    date_to: str | None = None,
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    thread_id: str | None = None,
    source_family: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Returns a compact, paginated list of candidate new sources to ingest.
    """
```

Use for:

- backlog selection
- targeted recompile windows
- narrowing the ingest surface without huge context dumps

#### What not to expose

Do not give the agent a generic `run_sql(query)` tool as a primary interface.

Why:

- it bloats prompt surface area
- it increases failure modes
- it makes tool usage inconsistent
- it turns stable workflows into ad hoc querying

Instead:

- expose a small set of task-shaped query tools backed by SQL
- keep raw SQL in implementation code or operator-only tooling

#### Best use of the DB in this architecture

The DB should answer:

- what is this message
- who is involved
- what thread is this part of
- what page already exists
- what pages did this message affect
- what messages support this page
- what changed recently
- what is failing operationally

The file layer should answer:

- what is the actual rendered wiki prose
- what are the raw source documents
- what will the reader see

The agent-facing tool should care about the job; the implementation can answer
from the DB, markdown, or both.

### Agent responsibilities after refactor

- choose which durable pages need updating
- synthesize the page body from cited evidence
- create topic/system/policy/timeline/conflict pages only when warranted

### Coordinator / tool responsibilities after refactor

- identity
- canonical slugging
- duplicate detection
- touched-page recording
- provenance joins
- validation
- freshness stamping

### Code areas

- [src/compile/compiler.py](../../src/compile/compiler.py)
- [src/compile/prompts.py](../../src/compile/prompts.py)
- `new helper modules under src/compile/`

### Acceptance criteria

- duplicate suffix pages stop appearing in new runs
- missing-link recovery no longer creates reader-visible junk pages
- entity pages stop growing mainly due to CC-only mentions

---

## Workstream 5 — Legacy cleanup and migration

### Goal

Improve the existing corpus enough that the new viewer hierarchy shows good content.

### Current issues

- duplicates already exist
- some slugs are ambiguous or ugly
- many stubs have low value
- topic/system/entity category drift remains in the corpus

### Changes

- merge duplicate pages into canonical pages
- add alias pages or redirects for common legacy names
- demote or hide thin stubs from primary navigation
- run targeted backfills for high-value topic clusters
- create missing rollup pages for major domains

### Suggested cleanup order

1. duplicate topics and duplicate entities
2. miscategorized people vs systems
3. thin stubs with no meaningful content
4. canonical alias setup
5. rollup and landing page generation

### Acceptance criteria

- top 20 most-read areas have canonical landing pages
- obvious duplicate pages are merged or redirected
- the primary navigation exposes high-value pages, not cleanup debris

---

## Workstream 6 — Verification, QA, and release gates

### Goal

Make wiki quality measurable and block regressions.

### Verification layers

#### 1. Hard validation

These should fail the run:

- invalid YAML frontmatter
- missing required frontmatter
- invalid page_type or status
- duplicate bodies
- obvious suffix-duplicate pages
- empty body
- page stored in the wrong category

Potential additions:

- missing required sections on topic/system/policy pages
- stub pages leaking into primary nav
- unresolved canonical alias conflicts

#### 2. Structural quality metrics

Track these every run:

- page counts by type
- ratio of topics to entities to systems
- number of policies / timelines / conflicts
- number of stub pages
- number of effective orphans excluding `index.md`
- duplicate / alias counts
- average page size by type
- average visible source count by type
- number of pages touched per compile batch
- number of pages reachable only from `index.md`
- number of visible nav nodes per section

#### 3. Evidence quality metrics

Track these on samples and eventually automate more of them:

- source paths exist
- sources match the claims on the page
- quotes are actually present in cited raw files
- material claims on entity pages are not sourced only by CC presence

#### 4. Reader-experience QA

Test flows:

- find a known topic from the home page
- find the canonical page for a product
- distinguish current policy from superseded history
- trace from a topic to supporting evidence
- distinguish a person page from a topic page

### Release gates

Do not publish a build when any of these are true:

- hard validation fails
- duplicate canonical pages are present for the same concept
- stub pages appear in primary nav
- topic pages are outnumbered by entity pages in the main landing surfaces
- evidence rendering is broken or missing

---

## Workstream 7 — Observability and agent feedback loops

### Goal

Make tool usage, agent uncertainty, and compile behavior measurable enough to
improve the system deliberately.

### Current issues

- aggregate tool-call counts exist, but per-tool behavior is still opaque
- the agent has no first-class channel for "this was ambiguous" or "this likely needs human review"
- it is hard to tell whether new tools are actually replacing freeform file thrash

### Changes

- add per-tool-name logging for compile runs
- persist a compact record of tool inputs/outputs and outcomes
- add `log_insight(kind, message, suggested_action=None)` for agent meta-observations
- track which tools are actually used for page resolution, quote verification, and source narrowing
- expose a simple operational report for the last N compile runs

Preferred storage target:

- Langfuse when tracing is enabled
- local JSONL fallback when `LANGFUSE_ENABLED=false`

This keeps the first version simple while avoiding a hard dependency on a flaky
remote tracing path.

### Acceptance criteria

- operators can see which tools are being used and how often
- ambiguous cases are captured without reading full traces
- new tooling work can be judged by actual usage, not guesswork

---

## How to get it to reality

### Recommended PR sequence

Keep each PR narrow and shippable.

#### PR 1 — Taxonomy and labels

- clarify topic vs system rules in prompts and docs
- add UI label `Products & Platforms`
- add template expectations

#### PR 2 — Explicit navigation skeleton

- stop relying on full auto-discovery for main nav
- add landing pages and top-level nav
- hide non-reader-facing pages from primary navigation

#### PR 3 — References redesign

- compact metadata banner
- compact references rendering
- move raw-email blocks behind expansion or separate detail views

Adjacent open work:

- #49 already proposes the metadata banner
- #48 is adjacent for attachment visibility
- #52 is adjacent on category hygiene
- #50 is adjacent on compile health

This plan should absorb those changes where they land rather than re-propose them blindly.

#### PR 4 — Tooling for canonical pages

- similar-page detection
- canonical page resolution
- alias / redirect support
- start by shimming existing tools (`create_entity`, `list_uncompiled_emails`,
  `list_wiki_pages`) behind the new outcome-focused entry points, then deprecate
  the old names once prompts and workflows stop depending on them

#### PR 5 — Entity-page de-noising

- strong vs weak evidence rules
- cap visible sources
- reduce CC-only inflation

#### PR 6 — Cleanup and migration

- merge known duplicate pages
- demote stubs
- create initial rollups for major clusters

#### PR 7 — Verification gates

- expand validator
- add quality metrics
- add release checklist / QA script

#### PR 8 — Observability and feedback loops

- per-tool logging
- `log_insight`
- operational compile reports

#### PR 9 — Recompile and publish

- re-run targeted compile / repair
- verify against audit sampling
- publish updated viewer

### Rollout sequence

```mermaid
flowchart LR
    A["Taxonomy + templates"] --> B["Explicit navigation"]
    B --> C["References redesign"]
    C --> D["Canonical-page tools"]
    D --> E["Entity de-noising"]
    E --> F["Legacy cleanup"]
    F --> G["Verification gates"]
    G --> H["Recompile + publish"]
```

Workstream 7 does not depend on AgentMiddleware landing first.

- If AgentMiddleware lands early, use `wrap_tool_call` for cleaner interception.
- If not, ship the first observability slice with callback handlers and the
  `log_insight` tool, then migrate later.

### Recommended ownership split

- Viewer / IA: MkDocs config, hooks, landing pages
- Compiler / tools: prompt rules, new tools, canonical resolution, compaction
- Catalog / DB: touched-page joins, page metadata, alias metadata if added
- QA / audit: validation, metrics, sample-based fact checks

---

## Verification plan

### Is `index.md` / `log.md` actually being used correctly?

Today: only partially.

- `index.md` is regenerated, but it is mostly a flat catalog with counts.
- `log.md` is appended to, but it is mainly an audit artifact, not a strong
  context source for reader-facing behavior.

What should change:

#### `index.md`

`index.md` should become a content-oriented map, not just a file inventory.

It should contain:

- top sections
- major clusters
- canonical page links
- one-line summaries
- freshness and status hints
- links to rollup pages

The agent should use it for:

- initial orientation
- finding canonical pages and clusters
- avoiding duplicate page creation

Wikilinks remain the intra-page relationship layer. Hierarchy is for browsing;
tags/facets are for filtering and alternate cuts.

#### `log.md`

`log.md` should remain chronological, but should be more intentionally useful.

It should record:

- ingest runs
- compile runs
- repair runs
- duplicate merges
- policy supersessions
- significant nav changes

The agent should use it for:

- recent-change awareness
- repair context
- operator visibility

Recommended format:

- one structured heading or row per event
- stable fields for type, pages touched, message count, and outcome
- grep-friendly structure

### Evolving from email-only to full company knowledge

The long-term target should be:

- one compile architecture
- multiple source families
- one common knowledge model

Source families:

- email
- Slack threads
- meeting transcripts
- customer calls
- project documents
- tickets / bug trackers

Recommended model:

- keep each raw source immutable
- normalize each source into a shared raw schema
- compile all of them into the same wiki layer
- store source-family metadata for provenance and filters

```mermaid
flowchart TD
    A["Email"] --> F["Normalized raw source layer"]
    B["Slack threads"] --> F
    C["Meeting transcripts"] --> F
    D["Project docs"] --> F
    E["Customer calls"] --> F
    F --> G["Compiler + validators"]
    G --> H["Unified company wiki"]
```

### Evolving the schema / AGENTS.md correctly

The schema should be treated like a product contract, not a random prompt file.

Recommended process:

1. Version it explicitly.
2. Change it only when an observed failure justifies the change.
3. Link each meaningful rule to a real failure mode or desired behavior.
4. Prefer shrinking ambiguity over adding prose volume.
5. Move deterministic instructions into tools when possible.

Good schema evolution loop:

```mermaid
flowchart TD
    A["Observed failure or recurring ambiguity"] --> B["Write audit note with concrete example"]
    B --> C["Decide: prompt rule, tool, validator, or coordinator change?"]
    C --> D["Implement smallest effective change"]
    D --> E["Measure on next runs"]
    E --> F{"Failure reduced?"}
    F -- Yes --> G["Keep rule and document rationale"]
    F -- No --> H["Escalate to stronger deterministic control"]
```

Rules of thumb:

- if the rule depends on stable identity, it probably belongs in code
- if the rule depends on output structure, it may belong in validation
- if the rule depends on nuanced synthesis, it belongs in the schema/prompt
- if the rule is repeatedly ignored, stop telling the model and build a tool

## 1. Automated checks in CI

Minimum CI for this phase:

- `uv run ruff check`
- `uv run pytest`
- `uv run python scripts/validate_wiki.py`
- a lightweight nav / rendering smoke check

Optional but recommended:

- generate the MkDocs site in CI
- fail if canonical landing pages are missing
- fail if stub pages appear in top-level nav exports

## 2. Batch-level compile verification

After each compile batch:

- validate `wiki/`
- record number of touched pages
- record number of source-bearing pages changed
- record duplicate / stub / orphan counts
- compare quality metrics against the previous run

## 3. Golden-path manual QA

Use a fixed set of representative subjects:

- one major topic
- one major product/system
- one policy
- one noisy person page
- one duplicate-prone area

For each, verify:

- canonical page is easy to find
- page type is correct
- current state is readable
- references are available
- related pages make sense

## 4. Audit sampling

Every release candidate:

- sample 5 topic pages
- sample 3 system pages
- sample 3 entity pages
- sample 1 policy page if present

Check:

- fidelity to cited evidence
- absence of unsupported role/ownership claims
- absence of wrong-source joins
- structural readability

Operationalize this as:

- a repeatable checklist in docs
- `scripts/audit.py` for reproducible samples
- eventually a dedicated audit skill for release-candidate review

## 5. Success metrics

Target directional improvements:

- fewer visible stub pages
- fewer duplicate canonical concepts
- more topic-first navigation clicks
- lower average visible evidence clutter on topic pages
- higher proportion of page-to-page navigation that does not depend on `index.md`
- more useful policies / timelines / conflicts where the content actually warrants them

---

## Risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Over-engineering the hierarchy | Too much structure becomes maintenance burden | Keep storage shallow; put hierarchy in landing pages and generated nav |
| Taxonomy drift | People will still create ambiguous pages | Add tooling + validation + examples in prompts |
| Provenance gets weaker | Compact references can accidentally hide evidence | Keep expandable raw detail and audit evidence checks |
| Cleanup takes too long | Legacy corpus is messy | Focus first on high-traffic / high-value areas |
| Viewer and compiler diverge | Docs say one thing, output shows another | Use landing pages, validator checks, and release gates tied to rendered output |

---

## Definition of done for Phase 1

Phase 1 is complete when:

- the home page and landing pages provide a real browsing experience
- `Topics` and `Products & Platforms` are the main reader path
- the topic vs system distinction is clear in both compile behavior and UI
- reader-visible stub pollution is low
- references remain strong but layered
- duplicate and miscategorized page creation is materially reduced
- the build has automated gates that detect structural regressions

---

## Immediate next actions

1. Approve the taxonomy and reader hierarchy in this document.
2. Create PR 1 for taxonomy, labels, and template rules.
3. Create PR 2 for explicit nav and landing-page scaffolding.
4. Add verification gates before any broader live-ingest work.

This ordering matters. A faster bad wiki is worse than a slower good one.
