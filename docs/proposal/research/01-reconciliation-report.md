# North-Star Reconciliation — email-knowledge-base

Generated: 2026-04-15. Read-only research pass.

Corpus surveyed: `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `docs/BACKLOG.md`
(2359 lines), `docs/issues/{01..11}.md`, `docs/reviews/*.md`,
`docs/incidents/2026-04-13-phase0-bootstrap.md`,
`docs/audits/audit-20260413T081547Z.md`, `mkdocs.yml`, `wiki/` tree,
`src/compile/prompts.py`, `scripts/validate_wiki.py`, the last 30 merged PRs
(#36–#69).

---

## 1. Working north star (what the project IS today)

A single-user backlog-mode wiki compiler for the IndiaMART
`marketplacelaunch@indiamart.com` mailing list. It pulls email out of Gmail
via OAuth, writes immutable per-message markdown into `raw/`, then runs a
Deep Agents + LiteLLM compiler loop (`scripts/compile_all.py`) that writes
interlinked topic/system/entity pages into `wiki/` and deterministic
coordinator hooks that own compile-state transitions in Postgres. A
MkDocs-Material viewer serves the wiki on GCP Cloud Run behind IAP at
`email-kb-viewer-kntbneg73q-el.a.run.app`, gated to the `indiamart.com`
Workspace org. Live ingestion (Gmail watch / Pub/Sub), search, and
chatbot-style querying are **not** shipped. The wiki is currently
~708 pages (176 topics, 463 entities, 68 systems, 1 policy, 0
timelines, 0 conflicts) with high entity/stub noise and active cleanup
work.

Put differently: it is a working compile pipeline that produces a
browsable but entity-dominated wiki, and Phase 1 of the plan (turn that
output into something that reads like a real internal wiki) is the
current workstream.

---

## 2. Documented goals (strategy-level docs)

All merged via `@amtagrwl` as author (Claude/Codex wrote portions as
subagents but the user committed). "Author" column below refers to
branch provenance, since the Codex coding-agent branches are named
`codex/*`.

| Doc | File | Author | Last touched | Asserted goal |
|---|---|---|---|---|
| README | `README.md:1-311` | user | 2026-04-15 | "Topic-first knowledge base that ingests Gmail into an interlinked markdown wiki. Raw emails are immutable evidence. Wiki pages are compiled knowledge." Lists 4 near-term optimizations (trustworthy topic pages, provenance without clutter, topic-first nav, people as support) `README.md:32-42`. |
| CLAUDE.md | `CLAUDE.md:1-228` | user | 2026-04-13 | Agent operating contract. "A living knowledge base compiled from email. Raw emails are immutable source documents. Wiki pages are LLM-compiled knowledge." `CLAUDE.md:4-6`. Enumerates 6 page types (topic/entity/system/policy/timeline/conflict) and hard rules. |
| Phased delivery | `docs/issues/07-phased-delivery.md:1-143` | user | 2026-04-15 | "Build a polished internal wiki that preserves references, keeps up with a fast-moving company, reads like curated knowledge, is organized around topics and systems first, people pages as support" `07-phased-delivery.md:7-14`. Phase 0 ✅, Phase 1 is current. |
| Wiki IA | `docs/issues/09-internal-wiki-structure.md:1-429` | user (merged via branch `codex/wiki-ia-implementation-plan`, PR #53) | 2026-04-15 | "Readers should land on an answer first and on evidence second." `09:36`. Topic-first, reference-backed, explicit uncertainty/supersession. |
| Phase 1 impl plan | `docs/issues/10-phase1-implementation-plan.md:1-1508` | user (same PR #53 as above) | 2026-04-15 | "Turn the current pipeline into a topic-first, reference-backed, hierarchical internal wiki." 7 workstreams + acceptance criteria + PR sequence. |
| Personas / flows | `docs/issues/11-user-personas-and-knowledge-flows.md:1-711` | user (merged via `codex/user-personas-followup`, PR #64, 2026-04-14) | 2026-04-15 | 4 personas (leadership, PM, engineer, new joiner) and 9 acceptance scenarios A–I. Introduces "domain hubs" as the primary reader entry point and explicitly asks for "ownership now vs earlier" + "opinion-change" sections on every durable page. |
| BACKLOG north-star | `docs/BACKLOG.md:243-459` (entry dated 2026-04-14) | user | 2026-04-14 | "Company knowledge base for IndiaMART, compiled from email traffic." Defines the "six-month-new-joiner test" as the acceptance bar `BACKLOG.md:313`. |
| BACKLOG priority index | `docs/BACKLOG.md:1000-1074` (dated 2026-04-13) | user | 2026-04-13 | Ordered NOW/SOON/LATER list: provenance split, topic-first, entity de-noising, compile guardrails, trivial-message filter, docs honesty. |
| 01 Project spec | `docs/issues/01-project-spec.md` | user | 2026-04-12 | Pointer to README. |
| 02 Data model | `docs/issues/02-data-model.md:1-213` | user | 2026-04-12 | Canonical raw + wiki markdown format with YAML frontmatter. Entity defined as "A person, team, product, or system" `02:117` — **this is now wrong**. |
| 03 Ingestor / 04 Compiler / 05 Schema / 06 Lint | `docs/issues/0{3,4,5,6}-*.md` | user | 2026-04-12 | Phase 0 completion records. Describe shipped tools (`list_uncompiled_emails`, `mark_as_compiled`, `update_wiki_index`, `append_to_log`). **These tool names are stale — three of the four have since been removed from the agent's toolbelt.** |
| 08 Phase 1 live ingest | `docs/issues/08-phase1-live-ingestion.md` | user | 2026-04-12 | Gmail watch + Pub/Sub + FastAPI `/webhook/gmail`. Demoted in `07-phased-delivery.md` to Phase 2. |
| Codex catalog review | `docs/reviews/codex-catalog-review-20260413T080000Z.md` | Codex (via subagent) | 2026-04-13 | Schema correction for the Postgres catalog: `slug` must be globally unique, drop `threads.topic_wiki_slug`, rename `wiki_pages.email` → `canonical_user_email`, add `page_id` stable FK. All applied. |
| Codex priority review | `docs/reviews/codex-priority-review-20260413T090000Z.md` | Codex (via subagent) | 2026-04-13 | Orders the next 5 PRs around moving queue state + provenance into Postgres. Mostly executed (PR #31 and downstream). |
| Knowledge-vs-index | `docs/reviews/knowledge-vs-index-20260413T032000Z.md` | user (read-only proposal) | 2026-04-13 | Proposes splitting wiki markdown → (prose + frontmatter); Postgres catalog → (provenance); mention index → (derived view). Partially implemented. |
| Persona audits (5x) | `docs/reviews/audit-persona-*.md` | Claude (5 blind-auditor subagents) | 2026-04-13 | 5 independent audits of wiki quality from newbie / PM / IA / fact-check / journalist personas. Synthesis at `docs/reviews/audit-synthesis-20260413T040000Z.md`. Leave as archive. |
| Other reviews | `docs/reviews/{audit-*,coherence-*,ecosystem-scan-*,systemic-quality-*,tool-audit-*,plan-24h-*,overnight-plan-*,deepagents-learnings-*,edit-tool-research-*,quality-trend-*,source-dedup-plan-*,auto-repair-plan-*,improve-internal-*,prompt-caching-*}.md` | Claude / user | 2026-04-13 | Operational snapshots / incident-postmortem-adjacent. Leave as archive. |
| Incidents | `docs/incidents/2026-04-13-phase0-bootstrap.md` | user | 2026-04-13 | Phase 0 postmortem. Leave as archive. |
| GCP migration | `docs/gcp-migration.md` | user | 2026-04-13 | Phased GCP deploy. Separate concern; not in conflict. |

---

## 3. Conflicts (documented contradictions)

### 3.1 Page-type taxonomy — CLAUDE.md / 02-data-model.md / prompts.py disagree

- `CLAUDE.md:64-73` lists **6** page types (topic / entity / system /
  policy / timeline / conflict). Entity defined as "A HUMAN PERSON
  referenced in From/To/CC/body".
- `docs/issues/02-data-model.md:110-119` still lists **5** page types
  and defines entity as "A person, team, product, or system" — i.e.,
  systems folded into entity. This was the pre-2026-04-13 taxonomy.
- `src/compile/prompts.py:73` and `scripts/validate_wiki.py:56` enforce
  6 types including `index` as a 7th "nav-only" exception.
- `docs/reviews/coherence-20260413T023902Z.md:19-21` explicitly flagged
  this drift. Fix not applied to `02-data-model.md`.

`02-data-model.md` is authoritative-looking (it's under `docs/issues/`,
names the schema) but **stale**. Any new agent reading it alone would
misclassify products/systems into `entities/`.

### 3.2 Navigation schema — personas doc invents `navigation_role`

- `docs/issues/11-user-personas-and-knowledge-flows.md:188` maps
  "Domain hub" to `topic` page with `navigation_role: domain_hub`
  frontmatter.
- Neither `CLAUDE.md:121-135` (the YAML-frontmatter contract) nor
  `scripts/validate_wiki.py` nor any page in `wiki/` has any
  `navigation_role` field. Grep confirms zero code references
  (`grep -r navigation_role src/ scripts/` → only the doc).
- Same doc also proposes a "Capability page" tier (11:282-302),
  "Ownership History" sections, "How the View Changed" sections (11:441-467).
  None are enforced, templated in prompts, or validated.

The personas doc is aspirational vocabulary. It was merged as #64 but
nothing downstream (prompts, validator, viewer hooks, tools) has caught up.

### 3.3 Wiki-IA doc vs Phase-1 plan — section-template drift

Different required sections for the same page types across the two docs
the user authored in the same PR (#53):

- **Topic**: `09:194-205` lists 9 sections (`Summary`, `Current state`,
  `Why it matters`, `Key decisions`, `Recent changes`, `Open questions`,
  `Timeline`, `Related topics / systems / people`, `References`).
  `10:297-308` lists 8 sections — drops `Timeline`, renames "Related
  topics / systems / people" → "Related pages".
- **System**: `09:208-216` requires "Current initiatives using this
  system" (7 sections). `10:309-320` renames to "Active related
  topics" (7 sections).
- **Entity**: `09:228-237` requires 6 sections ending in
  "Recent material contributions". `10:332-342` matches.
  Personas doc `11:570-583` replaces them with
  `Current Areas of Involvement`, `Current Ownership Mirror`,
  `Previous Ownership Mirror`, `Key Strategic Decisions Influenced` — a
  different schema entirely.

`scripts/validate_wiki.py::REQUIRED_SECTIONS` (referenced in
CHANGELOG line 70) enforces the `10-phase1-implementation-plan.md`
set, not the `09-internal-wiki-structure.md` or `11-user-personas...`
set. So the wiki-IA and personas docs are describing stricter/richer
pages than the validator will ever enforce.

### 3.4 Domain hub structure — BACKLOG vs personas doc

- `docs/BACKLOG.md:52` says: "Domain hubs — `wiki/domains/<domain>.md`
  auto-generated from frontmatter" (still in "NOT shipped" list).
- `docs/issues/11-user-personas-and-knowledge-flows.md:200-204`
  explicitly rejects a new directory: "Phase 1 should not invent a new
  `hub` page type. Domain hubs should be implemented as reader-facing
  `topic` pages with a clear navigation role."

BACKLOG has a directory (`wiki/domains/`); personas doc has a frontmatter
flag. Neither exists today.

### 3.5 BACKLOG "priority index" vs what actually shipped

The NOW list at `docs/BACKLOG.md:1021-1034` is now stale — most items
shipped in PRs #54–#69 but the priority index wasn't updated:

| BACKLOG "NOW" item | Status per CHANGELOG + PRs |
|---|---|
| 1. Finish provenance split (references from catalog) | Partial — per-page metadata header shipped (#49, `CHANGELOG:130-138`); Sources block now collapsed + capped (#60, `CHANGELOG:186-192`). But `sources:` still lives in frontmatter and `scripts/lint_wiki.py:37` still requires it. Not done. |
| 2. Topic-first + nav + glossary | Partial — explicit mkdocs nav (#59, `mkdocs.yml:13-24`), `Products & Platforms` label, landing pages. Glossary/rollups **not shipped** (see wiki check below). |
| 3. De-noise entity pages | Partial — entity evidence-gate (#67) + CC-filter prompt rules + auto-stub cleanup (#68) shipped. Entity page compaction / source cap = done via viewer only. |
| 4. Compile guardrails | Done — per-batch timeout (#50), stall detection, `--batch-timeout` default 900s, `TimeoutError` fail-fast. |
| 5. Trivial-message filter at ingest | **Not shipped.** Still open at `BACKLOG.md:2234-2261`. |
| 6. Keep docs honest | Actively regressing — this report is about that. |

The SOON list (items 7–11) mostly matches reality (agent scaffolding,
live ingestion, parallel compile all deferred). But the BACKLOG dashboard
at the top (`BACKLOG.md:8-66`) is more accurate than the priority
index below; the two are not in sync.

### 3.6 Provenance posture — IA plan vs lint_wiki.py

- `docs/issues/10-phase1-implementation-plan.md:467-500` (Workstream 3)
  says: "keep source truth in frontmatter and catalog data for machine
  use" — i.e., `sources:` stays in YAML.
- `docs/reviews/knowledge-vs-index-20260413T032000Z.md:42-53` says:
  "Entity pages go from 210-line YAML blobs to 8 lines" — i.e., drop
  `sources:` from YAML entirely, render from Postgres at build time.
- `docs/reviews/codex-priority-review-20260413T090000Z.md:56-66` ("PR4")
  says: "delete the exhaustive-source instructions in
  `src/compile/prompts.py:54-58,185-215`; remove `sources` from
  `scripts/lint_wiki.py:37`".

`scripts/lint_wiki.py:37` still has `sources` as required; prompts still
tell the agent to "populate sources exhaustively"
(`src/compile/prompts.py` per the knowledge-vs-index review). So the
"drop sources from frontmatter" direction was accepted in review but
never implemented. Only the **rendering** layer changed (mkdocs hooks
now collapse the Sources block).

### 3.7 Attachments posture — README vs 03-email-ingestor.md

- `README.md:50-51` says the project "Ingests emails from a Gmail
  mailing list (OAuth; backlog flow shipped, live flow designed but
  not yet shipped)".
- `docs/issues/03-email-ingestor.md:83-88` lists image captioning and
  thread-aware grouping as Phase-1 open items.
- `docs/issues/07-phased-delivery.md:30` says attachments are Phase 0
  ✅ with "code shipped; `--skip-attachments` default for now".

Attachments ship as code but not as default behavior. Image-captioning
path is not exercised. Three docs say three slightly different things
about the same thing.

### 3.8 Langfuse posture — shipped vs default-off

Flagged in `docs/reviews/coherence-20260413T023902Z.md:74-77`:

- `README.md:213-214` and `07-phased-delivery.md:35` list Langfuse
  as ✅ Phase 0.
- `.env.example` defaults `LANGFUSE_ENABLED=false`
  (`CHANGELOG.md:243-248`) because of the self-hosted OTLP-hang
  (issue #17).
- `docs/BACKLOG.md:537-557` treats "No tool-call trace — Langfuse
  disabled" as an open observability gap.

Resolution: default-off wins. The README/phased-delivery "✅" is
misleading — Langfuse works end-to-end (smoke-tested per
`CHANGELOG.md:222-228`) but is not the live tracing surface. Tool-call
telemetry to Postgres (`feat/tool-call-logging`, PR #62) replaced it
for the most-diagnostic path.

### 3.9 Agent toolbelt — CLAUDE.md "NEVER" list vs 04-wiki-compiler.md

- `CLAUDE.md:166-175` says: "NEVER call `mark_as_compiled`,
  `stamp_page_compiled_at`, `append_to_log`, or `update_wiki_index`
  (these are no longer agent tools as of 2026-04-13)."
- `docs/issues/04-wiki-compiler.md:27-37` still lists those 4 tools
  as the agent's "4 custom tools".

The issue doc is describing a past implementation. The prompt
(`src/compile/compiler.py::create_compiler`) now wires
`list_uncompiled_emails`, `list_wiki_pages`, `create_entity`,
`resolve_page`, `find_new_sources`, `log_insight`, `write_draft_page`
and hides `list_uncompiled_emails` from the agent's list per
`docs/BACKLOG.md:473-490`. Neither count is the current truth — another
gap.

### 3.10 Pool model (random-per-batch) — stated vs actual

`docs/BACKLOG.md:1083-1146` described a per-batch random-model pool
with the intent of comparing models on workload. CHANGELOG
`CHANGELOG.md:167-169` confirms `--model-pool a,b,c` shipped in #51.
`.env` default and `CHANGELOG.md:202-208` show current production
default is `z-ai/glm-4.6` (not pool, not glm-5.1). No conflict, but
`docs/reviews/prompt-caching-20260413.md` is the canonical record of
what pool members are safe. Flag for canonicalization only.

---

## 4. Duplication (same decision captured in multiple places)

| Decision | Duplicated across | Canonical | Redirect |
|---|---|---|---|
| "Topic vs system" rule | `09:156-191`, `10:265-274`, `CLAUDE.md:64-73`, `src/compile/prompts.py:141-164` | `src/compile/prompts.py` (the prompt is the runtime truth) | All three docs should link to the prompt, not re-state the rule. |
| Page-type templates | `09:193-254` and `10:293-362` and `11:509-583` (3 different versions) | `10-phase1-implementation-plan.md` matches validator enforcement | `09` should be folded into `10` (they were both in PR #53 and shouldn't diverge). `11` is aspirational — move templates into the follow-up spec, not the north star. |
| North star statement | `README.md:31-42`, `07:5-14`, `09:34-45`, `10:5-30`, `BACKLOG.md:243-322`, `11:700-710` | Either README or `07-phased-delivery.md` — pick one | All six restate it slightly differently. Pick the shortest (README:31-42) as canonical; strip the rest to one-line summaries + link. |
| Acceptance criteria for Phase 1 | `07:71-77`, `09:408-416`, `10:1487-1499`, `11:586-682` (as 9 scenarios) | `10:1487-1499` (Definition of Done) is the most concrete | Consolidate: Phase 1 DoD in `10`, scenarios in `11` stay as tests but reference `10`. |
| Hidden drafts strategy | `10:520-525` ("route unresolved page creation into a hidden review queue"), `BACKLOG.md:19` (auto-stub strategy), `CHANGELOG.md:46-55` (shipped in `feat/drafts-folder`) | `CHANGELOG.md` + `src/compile/compiler.py::write_draft_page` | Remove the BACKLOG entry. `10` can stay as the design rationale. |
| "Coordinators verify, LLMs propose" | `CLAUDE.md:184-219`, `BACKLOG.md:944-997`, `10:994-1009` | `CLAUDE.md:184-219` (the only one the agent actually reads at runtime) | BACKLOG entry is historical and can archive. `10` should reference CLAUDE.md. |
| Agent-tooling audit / gap list | `BACKLOG.md:463-582` (2026-04-14), `docs/reviews/tool-audit-20260413T050000Z.md` (2026-04-13), `10:504-777` (Workstream 4) | `10:504-777` is the forward-looking plan; BACKLOG is operational; tool-audit review is archive | Delete the BACKLOG version once its open items move to issues. Keep review as archive. |
| Multiple "priority for the next 24h" plans | `docs/reviews/plan-24h-20260413T031700Z.md`, `docs/reviews/overnight-plan-20260412T210837Z.md`, `BACKLOG.md:8-66` status dashboard | `BACKLOG.md:8-66` is freshest | Archive the dated `docs/reviews/plan-*` and `overnight-*`. |

---

## 5. Gaps (things docs imply but don't specify)

1. **`navigation_role` is specified but nowhere schema-enforced.**
   `11:188` introduces it. No validator check, no frontmatter example
   in CLAUDE.md, no `page_type: hub` surface in the viewer. Either
   remove from the doc or ship as part of the frontmatter contract.

2. **"Ownership history" section is mandatory in `11:527-549` but
   optional nowhere.** No prompt rule, no validator check, no
   existing page has it. Either add `owners:` to frontmatter schema
   and ship an `Ownership History` validator, or demote it to
   "guidance, not a requirement".

3. **"Opinion-change" / "How the View Changed" section from
   `11:441-467` has no enforcement mechanism.** Same issue. Current
   wiki has zero pages with this section. Needs a prompt rule OR
   explicit demotion.

4. **Domain hub taxonomy unspecified.** Personas doc lists 8 hubs
   (`11:212-219`: Buyer, Seller, Marketplace, Search & Discovery,
   Trust & Safety, Growth, AI Agents & Automation, Infra & Developer
   Platforms). No tool, no script, no compiler rule to populate them.
   Which page becomes which hub? Who writes them?

5. **Promotion criteria for "experiment → major product" (`11:317-324`)
   has no mechanism.** "3+ independent threads", "30+ day span",
   "2+ child experiments" — none of these are queryable against
   current Postgres schema. Either add a SQL query tool
   (e.g. `wiki_propose_promotions`) or remove the criteria.

6. **Glossary pages mentioned in 4 places, don't exist.** `09:140-150`,
   `10:443-450`, `11:206-215`, `BACKLOG.md:51`. The wiki has zero
   glossary content (`ls wiki/` — no `glossary.md`, no
   `wiki/glossary/`). Listed as Phase 1 in the IA doc but never got
   a workstream or acceptance check.

7. **Ownership mirror sync rule from `11:394-403` has no implementation
   path.** "Entity pages mirror that record for discovery and should
   be regenerated or synced from the canonical pages." No script, no
   tool, no rule in the compiler prompt. Pure intent.

8. **Policy/Timeline/Conflict pages are first-class categories in
   every doc, zero in the corpus.** `wiki/policies/` = 1 page,
   `wiki/timelines/` = 0, `wiki/conflicts/` = 0
   (verified via `ls wiki/*/`). Every strategy doc treats these as
   real navigation surfaces. Either (a) accept that only topics +
   systems + entities exist today and tell the reader that, or
   (b) prompt/tool the compiler to create them.

9. **`wiki/home.md` is explicitly a placeholder** (`wiki/home.md:18`:
   "Placeholder — a curated home view with freshness signals comes
   in a later PR."). Same for `wiki/topics/index.md:14`,
   `wiki/systems/index.md:15`. The Phase-1 Definition of Done says
   these must provide "a real browsing experience" (`10:1489`). Not
   done.

10. **"Six-month-new-joiner test" has no enforcement mechanism.**
    `BACKLOG.md:313-318` introduces it as "the acceptance bar". No
    validator check, no prompt rule, no auditor skill. It's
    editorial guidance without an instrument.

11. **"Compile health" dashboard tools (`get_compile_health`,
    `get_recent_runs`) are specified in `10:903-931` but not shipped.**
    Unclear whether these should live in the agent toolbelt or the
    coordinator. `10:541-543` says operator-only; no implementation yet.

12. **`wiki/log.md` behavior specified as "content-oriented map" in
    `10:1283-1295` but still renders as audit log.** Same file,
    two different aspirations.

---

## 6. Unfinished Phase-1 items (from `10-phase1-implementation-plan.md`)

Cross-checked against `CHANGELOG.md` and `gh pr list --state merged --limit 30`
(#36–#69).

### Shipped (retire these from the plan)

| Workstream | Item | Ship evidence |
|---|---|---|
| WS1 Taxonomy | Topic vs system rules in prompts | #55 `feat/prompt-taxonomy-retry` / `#66`, `CHANGELOG.md:193-201` |
| WS1 Taxonomy | UI label `Products & Platforms` | #59 `feat/mkdocs-nav`, `CHANGELOG.md:14-22` |
| WS1 Taxonomy | Section-template validator | #58 `feat/validate-sections`, `CHANGELOG.md:70-76`, `scripts/validate_wiki.py:650-675` |
| WS2 Viewer nav | Explicit `nav:` in mkdocs.yml | #59, `mkdocs.yml:13-24` |
| WS2 Viewer nav | Landing pages | #59, `wiki/{home,about,topics/index,systems/index,policies/index,entities/index}.md` |
| WS2 Viewer nav | `_drafts/**` exclude | #63, `mkdocs.yml:28-34` |
| WS3 Provenance | Compact metadata banner | #49 `worktree-agent-a443effa`, `CHANGELOG.md:130-138` |
| WS3 Provenance | Collapsed `<details>` Sources | #60 `feat/sources-collapsed`, `CHANGELOG.md:186-192` |
| WS3 Provenance | Cap entity pages at 10 recent sources | #60, `CHANGELOG.md:188-191` |
| WS3 Provenance | Attachment placeholder in viewer | #48 `worktree-agent-a0872248`, `CHANGELOG.md:122-128` |
| WS4 Tools | `resolve_page` | #57 `feat/resolve-page`, `CHANGELOG.md:34-43` |
| WS4 Tools | `find_new_sources` + paginated DB query | #56 `feat/find-new-sources`, `CHANGELOG.md:78-84` |
| WS4 Tools | `write_draft_page` (replaces stub-as-recovery) | #63 `feat/drafts-folder`, `CHANGELOG.md:46-55` |
| WS4 Tools | Entity evidence gate | #67 `feat/entity-evidence-gate` |
| WS5 Legacy | Miscategorized humans in `systems/` → relocate | #52 `worktree-agent-acf0698f`, `CHANGELOG.md:96-106` |
| WS5 Legacy | Auto-stub cleanup | #68 `feat/auto-stub-cleanup` |
| WS6 Verification | Per-batch stall detection / `--batch-timeout` | #50 `feat/compile-batch-timeout`, `CHANGELOG.md:107-120` |
| WS6 Verification | CI-friendly quality metrics | #54 `feat/wiki-quality-metrics`, `CHANGELOG.md:25-33` |
| WS6 Verification | `--min-topic-ratio` release gate | #54, `CHANGELOG.md:30-33` |
| WS7 Observability | Per-tool-call telemetry → Postgres + JSONL fallback | #62 `feat/tool-call-logging`, `CHANGELOG.md:56-69` |
| WS7 Observability | `log_insight` tool | #61 `feat/log-insight`, `CHANGELOG.md:86-96` |
| WS7 Observability | Prompt-cache stats + per-batch stats | #42 `spike/prompt-caching`, `CHANGELOG.md:154-166` |

### Not shipped (still in Phase 1 scope)

| Workstream | Item | Evidence it's unshipped |
|---|---|---|
| WS1 Taxonomy | "No new person pages created under `systems/`" invariant | `scripts/validate_wiki.py` hard-errors on `systems/*.md` with `email:` populated (#52) — partially enforced but not the full "person not under systems" check. |
| WS2 Viewer nav | "Domain / Cluster landing pages" | `wiki/home.md:18` placeholder. No cluster pages. `wiki/topics/index.md` is a stub. |
| WS2 Viewer nav | Tag/facet views on MkDocs tags plugin | Tags plugin installed (`mkdocs.yml:94-96`) but no tag frontmatter emitted by compiler, no pages tagged. |
| WS2 Viewer nav | `changes/index.md` | Not created. `Changes: log.md` is the fallback (`mkdocs.yml:23`). |
| WS3 Provenance | "Build on existing MkDocs tags plugin for facets" | Not shipped. |
| WS3 Provenance | "Support inline citations for sensitive claims" | Not shipped. See `BACKLOG.md:2012-2067` for the plan. |
| WS3 Provenance | **Move `sources` out of YAML** (`knowledge-vs-index` direction) | Not shipped. `scripts/lint_wiki.py:37` still requires `sources` in frontmatter. Only rendering changed. |
| WS4 Tools | `wiki_find_similar_pages` | Not shipped. |
| WS4 Tools | `wiki_classify_page` | Not shipped. |
| WS4 Tools | `wiki_update_frontmatter` | Not shipped. |
| WS4 Tools | `wiki_update_related` | Not shipped. |
| WS4 Tools | `wiki_verify_quote` | Not shipped. (Persona fact-check audit flagged quote hallucination as an open issue, `audit-persona-factcheck-*`.) |
| WS4 Tools | `wiki_merge_pages` | Not shipped. Manual `scripts/merge_suffix_dupes.py` only. |
| WS4 Tools | `wiki_compact_entity_sources` | Not shipped. Cap is rendered-only (viewer-side), not stored. |
| WS4 Tools | `get_thread_context`, `get_person_context`, `find_people_involved`, `get_pages_for_source`, `get_references_for_page`, `find_related_pages`, `get_compile_health`, `get_recent_runs` | None shipped. `BACKLOG.md:508-530` identifies `get_page_summary` and `get_thread_context` as highest-leverage — still open. |
| WS5 Legacy | Merge duplicate pages (the 11 near-dupes from `audit-synthesis`) | Partially — some merged; the `-clean`, numeric-suffix, and `-v2` suffix dupes remain per `docs/audits/audit-20260413T081547Z.md:55-62`. |
| WS5 Legacy | Demote or hide thin stubs | Partial — auto-stub cleanup (#68) removed ~26 lint-created shells; thin compiler-authored stubs remain (cluster at `BACKLOG.md:196-209`). |
| WS5 Legacy | Rollup / landing page generation | Not shipped. |
| WS6 Verification | Hard-block duplicate canonical pages in primary nav | `scripts/validate_wiki.py` warns, doesn't block. |
| WS6 Verification | "Stub pages appear in top-level nav" CI gate | Not shipped. |
| WS6 Verification | Topic-to-entity ratio hard gate on main landing surfaces | `scripts/wiki_quality_metrics.py` emits the number; `--min-topic-ratio 0.3` gates CI. Currently 176/463 = 0.38 so it passes — but the metric doesn't cover landing surfaces. |
| WS6 Verification | Evidence-rendering broken gate | Not shipped. |
| WS7 Observability | `AgentMiddleware` migration (`wrap_tool_call`) | Not shipped. Callback handler only. Per `CHANGELOG.md:56-69` we went with callback + Postgres writes, not middleware. `BACKLOG.md:832-940` still proposes it. |
| — | Trivial-message filter (Phase 1 priority #5 in BACKLOG) | Not shipped. |
| — | Second-pass evergreen compilation | Not shipped (BACKLOG `:51-54`). |
| — | Ownership / teams pages from `message_participants` clustering | Not shipped. |

### Personas-doc items not in the Phase-1 plan but implied by #64

These landed via PR #64 but have no implementation:

- `navigation_role` frontmatter
- Ownership history sections on topic/system/policy pages
- Opinion-change / "How the View Changed" sections
- Capability page middle tier
- 8 named domain hubs
- Promotion criteria tooling

None have been lifted into `10-phase1-implementation-plan.md`'s scope.
Either they're cut from Phase 1 or they need to be folded in.

---

## 7. Recommended resolution

Opinionated. The goal is one canonical north-star artifact, a clean
Phase-1 plan that matches reality, and an archive pile for the rest.

### A. Rewrites

1. **Merge `07`, `09`, `10`, `11` into one Phase-1 plan doc**, call it
   `docs/phase-1-plan.md`. Keep the **`10-phase1-implementation-plan.md`**
   skeleton since it's the one tools and validators already match.
   Fold in:
   - Single unified page-type template set (use `10:293-362`)
   - Single north-star paragraph (use README:31-42 verbatim)
   - Append one new section "Reader scenarios" using the A–I list from `11:586-682`
   - Append one new section "What will NOT ship in Phase 1" naming:
     `navigation_role`, ownership-history-as-section, opinion-change
     section, capability tier, promotion criteria. Either promote these
     to Phase 2 or drop them.
   - Delete `09` and `11` after the merge (preserve via git history
     only).
   - `07-phased-delivery.md` becomes a one-page roadmap pointer that
     links to the merged plan.

2. **Rewrite `docs/issues/02-data-model.md`** to match the current 6
   page types + status values. Delete the stale "person, team, product,
   or system" definition at line 117.

3. **Rewrite `docs/issues/03-email-ingestor.md` and
   `04-wiki-compiler.md`** as Phase-0 completion records. Move
   the open-items lists out of issue docs into `BACKLOG.md`. Stop
   listing agent tool names that no longer exist.

4. **Trim `BACKLOG.md` aggressively.** It's 2,359 lines. Split into:
   - `docs/BACKLOG.md` — only the status dashboard (`:8-66`) + one
     paragraph per open theme. Target: ≤300 lines.
   - `docs/archive/backlog-investigations-2026-04.md` — everything
     else, timestamped. Not load-bearing.

5. **Rewrite `CLAUDE.md`** to match the current toolbelt — not the
   toolbelt from 2026-04-13. Replace the "NEVER call" list with a
   positive statement of what tools ARE available (`resolve_page`,
   `find_new_sources`, `log_insight`, `write_draft_page`,
   `create_entity`, `list_wiki_pages`, `list_uncompiled_emails`
   [operator-only], plus the 6 filesystem tools).

### B. Deletions

Move these to `docs/archive/` (dated filename preserved, linked from a
one-line index). They're historical, not load-bearing:

- `docs/reviews/audit-*-20260412T*.md` (5 files) — pre-Phase-1 audits.
- `docs/reviews/audit-persona-*-20260413T040000Z.md` (5 files) — merged
  into `audit-synthesis-20260413T040000Z.md`; keep the synthesis,
  archive the 5 originals.
- `docs/reviews/plan-24h-20260413T031700Z.md`,
  `docs/reviews/overnight-plan-20260412T210837Z.md` —
  dated plans, executed, superseded by `BACKLOG.md`.
- `docs/reviews/ecosystem-scan-20260412T214246Z.md`,
  `docs/reviews/deepagents-learnings-20260413T050000Z.md`,
  `docs/reviews/edit-tool-research-20260413T031839Z.md`,
  `docs/reviews/source-dedup-plan-20260413T000000Z.md` — research,
  archive.
- `docs/reviews/quality-trend-*.md`,
  `docs/reviews/systemic-quality-*.md`,
  `docs/reviews/improve-internal-*.md`,
  `docs/reviews/coherence-*.md` — all operational snapshots.
  Archive.
- `docs/reviews/auto-repair-plan-20260413T084042Z.md` — mostly
  executed; archive.

Keep as live reviews (worth referencing from the north star):

- `docs/reviews/codex-catalog-review-20260413T080000Z.md` — ongoing
  schema reference for the catalog.
- `docs/reviews/codex-priority-review-20260413T090000Z.md` — some
  items still open (notably the "delete `sources` from YAML"
  direction). Either execute or formally reject.
- `docs/reviews/knowledge-vs-index-20260413T032000Z.md` — unresolved
  decision about where `sources:` lives. Needs a yes/no.
- `docs/reviews/tool-audit-20260413T050000Z.md` — proposed
  `AgentMiddleware` migration + tool taxonomy. Feeds `10`'s Workstream 4.
- `docs/reviews/prompt-caching-20260413.md` — canonical record of
  model pool cache behavior.

### C. What the single north-star artifact should say

A one-page `docs/NORTH-STAR.md` (new file):

```
# Email Knowledge Base — north star

## What it is
An email-to-wiki compile pipeline for one IndiaMART mailing list
(marketplacelaunch@indiamart.com). Raw emails are immutable evidence.
Wiki pages are compiled knowledge. The output is browsed via MkDocs
on Cloud Run behind IAP.

## What it is NOT yet
- Not a team wiki (single user, one mailing list)
- Not live (backlog only; Gmail watch is Phase 2)
- Not searchable beyond lunr (QA is Phase 3)
- Not polished — entity pages still dominate the corpus

## What we optimize for
Four things, in order:
1. Trustworthy topic pages — decisions, metrics, rationale preserved.
2. Provenance without clutter — references layered, not dumped.
3. Topic-first navigation — hubs, rollups, glossary.
4. People pages as support — not the primary product.

## Acceptance bar: six-month-new-joiner test
A joiner starting in six months, reading this wiki for an hour,
should map the IndiaMART ecosystem (products, systems, ownership,
recent decisions) without prior context.

## Phase ladder
- Phase 0 — Working pipeline ✅
- Phase 1 — Real wiki ← CURRENT (plan: docs/phase-1-plan.md)
- Phase 2 — Live ingestion
- Phase 3 — Searchable / askable
- Phase 4 — Team-scale
```

Everything else links here.

### D. Concrete decisions this report asks for

1. **`navigation_role` frontmatter: ship it or cut it.** If ship:
   add to CLAUDE.md schema + validator + compiler prompt. If cut:
   delete the row from `11:188` and the domain-hub section.

2. **`sources:` in frontmatter: keep it or remove it.** `10:467-500`
   says keep. `codex-priority-review` PR4 says remove. Knowledge-vs-index
   says remove. Pick one and actually apply it to `lint_wiki.py`
   and `prompts.py`.

3. **Ownership history section: required, optional, or future.**
   `11:355-403` treats it as mandatory on every durable page. Zero
   current pages have it. Either prompt+validate OR demote to Phase 2.

4. **Domain hubs: where do they live.** `BACKLOG.md:52` says
   `wiki/domains/`. `11:188-204` says `topic` page with
   `navigation_role`. Pick one or neither.

5. **`02-data-model.md`: delete or rewrite.** It currently contradicts
   the runtime.

6. **`wiki/home.md` placeholder: scope it as a Phase-1 PR (PR #10
   in the `10:1172-1241` sequence is effectively unstarted).**
   Until it's a real home page, the Phase-1 Definition of Done
   cannot be met.

7. **Personas doc PR #64: decide retroactively whether its
   additions are in Phase 1 or deferred.** As merged, it neither
   matches existing tooling nor appears in `10`'s workstreams. The
   user's earlier concern — "issues earlier about north-star vs what
   Codex had written as the strategy" — maps directly here.

These seven decisions unblock everything else.

---

Report path: /tmp/north-star-reconciliation.md
