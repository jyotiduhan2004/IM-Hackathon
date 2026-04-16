"""Conservative batch fixer for broken wikilinks in wiki/*.md.

`make publish-gate` runs `scripts/validate_wiki.py --quiet`, which fails
when wikilinks point at non-existent pages. A handful of recurring
patterns — `raw/*.md` targets, dotted-domain "wikilinks" like
`component.intermesh.net`, literal words like `[[system]]`, and case or
prefix drift on real slugs — account for nearly all of them. This script
rewrites those four classes deterministically and leaves everything else
for a human to triage via a timestamped audit file.

Rules (in order; the first match wins):

1. **Raw-file target** (`raw/*.md`): strip the `[[...]]` wrapper and
   replace with a plain-text citation like `see raw source (2026-01-14)`.
   raw/ files are never valid wikilink targets.
2. **Dotted-domain target** (`*.net`, `*.com`, `*.in`, etc): strip the
   wrapper, keep the literal domain. e.g. `[[component.intermesh.net]]`
   → `component.intermesh.net`.
3. **Exact normalized slug match**: lowercase + strip whitespace, try
   both `target` and `category/target` (e.g. `entities/foo`). If exactly
   one wiki page matches, rewrite to `[[slug]]`.
4. **Fuzzy candidate** with `difflib.SequenceMatcher().ratio() > 0.95`
   AND a single unique candidate: rewrite. Otherwise leave for manual.
5. **Leave for manual**: collected in
   `docs/audits/broken-wikilinks-<ISO>.md` with page, line number,
   original wikilink, and up to three best-guess candidates.

Usage:
    uv run python scripts/fix_broken_wikilinks.py --dry-run
    uv run python scripts/fix_broken_wikilinks.py --commit
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse validate_wiki's CATEGORY_TO_TYPE so we stay in lockstep with
# whatever directories the validator considers "pages". Avoids duplicate
# detection logic drifting apart.
from scripts.validate_wiki import CATEGORY_TO_TYPE  # noqa: E402
from src.config import settings  # noqa: E402

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
DATE_IN_RAW_RE = re.compile(r"raw/(\d{4}-\d{2}-\d{2})_")
# Any wikilink target containing a `.` followed by a 2-3 letter TLD-ish
# suffix counts as a dotted-domain. Covers intermesh.net, indiamart.com,
# foo.in, bar.co etc. We deliberately DON'T gate on a known TLD list —
# anything that looks like a domain is treated as a domain.
DOMAIN_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9\-.]*\.[a-z]{2,}(?:/.*)?$", re.IGNORECASE)


@dataclass
class Fix:
    """A single rewrite we applied (or would apply in --dry-run)."""

    page: Path
    original: str  # full `[[target]]` or `[[target|alias]]` text
    replacement: str  # new text (may be empty for strip-wrapper rules)
    rule: str  # which rule fired


@dataclass
class ManualItem:
    """A broken wikilink we refused to auto-fix."""

    page: Path
    line_no: int
    original: str
    target: str
    suggestions: list[str]


def _collect_known_slugs(wiki_dir: Path) -> dict[str, list[str]]:
    """Map normalized slug → list of real slugs that normalize to it.

    Normalization is `strip().lower()` — enough to catch the "typed
    Foo-Bar but file is foo-bar" drift without over-matching. Returns
    a list (not a set) so callers can detect ambiguous slugs ("two
    pages normalize to the same key — manual review").
    """
    by_normalized: dict[str, list[str]] = {}
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        for md in cat.glob("*.md"):
            key = md.stem.strip().lower()
            by_normalized.setdefault(key, []).append(md.stem)
    return by_normalized


def _strip_category_prefix(target: str) -> str:
    """`entities/foo` → `foo`, `foo` → `foo`.

    Sources frontmatter sometimes types the category path into a
    wikilink (e.g. `[[entities/alice]]`) even though our links use
    bare slugs. Strip a leading `<known-category>/` if present.
    """
    if "/" in target:
        head, _, tail = target.partition("/")
        if head in CATEGORY_TO_TYPE:
            return tail
    return target


def _classify_rule(
    target: str,
    alias: str | None,
    known_by_norm: dict[str, list[str]],
    all_slugs: list[str],
) -> tuple[str, str | None, list[str]]:
    """Decide which rule fires for this broken target.

    Returns `(rule_name, replacement_text | None, suggestions)`:
    - `rule_name` identifies which rule fired (see module docstring).
    - `replacement_text` is the literal string to splice back in, or
      `None` when we refuse to auto-fix (caller routes to manual).
    - `suggestions` is the top-3 closest real slugs (only populated on
      the manual path, for the audit file).
    """
    # Rule 1: raw/*.md → "see raw source (YYYY-MM-DD)" if we can parse
    # the date prefix, otherwise "see raw source".
    if target.startswith("raw/"):
        date_match = DATE_IN_RAW_RE.search(target)
        replacement = f"see raw source ({date_match.group(1)})" if date_match else "see raw source"
        return ("raw-target", replacement, [])

    # Rule 2: dotted-domain → keep literal string. Alias wins as
    # intended visible label if caller provided `[[foo.com|Foo]]`.
    if DOMAIN_TARGET_RE.match(target):
        return ("dotted-domain", alias.strip() if alias else target, [])

    # Strip `entities/` etc before matching, so the category-prefixed
    # variant shares the same normalized key as the bare slug.
    normalized = _strip_category_prefix(target).strip().lower()

    # Rule 3: exact normalized match → must be unique.
    matches = known_by_norm.get(normalized, [])
    if len(matches) == 1:
        canonical = matches[0]
        wiki = f"[[{canonical}|{alias}]]" if alias else f"[[{canonical}]]"
        return ("exact-match", wiki, [])

    # Rule 4: fuzzy match. Multiple candidates tied at the threshold →
    # manual; we don't want to guess between near-ties.
    close = difflib.get_close_matches(normalized, all_slugs, n=5, cutoff=0.95)
    if len(close) == 1:
        canonical = close[0]
        wiki = f"[[{canonical}|{alias}]]" if alias else f"[[{canonical}]]"
        return ("fuzzy-match", wiki, [])

    # Manual review — surface the top-3 closest slugs regardless of
    # threshold so the operator has somewhere to start.
    suggestions = difflib.get_close_matches(normalized, all_slugs, n=3, cutoff=0.0)
    return ("manual", None, suggestions)


def _process_page(
    path: Path,
    known_by_norm: dict[str, list[str]],
    all_slugs: list[str],
    real_slugs: set[str],
) -> tuple[str, list[Fix], list[ManualItem]]:
    """Rewrite broken wikilinks in one page's content.

    Returns the new content, the list of applied fixes, and the list
    of broken links we refused to auto-fix. Doesn't write to disk —
    that's the caller's decision (driven by --commit).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Unreadable pages are validate_wiki's problem, not ours.
        return "", [], []

    fixes: list[Fix] = []
    manual: list[ManualItem] = []

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        if "|" in inner:
            target, _, alias = inner.partition("|")
            target = target.strip()
            alias_val: str | None = alias.strip()
        else:
            target = inner.strip()
            alias_val = None

        # A link that already resolves to a real page is untouched.
        if target in real_slugs:
            return match.group(0)

        rule, replacement, suggestions = _classify_rule(target, alias_val, known_by_norm, all_slugs)

        if replacement is None:
            manual.append(
                ManualItem(
                    page=path,
                    line_no=content.count("\n", 0, match.start()) + 1,
                    original=match.group(0),
                    target=target,
                    suggestions=suggestions,
                )
            )
            return match.group(0)

        fixes.append(
            Fix(
                page=path,
                original=match.group(0),
                replacement=replacement,
                rule=rule,
            )
        )
        return replacement

    new_content = WIKILINK_RE.sub(replace, content)
    return new_content, fixes, manual


def fix_wiki(
    wiki_dir: Path,
    *,
    commit: bool,
) -> tuple[list[Fix], list[ManualItem]]:
    """Walk wiki/, apply (or preview) fixes, collect manual items."""
    known_by_norm = _collect_known_slugs(wiki_dir)
    all_slugs = [s for group in known_by_norm.values() for s in group]
    real_slugs = set(all_slugs)

    all_fixes: list[Fix] = []
    all_manual: list[ManualItem] = []

    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        for path in sorted(cat.glob("*.md")):
            new_content, fixes, manual = _process_page(path, known_by_norm, all_slugs, real_slugs)
            all_fixes.extend(fixes)
            all_manual.extend(manual)
            if commit and fixes:
                path.write_text(new_content, encoding="utf-8")

    return all_fixes, all_manual


def _write_manual_review(
    manual: list[ManualItem],
    audit_path: Path,
    timestamp: str,
) -> None:
    """Persist remaining broken links as a markdown file for human review.

    Grouped by page so the operator can tackle one file at a time.
    Creates the parent directory if missing (first run of this script
    after a clean checkout).
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    by_page: dict[Path, list[ManualItem]] = {}
    for item in manual:
        by_page.setdefault(item.page, []).append(item)

    lines: list[str] = [
        f"# Broken wikilinks — manual review ({timestamp})",
        "",
        f"Generated by `scripts/fix_broken_wikilinks.py` — {len(manual)} link(s) "
        f"across {len(by_page)} page(s) need human resolution.",
        "",
        "For each entry, either:",
        "- pick one of the suggested slugs and rewrite the wikilink, or",
        "- strip the `[[...]]` wrapper if no target fits, or",
        "- create the referenced page.",
        "",
    ]

    for page in sorted(by_page, key=lambda p: str(p)):
        lines.append(f"## {page}")
        lines.append("")
        for item in by_page[page]:
            suggestions = (
                ", ".join(f"`{s}`" for s in item.suggestions[:3])
                if item.suggestions
                else "_no close matches_"
            )
            lines.append(f"- Line {item.line_no}: `{item.original}` → suggestions: {suggestions}")
        lines.append("")

    audit_path.write_text("\n".join(lines), encoding="utf-8")


def _print_summary(
    fixes: list[Fix],
    manual: list[ManualItem],
    audit_path: Path,
    *,
    commit: bool,
) -> None:
    """Human-readable stdout summary: rule counts, manual count, file path."""
    by_rule: dict[str, int] = {}
    for fix in fixes:
        by_rule[fix.rule] = by_rule.get(fix.rule, 0) + 1

    verb = "Applied" if commit else "Would apply"
    click.echo(f"{verb} {len(fixes)} fix(es):")
    for rule in sorted(by_rule):
        click.echo(f"  - {rule}: {by_rule[rule]}")
    click.echo(f"Manual review needed: {len(manual)} link(s)")
    if manual:
        click.echo(f"  → see {audit_path}")


@click.command()
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Preview fixes without modifying wiki/ files (default).",
)
@click.option(
    "--commit",
    "commit",
    is_flag=True,
    default=False,
    help="Apply fixes to disk. Mutually exclusive with --dry-run.",
)
def main(dry_run: bool, commit: bool) -> None:
    """Conservative broken-wikilink batch fixer.

    Exit codes:
    - 0: nothing to fix OR all items auto-fixed.
    - 1 (--commit only): manual-review items remain; audit file written.
    - 2: misuse (both --dry-run and --commit, or neither).
    """
    if dry_run and commit:
        click.echo("error: --dry-run and --commit are mutually exclusive", err=True)
        sys.exit(2)
    # Default to dry-run for safety — callers must opt into mutation
    # explicitly. `commit` stays authoritative from here on.

    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"error: {wiki_dir} not found", err=True)
        sys.exit(2)

    fixes, manual = fix_wiki(wiki_dir, commit=commit)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    audit_path = REPO_ROOT / "docs" / "audits" / f"broken-wikilinks-{timestamp}.md"
    _write_manual_review(manual, audit_path, timestamp)

    _print_summary(fixes, manual, audit_path, commit=commit)

    # On --commit, we want `make publish-gate` to stay red if anything
    # remains manually. On --dry-run, exit 0 regardless — the caller is
    # just previewing.
    if commit and manual:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
