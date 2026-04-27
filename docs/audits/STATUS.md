# Audit status — living tracker

**Last verified**: 2026-04-28 · **Findings tracked**: 91 (across 30 source audits) · **Method**: see [Methodology](#methodology)

This is the consolidated answer to "have the audit findings actually been fixed, and what's left?". Source audits in `docs/audits/`, `docs/reviews/`, `docs/incidents/`, and `docs/archive/2026-04-15-pre-proposal/reviews/` get parsed into discrete findings, deduped across audits that flagged the same root issue, then verified against five independent channels.

The answer to "how do we make sure each finding never happens again" is the [Prevention scoreboard](#prevention-scoreboard) — every finding gets a regression-guard rating (Yes / Partial / None) pointing at a concrete validator / middleware / critique rule / prompt teaching / CI check.

The machine-readable companion is [`findings.jsonl`](./findings.jsonl) — it's the canonical source of truth; STATUS.md is the human-readable narrative built on top of it. Append a new finding to `findings.jsonl` and update STATUS.md to extend the tracker. (A render script is a follow-up; until it ships, the two must be kept in sync by hand — drift between them is a known risk and gets re-verified before each meaningful update.)

## TL;DR

| | Count | % |
|---|---|---|
| Done | 40 | 44% |
| Partial | 26 | 29% |
| Open | 22 | 24% |
| Won't-do | 3 | 3% |
| **Total** | **91** | **100%** |

**Headline**: 0 of the 11 S0 (blocker) and 31 S1 (structural) findings are fully Open. Every S0/S1 either fully shipped or is Partial — meaning the **forward-looking** fix landed (new compiles use the new behavior) but **legacy debt** on the existing corpus remains. All 22 fully-Open findings are S2 (quality, 16) or S3 (polish, 6).

The dominant gap pattern: the compiler is fixed; the wiki/ corpus that was produced before the fix is not. **Forward-looking vs backfill** is the right axis to plan against.

## Status × severity matrix

| Severity | Done | Partial | Open | Won't-do | Total |
|---|---|---|---|---|---|
| **S0** (blocker / data loss / corruption) | 8 | 3 | 0 | 0 | 11 |
| **S1** (structural / reader-facing wrongness) | 16 | 15 | 0 | 0 | 31 |
| **S2** (quality / friction) | 15 | 7 | 16 | 3 | 41 |
| **S3** (polish / nice-to-have) | 1 | 1 | 6 | 0 | 8 |
| **Total** | **40** | **26** | **22** | **3** | **91** |

## Key insights

1. **Heavy middleware investment paid off.** 12 middleware files in `src/compile/middleware/` (terminal-decision-guard, check-my-work-gate, chronological-scope, edit-payload-sanity, glob-narrowing, sibling-draft-check, same-thread-topic-guard, path-autoheal, entity-write-autoheal, read-file-truncation-hint, legacy-page-hint) plus the runtime critique pipeline cover most code-class findings. Of 91 findings, 48 (53%) have a concrete prevention guard.
2. **The depth gap is real.** The V12 50-compile deep audit (2026-04-23) found that prompt updates lifted *structure* (new sections, footnotes, concept-style H2s) but did not lift *content depth* (ownership, rollout state, decisions, cross-linking). Scorer mean climbed +1.08 vs pre-V12, judge mean only +0.4. The remaining open S2 cluster (F-064 through F-074) is this depth gap.
3. **Backfill is the unfinished half.** Forward-looking fixes shipped, but the existing 1035-page corpus carries the historical artifacts: 3,408 email-slug wikilinks across 296 topics (F-070), ~10 humans still in `systems/` (F-015), 5 specific dup-page pairs unconsolidated (F-013), 924 pages with legacy `sources:` frontmatter (F-017), 587/588 people pages still stubs by design (F-014).
4. **Prevention has gaps in the depth class.** 19 findings have *no* prevention guard. Of those, 12 are about page depth (owner / rollout / open-questions / scorer-vs-judge / persona-utility / nav-eval) and 7 are about platform housekeeping (qmd latency, qmd fixtures, cost ceiling, log-row UX). These are the next places a regression would land silently.
5. **Wiki landing pages still placeholder.** `wiki/home.md`, `wiki/topics/index.md`, `wiki/systems/index.md` are coordinator-rendered but show "No pages yet" because the top-pages-per-domain data isn't wiring into the cards (F-089). Until this lands, Phase-1 DoD is not met.
6. **All 11 S0 findings have at least partial fixes.** The original "data loss / corruption / silent crash" class is comprehensively addressed. The 3 S0 Partials (F-003 hallucination, F-010 schema misfit, F-011 pages-as-summary) are about *content quality*, not data integrity — the legacy debt is correctness in compiled prose, not lost or corrupted data.

## Prevention scoreboard

The "never happens again" question, answered per finding-class. Each row is a category of finding, with the regression-guard layer that prevents recurrence.

| Finding class | Guard layer(s) | Coverage | Example file |
|---|---|---|---|
| YAML frontmatter corruption | Strict parser + middleware payload sanity + critique mtime detection | **Yes** | `scripts/validate_wiki.py:_construct_mapping_strict`, `src/compile/middleware/edit_payload_sanity.py` |
| Compile-state crash / dual-write | Postgres state machine (claim/finish + FOR UPDATE SKIP LOCKED) | **Yes** | `src/db/messages.py::claim_next_message` |
| Worktree mount mismatch | Coordinator preflight | **Yes** | `scripts/compile_all.py::_preflight_mount_sanity` |
| LiteLLM transport errors (200-empty, 401, 4xx, 5xx) | `_is_model_unavailable_error` matcher + retry | **Yes** | `scripts/compile_all.py:1547` |
| Filesystem path leaks (`/raw/`, `/wiki/`, host paths) | path-autoheal middleware + slug-only resolve_page | **Yes** | `src/compile/middleware/path_autoheal.py` |
| Chronological scope leak (future-dated raws) | ChronologicalScopeMiddleware | **Yes** | `src/compile/middleware/chronological_scope.py` |
| Reviewer block on broken person-wikilinks | Demoted to warning + auto-stub fallback | **Yes** | `src/compile/critique.py:326-342` |
| Coordinator-only tools in agent toolbox | Excluded from `tools=[]` list (PR #206) | **Yes** | `src/compile/compiler.py:2458-2471` |
| Filing-cabinet H2s (Launch Announcement, QA Testing, …) | Critique anti-pattern check + scorer bad-list | **Yes** (warning) | `src/compile/critique.py:421` |
| Dated H2 sections | Critique check + lint backfill script | **Yes** (warning + backfill) | `scripts/fix_dated_h2_sections.py`, `src/compile/critique.py` |
| Same-thread duplicate topics | SameThreadTopicGuardMiddleware + sibling-draft-check | **Partial** — V12 audit confirms thresholds too loose | `src/compile/middleware/same_thread_topic_guard.py` |
| Domain-prefix mismatch | Post-run validator warning | **Partial** — not promoted to check_my_work blocker | `scripts/validate_wiki.py:902` |
| TL;DR adoption | None — prompt labels TL;DR as "Optional" | **None** | (gap) |
| Owner / DRI / rollout state on pages | None | **None** | (gap) |
| Open-question target dates | None | **None** | (gap) |
| Stale source coverage (newer raws on cited threads) | None | **None** | (gap) |
| Hallucinated quotes / facts not in cited raws | None — no substring-evidence validator | **None** | (gap) |
| qmd ranking regressions | No fixtures committed | **None** | (gap — fix is `tests/fixtures/qmd_spike/`) |
| Bug-shape pages forced through design-doc gate | No escape hatch in validator | **None** | (gap) |

**Prevention coverage**: 48 Yes (53%) · 24 Partial (26%) · 19 None (21%).

The 19 "None" guards are the regression-risk register — fixes that landed without a check that catches the next instance, plus open findings whose fix would itself be a guard. These are listed below in [Findings without prevention guards](#findings-without-prevention-guards).

## Top open findings (S2)

The 16 open S2 findings, roughly ranked by leverage × ease-of-fix. Numbers in `[F-NNN]` link into the [Findings table](#findings-table).

1. **[F-090] High-leverage Workstream-4 agent tools not shipped** — `wiki_find_similar_pages`, `wiki_classify_page`, `wiki_update_frontmatter`, `wiki_verify_quote`, `wiki_compact_entity_sources`, `find_people_involved`, `get_pages_for_source`, `get_references_for_page`, `find_related_pages`, `get_compile_health`, `get_recent_runs`. Each is a bite-sized agent tool that would cut several existing roundtrips. Highest near-term leverage on agent productivity.
2. **[F-066] Owner / DRI / stage / target_date frontmatter** — 319/326 topics lack `owner:` frontmatter. Single biggest gap for PM-style status lookups; one prompt rule + one validator field shipped together.
3. **[F-067] Current rollout / deployment state on pages** — judge feedback consistently flags "POC data from Dec 2025 but unclear if 100% rollout has happened". Add a `## Current state` requirement on launch-class topics.
4. **[F-073] z-ai/glm-5 tool-call outlier (25.8 avg)** — costs not justified by quality. Either drop from pool or cap tool calls per batch.
5. **[F-074] Cost ceiling 5-6x under full-queue requirement** — per-page $0.34 → ~$866 for full 5,348-email backlog vs $150 budget ceiling. Need top-up before scale.
6. **[F-058] Bug/incident pages forced through design-doc gate** — 5 bug pages in corpus get filler "Why it matters" sections forced on them. Add a length+H2-signature escape hatch.
7. **[F-068] Open questions lack target dates** — persona feedback flag. Prompt rule.
8. **[F-064] Scorer-judge correlation re-inverted on extremes** — meta-finding. Demote scorer to pre-filter, treat judge as primary signal.
9. **[F-065] V12 surface-vs-depth gap** — content-floor depth (ownership/rollout/decisions/cross-links) untouched.
10. **[F-045] Event-log voice leaks** — 'Vote of thanks to ...' / 'As of 2026-01-07: [[X]] approved ...' prose. Critique detector for named-attribution.
11. **[F-046] TL;DR adoption weak** — prompt labels TL;DR as "Optional"; flip to MUST.
12. **[F-069] Body `## Related` + frontmatter `related:` duplication** — 11/16 V12-audit pages had both. Critique warning.
13. **[F-084] qmd regression corpus not seeded as fixtures** — 77-query corpus exists at `docs/audits/qmd-spike-2026-04-23-queries.jsonl`, but `tests/fixtures/qmd_spike/` doesn't. Ranking-model bumps would regress silently.
14. **[F-070] Email-slug → canonical-name wikilink migration** — 3,408 instances of `[[*-indiamart-com]]` in 296 topics. Explicitly deferred to Tier 3 by the V12 audit.
15. **[F-043] Reviewer escalation paths defined but not invoked on new-page write_file** — V12 audit confirmed 4/17 batches had 0 reviewer cycles, including both new V12-shaped pages. Reviewer wired but not firing on the new-page path.
16. **[F-056] Backlinks ('Linked from' / 'pages that link here') break wiki-as-graph** — Person-page backlinks shipped (compiler.py:969 `_rebuild_person_backlinks`), but topic/system inverse-link generation not implemented. 0/50 sample topics have a `Linked from` section.

## Top open findings (S3)

The 6 open S3 findings, deferred:

- **[F-049]** `compiled-but-touches=0` log row is ambiguous (cited_prior not surfaced) — log-UX disambiguation
- **[F-076]** Scorer v2→v3 numeric delta mixes rubrics — re-run on pre-V12 snapshot deferred
- **[F-077]** Wikipedia-style random-walk navigation unevaluated — V12's primary north-star metric has no implementation; Phase-2 eval-suite item
- **[F-079]** V12-U3 footnote utility unmeasured (cited vs used) — instrumentation question
- **[F-080]** Scorer-judge alignment pipeline — proposal to run judge on scorer's top decile after each compile (~$9/run for 30 pages) not implemented
- **[F-091]** AgentMiddleware migration of legacy hand-rolled coordinator hooks (Phase 2 backlog)

## Partial findings — what would close them

The 28 Partial findings split into two clean clusters: **forward-looking fix shipped, legacy debt remains** (most), and **layered fix shipped but a final guard is missing** (some). The actionable ones:

- **[F-003]** Hallucination: forward-looking prompt + footnotes shipped; needs a substring-evidence validator that blocks fabricated quotes / list truncation. **Fix**: critique rule that checks every claim's footnote raw against substring presence.
- **[F-010]** Schema misfit: prompt + critique + reviewer all shipped; legacy 326-topic backfill unrun. **Fix**: one-shot rewrite pass over legacy topics OR accept that they're frozen.
- **[F-013]** Near-dups: detector + merge tooling shipped (`scripts/merge_suffix_dupes.py`, `scripts/apply_merge_candidate.py`); manual-merge backfill unrun on Lens, samarth, vikram, sahil-sharma, alok-kumar. (Note: `scripts/consolidate_duplicate_slug.py` from PR #154 was retired in PR #174 as a one-shot.) **Fix**: re-run merge tooling on these slugs + add Postgres UNIQUE constraint.
- **[F-014]** Stub padding: auto-stub disabled (forward-looking ✓); legacy systems/ stubs (61/101 = 60%) not pruned. **Fix**: min-body validator with hard-fail OR one-shot prune.
- **[F-015]** Humans in `systems/`: ~10 still there including `marketplace-launch.md` (134-inbound mailing list). **Fix**: re-run `scripts/audit_systems_entities.py` + add `@indiamart.com`-in-system-page validator.
- **[F-017]** Sources frontmatter: ~60% migration to `source_threads` incomplete. **Fix**: one-shot migration to drain the rest, OR strict-no-sources as default.
- **[F-023]** Empty `wiki/conflicts/` and `wiki/timelines/` dirs not deleted from disk. **Fix**: 1-line cleanup commit.
- **[F-024]** resolve_page: qmd shipped; specific legacy duplicates (Lens, etc.) cause it to surface stubs. Folds into F-013 cleanup.
- **[F-031]** Same-thread duplicate topics: V12 confirmed `sibling_draft_check` thresholds too loose. **Fix**: tighten threshold, validate against the seller-bl-api regression case.
- **[F-035]** Domain-prefix mismatch: validator catches it post-run; needs promotion into `check_my_work` as pre-write blocker.
- **[F-042]** Anti-pattern legacy H2s on existing pages: forward-looking critique catches new ones, but legacy 87 pages with thread-subject H2s never cleaned up. **Fix**: agent-led H2 rewrite pass OR backfill script.
- **[F-063]** Lazy-decision-page minting: prompt + warning shipped; no automatic stub materialization on inline `## Decision:` H2s. **Fix**: critique-action that mints the stub.
- **[F-089]** Wiki landing pages: home.md/topics/index.md/systems/index.md still placeholder. **Fix**: wire top-pages-per-domain data into the rendering layer.

## Findings without prevention guards

The 19 findings whose status is Open or Partial AND prevention is `None`. Adding a guard for each is the work to "make sure it never happens again":

| F-ID | Sev | Status | Title | What guard would close it |
|---|---|---|---|---|
| F-016 | S1 | Partial | Source coverage gaps — pages stale relative to newer same-thread raws | Lint check: `source_threads max date < newer raw on same thread` |
| F-045 | S2 | Open | Event-log voice and named-attribution prose leaks into wiki pages | Critique detector for `Vote of thanks` / `As of <date>:` patterns |
| F-046 | S2 | Open | TL;DR adoption weak | Validator rule: topic must have `## TL;DR` (or change prompt-label from Optional → MUST) |
| F-049 | S3 | Open | compiled-but-touches=0 log row is ambiguous | Add `cited_prior: bool` to coordinator batch row |
| F-058 | S2 | Open | Bug/incident pages forced through design-doc gate | Validator escape hatch: `<50 lines AND has 'Bug details'+'Impact' H2s` |
| F-064 | S2 | Open | Scorer-judge correlation re-inverted on extremes | Treat judge as primary signal, scorer as cheap pre-filter (architecture change) |
| F-065 | S2 | Open | V12 surface-vs-depth gap — content floor flat | Critique rules for ownership / rollout / target-date presence |
| F-066 | S2 | Open | Owner / DRI / PM frontmatter and content fields missing | Validator: warn on topic without `owner:` (combine with F-065/F-067) |
| F-068 | S2 | Open | Open questions lack target dates | Prompt rule + critique warning |
| F-069 | S2 | Open | Dup `## Related` body section + frontmatter `related:` | Critique warning when both present |
| F-073 | S2 | Open | z-ai/glm-5 tool-call outlier | Per-batch tool-call cap OR pool-config downweight |
| F-074 | S2 | Open | Cost ceiling 5-6x under full-queue requirement | Budget top-up + per-batch cost projection metric |
| F-076 | S3 | Open | Scorer v2→v3 numeric delta mixes rubrics | Apples-to-apples re-run on `.snapshots/pre-compile-20260423T102909Z/` |
| F-077 | S3 | Open | Wikipedia-style random-walk navigation unevaluated | Build hop-based eval (Phase-2 eval-suite item) |
| F-079 | S3 | Open | V12-U3 footnote utility unmeasured | Judge persona rubric scoring footnote usefulness |
| F-080 | S3 | Open | Scorer-judge alignment path | Pipeline gate: judge on scorer's top decile after each compile |
| F-081 | S1 | Partial | qmd p50 latency 12.34s — rerank unsplit | Per-stage Langfuse span attribute (rerank-latency) + alarm threshold |
| F-084 | S2 | Open | qmd regression corpus not seeded as fixtures | Seed `tests/fixtures/qmd_spike/` from `docs/audits/qmd-spike-2026-04-23-queries.jsonl` |
| F-090 | S2 | Open | High-leverage agent tools not shipped (Workstream-4) | One PR per tool — each ships a guard for its own use case |

## Per-audit roll-up

How each source audit's findings have resolved. `D` = Done, `P` = Partial, `O` = Open, `W` = Won't-do.

| Audit | Total | D | P | O | W |
|---|---|---|---|---|---|
| v12-50-compile-deep-audit (2026-04-23) | 20 | 5 | 3 | 12 | 0 |
| langfuse-trace-audit (2026-04-15) | 7 | 5 | 2 | 0 | 0 |
| cycle-10-smoke-30 | 6 | 1 | 2 | 3 | 0 |
| v12-north-star (2026-04-19) | 6 | 4 | 2 | 0 | 0 |
| north-star-reconciliation | 6 | 1 | 1 | 2 | 2 |
| phase0-bootstrap-incident (2026-04-13) | 5 | 5 | 0 | 0 | 0 |
| tool-audit (2026-04-13) | 5 | 3 | 2 | 0 | 0 |
| cycle-9-summary | 4 | 3 | 1 | 0 | 0 |
| qmd-phase0-spike (2026-04-23) | 4 | 1 | 1 | 1 | 1 |
| audit-synthesis (5-persona blind, 2026-04-13) | 3 | 0 | 3 | 0 | 0 |
| topic-archetypes (2026-04-18) | 3 | 1 | 1 | 1 | 0 |
| error-classes-autopsy (2026-04-15) | 2 | 2 | 0 | 0 | 0 |
| cycle-4-case-1 (SEO recursion) | 2 | 1 | 1 | 0 | 0 |
| audit-persona-newbie | 2 | 1 | 1 | 0 | 0 |
| cycle-4-summary | 2 | 2 | 0 | 0 | 0 |

The remaining 14 audits each contributed 1 finding apiece (cycle-5-summary, cycle-6-summary, cycle-7-summary, cycle-8-summary, deep-audit-10-pages, codex-catalog-review, codex-priority-review, knowledge-vs-index, prompt-caching, audit-persona-{factcheck, ia, pm}, cycle-10-deep-audit, cycle-5-case-bug-j, cycle-4-case-2, cycle-8-case-studies). All resolved or in flight.

**Read-out**: V12 deep audit dominates open work — that's expected; it's the most recent + most rigorous audit. Phase-0 bootstrap and error-class autopsy are clean (5/5 and 2/2 Done) — early infra issues fully resolved. Cycle 4–9 summaries are mostly Done (each 1 audit, mostly closed) — the cycle-by-cycle bug catalog is largely a fix-and-forget history.

## Findings table

The full per-finding view, sorted severity desc → status (Open first within each severity) → ID. `Prev` column: `Y` Yes, `P` Partial, `—` None, `?` Unknown.

| ID | Sev | Status | Prev | Title | Source audit | Gap |
|---|---|---|---|---|---|---|
| F-003 | S0 | Partial | P | Hallucination, fabricated quotes, date drift, truncated lists, silent normalization | audit-persona-factcheck-2026-04-13 | Forward-looking prompt + footnotes shipped; lacks substring-evidence validator |
| F-010 | S0 | Partial | Y | Validator enforces v9-U1 8-H2 schema that 0/303 organic topic pages match | cycle-9-summary | Forward-looking shipped; legacy 326-topic backfill unrun |
| F-011 | S0 | Partial | Y | Pages-as-summary framing produces filing-cabinets, not concept pages | cycle-4-case-1-seo-rework | Forward-looking concept-vs-thread reframe shipped; legacy debt drained but residual |
| F-001 | S0 | Done | Y | Boolean is_compiled queue cannot survive crashes | codex-catalog-review-2026-04-13 | State machine fully shipped (PR #132 + state-machine + FOR UPDATE SKIP LOCKED) |
| F-002 | S0 | Done | Y | YAML frontmatter corruption from edit_file destroys pages and goes undetected | phase0-bootstrap-incident | Strict YAML loader + payload-sanity middleware + critique fallback all in place |
| F-004 | S0 | Done | Y | Compile stalls on bloated entity pages (>20KB context blow-up) | phase0-bootstrap-incident | Sources moved out of frontmatter; entity pages bounded |
| F-005 | S0 | Done | Y | Worktree view-root mismatch crashes compile silently | error-classes-2026-04-15-autopsy | Coordinator preflight in place |
| F-006 | S0 | Done | P | find_new_sources psycopg3 placeholder crash on subject_contains | error-classes-2026-04-15-autopsy | Fix is in code; no specific test guarding the wildcard binding shape |
| F-007 | S0 | Done | Y | LiteLLM 200-empty / 401 / 403 / 5xx silent failures | cycle-5-case-bug-j-minimax-silent-fail | All four error shapes now caught and classified |
| F-008 | S0 | Done | Y | log_insight orphan skips drop email_path | cycle-4-case-2-orphan-skip-insights | Tool contract enforces email_path with autoheal fallback |
| F-009 | S0 | Done | Y | Bug H — chronological scope leak via greedy thread reading | cycle-4-case-1-seo-rework | Cutoff enforced via middleware; prompt + test guards in place |
| F-013 | S1 | Partial | P | Near-duplicate / cross-category slug collisions slip past detector and validator | audit-synthesis-2026-04-13 | Detector + merge tooling shipped; manual-merge backfill not run |
| F-014 | S1 | Partial | Y | Auto-stub and stub padding launder structural gaps as content | audit-synthesis-2026-04-13 | Auto-stub disabled; legacy systems/ stubs (61/101) not pruned |
| F-015 | S1 | Partial | P | Category mislabeling: humans in systems/, products in entities/ | audit-synthesis-2026-04-13 | ~10 humans/mailing lists still in systems/. Missing classify_page_category tool |
| F-016 | S1 | Partial | — | Source coverage gaps — pages stale relative to newer same-thread raws | audit-persona-newbie-2026-04-13 | No general staleness detector; specific symptom (whatsapp9696) fixed |
| F-017 | S1 | Partial | P | Sources frontmatter bloat couples knowledge to provenance — direction unresolved | knowledge-vs-index-2026-04-13 | ~60% migration incomplete. No SQLite-catalog rendering layer |
| F-023 | S1 | Partial | Y | Compiler prompt teaches dropped page types and entity-first workflow | langfuse-trace-audit-2026-04-15 | Prompt fully cleaned; 2 empty legacy dirs not deleted |
| F-024 | S1 | Partial | Y | resolve_page under-recalls and ranks alphabetically | qmd-phase0-spike | qmd shipped; legacy duplicates not consolidated |
| F-030 | S1 | Partial | Y | edit_file accepts oversized payloads and corrupts prose | tool-audit-2026-04-13 | Forward-fixed via middleware; legacy corruption not yet repaired |
| F-031 | S1 | Partial | P | Same-thread duplicate / sibling near-duplicate topic pages slip past in-batch dedupe | cycle-8-case-studies-20260417 | Two layers shipped but V12 confirms thresholds too loose |
| F-035 | S1 | Partial | P | Domain-prefix mismatch ships to disk and validator has false negatives | cycle-10-smoke-30 | Validator catches post-run; not yet a check_my_work blocker |
| F-042 | S1 | Partial | Y | Anti-pattern thread-leakage H2s and bad-list legacy H2s never cleaned up | cycle-4-case-1-seo-rework | Forward critique warns mid-batch; legacy backfill not implemented |
| F-054 | S1 | Partial | P | New-joiner is not the primary-reader anchor in prompt | v12-north-star | Single-mention only; no reader-anchor section in critique |
| F-063 | S1 | Partial | P | Lazy-decision-page minting not happening on inline ## Decision: H2s | v12-50-compile-deep-audit | Warning + prompt rule shipped; no automatic stub materialization |
| F-081 | S1 | Partial | — | qmd p50 latency 12.34s — rerank-latency unsplit | qmd-phase0-spike | qmd integration shipped; rerank latency unsplit in observability |
| F-089 | S1 | Partial | P | Wiki landing pages still placeholders | north-star-reconciliation | Coordinator hook exists, content rendering broken/empty |
| F-012 | S1 | Done | Y | mark_as_compiled / coordinator-only tools in agent toolbox (citation-evidence trust) | tool-audit-2026-04-13 | Trust boundary closed; coordinator owns flips with citation evidence |
| F-018 | S1 | Done | Y | Dual-write drift between raw markdown and Postgres queue | codex-priority-review-2026-04-13 | Catalog is single source of truth; raw frontmatter no longer mutated |
| F-019 | S1 | Done | P | Deep Agents virtual filesystem silently swallowed writes | phase0-bootstrap-incident | One-line config fix shipped |
| F-020 | S1 | Done | Y | create_entities forces model to restate coordinator-known raw_paths | langfuse-trace-audit-2026-04-15 | Coordinator injects raw_paths via ContextVar |
| F-021 | S1 | Done | Y | Filesystem scoping too loose — agent uses absolute and foreign-worktree paths | langfuse-trace-audit-2026-04-15 | Forward-fixed via autoheal middleware + slug-only resolve_page |
| F-022 | S1 | Done | Y | log_insight unused / leading-slash / orphan-path issues | langfuse-trace-audit-2026-04-15 | Heavily prompted; terminal-decision middleware accepts log_insight |
| F-025 | S1 | Done | Y | Agent bypasses entity tooling and writes entity pages directly | langfuse-trace-audit-2026-04-15 | Hint-only middleware + prompt forbids write_file for entities |
| F-026 | S1 | Done | Y | create_entity overused vs write_draft_page underused | langfuse-trace-audit-2026-04-15 | Prompt reframed concept-first |
| F-029 | S1 | Done | Y | TPM rate limits cause mid-edit page corruption | phase0-bootstrap-incident | Tracing wired and used by V12 audit |
| F-032 | S1 | Done | Y | Per-message terminality miss on low-signal follow-ups | cycle-7-summary | Prompt aggressive about already_captured; terminal-decision middleware |
| F-033 | S1 | Done | Y | Bug E — reviewer blocks on broken person-wikilinks | cycle-4-summary | Prompt teaches recovery; reviewer demotes person-slug to warning |
| F-034 | S1 | Done | Y | Orphaned compile / silent batch exit (kimi batch 45) | v12-50-compile-deep-audit | Runtime-enforced via terminal_decision_guard |
| F-036 | S1 | Done | Y | Glob timeouts on **/<slug>.md fuzzy lookups | cycle-9-summary | Slug-shaped glob patterns blocked at middleware |
| F-038 | S1 | Done | Y | Observability scripts lag runtime architecture | cycle-8-summary-20260417 | Audit script aligned to current tool surface |
| F-044 | S1 | Done | Y | Pages lack lead paragraph (Wikipedia-style 2-sentence opener) | cycle-10-smoke-30 | Validator warning + prompt teaching shipped |
| F-050 | S1 | Done | Y | Update-time Summary rewrite not enforced | v12-50-compile-deep-audit | Both warning + blocker tiers shipped |
| F-051 | S1 | Done | P | Strikethrough and deletion violate no-vanishing rule | v12-north-star | Prompt teaching shipped; corpus shows compliance is high |
| F-052 | S1 | Done | Y | 5W+IndiaMart hidden-curriculum questions absent from prompt | v12-north-star | Prompt teaching shipped at v12-U2; behavioral lift visible |
| F-053 | S1 | Done | Y | Per-section sourcing enables filing-cabinet failure mode | v12-north-star | Footnote teaching + def-completeness check both shipped |
| F-059 | S1 | Done | Y | Reviewer-gate middleware not firing on new-page write_file | v12-50-compile-deep-audit | Middleware now requires check_my_work after every content write |
| F-060 | S1 | Done | Y | Recurring anti-pattern H2s caught only by scorer | v12-50-compile-deep-audit | Both warning and blocker tiers shipped |
| F-061 | S1 | Done | Y | Sibling-draft-check middleware not catching slug churn | v12-50-compile-deep-audit | Middleware prevents normalized=0 silent exits |
| F-062 | S1 | Done | Y | Open-question footnotes don't resolve to References definitions | v12-50-compile-deep-audit | Promoted from scorer heuristic to critique blocker |
| F-071 | S1 | Done | Y | Title Case wikilinks broke 210+ cross-references | phase0-bootstrap-incident | Lint normalizes drift; prompt + draft tool enforce kebab-case |
| F-027 | S1 | Partial | P | Coordinator-only tools still exposed in agent toolbox | tool-audit-2026-04-13 | Agent surface clean; @tool retained for scripts; update_wiki_index split not done |
| F-040 | S1 | Partial | P | LiteLLM model allowlist + bad model variants fail batches without retry | cycle-6-summary | Retry classification shipped; outcome enum lacks infrastructure_error tag |
| F-041 | S1 | Partial | Y | Validator backlog: legacy entities/index, legacy-sources-only, frontmatter housekeeping | cycle-5-summary | Backfill scripts shipped; legacy debt residual |
| F-043 | S2 | Open | P | Reviewer escalation paths defined but not invoked on new-page write_file | cycle-10-smoke-30 | Reviewer wired but V12 confirms not invoked on new-page path |
| F-045 | S2 | Open | — | Event-log voice and named-attribution prose leaks into wiki pages | cycle-10-smoke-30 | No event-log voice detector; synthesis depth gap remains |
| F-046 | S2 | Open | — | TL;DR adoption weak despite plumbing in place | cycle-10-smoke-30 | Prompt labels TL;DR Optional; no critique rule |
| F-058 | S2 | Open | — | Bug/incident pages forced through design-doc gate | topic-archetypes | Validator/critique still applies design-doc gate; escape hatch missing |
| F-064 | S2 | Open | — | Scorer-judge correlation re-inverted on extremes | v12-50-compile-deep-audit | Path-forward not implemented; architecture unchanged |
| F-065 | S2 | Open | — | V12 surface-vs-depth gap — content floor flat | v12-50-compile-deep-audit | Structure raised; depth markers untouched |
| F-066 | S2 | Open | — | Owner / DRI / PM frontmatter and content fields missing | v12-50-compile-deep-audit | PM-fields gap unaddressed |
| F-067 | S2 | Open | P | No current rollout / deployment state on pages | v12-50-compile-deep-audit | Scorer rewards rollout-state language but no critique block requiring it |
| F-068 | S2 | Open | — | Open questions lack target dates/timelines | v12-50-compile-deep-audit | Persona feedback acknowledged; no prompt/critique change |
| F-069 | S2 | Open | — | Dup `## Related` body section + frontmatter related: on 11/16 pages | v12-50-compile-deep-audit | No critique warning for body+frontmatter Related dup |
| F-070 | S2 | Open | P | Email-slug wikilinks instead of canonical-name slugs (3408 instances) | v12-50-compile-deep-audit | Migration explicitly deferred to Tier 3 |
| F-073 | S2 | Open | — | z-ai/glm-5 tool-call outlier (25.8 avg) without quality justification | v12-50-compile-deep-audit | Pool reorganized for health; outlier cost not addressed |
| F-074 | S2 | Open | — | Cost ceiling 5-6x under full-queue requirement | v12-50-compile-deep-audit | No budget top-up; per-page cost optimization not shipped |
| F-080 | S3 | Open | — | Scorer-judge alignment path: judge as primary signal | v12-50-compile-deep-audit | Judge runs ad-hoc; no automated judge-on-top-decile |
| F-084 | S2 | Open | — | qmd regression corpus not yet committed as fixtures | qmd-phase0-spike | Spike corpus never seeded; ranking-model bumps would regress silently |
| F-090 | S2 | Open | — | High-leverage agent tools not shipped (Workstream-4 backlog) | north-star-reconciliation | Only resolve_page + get_thread_context shipped; rest backlog |
| F-077 | S3 | Open | — | Wikipedia-style random-walk navigation unevaluated | v12-50-compile-deep-audit | Phase-2 eval-suite item; not built |
| F-079 | S3 | Open | — | V12-U3 footnote utility unmeasured (cited vs used) | v12-50-compile-deep-audit | Judge persona rubric does not score footnote utility |
| F-076 | S3 | Open | — | Scorer v2->v3 numeric delta mixes rubrics — direction solid, magnitude approximate | v12-50-compile-deep-audit | Pre-V12 snapshot rescoring deferred |
| F-049 | S3 | Open | — | compiled-but-touches=0 log row is ambiguous (cited_prior not surfaced) | cycle-10-deep-audit | Coordinator records touches_inserted but not cited_prior |
| F-091 | S3 | Open | P | AgentMiddleware migration deferred — callback-only telemetry | north-star-reconciliation | Phase-2; new middleware uses pattern, legacy hooks unmigrated |
| F-082 | S2 | Won't-do | P | qmd weak on bare numbers / code identifiers / timestamps | qmd-phase0-spike | Decision: keep grep complementary. No code fix expected |
| F-085 | S2 | Won't-do | Y | navigation_role frontmatter specified but not enforced | north-star-reconciliation | Decision: cut. Documented rejection in NORTH-STAR |
| F-088 | S2 | Won't-do | Y | Ownership-history / opinion-change sections unenforced | north-star-reconciliation | Decision: demote. NORTH-STAR explicitly rejects forced sections |
| F-028 | S2 | Done | Y | list_wiki_pages too thin, forcing extra read_file roundtrips | tool-audit-2026-04-13 | Dual-format response per Anthropic best practice |
| F-037 | S2 | Done | Y | Reviewer emits merge_candidates but no merge tool exists | cycle-9-summary | Coordinator-driven queue + manual apply script + integrity fix |
| F-039 | S2 | Done | Y | Scorecard effective-rate denominator underflow (160% bug) | cycle-9-summary | Scorecard caps denominator and skips when count is None |
| F-047 | S2 | Done | Y | MD024 duplicate-H2 leakage past in-batch critique | cycle-10-smoke-30 | Pre-write blocker via duplicate-h2 critique |
| F-048 | S2 | Done | P | Legacy ## Sources body section + missing source_threads citations | deep-audit-10-pages | Validator surfaces legacy-sources-only as warning; strict via flag |
| F-055 | S2 | Done | P | Trivial-skip filter is length-based, missing concept-value signal | v12-north-star | Filter exists as backfill; not wired into live ingest path |
| F-057 | S2 | Done | Y | Reviewer flags coherent alternative shapes as non-compliant | topic-archetypes | Reviewer rule loosened to accept any coherent alternative shape |
| F-072 | S2 | Done | P | Scorer summary_currency heuristic doesn't catch weak summaries | v12-50-compile-deep-audit | Token list expanded but heuristic still fires on bad-token presence only |
| F-078 | S2 | Done | Y | Model pool quality differentiation underpowered | v12-50-compile-deep-audit | Head-to-head A/B script shipped post-audit |
| F-083 | S2 | Done | Y | Snippet field not surfaced in resolve_page response | qmd-phase0-spike | Snippet now passed through resolve_page envelope when semantic fires |
| F-086 | S2 | Done | Y | Domain hubs unspecified — directory vs frontmatter conflict | north-star-reconciliation | Directory-route chosen; hub generator coordinator-owned |
| F-087 | S2 | Done | Y | Glossary pages mentioned in 4 docs, zero in corpus | audit-persona-newbie-2026-04-13 | Glossary delivered; 100+ acronyms |
| F-075 | S2 | Partial | P | 3 pages have 0 inline footnotes despite V12-U3 teaching | v12-50-compile-deep-audit | Lint catches missing definitions but does NOT enforce minimum N footnotes |
| F-056 | S2 | Open | P | Backlinks ('Linked from' / 'pages that link here') break wiki-as-graph | v12-north-star | Person-page backlinks shipped; topic/system not implemented |

## Tracking + improvement loop

**Where opens live going forward**:
- This doc (`docs/audits/STATUS.md`) — the human-readable rollup. Updated when a finding's status changes.
- `docs/audits/findings.jsonl` — the canonical, machine-readable record. Append-only. Adding a new audit means appending findings here.
- `docs/BACKLOG.md` — phase-aligned roadmap. Top-N open S2/S3 from this doc bubble up to BACKLOG when prioritized.
- `docs/feedback/` — page-level critique rollups, append-only per run, flow into the Postgres `page_feedback` table.
- Postgres `page_feedback` and `compile_runs` tables — live metric source for cycle-by-cycle quality trends.

**Cadence**:
- **Per batch**: `docs/audits/critique-*.md` and `docs/audits/broken-wikilinks-*.md` are auto-generated by the post-batch hook. Routine; not enumerated here. Trends should go to a dashboard.
- **Per cycle (~weekly)**: cycle summary doc + 50-trace audit (`scripts/audit_50_traces.py`).
- **Ad-hoc**: deep audits like the V12 50-compile one (2026-04-23) on major prompt revs. Trigger: new prompt version (V11→V12) lands AND has compiled ≥30 pages.
- **Nightly** (intended): `scripts/nightly_trace_audit.py` is wired but no `docs/audits/nightly-*.json` artifacts seen — needs to be set up as cron / launchd to actually run.

**How to keep this doc fresh**:
1. When a new audit is filed, extract findings into `findings.jsonl` with the schema below.
2. When a finding changes status (e.g. PR ships that addresses it), update the corresponding line in `findings.jsonl` AND the matching row + summary counts in STATUS.md.
3. The two must be kept in sync by hand until a render script is built. (Keeping `findings.jsonl` as the source of truth and re-deriving the summary tables before each update is the safer path.)
4. Quarterly: re-verify Open findings haven't drifted (a fix was reverted) and Partial findings haven't quietly become Open again. Re-run the 5-channel verification.

**What to add**: when a new finding doesn't fit any audit, file it as a one-line entry under a new audit name (e.g. `ad-hoc-2026-05-DD`) — every finding has provenance.

**Schema** (`findings.jsonl`):

```json
{
  "id": "F-NNN",
  "title": "short canonical name",
  "severity": "S0|S1|S2|S3",
  "description": "2-4 sentence consolidated description",
  "source_findings": ["A1-XX"],
  "source_audits": ["audit-name-YYYY-MM-DD"],
  "fix_shape": "what kind of fix solves this",
  "verify_methods": ["code", "wiki", "email", "trace"],
  "tags": ["taxonomy"],
  "status": "Done|Partial|Open|Won't-do|Unknown",
  "evidence_code": "PR #N or path",
  "evidence_wiki": "current state",
  "evidence_email": "current state",
  "evidence_trace": "current state",
  "prevention_status": "Yes|Partial|None",
  "prevention_where": "concrete file/PR/none",
  "gap_notes": "1-2 sentences on what's still open"
}
```

## Methodology

Each finding was verified against five independent channels; status is the **worst** of all applicable channels:

1. **code** — search merged PRs (200 since 2026-04-13), git log, and current source. Did the named fix actually land?
2. **wiki** — does the symptom still appear in current `wiki/`? Spot-check specific slugs / patterns.
3. **email** — does `raw/` or the messages table look better? Skip if not applicable.
4. **trace** — does live agent behavior reflect the fix? The V12 50-compile audit (2026-04-23) is the latest live trace data available; if that audit re-flagged the issue → still Open in trace channel.
5. **prevention** — is there a regression-guard (CI check, validator rule, critique rule, middleware, prompt rule) that prevents recurrence?

**Status decision rule**: Done = all channels verify; Partial = some channels verify, others show residual issues (e.g. forward-looking shipped, legacy debt remains; or fix landed but no prevention guard); Open = no fix or fix ineffective; Won't-do = explicitly rejected; Unknown = insufficient evidence.

**Baseline artifacts** captured during verification (intermediate, not committed):
- 200 merged PRs since 2026-04-13 (titles + dates)
- Wiki state metrics (stub rate by directory, near-dup spot-checks, persona-finding spot-checks)
- BACKLOG.md and CHANGELOG.md state

The dedupe pass consolidated 136 raw findings (extracted from 25 canonical audits across 30 source docs) into 91 unique findings. Largest merges:
- F-003 (5-way hallucination/factcheck symptoms)
- F-010 (5-way schema-drift / v9-U1 misfit, escalated to S0)
- F-024 (5-way resolve_page weakness)
- F-007 (4-way LiteLLM transport failures)
- F-042 (4-way anti-pattern thread-leakage H2s)

## References — source audits

Canonical audits whose findings flow into this tracker. Listed by finding-count contribution.

- [`docs/audits/v12-50-compile-deep-audit-2026-04-23.md`](./v12-50-compile-deep-audit-2026-04-23.md) — 24 findings
- [`docs/audits/langfuse-trace-audit-20260415T175816Z.md`](./langfuse-trace-audit-20260415T175816Z.md) — 10 findings
- [`docs/audits/v12-north-star-2026-04-19.md`](./v12-north-star-2026-04-19.md) — 9 findings
- [`docs/proposal/research/01-reconciliation-report.md`](../proposal/research/01-reconciliation-report.md) — 9 findings
- [`docs/audits/cycle-10-smoke-30-2026-04-18.md`](./cycle-10-smoke-30-2026-04-18.md) — 7 findings
- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-synthesis-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-synthesis-20260413T040000Z.md) (5-persona blind, see also [`docs/reviews/persona-blind-audit-2026-04-13.md`](../reviews/persona-blind-audit-2026-04-13.md)) — 6 findings
- [`docs/audits/cycle-9-summary.md`](./cycle-9-summary.md) — 6 findings
- [`docs/incidents/2026-04-13-phase0-bootstrap.md`](../incidents/2026-04-13-phase0-bootstrap.md) — 5 findings
- [`docs/audits/deep-audit-10-pages-2026-04-17.md`](./deep-audit-10-pages-2026-04-17.md) — 5 findings
- [`docs/reviews/tool-audit-20260413T050000Z.md`](../reviews/tool-audit-20260413T050000Z.md) — 5 findings
- [`docs/audits/qmd-spike-2026-04-23.md`](./qmd-spike-2026-04-23.md) — 5 findings
- [`docs/audits/topic-page-structure-archetypes-2026-04-18.md`](./topic-page-structure-archetypes-2026-04-18.md) — 4 findings
- [`docs/audits/error-classes-2026-04-15-autopsy.md`](./error-classes-2026-04-15-autopsy.md) — 3 findings
- [`docs/audits/cycle-10-deep-audit-2026-04-18.md`](./cycle-10-deep-audit-2026-04-18.md) — 3 findings
- 5 individual persona audits (newbie, PM, IA, factcheck, journalist) at [`docs/archive/2026-04-15-pre-proposal/reviews/audit-persona-*.md`](../archive/2026-04-15-pre-proposal/reviews/) — 5 findings combined
- Cycle 4–8 summaries and case studies — 9 findings combined
- Codex catalog/priority reviews + knowledge-vs-index — 3 findings combined
