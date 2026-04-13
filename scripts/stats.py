"""Pipeline stats: emails per day, compile coverage, cost per day.

Counts raw/ files by date (filenames carry YYYY-MM-DD prefixes) and pulls
compile-state distribution from the messages catalog in Postgres. Joins
that with the LiteLLM budget spend to estimate cost per compiled email.
Rough but useful for overnight planning.

Compile state used to live in raw frontmatter (`compiled: true`) but
moved to the Postgres `messages` table in commit ecbd4ad. Reading both
sources risked dual-write drift (Codex priority review, 2026-04-13), so
this script now reads from the DB.

Usage:
    uv run python scripts/stats.py
    uv run python scripts/stats.py --days-back 30
    uv run python scripts/stats.py --days-back 0  # show all
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.budget import fetch_budget  # noqa: E402
from src.config import settings  # noqa: E402
from src.db.messages import count_by_state  # noqa: E402

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


@click.command()
@click.option(
    "--days-back",
    default=14,
    help="Show only days within this many days back from today. 0 = all.",
)
def main(days_back: int) -> None:
    """Print emails/day, global compile state, and rough cost estimates."""
    raw_dir = settings.raw_dir
    if not raw_dir.exists():
        click.echo(f"ERROR: {raw_dir} not found", err=True)
        sys.exit(1)

    total_by_date: dict[str, int] = defaultdict(int)

    for md_file in raw_dir.glob("*.md"):
        match = DATE_RE.match(md_file.name)
        if not match:
            continue
        total_by_date[match.group(1)] += 1

    if not total_by_date:
        click.echo("No raw emails on disk.")
        return

    all_dates = sorted(total_by_date.keys())
    if days_back > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        all_dates = [d for d in all_dates if d >= cutoff]

    state = count_by_state()
    compiled_count = state.get("compiled", 0)
    pending_count = state.get("pending", 0)
    failed_count = state.get("failed", 0)
    claimed_count = state.get("claimed", 0)
    total = sum(state.values())

    budget = fetch_budget()

    click.echo(f"{'Date':<12}  {'Ingested':>8}")
    click.echo("-" * 24)
    grand_ingested = 0
    for d in all_dates:
        t = total_by_date[d]
        click.echo(f"{d:<12}  {t:>8}")
        grand_ingested += t
    click.echo("-" * 24)
    click.echo(f"{'TOTAL':<12}  {grand_ingested:>8}")

    click.echo()
    click.echo("Compile state (DB):")
    click.echo(f"  compiled : {compiled_count}")
    click.echo(f"  pending  : {pending_count}")
    click.echo(f"  failed   : {failed_count}")
    if claimed_count:
        click.echo(f"  claimed  : {claimed_count}")
    click.echo(f"  total    : {total}")
    if total:
        pct = (compiled_count / total) * 100
        click.echo(f"  compiled %: {pct:.1f}%")

    click.echo()
    if budget:
        click.echo(f"Budget: {budget}")
        if compiled_count > 0:
            cost_per_email = budget.spend / compiled_count
            click.echo(f"Implied cost per compiled email: ${cost_per_email:.4f}")
            uncompiled = pending_count + failed_count
            projected = uncompiled * cost_per_email
            click.echo(f"Uncompiled: {uncompiled}. Projected cost to finish: ${projected:.2f}")
            if budget.remaining is not None:
                affordable = int(budget.remaining / cost_per_email) if cost_per_email > 0 else "∞"
                click.echo(
                    f"Affordable at current rate with remaining budget: ~{affordable} emails"
                )


if __name__ == "__main__":
    main()
