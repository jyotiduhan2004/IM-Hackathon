"""Per-batch dipstick — quick quality + cost + timing snapshot.

Run after every compile batch (or on demand) to produce a short report
capturing: what changed, what it cost, what's broken. Writes to
docs/runs/YYYYMMDDTHHMMSSZ.md so we can see trends across runs.

Usage:
    # after compile
    uv run python scripts/dipstick.py --since "2 minutes ago" \
        --emails-compiled 40 --run-label threaded-test-1

    # bare run — uses last git activity time as baseline
    uv run python scripts/dipstick.py
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import sys
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.budget import fetch_budget  # noqa: E402
from src.config import settings  # noqa: E402
from src.db.messages import count_by_state  # noqa: E402
from src.utils import extract_frontmatter as _shared_extract  # noqa: E402

RUNS_DIR = REPO_ROOT / "docs" / "runs"


def _parse_duration(arg: str) -> datetime:
    """Parse '5 minutes ago', '1 hour ago', or ISO timestamp → UTC datetime."""
    now = datetime.now(UTC)
    lowered = arg.strip().lower()
    match = re.match(r"^(\d+)\s+(second|minute|hour|day)s?\s+ago$", lowered)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        delta = {
            "second": timedelta(seconds=n),
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
        }[unit]
        return now - delta
    try:
        return datetime.fromisoformat(lowered.replace("z", "+00:00"))
    except ValueError as e:
        raise click.BadParameter(f"Expected '<N> minutes ago' or ISO: {arg}") from e


_extract_frontmatter = _shared_extract


@dataclass
class SpotCheck:
    page: str
    passed_sources_exist: bool
    passed_wikilinks_resolve: bool
    passed_page_type_matches_dir: bool
    notes: str = ""


@dataclass
class Report:
    run_label: str
    started_at: str
    ended_at: str
    since_timestamp: str
    emails_compiled_claim: int | None
    wiki_pages_created: int = 0
    wiki_pages_modified: int = 0
    wiki_pages_total: int = 0
    raw_compiled_delta: int | None = None
    raw_compiled_total: int = 0
    raw_on_disk: int = 0
    budget_spend: float | None = None
    budget_remaining: float | None = None
    cost_this_run: float | None = None
    cost_per_email: float | None = None
    validator_ok: bool = False
    validator_errors: list[str] = field(default_factory=list)
    lint_summary: str = ""
    spot_checks: list[SpotCheck] = field(default_factory=list)
    created_page_samples: list[str] = field(default_factory=list)


def _pages_modified_since(since: datetime) -> tuple[list[Path], list[Path]]:
    """Return (created, modified) lists of wiki .md files touched since `since`."""
    created: list[Path] = []
    modified: list[Path] = []
    since_ts = since.timestamp()
    for category in (
        "topics",
        "entities",
        "systems",
        "policies",
        "timelines",
        "conflicts",
    ):
        cat = settings.wiki_dir / category
        if not cat.exists():
            continue
        for p in cat.glob("*.md"):
            try:
                stat = p.stat()
            except OSError:
                continue
            if stat.st_ctime > since_ts:
                created.append(p)
            elif stat.st_mtime > since_ts:
                modified.append(p)
    return created, modified


def _get_wiki_page_stems() -> set[str]:
    stems: set[str] = set()
    for category in (
        "topics",
        "entities",
        "systems",
        "policies",
        "timelines",
        "conflicts",
    ):
        cat = settings.wiki_dir / category
        if cat.exists():
            stems.update(p.stem for p in cat.glob("*.md"))
    return stems


def _spot_check_page(path: Path, known_stems: set[str]) -> SpotCheck:
    """Check a wiki page: sources exist on disk, wikilinks resolve, type matches dir."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT.resolve())
        page_str = str(rel)
    except ValueError:
        page_str = str(path)
    check = SpotCheck(
        page=page_str,
        passed_sources_exist=True,
        passed_wikilinks_resolve=True,
        passed_page_type_matches_dir=True,
    )
    notes = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        check.notes = f"unreadable: {e}"
        check.passed_sources_exist = False
        return check

    fm = _extract_frontmatter(content)

    sources = fm.get("sources") or []
    missing_sources = [s for s in sources if isinstance(s, str) and not (REPO_ROOT / s).exists()]
    if missing_sources:
        check.passed_sources_exist = False
        notes.append(f"{len(missing_sources)} sources missing on disk")

    expected_types = {
        "topics": "topic",
        "entities": "entity",
        "systems": "system",
        "policies": "policy",
        "timelines": "timeline",
        "conflicts": "conflict",
    }
    got = fm.get("page_type")
    want = expected_types.get(path.parent.name)
    if want and got != want:
        check.passed_page_type_matches_dir = False
        notes.append(f"page_type={got!r} but in {path.parent.name}/")

    wikilinks = re.findall(r"\[\[([^\]]+)\]\]", content)
    broken = [w.split("|")[0].strip() for w in wikilinks]
    broken = [w for w in broken if w not in known_stems]
    if broken:
        check.passed_wikilinks_resolve = False
        notes.append(f"{len(broken)} unresolved wikilinks (e.g., {broken[0]})")

    check.notes = "; ".join(notes)
    return check


def _run_validator() -> tuple[bool, list[str]]:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/validate_wiki.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode == 0:
        return True, []
    errors = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(("✓", "✗", "Summary"))
    ]
    return False, errors[:20]


def _run_lint() -> str:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/lint_wiki.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    for line in result.stdout.splitlines():
        if line.startswith("Summary:"):
            return line.strip()
    return "(no summary line in lint output)"


def _count_raw_compiled() -> dict[str, int]:
    """Compile-state counts from the Postgres messages catalog.

    Replaces the previous raw/*.md frontmatter scan. The DB is the
    source of truth now; raw files no longer carry a `compiled: true`
    marker. Keys: 'compiled', 'pending', 'failed', 'total' (sum of all
    states, including 'claimed').
    """
    state = count_by_state()
    return {
        "compiled": state.get("compiled", 0),
        "pending": state.get("pending", 0),
        "failed": state.get("failed", 0),
        "total": sum(state.values()),
    }


def build_report(
    since: datetime,
    run_label: str,
    emails_claim: int | None,
    budget_before: float | None,
) -> Report:
    started = datetime.now(UTC).isoformat()

    created, modified = _pages_modified_since(since)
    state_counts = _count_raw_compiled()
    compiled = state_counts["compiled"]
    total_raw = state_counts["total"]

    budget = fetch_budget()
    cost_this_run: float | None = None
    if budget and budget_before is not None:
        cost_this_run = round(budget.spend - budget_before, 4)

    known_stems = _get_wiki_page_stems()
    sample = random.sample(created, k=min(3, len(created))) if created else []
    spot_checks = [_spot_check_page(p, known_stems) for p in sample]

    validator_ok, validator_errors = _run_validator()
    lint_summary = _run_lint()

    cost_per_email = None
    if cost_this_run is not None and emails_claim and emails_claim > 0:
        cost_per_email = round(cost_this_run / emails_claim, 4)

    wiki_total = sum(
        len(list((settings.wiki_dir / c).glob("*.md")))
        for c in (
            "topics",
            "entities",
            "systems",
            "policies",
            "timelines",
            "conflicts",
        )
        if (settings.wiki_dir / c).exists()
    )

    return Report(
        run_label=run_label,
        started_at=started,
        ended_at=datetime.now(UTC).isoformat(),
        since_timestamp=since.isoformat(),
        emails_compiled_claim=emails_claim,
        wiki_pages_created=len(created),
        wiki_pages_modified=len(modified),
        wiki_pages_total=wiki_total,
        raw_compiled_total=compiled,
        raw_on_disk=total_raw,
        budget_spend=budget.spend if budget else None,
        budget_remaining=budget.remaining if budget else None,
        cost_this_run=cost_this_run,
        cost_per_email=cost_per_email,
        validator_ok=validator_ok,
        validator_errors=validator_errors,
        lint_summary=lint_summary,
        spot_checks=spot_checks,
        created_page_samples=[
            str(p.resolve().relative_to(REPO_ROOT.resolve()))
            if p.resolve().is_relative_to(REPO_ROOT.resolve())
            else str(p)
            for p in sample
        ],
    )


def format_markdown(report: Report) -> str:
    lines = [
        f"# Dipstick — {report.run_label}",
        "",
        f"**Started**: {report.started_at}",
        f"**Ended**: {report.ended_at}",
        f"**Change window (since)**: {report.since_timestamp}",
        "",
        "## Throughput",
        "",
        f"- Emails compiled this run (claim): **{report.emails_compiled_claim or 'unknown'}**",
        f"- Raw compiled total: {report.raw_compiled_total} / {report.raw_on_disk}",
        f"- Wiki pages created: {report.wiki_pages_created}",
        f"- Wiki pages modified: {report.wiki_pages_modified}",
        f"- Wiki pages total: {report.wiki_pages_total}",
        "",
        "## Cost",
        "",
    ]
    if report.budget_spend is not None:
        lines.append(
            f"- Budget: ${report.budget_spend:.4f} spent, ${report.budget_remaining:.2f} left"
        )
    if report.cost_this_run is not None:
        lines.append(f"- Cost this run: ${report.cost_this_run:.4f}")
    if report.cost_per_email is not None:
        lines.append(f"- Cost per email: ${report.cost_per_email:.4f}")

    lines.extend(
        ["", "## Integrity", "", f"- Validator: {'✓ clean' if report.validator_ok else '✗ failed'}"]
    )
    if report.validator_errors:
        lines.append("  - Errors:")
        for e in report.validator_errors[:10]:
            lines.append(f"    - {e}")
    lines.append(f"- Lint: {report.lint_summary}")

    lines.extend(["", "## Spot checks"])
    if not report.spot_checks:
        lines.append("")
        lines.append("_(no new pages to sample)_")
    for sc in report.spot_checks:
        lines.append("")
        lines.append(f"### {sc.page}")
        lines.append(f"- sources exist: {'yes' if sc.passed_sources_exist else 'NO'}")
        lines.append(f"- wikilinks resolve: {'yes' if sc.passed_wikilinks_resolve else 'NO'}")
        lines.append(
            f"- page_type matches dir: {'yes' if sc.passed_page_type_matches_dir else 'NO'}"
        )
        if sc.notes:
            lines.append(f"- notes: {sc.notes}")

    return "\n".join(lines) + "\n"


@click.command()
@click.option(
    "--since",
    default="30 minutes ago",
    help="Cutoff for 'changed recently' — '5 minutes ago' or ISO timestamp",
)
@click.option(
    "--run-label",
    default=None,
    help="Label for this run (defaults to timestamp)",
)
@click.option(
    "--emails-compiled",
    type=int,
    default=None,
    help="How many emails this batch tried to compile (for cost/email math)",
)
@click.option(
    "--budget-before",
    type=float,
    default=None,
    help="Budget spend value BEFORE the batch (for cost-this-run delta)",
)
@click.option(
    "--json-out",
    is_flag=True,
    help="Also write JSON alongside the markdown report",
)
def main(
    since: str,
    run_label: str | None,
    emails_compiled: int | None,
    budget_before: float | None,
    json_out: bool,
) -> None:
    """Generate a per-batch dipstick report."""
    since_dt = _parse_duration(since)
    label = run_label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    report = build_report(
        since=since_dt,
        run_label=label,
        emails_claim=emails_compiled,
        budget_before=budget_before,
    )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = RUNS_DIR / f"{label}.md"
    md_path.write_text(format_markdown(report), encoding="utf-8")

    if json_out:
        json_path = RUNS_DIR / f"{label}.json"
        # dataclasses with nested dataclasses need asdict recursion
        data = asdict(report)
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    click.echo(format_markdown(report))
    click.echo(f"Wrote: {md_path}")

    # Quick integrity signal via exit code for scripting
    if not report.validator_ok:
        sys.exit(1)


def _sha256(path: Path) -> str:
    """Unused but kept for future hash-based diffing of page bodies."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _filter_known(candidates: list[str], known: set[str]) -> list[str]:
    """Unused helper retained for extensibility."""
    return [c for c in candidates if c in known]


def _json_safe(obj: Any) -> Any:
    """Unused — json serialization helper."""
    return obj


if __name__ == "__main__":
    main()
