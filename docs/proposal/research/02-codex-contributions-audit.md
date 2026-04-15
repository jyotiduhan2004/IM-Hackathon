# Codex Contributions + Strategic Positions Audit

Generated: 2026-04-15. Read-only research pass.

Source: review of `docs/reviews/codex-*.md`, `docs/BACKLOG.md`, `CHANGELOG.md`, `gh pr list --search "head:codex/"`, `/Users/amtagrwl/.codex/worktrees/`, plus cross-reference with the reconciliation report.

---

## 1. Codex contributions ledger

| What | Where | Status | Notes |
|---|---|---|---|
| Schema correction (`slug` globally unique, drop `topic_wiki_slug`, rename `email` → `canonical_user_email`, add `page_id` PK) | `docs/reviews/codex-catalog-review-20260413T080000Z.md` | **Shipped** | PR #31 merged; schema fully applied. Strategic + tactical. |
| 5-PR roadmap (messages table + ingest dual-write + users/threads + wiki catalog + provenance split) | `docs/reviews/codex-priority-review-20260413T090000Z.md` | **Mostly shipped** | PRs #31–#62 implement first 4 workstreams. PR5 (ingest cursors) still pending. Strategic sequence. |
| Langfuse fix-up (OTLP timeout envvars, pinned SDK) | PR #65 `codex/fix-langfuse-setup` | **Unknown / pending** | Listed `UNKNOWN` in BACKLOG; worktree exists; unmerged. |
| Personas doc + knowledge flows | PR #64 `codex/user-personas-followup` | **Shipped (aspirational)** | Merged as `docs/issues/11-*`. Contains unshipped/unvalidated features (`navigation_role`, "Ownership History", domain hubs). |
| Auto-stub cleanup advice (reject silent stripping; one-shot delete only) | `docs/BACKLOG.md:649–680` | **Partially shipped** | PR #68 landed; silent-strip variant deferred per Codex guidance. |
| Snapshot parser fix (broken `split("---", 2)` → use `extract_frontmatter`) | `CHANGELOG.md:218–221` | **Shipped** | Fixed in `scripts/snapshot_wiki.py`. |

Strategic vs tactical split: catalog schema, 5-PR sequence, provenance split direction = strategic. Langfuse timeout, parser bug, cleanup heuristics = tactical.

---

## 2. Strategic disagreements (Codex vs user)

### 2.1 Provenance posture: split vs stay-in-YAML

- **Codex** (`codex-priority-review:56–66`, PR4): "Delete `sources` from YAML. Render from Postgres catalog at build time. Remove `sources` from `lint_wiki.py:37`. Stop telling the agent to populate exhaustively."
- **User** (`docs/issues/10-phase1-implementation-plan:467–500`, Workstream 3): "Keep source truth in frontmatter AND catalog. YAML remains authority for machine use."
- **Status**: User's position partially wins. `mkdocs_hooks.py` now renders collapsed Sources blocks, but `scripts/lint_wiki.py:37` **still requires** `sources:` in YAML; `src/compile/prompts.py` **still tells the agent to populate exhaustively**. Only rendering changed, not storage. Codex's direction is acknowledged but blocked.

### 2.2 Queue-state truth: Postgres-first vs raw-markdown legacy

- **Codex**: "Postgres is truth. Mark raw frontmatter `compiled:` as dead legacy. Don't rewrite 6,759 raws; backfill once then ignore."
- **User**: Pragmatic middle ground — messages table shipped in #31, but `parser.write_raw_email()` keeps raw-file sync automatic. No explicit "backfill, then stop" moment documented.
- **Status**: Codex wins tactically (Postgres is de facto truth), but user keeps dual-write safety net running. Trade-off accepted but not made explicit.

### 2.3 Phase 1 definition: scope creep via personas doc

- **Codex** (implicit): Focus Phase 1 on core pipelines (queue → catalog → provenance). Skip taxonomy elaborations.
- **User** (PR #64): Adds 4 personas, 9 acceptance scenarios, "domain hubs", `navigation_role` frontmatter, "Ownership History" sections, "How the View Changed" sections.
- **Status**: Personas doc merged but unshipped. Reconciliation report flags these as "aspirational vocabulary" — not enforced by validator, not in prompts, zero pages implement them. Codex would likely have objected without implementation commitment.

### 2.4 Auto-stub strategy: silent stripping vs explicit cleanup

- **Codex** (`BACKLOG.md:649–680`): "Don't silent-strip stubs in `compile_all.py` post-batch — erases failure signal. One-shot delete of ~26 lint-created shells is fine; do NOT blanket-remove."
- **User** (#68): Accepts. Adds `--create-stubs` gate; one-shot cleanup script; defers silent-strip variant.
- **Status**: **Codex wins**. User explicitly credits "Codex advised against commit 2, silent stripping, so it's deferred/dropped."

### 2.5 Deliverable honesty: week-1 scope claim

- **Codex** (`codex-priority-review:136–164`): "Week 1 realistic outcome = owner-operated internal pilot, NOT team-ready browse/search. 64% stubs, 96 orphans, entity dominance, MkDocs search weak."
- **User** (README, phased-delivery, BACKLOG): "Team-ready internal wiki" is Phase 1 goal.
- **Status**: Codex's pessimism is fact-checked by reconciliation report. Codex's warning was correct. User's scope is aspirational.

---

## 3. Stuck-in-flight Codex work

- **PR #65** (`codex/fix-langfuse-setup`) — marked UNKNOWN in BACKLOG, unmerged. Worktree exists at `/Users/amtagrwl/.codex/worktrees/dd7b/email-knowledge-base`.
- **PR #64** (`codex/user-personas-followup`) — merged, but content is aspirational (no downstream implementation).
- **PR5 of `codex-priority-review`** — ingest cursors, "daily quiet-thread claiming from Postgres" — not yet wired.

---

## 4. One-paragraph recommendation

Codex's catalog schema and 5-PR roadmap are sound and broadly shipped. The three open gaps are: (1) provenance split direction (Codex says drop YAML, user says keep both — only rendering changed, storage didn't), (2) personas doc shipped without implementation commitment for any of its concepts, and (3) week-1 honesty (Codex's "owner-operated pilot" framing was correct; "team-ready" framing in user docs is aspirational). Codex consistently favors simplicity + auditability; user's docs add richness without follow-through. The consolidation should either embrace Codex minimalism or commit tooling/testing to back the richer vision — doing both half-way is the current incoherent state.
