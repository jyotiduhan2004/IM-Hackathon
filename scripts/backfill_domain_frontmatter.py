"""Backfill `domain:` frontmatter for topic + system pages.

Safe to delete after: 2026-06-18

Walks `wiki/topics/*.md` + `wiki/systems/*.md` and fills in `domain:`
frontmatter for every page that's missing it. Domain is inferred by
scoring each page's title + first body paragraph against the canonical
8-domain keyword lists defined in `src.wiki.domains._DOMAINS`.

When two domains score within 20% of each other the page is flagged
ambiguous: a `domain_candidates: [...]` list is written alongside
`domain:` with an inline `# ambiguous: review manually` comment so a
human can resolve it later. This is the one place in the pipeline
where we write a comment into frontmatter — PyYAML's `safe_dump`
can't emit comments, so we post-process the rendered output to splice
the marker onto the `domain_candidates:` line.

One-shot lifecycle:
    - Classification: one-shot — motivation doc `docs/proposal/
      NORTH-STAR-DRAFT.md` (399 pages missing `domain:` as of
      cycle 9 audit).
    - No flag = dry-run behaviour (writes are gated on `--commit`).
      `--commit` must be passed to apply. `--dry-run` is explicit and
      mutually exclusive with `--commit`.
    - `--limit N` processes only the first N pages (including
      unresolved) in sorted order — tranche rollout so a bad
      heuristic doesn't touch the whole corpus at once.

Usage:
    uv run python scripts/backfill_domain_frontmatter.py --dry-run
    uv run python scripts/backfill_domain_frontmatter.py --dry-run --limit 10
    uv run python scripts/backfill_domain_frontmatter.py --commit --limit 10
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402
from src.wiki.domains import _DOMAINS  # noqa: E402
from src.wiki.pages import _first_paragraph  # noqa: E402
from src.wiki.pages import _iter_content_pages  # noqa: E402
from src.wiki.pages import _read_page  # noqa: E402

# Ratio threshold for flagging a page as ambiguous. Runner-up within
# 20% of the leader (by keyword-hit count) → write both candidates and
# a `# ambiguous` comment. Matches the Wave-B spec in the v9 plan.
_AMBIGUITY_RATIO = 0.8

_AMBIGUITY_COMMENT = "# ambiguous: review manually"


@dataclass(frozen=True)
class _DomainVerdict:
    """Result of scoring a single page's keyword hits across all 8 domains.

    `winner` is None iff zero keywords matched — caller handles that as
    an unresolved page (no domain assigned).
    `candidates` is populated only when the runner-up is within 20% of
    the leader; empty otherwise.
    """

    winner: str | None
    candidates: list[str]
    scores: dict[str, int]


def _score_domains(title: str, body: str) -> _DomainVerdict:
    """Score a page's title + first paragraph against all 8 domain keyword lists.

    Mirrors the haystack strategy in `_infer_domain_from_keywords`
    (title + first paragraph, lowercased) — we can't reuse that helper
    directly because it returns a single slug on first-match and
    discards the per-domain counts we need for ambiguity detection.

    Ties are broken by `_DOMAINS` declaration order, matching the
    compiler's own first-match-wins behaviour.
    """
    haystack = f"{title}\n{_first_paragraph(body)}".lower()
    scores: dict[str, int] = {}
    for slug, _label, keywords in _DOMAINS:
        hits = sum(1 for kw in keywords if kw in haystack)
        if hits:
            scores[slug] = hits

    if not scores:
        return _DomainVerdict(winner=None, candidates=[], scores={})

    # Tie-break by _DOMAINS declaration order (matches the compiler's
    # first-match-wins). Explicit iteration rather than `max(scores,
    # key=scores.get)` because the latter's tiebreaker is insertion
    # order, which happens to be _DOMAINS order today but would drift
    # silently if the scoring loop ever changed.
    top_score = max(scores.values())
    winner = next(slug for slug, _l, _k in _DOMAINS if scores.get(slug, 0) == top_score)

    candidates: list[str] = []
    for slug, _label, _kw in _DOMAINS:
        score = scores.get(slug, 0)
        if score == 0:
            continue
        if slug == winner:
            candidates.append(slug)
            continue
        if score >= top_score * _AMBIGUITY_RATIO:
            candidates.append(slug)

    # Keep the `candidates` list only when there's a genuine runner-up;
    # a solo leader produces `[winner]` which is noise.
    if len(candidates) < 2:
        candidates = []

    return _DomainVerdict(winner=winner, candidates=candidates, scores=scores)


def _annotate_ambiguity_comment(rendered: str) -> str:
    """Splice `  # ambiguous: review manually` onto the `domain_candidates:` line.

    YAML round-trip loses comments, so we do a single in-place line
    edit after `render_with_frontmatter`. The comment lands on the
    list-header line (`domain_candidates:`) so operators grep'ing
    `ambiguous` find the page immediately.
    """
    lines = rendered.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("domain_candidates:"):
            stripped = line.rstrip("\n")
            eol = "\n" if line.endswith("\n") else ""
            lines[i] = f"{stripped}  {_AMBIGUITY_COMMENT}{eol}"
            break
    return "".join(lines)


@dataclass(frozen=True)
class _PageOutcome:
    """Per-page result after domain inference — used by both dry-run and commit paths.

    `kind` is one of:
        - "assigned"     — single winning domain, non-ambiguous
        - "ambiguous"    — winner plus runners-up within 20%
        - "unresolved"   — zero keywords matched; page gets no domain
    """

    path: Path
    kind: str
    winner: str | None
    candidates: list[str]


def _has_domain(fm: dict[str, object]) -> bool:
    """True iff frontmatter declares at least one domain.

    Accepts either the singular `domain:` string or the plural
    `domains:` list (v10-U2). Matches the skip logic in
    `check_missing_domain` in `scripts/validate_wiki.py` so this
    backfill doesn't rewrite pages that already self-declared
    via the multi-value form.
    """
    plural = fm.get("domains")
    if isinstance(plural, list) and plural:
        return True
    domain = fm.get("domain")
    return isinstance(domain, str) and bool(domain.strip())


def _classify_pending_pages(wiki_dir: Path) -> list[_PageOutcome]:
    """Walk topic+system pages missing `domain:`, score each, return outcomes.

    Single read per page via `_read_page` from the compiler. Pages with
    unreadable bytes, corrupt frontmatter, or a pre-existing `domain:`
    are silently skipped — same semantics as `_iter_content_pages` in
    the compiler.
    """
    outcomes: list[_PageOutcome] = []
    # Sort for deterministic ordering — `--limit N` tranche rollout must be
    # reproducible across runs regardless of the underlying dir-walk order.
    for path in sorted(_iter_content_pages(wiki_dir)):
        read = _read_page(path)
        if read is None:
            continue
        fm, body = read
        if _has_domain(fm):
            continue
        title = str(fm.get("title", path.stem.replace("-", " ").title()))
        verdict = _score_domains(title, body)
        if verdict.winner is None:
            outcomes.append(_PageOutcome(path=path, kind="unresolved", winner=None, candidates=[]))
        elif verdict.candidates:
            outcomes.append(
                _PageOutcome(
                    path=path,
                    kind="ambiguous",
                    winner=verdict.winner,
                    candidates=verdict.candidates,
                )
            )
        else:
            outcomes.append(
                _PageOutcome(path=path, kind="assigned", winner=verdict.winner, candidates=[])
            )
    return outcomes


def _apply_page(outcome: _PageOutcome) -> None:
    """Write `domain:` (and optionally `domain_candidates:`) into the page's frontmatter.

    Idempotent — re-running on an already-patched page just rewrites
    the same frontmatter shape. Body is preserved verbatim via the
    shared `render_with_frontmatter` round-trip.
    """
    if outcome.winner is None:
        raise ValueError(f"apply_page called on unresolved outcome: {outcome.path}")
    content = outcome.path.read_text(encoding="utf-8")
    fm = extract_frontmatter(content)
    body = extract_body(content)
    fm["domain"] = outcome.winner
    if outcome.candidates:
        fm["domain_candidates"] = outcome.candidates
    rendered = render_with_frontmatter(fm, body)
    if outcome.candidates:
        rendered = _annotate_ambiguity_comment(rendered)
    outcome.path.write_text(rendered, encoding="utf-8")


def _format_summary(outcomes: list[_PageOutcome], *, committed: bool) -> str:
    """Build the human-readable summary line printed at exit.

    Shape: `N changed, M ambiguous, K still-unresolved` — matches the
    wording in the v9 plan. `changed` includes ambiguous pages because
    they do get a `domain:` written (the `domain_candidates:` is the
    review nudge, not a block).
    """
    assigned = sum(1 for o in outcomes if o.kind == "assigned")
    ambiguous = sum(1 for o in outcomes if o.kind == "ambiguous")
    unresolved = sum(1 for o in outcomes if o.kind == "unresolved")
    changed = assigned + ambiguous
    verb = "changed" if committed else "would change"
    return f"{changed} {verb}, {ambiguous} ambiguous, {unresolved} still-unresolved"


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview only. Prints per-page assignments + summary. Default mode when neither flag is passed.",
)
@click.option(
    "--commit",
    is_flag=True,
    help="Apply the frontmatter edits. Required to actually write changes.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process only the first N pages (including unresolved). Tranche rollout guardrail.",
)
@click.option(
    "--repo-root",
    default=None,
    help="Override repo root (tests).",
)
def main(dry_run: bool, commit: bool, limit: int | None, repo_root: str | None) -> None:
    # Both flags are opt-in. Writes are gated on `commit`, so passing
    # neither flag behaves identically to `--dry-run`. Passing both is
    # a mistake — dry-run would silently lose to commit, so reject it.
    if dry_run and commit:
        raise click.UsageError("--dry-run and --commit are mutually exclusive")

    root = Path(repo_root).resolve() if repo_root else REPO_ROOT
    wiki_dir = root / "wiki"

    outcomes = _classify_pending_pages(wiki_dir)
    if limit is not None:
        outcomes = outcomes[:limit]

    for outcome in outcomes:
        rel = outcome.path.relative_to(root) if outcome.path.is_absolute() else outcome.path
        if outcome.kind == "assigned":
            click.echo(f"  {rel} → {outcome.winner}")
        elif outcome.kind == "ambiguous":
            cands = ", ".join(outcome.candidates)
            click.echo(f"  {rel} → {outcome.winner}  (ambiguous: {cands})")
        else:
            click.echo(f"  {rel} → <unresolved>")

    if commit:
        for outcome in outcomes:
            if outcome.kind == "unresolved":
                continue
            _apply_page(outcome)

    click.echo(_format_summary(outcomes, committed=commit))


if __name__ == "__main__":
    main()
