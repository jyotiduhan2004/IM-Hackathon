"""One-shot dedupe of three confirmed legacy slug pairs surfaced by the
audit-status tracker (`docs/audits/STATUS.md`, finding F-013).

Each pair is the same person/concept written under both a legacy
display-name slug and the email-canonical slug. The fix wraps
`scripts/apply_merge_candidate.py` and is idempotent — running on an
already-merged pair re-applies the same supersession state (no-op in
practice).

Pairs:
1. ``alok-kumar2`` (loser) → ``alok-kumar2-indiamart-com`` (keeper) —
   both are alok.kumar2@indiamart.com.
2. ``vikram-varshney`` (loser) → ``vikram-varshney-indiamart-com``
   (keeper) — both are vikram.varshney@indiamart.com.
3. ``samarth`` (loser, mis-categorised system stub from before
   PR #194 disabled auto-stubs) → ``samarth-indiamart-com`` (keeper,
   real person page).

Out of scope (explicitly):

- ``alok-kumar`` (alok.kumar@) and ``sahil-sharma`` (sahil.sharma@) are
  *separate* people from alok.kumar2 and sahil.sharma2 — not dups.
  Their slugs are still legacy display-name form; renaming to
  email-canonical is a follow-up, not a dedupe.
- The Lens system dup (``Lens.IndiaMART.md`` + ``lens-indiamart-com.md``)
  needs a rename to ``indiamart-lens.md`` (per the new-joiner fixture)
  plus rewriting 23 incoming wikilinks. Tracked separately as
  STATUS.md F-024 backfill.

One-shot lifecycle:

- Last production run: 2026-04-28
- Safe to delete after: 2026-05-28
- Deletion gate: `scripts/audit.py` reports zero ``samarth`` /
  ``vikram-varshney`` / ``alok-kumar2`` rows with status='active' on
  the loser slug for 7 consecutive days, and `wiki/people/<loser>.md`
  pages remain at status='superseded'.

Usage::

    uv run python scripts/repair_legacy_dup_slugs_2026_04_28.py --dry-run
    uv run python scripts/repair_legacy_dup_slugs_2026_04_28.py --commit
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parent.parent

PAIRS: list[tuple[str, str]] = [
    # (loser, keeper)
    ("alok-kumar2", "alok-kumar2-indiamart-com"),
    ("vikram-varshney", "vikram-varshney-indiamart-com"),
    ("samarth", "samarth-indiamart-com"),
]


def _run_merge(loser: str, keeper: str, commit: bool) -> int:
    args = [
        "uv",
        "run",
        "python",
        "scripts/apply_merge_candidate.py",
        "--pair",
        f"{loser},{keeper}",
        "--keep",
        keeper,
        "--commit" if commit else "--dry-run",
    ]
    return subprocess.run(args, cwd=REPO_ROOT, check=False).returncode


@click.command()
@click.option("--commit", is_flag=True, help="Apply merges for real.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would change.")
def main(commit: bool, dry_run: bool) -> None:
    if commit == dry_run:
        click.echo("Pass exactly one of --commit / --dry-run.", err=True)
        sys.exit(2)
    failures = 0
    for loser, keeper in PAIRS:
        click.echo(f"\n=== {loser} -> {keeper} ===")
        rc = _run_merge(loser, keeper, commit=commit)
        if rc != 0:
            failures += 1
            click.echo(f"  exit code {rc}", err=True)
    if failures:
        click.echo(f"\n{failures} pair(s) failed.", err=True)
        sys.exit(1)
    click.echo("\nAll pairs processed.")


if __name__ == "__main__":
    main()
