# Issue: Internal Wiki Structure — Information Architecture & Operating Model

**Labels**: `documentation`, `architecture`, `phase-1`

---

## Why this exists

For the implementation sequence and verification plan, see
[`10-phase1-implementation-plan.md`](10-phase1-implementation-plan.md).

The current pipeline preserves evidence, but the reader experience is still too
close to "generated catalog of pages" instead of "curated internal wiki."

The audit evidence is consistent:

- `entities/` dominates the corpus while `policies/`, `timelines/`, and `conflicts/` are barely used.
- Many pages are effectively reachable only from `index.md`, not from meaningful prose links.
- Auto-created stubs keep dead-link counts low but turn missing structure into visible clutter.
- The MkDocs viewer currently auto-discovers the entire `wiki/` tree as navigation and appends full raw-email sources to every page.

That combination is good for preservation but weak for readability.

The target is a wiki that is:

- topic-first
- reference-backed
- easy to browse
- current enough for a fast-moving company
- explicit about uncertainty, supersession, and freshness

---

## North star

Readers should land on an answer first and on evidence second.

That means:

- most reading starts on `topics/` and `systems/`
- most knowledge is synthesized into a smaller number of durable pages
- people pages support attribution and navigation, but do not define the product
- references remain available everywhere, but do not dominate the visible page
- unresolved contradictions are surfaced intentionally, not buried

---

## Reader-facing structure

### 1. Home

The home page should answer:

- what changed recently
- what topics are active
- what systems matter most right now
- which policies changed
- what needs review

Good home-page sections:

- `Active topics`
- `Recently updated`
- `Current policies`
- `Critical systems`
- `Open conflicts`
- `Glossary / common acronyms`

### 2. Topics

Topics are the primary reading unit.

A topic is a project, initiative, workstream, program, or recurring theme such as:

- complaint agent v2
- buyer feedback workflows
- Lens search quality
- WhatsApp 9696 work

Every important cluster of email should usually roll up into an existing topic or
system page before it creates a new page.

### 3. Systems

Systems are durable nouns:

- products
- platforms
- services
- tools
- URLs
- mailing lists

System pages answer "what is this thing and where is it involved?"

Reader-facing UI should probably label this section **Products & Platforms**
even if the internal page type remains `system`.

### 4. Policies

Policies should be explicit first-class pages whenever an email changes:

- a rule
- an approval flow
- a procedure
- an operational guideline

Policy pages should privilege "current policy" first and "history" second.

### 5. People

People pages are supporting context, not the front door.

They should answer:

- who this person is
- what domains they are materially involved in
- which major topics they appear on

They should not become exhaustive per-email activity ledgers.

### 6. Timelines

Timeline pages exist only when chronology matters enough to justify them.

Examples:

- a long-running rollout
- a production incident stream
- a multi-month migration

### 7. Conflicts

Conflict pages are explicit holding areas for unresolved contradictions.

They should exist when the system cannot truthfully collapse competing claims into
one "current" statement.

### 8. Glossary and rollups

The wiki needs reader-facing aggregation pages that are not just raw folder indexes.

Examples:

- all WhatsApp-related work
- all buyer-chat initiatives
- common teams and acronyms
- major product surfaces

These can be generated pages, but they must read like navigation aids, not dumps.

---

## Page model

## Topic vs system

This distinction should be explicit because it drives both compile behavior and
viewer navigation.

- A **topic** answers: "what is happening?"
- A **system** answers: "what is this thing?"

Use a `topic` when the page primarily tracks:

- status
- decisions
- milestones
- rollout details
- incidents
- open questions

Use a `system` when the page primarily explains:

- a durable product
- a platform
- a tool
- a service
- a mailing list
- a URL / technical surface

Examples:

- `Lens` = system
- `City-based filters on Lens results page` = topic
- `WhatsApp 9696` = system
- `Complaint agent v2 on WhatsApp 9696` = topic

If a product is early and still mostly discussed as a rollout, keep one
canonical durable `system` page for the thing and use `topic` pages for the
changes happening around it.

### Topic page template

Every serious topic page should aim for this order:

1. `Summary`
2. `Current state`
3. `Why it matters`
4. `Key decisions`
5. `Recent changes`
6. `Open questions`
7. `Timeline`
8. `Related topics / systems / people`
9. `References`

### System page template

1. `Summary`
2. `Role in the business or stack`
3. `Current initiatives using this system`
4. `Dependencies / related systems`
5. `Known issues or active changes`
6. `Related topics / people`
7. `References`

### Policy page template

1. `Current policy`
2. `Who or what it affects`
3. `Effective date`
4. `Supersedes / superseded by`
5. `History`
6. `References`

### People page template

1. `Who this is`
2. `Areas of involvement`
3. `Major related topics`
4. `Major related systems`
5. `Recent material contributions`
6. `References`

For people pages, "recent material contributions" means decisions, ownership, or
direct comments that mattered. It does not mean "appeared on CC."

### Timeline page template

1. `Scope`
2. `Chronological events`
3. `Current status`
4. `Related pages`
5. `References`

### Conflict page template

1. `Question in dispute`
2. `Position A`
3. `Position B`
4. `What evidence exists`
5. `What would resolve it`
6. `Affected pages`
7. `References`

---

## Navigation model

The current "auto-discover everything under `wiki/`" model is acceptable for raw
coverage, but not for a polished wiki.

The viewer should move toward:

- explicit top-level navigation
- curated landing pages per section
- generated rollups for major domains
- aliases or redirects for common names
- fewer raw file lists in the visible sidebar

Recommended top-level navigation:

- `Home`
- `Topics`
- `Systems`
- `Policies`
- `People`
- `Changes`
- `About`

Recommended section landing pages:

- `topics/index.md` should highlight major themes and stable clusters
- `systems/index.md` should group systems by business area or stack layer
- `policies/index.md` should show current policies first
- `entities/index.md` should emphasize domain owners and major contributors, not every page equally

Important rule:

- the navigation should expose canonical pages, not stub pages, alias pages, or dedupe leftovers

---

## Provenance model

References are a core feature, but they should be layered.

The visible page should show:

- freshness
- status
- number of supporting source emails
- optionally the model / run metadata if useful for operators

Then the page should provide:

- a compact `References` section
- expandable raw-email evidence on demand
- inline citations for sensitive factual claims where needed

The system should avoid:

- giant frontmatter blocks rendered into the reader path
- repeating the same raw-email dump pattern on every page regardless of page value
- letting source lists become the dominant visual content

The rule is:

- evidence must be preserved
- evidence does not need to be loud

---

## Compile model

The compiler should treat email as input evidence, not as the target page shape.

Good compile behavior:

- many emails update a few durable pages
- acknowledgements and low-signal replies do not create visible knowledge pages
- new pages are created only when a genuinely new durable concept appears
- page creation prefers topics and systems over person pages
- unresolved references go to an operator queue or alias layer before they become reader-facing stubs

Bad compile behavior:

- one email becomes one page
- unknown wikilinks create user-visible stub pages by default
- entity pages absorb every thread because someone was copied
- date-adjacent or CC-adjacent joins get treated as material participation

---

## Tool, agent, and skill boundaries

### Tools should own deterministic work

Examples:

- canonical page lookup
- alias resolution
- entity identity by email
- similar-page detection
- category validation
- provenance lookup
- source-count and freshness calculation
- duplicate detection
- dead-link detection
- policy supersession bookkeeping

### The compiler agent should own synthesis

Its job should be:

- decide which durable pages need to change
- synthesize current knowledge from cited evidence
- update topic, system, policy, timeline, and conflict pages
- express uncertainty when evidence does not support a merge

Its job should not be:

- inventing slugs
- managing compile state
- deciding canonical identity from fuzzy names alone
- mass-grepping the corpus without helper tools
- creating reader-facing stubs as a recovery path

### Skills should own operator workflows

High-value skills for this repo:

- `audit-wiki-quality`
- `repair-duplicate-pages`
- `recompile-topic-cluster`
- `viewer-publish`
- `live-ingest-ops`
- `backlog-triage`

These are recurring workflows, not part of the main compile reasoning loop.

---

## Operational rules

Rules that keep the wiki feeling like a wiki:

- no page should exist only because a link resolver needed a target
- no people page should be treated as equivalent to a topic page in navigation priority
- no policy change should be buried inside a generic topic page if it affects operating behavior
- no unresolved contradiction should silently collapse into a single claim
- no raw source should be modified
- no compile run should be marked successful if it leaves corruption, duplicate canonical pages, or broken category boundaries behind

---

## Phase-1 acceptance criteria

This structure is working when:

- a new reader can browse the wiki by subject without knowing filenames
- topic pages answer "what is happening?" better than people pages do
- references are still visible and trustworthy without overwhelming the prose
- stub pages are rare and mostly hidden from reader-facing navigation
- canonical aliases and deduped pages prevent common-name confusion
- policies, timelines, and conflicts are used when they add meaning, not just declared as empty categories

---

## Concrete implementation order

1. Define section landing pages and page templates.
2. Move provenance rendering out of page body/frontmatter clutter.
3. Stop user-visible stub creation as the default missing-link strategy.
4. Add canonical lookup, alias, and duplicate-detection tools.
5. Tighten entity-page rules so only material contributions appear.
6. Rebuild viewer navigation around landing pages and rollups.
7. Only then automate live ingestion more aggressively.
