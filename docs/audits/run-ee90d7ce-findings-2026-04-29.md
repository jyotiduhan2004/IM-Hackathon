---
title: "Smoke ee90d7ce — consolidated findings (round 3)"
run_id: ee90d7ce-2225-405b-8a07-eedd8f997159
audit_date: 2026-04-29
audited_at_emails_compiled: 102 / 281 (mid-run)
commit: a52672a (post #259/#260/#261/#262/#281/#282/#283/#284)
prior_findings: docs/audits/run-8fa45533-findings-2026-04-29.md (PR #285)
---

# Smoke ee90d7ce — consolidated audit findings

This is the 3rd audit round. 5 parallel audits ran (page-quality+
boyscout, dual-persona, LLM judge, Langfuse trace, cross-run
synthesis). 0 fatal failures across 102 emails compiled. Re-prioritises
PR #285's 10-finding punch list against fresh evidence and adds 4
new findings.

## Run baseline (4-run trend)

| | 3e88f996 | 5928c151 | 8fa45533 | ee90d7ce |
|---|---|---|---|---|
| commit | pre-#259 | post-#259/#260 | + #261 refactor | + #281/#282/#284 |
| compiled @ audit | 153 | 84 | 65 | 102 |
| **fatal failures** | n/a | 5 | 0 | **0** ✅ |
| cost / email | $0.086 | $0.107 | $0.092 | **$0.077 net** ✅ |
| p50 latency / batch | n/a | ~7m | ~5.5m | ~5.2m |
| p95 latency / batch | n/a | 12.1m | 9.7m | similar |
| read:write ratio | 3.47:1 | 2.75:1 | 1.79:1 | similar |
| heuristic mean | 7.76 | 7.67 | 7.65 | **7.52** (saturated) |
| LLM-judge mean | n/a | 7.13 (n=5) | 5.93 (n=5) | **6.88 (n=8)** |
| heuristic ⊥ judge r | n/a | −0.758 | +0.518 | **+0.401** |
| director-forwardable | 0/9 | 8/8 | 8/8 | **8/10** ⬇ |
| PM mean | 1.0 | 2.13 | 2.50 | 2.30 |
| PM=3 ceiling | 0/9 | 1/8 | 4/8 | **5/10** ✅ |
| ramp-ready (Eng≥2) | 0/9 | 4/8 | 6/8 | **6/10** flat % |
| Eng mean | 1.0 | 1.50 | 1.88 | **1.50** ⬇ |
| Eng=3 ceiling | 0/9 | 0/9 | 1/8 | **0/10** ⬇ |
| F1 — body refs / 0 defs | 4.7% | 50% | 58% | **0%** ✅ |
| F1+ — orphan defs (multi-pass) | n/a | new | found | **0% (1 minor)** ✅ |
| boy-scout deep-read | 0–1/3 | 0–1/3 | 1/19 | **0.625/3** flat |
| `resolve_page` 45s timeout | 8.9% | 15.4% | 33.9% | **9.0%** ✅ |
| qmd timeout cap (effective) | 45s | 45s | 45s | **45s (NOT 60s)** 🔴 |
| pool fairness (chi-sq) | n/a | suspect | settled | **passes χ²=1.52** ✅ |
| validator pass | fail | fail | fail | fail (~12 broken wikilinks, same backlog) |

## P0-promoted observations / new

### F19 — `.env` shadows source default; PR #281 didn't actually take effect (NEW)

**What.** Trace audit found every `resolve_page.semantic` timeout this
run lands at exactly `latency_s=45.0`, never 60s. Source code has
`qmd_timeout_s: int = 60` (PR #281), but `.env` has
`QMD_TIMEOUT_S=45` shadowing it. The 33.9% → 9.0% improvement we
celebrated is **pure service-side recovery**, not the cap bump.

**Evidence.** `docs/audits/run-ee90d7ce-trace-audit-2026-04-29.md`
finding 1.

**Why it matters.** F11's masking concern was correct — bumping the
cap doesn't address service tier degradation. This run, the tier
healed organically. Next regression cycle, we want headroom in place.

**Proposed fix.** One-line `.env` change: `QMD_TIMEOUT_S=60`. If we
keep it as a runtime override, document it in `.env.example`. **Do
this before the next batch run.**

**Severity.** P0 — config drift defeats the source-level fix. Trivial
to land.

### F20 — Stale system pages don't refactor on revisit (NEW)

**What.** Two of the 4 system pages this run are pre-V12 stubs
(`buyermy`, `photosearch`). When topic pages reference them, the
system pages get *citation backfill* (F1 fix works on them) but no
*archetype rewrite*. They retain the old stub shape: missing lead,
missing `owner:`, missing Why/Open/Refs blocks. Both PM≤1 cases come
from these two pages. Both Eng=3=0 case (regression from 1/8 prior)
is them.

**Evidence.**
- `docs/audits/run-ee90d7ce-personas-2026-04-29.md` — both PM≤1 are
  stale system pages
- `docs/audits/run-ee90d7ce-page-quality-2026-04-29.md` — `buyermy`
  4th rev, still 1-sentence stub lead
- `docs/audits/run-ee90d7ce-llm-judge-2026-04-29.md` — judges score
  systems −1.58 vs topics; same 3 stale systems get the lowest scores

**Same root cause as F2** — agent doesn't refactor inherited bad
shape. F2 is at the `## Related` block level; F20 is at the whole-
page archetype level.

**Proposed fix.** Coordinator-side V12 system-page lint:
- Detect system pages with `update_count > 0` AND missing the V12
  archetype (no lead paragraph, missing `owner:`, no `## Why it
  matters`, no `## Open questions`).
- Either flag in critique with severity=blocker, OR enqueue them
  for a forced "rewrite to V12 archetype" batch.

**Severity.** P0 — every new system page risks landing on top of a
pre-V12 stub. The persona regression vs 8fa45533 is fully attributable.

## P1 (re-evaluated)

### F2 — Boy-scout still inert despite #282; need prompt-by-example (UPGRADED reasoning)

**What.** PR #282 broadened the boy-scout clause with explicit repair
verbs. Score moved 0.6 → 0.625/3 — within noise. Concrete misses:

- `contextual-blni-feedback` (3rd revision) still ships parallel
  `## Related pages` + `## Related` blocks
- `auditmate` (3rd revision) ships 2 parallel detection-gap sections
- `photosearch` (revisit) leaves
  `domain_candidates: # ambiguous: review manually` TODO unresolved
- `buyermy` (4th revision) still has a 1-sentence stub lead

**Diagnosis.** Verb expansion isn't enough. The agent needs a
**before/after diff in the workflow section** to recognise "this
shape on entry → this shape on exit". Same mechanism that landed the
PM win (lead-paragraph contract w/ explicit example).

**Proposed fix.** Add a worked example to the boy-scout clause in
`src/compile/prompts.py`:
```
Bad shape on entry:
  ## Related
  - foo
  ## Related pages
  - bar

Good shape after your edit:
  ## Related
  - foo
  - bar
```

**Severity.** P1 — same template-discipline pattern that landed
the PM win.

### F1+ — closed (PR #284 100% landing)

100% on this run (24/24 pages w/ body refs have all defs; 1 minor
orphan from union-backfill). Closed.

### F4 — `_healthy_pool` consecutive-429 short-circuit (unchanged)

Pool stayed healthy this run — fast-path still untested. Synthetic-429
injection test still recommended.

### F5 — Inter-batch dispatcher watchdog (unchanged)

No new evidence either way.

### F6 — Heuristic-vs-judge correlation (DOWNGRADED, then RE-CONFIRMED weak)

3-run correlations: −0.758 / +0.518 / +0.401. Direction is positive
but per-run n=5 too small. Heuristic mean is **flat 7.52–7.76 across
4 runs while persona scores tripled** — confirms F6's "saturated and
weak" diagnosis. Won't actively mislead, but it can't distinguish
prompt versions either.

**Proposed fix (revised).** Don't rebalance — replace. Drop saturated
columns (`concept_shape`, `source_density` now near-10 always).
Keep `structural_smells` as a *report*, not score. Add discrimination
columns: temporal-staleness, owner-presence, references-completeness.

**Severity.** P1 — affects every future audit triage decision.

### F7 — `engineering_surface:` structured frontmatter — empirically validated again

Persona audit: 0/10 pages have it; ramp-ready percentage flat
because the only pages that score Eng≥2 are systems, and the only
Eng=3 ever (now lost) had endpoint URLs/Swagger inline as prose.
Ship the frontmatter + critique rule.

**Severity.** P1 — same template pattern as `owner:`.

### F12 — System archetype variance (UPGRADED)

This round 4 systems: 2 great, 2 stub. Greenfield systems hit the
canonical archetype perfectly (`indiamart-n8n-nodes` 60-word 5W
lead); revisits to legacy stubs **inherit and don't refactor**.
**Same root cause as F2 + F20.** The fix is one and the same: prompt-
by-example for archetype enforcement OR coordinator-side lint that
flags pre-V12 system pages for forced rewrite.

### F13 — Cross-domain wikilinks (PARTIALLY CLOSED)

Last round: "topics don't link to systems even when relevant".
This round: 6 topics now wikilink to systems (`auditmate` ×3,
`buyermy` ×3, `photosearch` ×1, `[[system/whatsapp-9696-bot]]` from
`whatsapp-photo`). **Improving organically** — likely because more
systems exist now. Lower urgency.

### F18 — Temporal-staleness detection (NEW)

**What.** LLM judges' top weakness shifted from "claims hard to
verify" (now closed by F1+) to **temporal-staleness**: pages
ship `status: active` with bug-fix dates / decisions / target dates
that are 2-4 months past with no resolution recorded. Multi-pass
revisits don't refresh the stale state.

**Evidence.** `run-ee90d7ce-llm-judge-2026-04-29.md` — every judged
page got at least one staleness complaint; quotes available.

**Proposed fix.** Coordinator-side staleness lint at compile time:
- Scan touched pages for `## Recent changes` entries with dates
  >60 days past.
- If newest Recent-changes date > 60 days, flag in critique with
  severity=warning, message="Summary may be stale relative to
  newest known change."
- If `status: active` AND newest Recent-changes >120 days, severity
  =blocker.

**Severity.** P1 — pages claiming `active` while quietly
out-of-date is a wiki-credibility problem.

### F15 — `judge_wiki.py` systems support — IMPLEMENTED in tmp, not committed

Judge agent locally patched `scripts/judge_wiki.py` to glob
`wiki/systems/*.md` in addition to topics. Worked perfectly. Land
the patch as a small PR.

**Severity.** P1 — every future audit needs system-page judgment.

## P2 / unchanged

### F14 — Duplicate `## Related` template bug (DOWNGRADED, mostly closed)

22% → 4% across runs. F2's prompt fix may help close fully if the
boy-scout clause lands the worked example.

### F8 — grok pool fairness (CLOSED)

χ²=1.52, p≈0.47. Pool selection is uniform. PR #283's logging
landed cleanly. F8 closed.

### F11 — qmd service degradation (RESOLVED)

8.9% → 15.4% → 33.9% → **9.0%** — the qmd/embedding tier degraded
then recovered organically. F11's masking concern was correct in
theory but moot in practice this run. Re-arm via F19 env fix so
next degradation cycle has 60s headroom.

### F9 — cost regression (CLOSED)

$0.107 → $0.092 → $0.077 net. Below V12 baseline ($0.086). The
"composite of model-mix + qmd retries" diagnosis was right — both
healed.

## Net change vs PR #285 punch list

| Finding | PR #285 status | This audit |
|---|---|---|
| F1 (citation plumbing) | P0 | **CLOSED — #284 100% landed** |
| F2 (boy-scout) | P1 | **P1 (upgraded fix proposal: prompt-by-example)** |
| F4 (#252 fast-path) | P1 | unchanged — still untested |
| F5 (watchdog) | P1 | unchanged |
| F6 (scorer rebalance) | P1 | **REVISED — replace columns, not rebalance** |
| F7 (`engineering_surface:`) | P1 | **revalidated — Eng=3 ceiling regressed without it** |
| F11 (qmd service) | P0 | **RESOLVED — service recovered** |
| F12 (system archetype) | P1 | **UPGRADED to P0 (drives PM/Eng regression)** |
| F13 (cross-domain wikilinks) | P1 | **PARTIALLY CLOSED — 6 cases now** |
| F14 (duplicate Related) | P1 | **DOWNGRADED to P2 — mostly closed** |
| F15 (judge_wiki systems) | P1 | **READY (already patched in tmp)** |
| F8 (grok fairness) | P2 | **CLOSED** |
| F9 (cost regression) | P2 | **CLOSED** |
| F10 (page outliers) | P2 | folded into F1+ (closed) |
| **F18 (temporal-staleness lint)** | — | **NEW P1** |
| **F19 (qmd env shadow)** | — | **NEW P0** |
| **F20 (stale system pages)** | — | **NEW P0** (sub-class of F12) |

**Net**:
- 4 closed (F1, F8, F9, F11)
- 1 partially closed (F13)
- 1 downgraded (F14)
- 2 new P0 (F19, F20)
- 1 new P1 (F18)
- 1 P0→P0 upgrade (F12)
- 2 fix-proposals revised (F2 prompt-by-example, F6 column replacement)

## What got better this round

- **F1 closed cleanly** — PR #284 fully landed
- **PM ceiling 4 → 5** PM=3 pages
- **Cost dropped to $0.077/email net** — best of any run
- **0 fatal failures** across 102 compiled
- **Pool fairness confirmed uniform** with PR #283 logging
- **Cross-domain wikilinks emerging organically** (F13)
- **Duplicate `## Related` rate 22% → 4%**

## What got worse / surfaced

- **System-page archetype variance** — Eng=3 ceiling lost. Stale
  pre-V12 system pages drag the whole sample down (F20).
- **Temporal staleness** — newly-dominant judge complaint (F18)
- **#281 didn't actually land** because of `.env` shadow (F19)

## Cross-references

- `docs/audits/run-ee90d7ce-page-quality-2026-04-29.md`
- `docs/audits/run-ee90d7ce-personas-2026-04-29.md`
- `docs/audits/run-ee90d7ce-llm-judge-2026-04-29.md`
- `docs/audits/run-ee90d7ce-trace-audit-2026-04-29.md`
- `docs/audits/run-ee90d7ce-cross-run-synthesis-2026-04-29.md`
- Prior round: `docs/audits/run-8fa45533-findings-2026-04-29.md` (PR #285)
