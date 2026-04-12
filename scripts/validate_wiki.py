"""Hard validation of wiki/ integrity — non-zero exit if any page is broken.

Complements lint_wiki.py (which is advisory). validate_wiki.py fails the run
if there's corruption that MUST be fixed before moving on. Intended to be
invoked after every compile_all batch so we notice damage immediately.

Checks (all ERROR severity):
- Page has parseable YAML frontmatter
- Required fields present: title, page_type, status
- page_type matches the directory the file lives in
- No duplicate bodies (after hashing minus last_compiled)
- No "orphan body" where frontmatter was destroyed (only last_compiled present)
- status is one of the allowed values

Usage:
    uv run python scripts/validate_wiki.py            # report + exit 1 if bad
    uv run python scripts/validate_wiki.py --quiet    # only errors to stderr
    uv run python scripts/validate_wiki.py --list-bad # print bad file paths, one per line
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402

REQUIRED_FIELDS = {"title", "page_type", "status"}
VALID_STATUSES = {"current", "superseded", "contested"}
VALID_PAGE_TYPES = {"topic", "entity", "system", "policy", "timeline", "conflict"}
CATEGORY_TO_TYPE = {
    "topics": "topic",
    "entities": "entity",
    "systems": "system",
    "policies": "policy",
    "timelines": "timeline",
    "conflicts": "conflict",
}


@dataclass
class Error:
    page: Path
    reason: str


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].lstrip("\n")


def validate_page(path: Path) -> list[Error]:
    errors: list[Error] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Error(path, f"unreadable: {e}")]

    fm, body = _extract_frontmatter(content)

    if not fm:
        return [Error(path, "no parseable YAML frontmatter")]

    # Detect orphan body: only last_compiled present means auto-stamp
    # recovered from a broken frontmatter. Caller should re-compile this page.
    if set(fm.keys()) == {"last_compiled"}:
        return [Error(path, "orphan frontmatter (only last_compiled present)")]

    # Required fields
    missing = REQUIRED_FIELDS - set(fm.keys())
    if missing:
        errors.append(Error(path, f"missing required fields: {sorted(missing)}"))

    # page_type valid
    pt = fm.get("page_type")
    if pt and pt not in VALID_PAGE_TYPES:
        errors.append(Error(path, f"invalid page_type: {pt!r}"))

    # status valid
    st = fm.get("status")
    if st and st not in VALID_STATUSES:
        errors.append(Error(path, f"invalid status: {st!r}"))

    # page_type matches directory
    category = path.parent.name
    want = CATEGORY_TO_TYPE.get(category)
    if want and pt and pt != want:
        errors.append(
            Error(path, f"in {category}/ but page_type={pt!r}, expected {want!r}")
        )

    # Body exists (empty body is suspicious)
    if not body.strip():
        errors.append(Error(path, "empty body"))

    return errors


def check_duplicates(wiki_dir: Path) -> list[Error]:
    errors: list[Error] = []
    by_hash: dict[str, list[Path]] = {}

    for category in CATEGORY_TO_TYPE:
        cat_dir = wiki_dir / category
        if not cat_dir.exists():
            continue
        for path in cat_dir.glob("*.md"):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            normalized = re.sub(
                r"^last_compiled:.*$", "", content, flags=re.MULTILINE
            ).strip()
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            by_hash.setdefault(digest, []).append(path)

    for paths in by_hash.values():
        if len(paths) > 1:
            peers = ", ".join(p.name for p in paths)
            for p in paths:
                errors.append(Error(p, f"duplicate body shared with: {peers}"))

    return errors


def run(wiki_dir: Path) -> list[Error]:
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat_dir = wiki_dir / category
        if not cat_dir.exists():
            continue
        for path in cat_dir.glob("*.md"):
            errors.extend(validate_page(path))
    errors.extend(check_duplicates(wiki_dir))
    return errors


@click.command()
@click.option("--quiet", is_flag=True, help="Only print errors (to stderr)")
@click.option("--list-bad", is_flag=True, help="Print bad page paths, one per line")
def main(quiet: bool, list_bad: bool) -> None:
    """Validate wiki/; exit non-zero if any page is broken."""
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: {wiki_dir} not found", err=True)
        sys.exit(2)

    errors = run(wiki_dir)

    if list_bad:
        for e in sorted({str(e.page) for e in errors}):
            click.echo(e)
        sys.exit(1 if errors else 0)

    if not errors:
        if not quiet:
            click.echo("✓ Wiki is valid.")
        sys.exit(0)

    click.echo(f"✗ {len(errors)} validation error(s):", err=quiet)
    for e in errors:
        click.echo(f"  {e.page}: {e.reason}", err=quiet)
    sys.exit(1)


if __name__ == "__main__":
    main()
