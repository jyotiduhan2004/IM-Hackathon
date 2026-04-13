"""Hard validation of wiki/ integrity — non-zero exit if any page is broken.

Complements lint_wiki.py (which is advisory). validate_wiki.py fails the run
if there's corruption that MUST be fixed before moving on. Intended to be
invoked after every compile_all batch so we notice damage immediately.

Checks (ERROR severity — exit code 1):
- Page has parseable YAML frontmatter
- Required fields present: title, page_type, status
- page_type matches the directory the file lives in
- No duplicate bodies (after hashing minus last_compiled)
- No "orphan body" where frontmatter was destroyed (only last_compiled present)
- status is one of the allowed values

Checks (WARN severity — stderr only, no exit-code effect):
- Entity page has `email:` in frontmatter (entity-missing-email)
- Entity `email:` is a valid RFC-ish address (entity-invalid-email)
- Entity slug matches email_to_slug(email) (entity-slug-mismatch —
  legacy display-name slugs are flagged but not blocked)

Usage:
    uv run python scripts/validate_wiki.py            # report + exit 1 on errors
    uv run python scripts/validate_wiki.py --quiet    # suppress warnings, only errors
    uv run python scripts/validate_wiki.py --list-bad # print bad file paths (errors only)
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


@dataclass
class ValidationWarning:
    page: Path
    reason: str
    check: str


from src.utils import split_frontmatter  # noqa: E402

# Entity identity helpers — prefer the canonical implementation in
# src.compile.entities (shipped by W0). Fallback to inline regex when that
# module isn't on the current branch, so this validator stays runnable even
# on main before W0 merges.
try:
    from src.compile.entities import email_to_slug as _email_to_slug
    from src.compile.entities import is_valid_email as _is_valid_email

    _HAS_ENTITY_HELPERS = True
except ImportError:
    _HAS_ENTITY_HELPERS = False
    _FALLBACK_EMAIL_RE = re.compile(r"^[a-z0-9._+\-]+@[a-z0-9.\-]+\.[a-z]+$")

    def _is_valid_email(email: str) -> bool:
        return bool(_FALLBACK_EMAIL_RE.match(email.strip().lower()))

    def _email_to_slug(email: str) -> str:  # pragma: no cover - fallback only
        raise NotImplementedError(
            "email_to_slug unavailable without src.compile.entities"
        )


class _DuplicateKeyLoader(yaml.SafeLoader):
    """PyYAML loader that raises on duplicate mapping keys.

    Default SafeLoader silently keeps the last value, hiding corruption like
    `last_compiled:` appearing twice. We need to flag those for humans.
    """


def _construct_mapping_strict(loader: yaml.Loader, node: yaml.nodes.MappingNode) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key: {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_strict
)


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Line-aware frontmatter extraction. See src.utils.split_frontmatter."""
    fm_text, body = split_frontmatter(content)
    if not fm_text:
        return {}, body
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


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
        errors.append(Error(path, f"in {category}/ but page_type={pt!r}, expected {want!r}"))

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
            normalized = re.sub(r"^last_compiled:.*$", "", content, flags=re.MULTILINE).strip()
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            by_hash.setdefault(digest, []).append(path)

    for paths in by_hash.values():
        if len(paths) > 1:
            peers = ", ".join(p.name for p in paths)
            for p in paths:
                errors.append(Error(p, f"duplicate body shared with: {peers}"))

    return errors


SUFFIX_PATTERN = re.compile(
    r"^(.*?)-(new|v\d+|copy|latest|updated|temp|draft|rev\d*|clean)$"
)
NUMERIC_SUFFIX_PATTERN = re.compile(r"^(.+?)(\d+)$")
SPELLING_PAIRS = (
    ("labelling", "labeling"),
    ("optimise", "optimize"),
    ("behaviour", "behavior"),
    ("favourite", "favorite"),
    ("colour", "color"),
    ("centre", "center"),
    ("organisation", "organization"),
)


def check_duplicate_suffix_variants(wiki_dir: Path) -> list[Error]:
    """Flag pages with suffix-twin pattern: `-new`, `-v2`, `-clean`, etc.

    The compiler creates sibling pages with these suffixes when it can't
    tell a page already exists (e.g. `varnika-singh.md` plus
    `varnika-singh-new.md`). Detects the pattern so the merger can collapse them.
    """
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        stems = {p.stem for p in cat.glob("*.md")}
        for p in cat.glob("*.md"):
            match = SUFFIX_PATTERN.match(p.stem)
            if not match:
                continue
            base = match.group(1)
            if base in stems:
                errors.append(
                    Error(
                        p,
                        f"suspected duplicate of {base} "
                        f"(suffix '{match.group(2)}' — agent variant, should be merged)",
                    )
                )
    return errors


def check_numeric_variants(wiki_dir: Path) -> list[Error]:
    """Flag `alok-kumar2` when `alok-kumar` exists in the same category.

    Only fires when the non-digit base exists — avoids flagging legit slugs
    with embedded digits like `himanshu-jain01` when `himanshu-jain` does
    not exist.
    """
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        stems = {p.stem for p in cat.glob("*.md")}
        for p in cat.glob("*.md"):
            match = NUMERIC_SUFFIX_PATTERN.match(p.stem)
            if not match:
                continue
            base = match.group(1)
            if base in stems and base != p.stem:
                errors.append(
                    Error(
                        p,
                        f"numeric-suffix duplicate of {base} "
                        f"(trailing '{match.group(2)}' — should be merged)",
                    )
                )
    return errors


def check_spelling_variants(wiki_dir: Path) -> list[Error]:
    """Flag US/UK spelling pairs sitting in the same category.

    Example: `dspy-gepa-...-labeling` and `dspy-gepa-...-labelling-pipeline`
    co-exist — same topic, different spelling. Merger picks a canonical.
    """
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        stems = {p.stem for p in cat.glob("*.md")}
        seen: set[tuple[str, str]] = set()
        for stem in stems:
            for uk, us in SPELLING_PAIRS:
                for a, b in ((uk, us), (us, uk)):
                    if a in stem:
                        sibling = stem.replace(a, b, 1)
                        if sibling != stem and sibling in stems:
                            pair = tuple(sorted((stem, sibling)))
                            if pair in seen:
                                continue
                            seen.add(pair)
                            errors.append(
                                Error(
                                    cat / f"{pair[1]}.md",
                                    f"US/UK spelling twin of {pair[0]} "
                                    f"({a} ↔ {b}); pick a canonical",
                                )
                            )
    return errors


def check_same_email_duplicates(wiki_dir: Path) -> list[Error]:
    """Two entity pages declaring the same `email:` → same person, two slugs."""
    errors: list[Error] = []
    by_email: dict[str, list[Path]] = {}
    ent = wiki_dir / "entities"
    if not ent.exists():
        return errors
    for p in ent.glob("*.md"):
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _extract_frontmatter(content)
        email = fm.get("email")
        if not isinstance(email, str):
            # Fall back to scanning the body for "Email: x@y"
            m = re.search(
                r"(?mi)^\s*(?:\*\*)?email(?:\*\*)?[:\s]+"
                r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+)",
                body,
            )
            if m:
                email = m.group(1).lower()
        if email:
            by_email.setdefault(email.lower(), []).append(p)
    for email, paths in by_email.items():
        if len(paths) > 1:
            peers = ", ".join(sorted(p.stem for p in paths))
            for p in paths:
                errors.append(
                    Error(p, f"shares email '{email}' with: {peers} — same person, pick canonical")
                )
    return errors


def check_yaml_integrity(wiki_dir: Path) -> list[Error]:
    """Parse each page's frontmatter with a strict loader that rejects dup keys.

    Default `yaml.safe_load` silently keeps the last duplicate — hiding bugs
    like a page ending up with two `last_compiled:` keys after a messy
    edit_file pass.
    """
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        for path in cat.glob("*.md"):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm_text, _ = split_frontmatter(content)
            if not fm_text:
                continue
            try:
                yaml.load(fm_text, Loader=_DuplicateKeyLoader)
            except yaml.YAMLError as e:
                errors.append(Error(path, f"YAML integrity: {e}"))
    return errors


def check_duplicate_headings(wiki_dir: Path) -> list[Error]:
    """Fail when a body has two H2 with the same text.

    Most common cause: the updater appends a new `## Related` section
    instead of merging into the existing one.
    """
    heading_re = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        for path in cat.glob("*.md"):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            _, body = split_frontmatter(content)
            headings = [h.strip() for h in heading_re.findall(body)]
            seen: dict[str, int] = {}
            for h in headings:
                seen[h] = seen.get(h, 0) + 1
            dups = sorted(h for h, n in seen.items() if n > 1)
            if dups:
                errors.append(Error(path, f"duplicate H2 heading(s): {dups}"))
    return errors


def check_broken_wikilinks(wiki_dir: Path) -> list[Error]:
    """Fail-hard on wikilinks that don't resolve to a real page.

    Previously only the advisory lint flagged these. Broken wikilinks mean
    users click and 404 in the browser — promoted to blocking error.
    """
    errors: list[Error] = []
    known: set[str] = set()
    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if cat.exists():
            known.update(p.stem for p in cat.glob("*.md"))

    import re as _re

    for category in CATEGORY_TO_TYPE:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        for path in cat.glob("*.md"):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            broken = [
                link.split("|")[0].strip()
                for link in _re.findall(r"\[\[([^\]]+)\]\]", content)
                if link.split("|")[0].strip() not in known
            ]
            if broken:
                # Report one line per page, listing first 3 broken targets
                preview = ", ".join(broken[:3])
                more = f" (+{len(broken) - 3} more)" if len(broken) > 3 else ""
                errors.append(
                    Error(path, f"{len(broken)} broken wikilink(s): {preview}{more}")
                )
    return errors


def check_entity_identity(wiki_dir: Path) -> list[ValidationWarning]:
    """Entity pages should carry `email:` and use a deterministic slug.

    Three WARN-level signals:
    - `entity-missing-email`: no `email:` in frontmatter
    - `entity-invalid-email`: `email:` is set but doesn't match an RFC-ish shape
    - `entity-slug-mismatch`: filename stem doesn't match email_to_slug(email);
      most existing pages were named after display names, so this is legacy
      drift rather than corruption. Skipped if src.compile.entities isn't
      importable (keeps this validator runnable on branches without W0).
    """
    warnings: list[ValidationWarning] = []
    ent = wiki_dir / "entities"
    if not ent.exists():
        return warnings
    for path in sorted(ent.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = _extract_frontmatter(content)
        if not fm:
            # Frontmatter corruption is already an ERROR in validate_page.
            continue
        email = fm.get("email")
        if not isinstance(email, str) or not email.strip():
            warnings.append(
                ValidationWarning(
                    path, "missing `email:` in frontmatter", "entity-missing-email"
                )
            )
            continue
        email_lc = email.strip().lower()
        if not _is_valid_email(email_lc):
            warnings.append(
                ValidationWarning(
                    path,
                    f"`email:` is not a valid address: {email!r}",
                    "entity-invalid-email",
                )
            )
            continue
        if _HAS_ENTITY_HELPERS:
            try:
                canonical = _email_to_slug(email_lc)
            except (TypeError, ValueError):
                continue
            if path.stem != canonical:
                warnings.append(
                    ValidationWarning(
                        path,
                        f"slug {path.stem!r} != email_to_slug({email_lc!r})={canonical!r} "
                        "(legacy display-name slug)",
                        "entity-slug-mismatch",
                    )
                )
    return warnings


def run(wiki_dir: Path) -> tuple[list[Error], list[ValidationWarning]]:
    errors: list[Error] = []
    for category in CATEGORY_TO_TYPE:
        cat_dir = wiki_dir / category
        if not cat_dir.exists():
            continue
        for path in cat_dir.glob("*.md"):
            errors.extend(validate_page(path))
    errors.extend(check_duplicates(wiki_dir))
    errors.extend(check_duplicate_suffix_variants(wiki_dir))
    errors.extend(check_numeric_variants(wiki_dir))
    errors.extend(check_spelling_variants(wiki_dir))
    errors.extend(check_same_email_duplicates(wiki_dir))
    errors.extend(check_yaml_integrity(wiki_dir))
    errors.extend(check_duplicate_headings(wiki_dir))
    errors.extend(check_broken_wikilinks(wiki_dir))
    warnings = check_entity_identity(wiki_dir)
    return errors, warnings


@click.command()
@click.option("--quiet", is_flag=True, help="Suppress warnings; still print errors")
@click.option("--list-bad", is_flag=True, help="Print bad page paths (errors only), one per line")
def main(quiet: bool, list_bad: bool) -> None:
    """Validate wiki/; exit non-zero on ERRORs only (warnings are informational)."""
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: {wiki_dir} not found", err=True)
        sys.exit(2)

    errors, warnings = run(wiki_dir)

    if list_bad:
        for e in sorted({str(e.page) for e in errors}):
            click.echo(e)
        sys.exit(1 if errors else 0)

    if errors:
        click.echo(f"✗ {len(errors)} validation error(s):", err=quiet)
        for e in errors:
            click.echo(f"  {e.page}: {e.reason}", err=quiet)
    if warnings and not quiet:
        click.echo(f"⚠ {len(warnings)} warning(s):", err=True)
        for w in warnings:
            click.echo(f"  [{w.check}] {w.page}: {w.reason}", err=True)

    if not errors:
        if not quiet:
            if warnings:
                click.echo(f"✓ no errors, {len(warnings)} warnings")
            else:
                click.echo("✓ Wiki is valid.")
        sys.exit(0)

    sys.exit(1)


if __name__ == "__main__":
    main()
