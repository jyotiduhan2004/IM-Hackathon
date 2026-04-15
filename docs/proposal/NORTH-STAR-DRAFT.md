# Email Knowledge Base — Final-State Proposal (Draft)

> **Status**: DRAFT awaiting decisions in §6.
> **Audience**: project owner (you) + Codex (next reviewer).
> **Source artifacts**: `docs/proposal/research/01-reconciliation-report.md`, `02-codex-contributions-audit.md`, `03-wiki-reader-experience-audit.md`. Every claim below is backed by one of these.

---

## TL;DR

We've shipped a working compile pipeline + viewer, but the project has **6 north-star statements**, **7 unresolved binary decisions**, and a corpus where **~80% of pages read like a filing cabinet**. The proposal: pick **either Codex's minimalism path OR commit tooling/testing to back the richer Persona doc** — you can't do both honestly. Then close Phase 1 with one canonical doc, ship the landing pages + glossary, demote entity pages, and call Phase 1 done. Live ingestion + multi-user is Phase 2.

---

## 1. What the project IS today (honest)

- **709 wiki pages**: 177 topics, 462 entities, 69 systems, 1 policy, **0 timelines, 0 conflicts** (despite both being first-class categories in every doc)
- **Reader-experience score: ~15-20% of the corpus would pass a "useful wiki" bar**
  - Topics: ~40% pass (larger ones synthesize well; smaller are stubs)
  - Entities: **0% pass** (every sampled entity is a filing cabinet — list of 80+ emails the person appeared in, zero prose)
  - Systems: ~5% pass (39% are sub-500-byte stubs that just say "Email: X@company.com")
- **Landing pages are placeholders** — `home.md`, `topics/index.md`, `systems/index.md` all explicit "(Placeholder — coming in a later PR)"
- **Live ingestion / search / chat: not shipped** (search is MkDocs lunr only)
- **Compile pipeline: solid** — per-tool telemetry, per-model attempt tracking with auto-exclusion guard, drift detection, post-batch validation
- **Viewer**: MkDocs-Material on Cloud Run behind IAP, gated to `indiamart.com` Workspace org

---

## 2. What you've said you want (this conversation + recent docs)

1. **"Company knowledge base"** → multi-user audience (currently single-user, single-list)
2. **"Always up to date"** → live ingestion is desirable but not the only path; daily backlog re-runs would also satisfy
3. **"Doesn't read like a filing cabinet"** → entity-domination + email-dump pattern is the failure mode
4. **"Wiki that is actually useful"** → pages should answer questions, not preserve emails
5. **"Progressive disclosure"** → TL;DR → details → sources hierarchy on every page

These are clear directional preferences. The research is to verify what we have vs what we want, not to figure out what we want.

---

## 3. The central tension (from Codex audit)

Codex's contribution pattern is consistently **minimalist + auditable**: small schema, sharp queue rules, explicit cleanup boundaries.

Your doc set (especially the personas doc, PR #64) is **richer**: 4 personas, 9 acceptance scenarios, 8 named domain hubs, `navigation_role` frontmatter, "Ownership History" sections, "How the View Changed" sections.

**Of all those richer concepts, ZERO have a validator check, prompt rule, or shipped page.** Codex would have rejected the scope expansion without implementation commitment.

You can pick one path or the other; doing both half-way is what's killing focus. This proposal picks **a hybrid that leans Codex-minimalist for Phase 1, with the richer concepts deferred to Phase 2 only IF we commit tooling for them**.

---

## 4. The 3-phase ladder we should commit to

### Phase 1 — Useful wiki for the owner *(close honestly, 2-4 weeks)*

Drop the "team-ready" framing for now. New bar: **you (or the on-call PM) can find any decision/state from the last 6 months in <2 minutes via the wiki, without grepping email**.

**Open work**:
- Ship `home.md`, `topics/index.md`, `systems/index.md` as real landing pages (curated, with freshness signals)
- Build the glossary (30-50 hand-written acronym/term definitions)
- Demote entity pages: entity-as-reference, not primary surface (hide from primary nav; reachable only via wikilinks from topic/system pages)
- Build 2-3 critical missing tools: `get_thread_context`, `get_page_summary`, `wiki_merge_pages`
- Resolve the 7 + 3 binary decisions in §6
- One canonical north-star doc; archive the rest

### Phase 2 — Always up to date + multi-user *(3-6 months out)*

**Bar**: 5-20 IndiaMART teammates can use the wiki daily without the owner present. Wiki reflects email state within 1 hour.

- Live ingestion (Gmail watch + Pub/Sub + FastAPI webhook)
- Multiple mailing lists (one ingest cursor per list, share catalog)
- Read-only viewer for the team behind IAP (already there — just open the door)
- Search upgrade (MkDocs lunr → semantic if needed)
- THIS is where personas-doc concepts (`navigation_role`, ownership history, domain hubs as first-class) get implemented if we still want them

### Phase 3 — Askable + writable *(6-12 months out)*

**Bar**: a teammate can ask "what's the current state of X?" and get a cited answer; manual edits flow back via PR.

- "Ask this wiki" interface (Postgres + LLM, citing wiki + raw)
- Manual edit workflow (markdown PRs against `wiki/`)
- Inline citations for sensitive claims
- Trust signals (page freshness, source reliability)

---

## 5. What we're explicitly NOT doing in Phase 1

- No live ingestion (cron-runs are fine)
- No multi-user (single-owner mode)
- No semantic search (MkDocs lunr is enough)
- No chatbot
- No domain hubs as first-class navigation (handwritten markdown hubs are fine)
- No `navigation_role` frontmatter
- No "Ownership History" required section (no current pages have it)
- No `AgentMiddleware` migration (callback handler is good enough)

---

## 6. Binary decisions blocking Phase 1 closure (10 of them)

I have a recommendation on each. Mark each ✅ accept, ❌ reject, or 🔁 different.

### From the reconciliation report

1. **`navigation_role` frontmatter** — ship it (validator + prompt) or cut it (delete from personas doc)?
   - **Recommendation: cut.** Zero pages have it; you can re-add when there's a validator + actual hubs to navigate.

2. **`sources:` in YAML frontmatter** — keep (current state) or remove (Codex direction)?
   - **Recommendation: keep but reduce.** Postgres remains the truth for catalog queries; YAML stays as audit trail. **Update prompt to NOT exhaustively populate** (one source per claim is enough — fixes one cause of the filing-cabinet pattern).

3. **Ownership-history section** — required, optional, or Phase-2-only?
   - **Recommendation: Phase 2 only.** Currently zero pages have it; making it required without backfill = mass validation errors.

4. **Domain hubs** — `wiki/domains/` (BACKLOG) or topic-pages-with-`navigation_role` (personas) or neither?
   - **Recommendation: `wiki/domains/`** with handwritten markdown (8 hubs from personas doc as starting set). No `navigation_role` frontmatter.

5. **`02-data-model.md`** — delete or rewrite?
   - **Recommendation: rewrite as 1-page reference** matching current 6 page types + status values.

6. **`wiki/home.md` placeholder** — scope as Phase-1 PR or defer?
   - **Recommendation: Phase 1, must-ship.** This is the discoverability bottleneck. Without it, Phase 1 DoD cannot be met.

7. **Personas PR #64 additions** — in Phase 1 or deferred?
   - **Recommendation: defer to Phase 2.** Strip personas doc to user-research summary; move all section/template/role concepts to a Phase-2 plan that ships only with implementation commitment.

### New from this audit pass

8. **Entity page demotion** (filing-cabinet root cause) — full demote (hide from primary nav) or surface-with-edit (require synthesis sentence at top)?
   - **Recommendation: full demote.** Entities don't appear in primary nav; reachable only via wikilinks from topic/system pages. Compiler stops creating entity pages with no synthesis (the evidence-gate guard from PR #67 was a start; tighten further).

9. **System page stub policy** — drop systems with body <500B and no inbound wikilinks, or auto-promote-to-stub-then-prune?
   - **Recommendation: prune.** 27 systems are sub-500B stubs. Either a real system gets a paragraph, or it's an alias that should be a wikilink target only (not a separate page).

10. **Phase 1 DoD honesty** — "owner-operated" or "team-ready browse"?
    - **Recommendation: owner-operated.** Codex's pessimism was correct; team-ready needs Phase 2's live ingestion + multi-user + landing pages that survive a real reader.

---

## 7. Doc + code consolidation plan (3 PRs)

### PR-A: Consolidate strategy docs

- **Create** `docs/NORTH-STAR.md` (1 page — the Phase ladder above, this section becomes the canonical statement)
- **Create** `docs/phase-1-plan.md` (use `10-phase1-implementation-plan.md` skeleton; mark shipped items; list remaining)
- **Rewrite** `02-data-model.md` (current 6 page types + status values; drop the stale "person, team, product, or system" definition)
- **Rewrite as Phase-0 records** `03-email-ingestor.md`, `04-wiki-compiler.md` (drop forward-looking lists; stop listing tools that no longer exist)
- **Trim** `BACKLOG.md` (2,359 → ≤300 lines; archive the rest under `docs/archive/backlog-investigations-2026-04.md`)
- **Update** `CLAUDE.md` (current toolbelt; remove stale NEVER list; replace with positive list of what tools ARE available)
- **Delete** `docs/issues/09-internal-wiki-structure.md` and `docs/issues/11-user-personas-and-knowledge-flows.md` (folded into Phase-1 plan + archived persona research)
- **Archive** ~15 dated review files under `docs/archive/reviews/`

### PR-B: Ship landing pages + glossary

- `wiki/home.md` — real curated home (top 10 topics + freshness signal + glossary link + recent changes)
- `wiki/topics/index.md` — domain-grouped TOC (use the 8 domain hubs as the top-level structure)
- `wiki/systems/index.md` — list of real systems (post-prune)
- `wiki/glossary.md` — 30-50 hand-written acronym/term definitions (IndiaMART-specific: ISQ, PNS, Lens, etc.)
- `wiki/domains/{buyer,seller,marketplace,search,trust,growth,ai,infra}.md` — 8 domain hubs from personas doc, handwritten

### PR-C: Tooling for the demote/prune

- `scripts/demote_thin_systems.py` — prune system pages <500B with no inbound wikilinks (one-shot, like the auto-stub cleanup)
- `scripts/wiki_quality_metrics.py` updates — add "useful-wiki score" via simple heuristic (sources_bytes/body_bytes ratio + presence of synthesis section)
- New tool `get_page_summary(slug)` — agent uses to fetch existing page state without re-reading file
- New tool `get_thread_context(thread_id)` — agent loads thread without re-querying messages
- Compiler prompt update — "DO NOT create entity pages without one-sentence synthesis"; "DO NOT exhaustively cite sources — one source per claim"

### Ordering
- PR-A first (decisions baked in)
- PR-B and PR-C in parallel after PR-A merges

---

## 8. Questions for you (the only blockers)

A. **Audience scope confirmation**: Phase 1 = single-user (you). Phase 2 = team (5-20 readers, behind IAP). Phase 3 = company-wide (potentially 100+). Right calibration?

B. **"Always up to date" timing**: Phase 2 has live Gmail watch. But — is daily backlog ingestion via cron something you want NOW (1 day of work) so the wiki stays fresh while Phase 2 cooks?

C. **Multi-mailing-list scope**: currently `marketplacelaunch@indiamart.com` only. Phase 2 supports N lists per user. Specific additional lists in mind, or design for arbitrary N?

D. **Codex collaboration model going forward**:
   - (a) Codex reviews this proposal, marks decisions in-line, opens PR with their proposed edits
   - (b) Codex takes ownership of one of PR-A/B/C end-to-end
   - (c) Codex stays reviewer-only on every change
   - I lean (a) for this proposal + (b) for PR-A specifically (Codex's strength is doc consolidation), with Claude (me) running PR-B and PR-C.

E. **"Six-month-new-joiner test" enforcement**: is this a real acceptance bar (with a periodic blind audit) or editorial guidance? If real, we need a quarterly process to schedule it.

---

## 9. Suggested Codex hand-off

After you decide on §6 + §8:

1. Codex reads this directory (`docs/proposal/`) — proposal + 3 research files give them full context
2. Codex marks each decision (✅ ❌ 🔁) in-line on this file via PR
3. We resolve remaining disagreements in PR review
4. Codex opens PR-A (consolidation) — this aligns the strategy docs with the picked path
5. Claude (me) opens PR-B (landing pages) + PR-C (tooling)
6. After PR-A merges, this proposal doc moves to `docs/NORTH-STAR.md` (or links to it) and the proposal directory archives

---

## 10. What stops once Phase 1 is closed

- 5 of the 6 north-star statements get archived (only `docs/NORTH-STAR.md` remains)
- The personas doc (`11-*`) gets archived as user research
- The wiki-IA doc (`09-*`) gets archived (folded into `phase-1-plan.md`)
- BACKLOG.md is no longer the priority dashboard — `phase-1-plan.md` is
- `docs/reviews/` becomes an archive only; new reviews go into PR descriptions
- We stop adding aspirational vocabulary without implementation commitment (the lesson from PR #64)

That's the proposal. Awaiting your marks on §6 + answers on §8.
