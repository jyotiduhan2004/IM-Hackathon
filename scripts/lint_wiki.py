"""Run wiki health checks.

Usage:
    uv run python scripts/lint_wiki.py
    uv run python scripts/lint_wiki.py --fix
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import click
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402


Severity = Literal["error", "warning", "info"]


@dataclass
class LintIssue:
    severity: Severity
    category: str
    page: str
    message: str
    auto_fixable: bool = False


REQUIRED_FRONTMATTER = {"title", "page_type", "status", "sources", "last_compiled"}
VALID_STATUSES = {"current", "superseded", "contested"}
VALID_PAGE_TYPES = {"topic", "entity", "policy", "timeline", "conflict"}


def _extract_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        parsed = yaml.safe_load(parts[1])
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}


def _extract_wikilinks(content: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", content)


def _get_wiki_pages(wiki_dir: Path) -> dict[str, Path]:
    """Map page name (stem) → file path for all wiki pages."""
    pages: dict[str, Path] = {}
    for category in ("topics", "entities", "policies", "timelines", "conflicts"):
        cat_dir = wiki_dir / category
        if cat_dir.exists():
            for md in cat_dir.glob("*.md"):
                pages[md.stem] = md
    return pages


def check_frontmatter(wiki_dir: Path) -> list[LintIssue]:
    """Check every wiki page has valid frontmatter."""
    issues: list[LintIssue] = []
    pages = _get_wiki_pages(wiki_dir)

    for name, path in pages.items():
        content = path.read_text(encoding="utf-8")
        fm = _extract_frontmatter(content)

        if not fm:
            issues.append(
                LintIssue(
                    severity="error",
                    category="missing_frontmatter",
                    page=str(path),
                    message="No YAML frontmatter found",
                )
            )
            continue

        missing = REQUIRED_FRONTMATTER - set(fm.keys())
        if missing:
            issues.append(
                LintIssue(
                    severity="error",
                    category="incomplete_frontmatter",
                    page=str(path),
                    message=f"Missing required fields: {sorted(missing)}",
                )
            )

        if fm.get("status") not in VALID_STATUSES:
            issues.append(
                LintIssue(
                    severity="error",
                    category="invalid_status",
                    page=str(path),
                    message=f"Invalid status '{fm.get('status')}' (must be one of {VALID_STATUSES})",
                )
            )

        if fm.get("page_type") not in VALID_PAGE_TYPES:
            issues.append(
                LintIssue(
                    severity="error",
                    category="invalid_page_type",
                    page=str(path),
                    message=f"Invalid page_type '{fm.get('page_type')}'",
                )
            )

    return issues


def check_broken_wikilinks(wiki_dir: Path) -> list[LintIssue]:
    """Check all [[wikilinks]] point to existing pages."""
    issues: list[LintIssue] = []
    pages = _get_wiki_pages(wiki_dir)
    known_names = set(pages.keys())

    for name, path in pages.items():
        content = path.read_text(encoding="utf-8")
        links = _extract_wikilinks(content)
        for link in links:
            target = link.split("|")[0].strip()  # handle [[target|display]]
            if target not in known_names:
                issues.append(
                    LintIssue(
                        severity="warning",
                        category="broken_link",
                        page=str(path),
                        message=f"Broken wikilink [[{target}]] — page does not exist",
                    )
                )

    return issues


def check_orphan_pages(wiki_dir: Path) -> list[LintIssue]:
    """Check for pages not referenced by any other page or the index."""
    issues: list[LintIssue] = []
    pages = _get_wiki_pages(wiki_dir)

    # Collect all wikilink targets across all pages + index
    all_links: set[str] = set()
    for path in pages.values():
        content = path.read_text(encoding="utf-8")
        for link in _extract_wikilinks(content):
            all_links.add(link.split("|")[0].strip())

    index_path = wiki_dir / "index.md"
    if index_path.exists():
        for link in _extract_wikilinks(index_path.read_text(encoding="utf-8")):
            all_links.add(link.split("|")[0].strip())

    for name, path in pages.items():
        if name not in all_links:
            issues.append(
                LintIssue(
                    severity="warning",
                    category="orphan",
                    page=str(path),
                    message="Not linked from any other page or index",
                    auto_fixable=True,
                )
            )

    return issues


def check_missing_index_entries(wiki_dir: Path) -> list[LintIssue]:
    """Check all wiki pages are listed in index.md."""
    issues: list[LintIssue] = []
    pages = _get_wiki_pages(wiki_dir)

    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        return [
            LintIssue(
                severity="warning",
                category="missing_index",
                page=str(wiki_dir),
                message="wiki/index.md does not exist",
                auto_fixable=True,
            )
        ]

    index_content = index_path.read_text(encoding="utf-8")
    indexed = set(_extract_wikilinks(index_content))
    indexed = {x.split("|")[0].strip() for x in indexed}

    for name in pages:
        if name not in indexed:
            issues.append(
                LintIssue(
                    severity="info",
                    category="missing_index_entry",
                    page=str(pages[name]),
                    message=f"Page '{name}' not listed in index.md",
                    auto_fixable=True,
                )
            )

    return issues


def run_all_checks(wiki_dir: Path) -> list[LintIssue]:
    """Run all lint checks."""
    all_issues: list[LintIssue] = []
    all_issues.extend(check_frontmatter(wiki_dir))
    all_issues.extend(check_broken_wikilinks(wiki_dir))
    all_issues.extend(check_orphan_pages(wiki_dir))
    all_issues.extend(check_missing_index_entries(wiki_dir))
    return all_issues


def print_report(issues: list[LintIssue]) -> None:
    """Print lint report grouped by severity."""
    if not issues:
        click.echo("✓ Wiki is clean. No issues found.")
        return

    by_severity: dict[str, list[LintIssue]] = {
        "error": [],
        "warning": [],
        "info": [],
    }
    for issue in issues:
        by_severity[issue.severity].append(issue)

    errors = len(by_severity["error"])
    warnings = len(by_severity["warning"])
    infos = len(by_severity["info"])
    auto = sum(1 for i in issues if i.auto_fixable)

    click.echo(f"Wiki Lint Report — {sum(len(v) for v in by_severity.values())} issues")
    click.echo("=" * 60)

    for severity in ("error", "warning", "info"):
        items = by_severity[severity]
        if not items:
            continue
        marker = {"error": "✗", "warning": "⚠", "info": "ℹ"}[severity]
        click.echo(f"\n{severity.upper()}S ({len(items)}):")
        for issue in items:
            click.echo(f"  {marker} [{issue.category}] {issue.page}")
            click.echo(f"    {issue.message}")
            if issue.auto_fixable:
                click.echo("    Auto-fixable: yes")

    click.echo()
    click.echo(f"Summary: {errors} errors, {warnings} warnings, {infos} info. {auto} auto-fixable.")


@click.command()
@click.option("--fix", is_flag=True, help="Auto-fix safe issues")
@click.option("--category", help="Only check this category")
def main(fix: bool, category: str | None) -> None:
    """Check wiki health and report issues."""
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: wiki directory not found: {wiki_dir}", err=True)
        sys.exit(1)

    issues = run_all_checks(wiki_dir)

    if category:
        issues = [i for i in issues if i.category == category]

    print_report(issues)

    if fix:
        click.echo("\n--fix not yet implemented. Issues reported but not modified.")
        click.echo("(Auto-fix will come in Phase 2.)")

    has_errors = any(i.severity == "error" for i in issues)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
