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
from typing import Any
from typing import Literal

import click

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
# Nav-only landing pages (page_type: index) don't carry sources/last_compiled —
# they're MkDocs nav placeholders, not compiled content.
_INDEX_REQUIRED_FRONTMATTER = {"title", "page_type", "status"}
VALID_STATUSES = {"current", "superseded", "contested"}
VALID_PAGE_TYPES = {"topic", "entity", "system", "policy", "timeline", "conflict", "index"}
WIKI_CATEGORIES = ("topics", "entities", "systems", "policies", "timelines", "conflicts")


from src.utils import extract_frontmatter as _shared_extract  # noqa: E402


def _extract_frontmatter(content: str) -> dict[str, Any]:
    """Delegate to shared line-aware parser."""
    return _shared_extract(content)


def _extract_wikilinks(content: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", content)


def _get_wiki_pages(wiki_dir: Path) -> dict[str, Path]:
    """Map page name (stem) → file path for all wiki pages."""
    pages: dict[str, Path] = {}
    for category in WIKI_CATEGORIES:
        cat_dir = wiki_dir / category
        if cat_dir.exists():
            for md in cat_dir.glob("*.md"):
                pages[md.stem] = md
    return pages


def check_frontmatter(wiki_dir: Path) -> list[LintIssue]:
    """Check every wiki page has valid frontmatter."""
    issues: list[LintIssue] = []
    pages = _get_wiki_pages(wiki_dir)

    for _name, path in pages.items():
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

        required = (
            _INDEX_REQUIRED_FRONTMATTER if fm.get("page_type") == "index" else REQUIRED_FRONTMATTER
        )
        missing = required - set(fm.keys())
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

    for _name, path in pages.items():
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


def _slugify(text: str) -> str:
    """Convert a string to a kebab-case slug for filename matching."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")


# Auto-stub creation for unresolved wikilinks was REMOVED in v10-U8 (GH #12).
# The old `create_missing_stubs` function inferred "person vs system" from
# a slug-shape heuristic and wrote `wiki/entities/<slug>.md` — which is how
# garbage slugs (`vishakha-indiamart`, `akash-singh6`, `arjun-gaur-clean`)
# entered the catalog. Per CLAUDE.md ("NEVER invent entity slugs"), entity
# pages must go through `create_entities(email=..., display_name=...)` so
# the slug is derived from the canonical email address.
#
# Broken wikilinks are still surfaced — see `check_broken_wikilinks` (here)
# and the reviewer's `broken_wikilink` rule (src/compile/reviewer.py). The
# agent can respond by calling `create_entities()` with the proper email,
# rewriting the wikilink, or dropping it.


def normalize_wikilinks(wiki_dir: Path, dry_run: bool = False) -> list[LintIssue]:
    """Auto-fix broken wikilinks by matching Title Case targets to kebab-case files.

    For every `[[Some Target]]` in wiki pages:
    - If `wiki/**/Some Target.md` exists (exact match), skip
    - Else try `_slugify("Some Target") == file.stem` → rewrite link to `[[some-target]]`
    - Else lowercase and compare → rewrite if match
    - Else leave as-is (will be caught by check_broken_wikilinks)

    Returns list of LintIssue entries describing what was (or would be) fixed.
    """
    pages = _get_wiki_pages(wiki_dir)
    # Build lookup: lowercase stem → canonical stem
    stem_by_lower: dict[str, str] = {p.lower(): p for p in pages}

    issues: list[LintIssue] = []

    for _name, path in pages.items():
        content = path.read_text(encoding="utf-8")
        original = content

        def replace_link(match: re.Match[str]) -> str:
            target = match.group(1).split("|")[0].strip()
            if target in pages:
                return match.group(0)  # already canonical
            # Try case-insensitive exact match
            if target.lower() in stem_by_lower:
                canonical = stem_by_lower[target.lower()]
                return f"[[{canonical}]]"
            # Try slugify + lookup
            slug = _slugify(target)
            if slug in pages:
                return f"[[{slug}]]"
            return match.group(0)  # can't fix

        new_content = re.sub(r"\[\[([^\]]+)\]\]", replace_link, content)

        if new_content != original:
            diff_count = sum(
                1
                for a, b in zip(
                    re.findall(r"\[\[[^\]]+\]\]", original),
                    re.findall(r"\[\[[^\]]+\]\]", new_content),
                    strict=False,
                )
                if a != b
            )
            issues.append(
                LintIssue(
                    severity="info",
                    category="wikilink_normalized",
                    page=str(path),
                    message=f"Would normalize {diff_count} wikilinks"
                    if dry_run
                    else f"Normalized {diff_count} wikilinks",
                    auto_fixable=True,
                )
            )
            if not dry_run:
                path.write_text(new_content, encoding="utf-8")

    return issues


def check_duplicate_bodies(wiki_dir: Path) -> list[LintIssue]:
    """Detect wiki pages with byte-identical bodies (sans last_compiled).

    Caught a real bug: systems/export-indiamart.md was a byte-for-byte copy of
    systems/tawk-to.md (only last_compiled differed). This check would have
    flagged it.
    """
    import hashlib

    issues: list[LintIssue] = []
    by_hash: dict[str, list[Path]] = {}

    for path in _get_wiki_pages(wiki_dir).values():
        content = path.read_text(encoding="utf-8")
        # Strip last_compiled line so timestamp differences don't mask dupes
        normalized = re.sub(r"^last_compiled:.*$", "", content, flags=re.MULTILINE).strip()
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        by_hash.setdefault(digest, []).append(path)

    for paths in by_hash.values():
        if len(paths) > 1:
            names = ", ".join(p.name for p in paths)
            for p in paths:
                issues.append(
                    LintIssue(
                        severity="error",
                        category="duplicate_body",
                        page=str(p),
                        message=f"Byte-identical body shared with: {names}",
                    )
                )

    return issues


def check_page_type_mismatch(wiki_dir: Path) -> list[LintIssue]:
    """Flag pages where directory and page_type frontmatter disagree."""
    issues: list[LintIssue] = []
    expected = {
        "topics": "topic",
        "entities": "entity",
        "systems": "system",
        "policies": "policy",
        "timelines": "timeline",
        "conflicts": "conflict",
    }
    for path in _get_wiki_pages(wiki_dir).values():
        category = path.parent.name
        want = expected.get(category)
        if not want:
            continue
        fm = _extract_frontmatter(path.read_text(encoding="utf-8"))
        got = fm.get("page_type")
        if got and got != want and got != "index":
            issues.append(
                LintIssue(
                    severity="error",
                    category="page_type_mismatch",
                    page=str(path),
                    message=f"In {category}/ but page_type={got!r}, expected {want!r}",
                )
            )
    return issues


def run_all_checks(wiki_dir: Path) -> list[LintIssue]:
    """Run all lint checks."""
    all_issues: list[LintIssue] = []
    all_issues.extend(check_frontmatter(wiki_dir))
    all_issues.extend(check_page_type_mismatch(wiki_dir))
    all_issues.extend(check_duplicate_bodies(wiki_dir))
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
        marker = {"error": "FAIL", "warning": "WARN", "info": "INFO"}[severity]
        click.echo(f"\n{severity.upper()}S ({len(items)}):")
        for issue in items:
            click.echo(f"  {marker} [{issue.category}] {issue.page}")
            click.echo(f"    {issue.message}")
            if issue.auto_fixable:
                click.echo("    Auto-fixable: yes")

    click.echo()
    click.echo(f"Summary: {errors} errors, {warnings} warnings, {infos} info. {auto} auto-fixable.")


@click.command()
@click.option("--fix", is_flag=True, help="Auto-fix safe issues (normalize wikilinks)")
@click.option("--category", help="Only check this category")
def main(fix: bool, category: str | None) -> None:
    """Check wiki health and report issues."""
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: wiki directory not found: {wiki_dir}", err=True)
        sys.exit(1)

    if fix:
        click.echo("Auto-fixing wikilinks (normalizing Title Case → kebab-case)...")
        fixed = normalize_wikilinks(wiki_dir, dry_run=False)
        click.echo(f"Normalized wikilinks in {len(fixed)} pages.")
        click.echo()

    issues = run_all_checks(wiki_dir)

    if category:
        issues = [i for i in issues if i.category == category]

    print_report(issues)

    if not fix and any(i.auto_fixable for i in issues):
        click.echo("\nRun with --fix to auto-normalize wikilinks.")

    has_errors = any(i.severity == "error" for i in issues)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
