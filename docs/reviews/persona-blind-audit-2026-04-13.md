# 5-persona blind audit — 2026-04-13 (record)

This doc is a retroactive index for the 5-persona blind audit run on
2026-04-13. The audit was direct-pushed to `main` in commit
[`246bd7a`](https://github.com/indiamart-ai/email-knowledge-base/commit/246bd7a)
without going through a pull request, so there has been no PR to point at when
referencing it. The 6 deliverables were later moved (2026-04-15, commit
[`9f7a8d8`](https://github.com/indiamart-ai/email-knowledge-base/commit/9f7a8d8))
into the pre-proposal archive when the strategy docs were consolidated.

This file lives in `docs/reviews/` for discoverability — the actual audit
content stays at its archived path and is still loaded verbatim by
`src/compile/judge.py` for the LLM-judge eval.

## Method

Five Task agents were spawned in parallel with different persona prompts. Each
explored the wiki blind — no prior context about architecture, compiler, or
intended page model. Reports were filed independently; a sixth pass synthesized
the cross-cutting findings.

| Persona | Lens |
|---|---|
| New employee | What's confusing for a 6-month joiner? |
| PM status-hunter | Can I read off "current state" of a launch? |
| Information-architecture reviewer | Is the topology coherent? |
| Fact-faithfulness auditor | Do claims match the cited raw emails? |
| Business journalist | Could I write a story from this? |

## Files (archived path)

- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-persona-newbie-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-persona-newbie-20260413T040000Z.md)
- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-persona-pm-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-persona-pm-20260413T040000Z.md)
- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-persona-ia-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-persona-ia-20260413T040000Z.md)
- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-persona-factcheck-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-persona-factcheck-20260413T040000Z.md)
- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-persona-journalist-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-persona-journalist-20260413T040000Z.md)
- [`docs/archive/2026-04-15-pre-proposal/reviews/audit-synthesis-20260413T040000Z.md`](../archive/2026-04-15-pre-proposal/reviews/audit-synthesis-20260413T040000Z.md)

## Cross-cutting findings (verbatim from `246bd7a`)

- 64% of pages are stubs (<100 words body). Compiler auto-creates stubs for
  unresolved wikilinks, hiding broken links by manufacturing placeholders.
- 11 near-duplicate page pairs in the wild. Suffix detector misses `-clean`,
  numeric, US/UK-spelling, and same-email-different-slug variants.
- 5 humans stranded in `systems/`, 4 products stranded in `entities/`. `samarth`
  exists in both — slug collision.
- Source coverage gaps: `whatsapp9696` topic cites 2 raws; `raw/` has 30 on the
  same thread (2 months of updates missing).
- Factual degradation on entity pages: 1 of 10 claims hallucinated (Vikram
  Varshney attributed a quote he never made).
- YAML corruption: `sonarqube-quality-profile-transformation` has two
  `last_compiled` keys and split sources — validator missed it.

The synthesis ranked findings on impact × ease and proposed Tier 1 (no LLM,
~2h), Tier 2 (no LLM, ~half day), Tier 3/4 (gated on LLM budget). Tier 1+2
landed in the days that followed.

## Where this work lives now

- **LLM-judge eval** — `src/compile/judge.py` loads three of these personas
  (`newbie`, `pm`, `ia`) verbatim as system prompts, so a single wiki page can
  be re-audited on demand. Shipped in PR
  [#210](https://github.com/indiamart-ai/email-knowledge-base/pull/210).
- **Reconciliation report** — `docs/proposal/research/01-reconciliation-report.md`
  references the persona audits as the original source for the "leave as
  archive" decision.
- **Inbound citations** — the synthesis is referenced from
  `docs/reviews/codex-priority-review-20260413T090000Z.md`,
  `docs/reviews/codex-catalog-review-20260413T080000Z.md`,
  `docs/reviews/tool-audit-20260413T050000Z.md`,
  `docs/archive/README.md`.
