"""Pipeline stats: emails per day, compile coverage, cost per day.

Counts raw/ files by date, tracks compiled status, and joins with LiteLLM
budget spend to estimate per-day cost. Rough but useful for overnight
planning.

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
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.budget import fetch_budget  # noqa: E402
from src.config import settings  # noqa: E402

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def _extract_frontmatter(content: str) -> dict:
    """Line-aware split; see src.compile.compiler._split_frontmatter."""
    if not content.startswith("---"):
        return {}
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return {}
    fm_lines: list[str] = []
    for line in lines[1:]:
        if line.rstrip() == "---":
            break
        fm_lines.append(line)
    try:
        fm = yaml.safe_load("".join(fm_lines))
        return fm if isinstance(fm, dict) else {}
    except yaml.YAMLError:
        return {}


@click.command()
@click.option(
    "--days-back",
    default=14,
    help="Show only days within this many days back from today. 0 = all.",
)
def main(days_back: int) -> None:
    """Print emails/day, compiled/day, and rough cost estimates."""
    raw_dir = settings.raw_dir
    if not raw_dir.exists():
        click.echo(f"ERROR: {raw_dir} not found", err=True)
        sys.exit(1)

    total_by_date: dict[str, int] = defaultdict(int)
    compiled_by_date: dict[str, int] = defaultdict(int)

    for md_file in raw_dir.glob("*.md"):
        match = DATE_RE.match(md_file.name)
        if not match:
            continue
        date_str = match.group(1)
        total_by_date[date_str] += 1
        try:
            content = md_file.read_text(encoding="utf-8")
            fm = _extract_frontmatter(content)
            if fm.get("compiled") is True:
                compiled_by_date[date_str] += 1
        except (OSError, UnicodeDecodeError):
            continue

    if not total_by_date:
        click.echo("No raw emails on disk.")
        return

    all_dates = sorted(total_by_date.keys())
    if days_back > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        all_dates = [d for d in all_dates if d >= cutoff]

    budget = fetch_budget()

    click.echo(f"{'Date':<12}  {'Total':>6}  {'Compiled':>8}  {'%':>5}")
    click.echo("-" * 40)
    grand_total = 0
    grand_compiled = 0
    for d in all_dates:
        t = total_by_date[d]
        c = compiled_by_date.get(d, 0)
        pct = f"{(c / t) * 100:.0f}%" if t else "-"
        click.echo(f"{d:<12}  {t:>6}  {c:>8}  {pct:>5}")
        grand_total += t
        grand_compiled += c

    click.echo("-" * 40)
    grand_pct = f"{(grand_compiled / grand_total) * 100:.1f}%" if grand_total else "-"
    click.echo(f"{'TOTAL':<12}  {grand_total:>6}  {grand_compiled:>8}  {grand_pct:>5}")

    click.echo()
    if budget:
        click.echo(f"Budget: {budget}")
        if grand_compiled > 0:
            cost_per_email = budget.spend / grand_compiled
            click.echo(f"Implied cost per compiled email: ${cost_per_email:.4f}")
            uncompiled = grand_total - grand_compiled
            projected = uncompiled * cost_per_email
            click.echo(
                f"Uncompiled: {uncompiled}. Projected cost to finish: ${projected:.2f}"
            )
            if budget.remaining is not None:
                affordable = (
                    int(budget.remaining / cost_per_email)
                    if cost_per_email > 0
                    else "∞"
                )
                click.echo(
                    f"Affordable at current rate with remaining budget: ~{affordable} emails"
                )


if __name__ == "__main__":
    main()
