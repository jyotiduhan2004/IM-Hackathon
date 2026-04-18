"""Strip dates from H2 section titles; move into body prefix.

Bug F legacy cleanup. The compile agent used to bake dates directly
into H2 headings (`## Bug report (Jan 16, 2026)`), creating parallel
dated sections rather than growing one canonical block. The prompt
has since been fixed; this script cleans up the ~207 remaining
offenders across ~74 pages.

For each dated H2:
1. Strip the dated parenthetical(s) from the title (leaves a bare
   concept like `## Bug report`).
2. If we could parse a specific YYYY-MM-DD from the stripped text,
   prepend `As of YYYY-MM-DD:` to the first content line of the
   section. Body sentences are otherwise preserved byte-for-byte.

Usage:
    uv run python scripts/fix_dated_h2_sections.py --dry-run
    uv run python scripts/fix_dated_h2_sections.py --commit
    uv run python scripts/fix_dated_h2_sections.py --commit --limit 5

--dry-run is the default (safe). Only --commit writes to disk.

Safe to delete after: 2026-06-18
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import split_frontmatter  # noqa: E402

# Categories to scan — matches validate_wiki.check_dated_h2_sections.
# People/decisions/entities pages are outside the Bug F blast radius.
CATEGORIES = ("topics", "systems", "policies")

# Month name → zero-padded month number. Covers both abbreviated and
# full forms; case-insensitive match on the key.
_MONTHS: dict[str, str] = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}  # fmt: skip

_MONTH_PATTERN = "|".join(sorted(_MONTHS.keys(), key=len, reverse=True))

# ISO date 2026-01-14 — most specific, highest priority.
_RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# "Jan 14, 2026" / "January 14, 2026" / "Jan 7-8, 2026" / day-range with
# en/em-dash. First day of a range wins — "most specific" for a
# multi-day span is the start. Day supports optional ordinal suffix
# ("14th"). Dash covers hyphen, en-dash (U+2013), em-dash (U+2014).
_RE_MONTH_DAY_YEAR = re.compile(
    rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:\s*[\u2013\u2014-]\s*\d{{1,2}}(?:st|nd|rd|th)?)?"
    rf"(?:,|\s)\s*(\d{{4}})\b",
    re.IGNORECASE,
)

# "14 Jan 2026" / "14th December 2025" (DMY order) / "24-31 Dec 2025".
_RE_DAY_MONTH_YEAR = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:\s*[\u2013\u2014-]\s*\d{{1,2}}(?:st|nd|rd|th)?)?"
    rf"\s+({_MONTH_PATTERN})\s+(\d{{4}})\b",
    re.IGNORECASE,
)

_RE_HEADING = re.compile(r"^(##\s+)(.+?)\s*$")
# Trailing editorial annotation like " — superseded" / " - live" that
# modified the stripped date. Peeled alongside the dated parens so
# "Original rules (v1 — Jan 16, 2026) — superseded" collapses cleanly.
_RE_TRAILING_ANNOTATION = re.compile(
    r"\s*[\u2014\u2013-]\s*(superseded|live|launched|effective)\s*$",
    re.IGNORECASE,
)
# "Dated" detection must mirror validate_wiki._is_dated_h2 so we target
# the exact set it flags: a bare month-name in parens like
# `(January 2026)` counts even without a day, and a parens-wrapped
# year like `(Q1 2026)` counts too. These are broader than the
# YYYY-MM-DD extraction used by _extract_date.
_RE_DATED_MONTH_IN_PARENS = re.compile(
    rf"\([^)]*\b({_MONTH_PATTERN})\b[^)]*\)",
    re.IGNORECASE,
)
_RE_DATED_YEAR_IN_PARENS = re.compile(r"\([^)]*\b20\d{2}\b[^)]*\)")


@dataclass
class Rewrite:
    """A single H2 title rewrite on one page."""

    page: Path
    original_title: str
    new_title: str
    parsed_date: str | None  # YYYY-MM-DD, or None if no specific date found


@dataclass
class PageResult:
    """Outcome of processing one page: new body + all rewrites applied."""

    new_body: str
    rewrites: list[Rewrite] = field(default_factory=list)


def _is_dated_title(title: str) -> bool:
    """Mirror validate_wiki._is_dated_h2 exactly — dates, month names,
    or a parens-wrapped year. The regex here is intentionally identical
    so the fixer targets the same set the validator flags."""
    return bool(
        _RE_ISO.search(title)
        or _RE_DATED_MONTH_IN_PARENS.search(title)
        or _RE_DATED_YEAR_IN_PARENS.search(title)
    )


def _extract_date(text: str) -> str | None:
    """Return the most specific YYYY-MM-DD from `text`, or None.

    Priority order:
      1. ISO date (2026-01-14) — unambiguous.
      2. Month-Day-Year (Jan 14, 2026) — day-specific.
      3. Day-Month-Year (14 Jan 2026) — day-specific.

    Month-only ("January 2026") and year-only references don't give
    us a day, so we return None and the caller skips the body prefix.
    That matches the plan's rule: use the *most specific* date.
    """
    iso_match = _RE_ISO.search(text)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

    mdy = _RE_MONTH_DAY_YEAR.search(text)
    if mdy:
        month = _MONTHS[mdy.group(1).lower()]
        day = mdy.group(2).zfill(2)
        year = mdy.group(3)
        return f"{year}-{month}-{day}"

    dmy = _RE_DAY_MONTH_YEAR.search(text)
    if dmy:
        day = dmy.group(1).zfill(2)
        month = _MONTHS[dmy.group(2).lower()]
        year = dmy.group(3)
        return f"{year}-{month}-{day}"

    return None


def _find_trailing_paren_start(text: str) -> int | None:
    """Return the start index of the last balanced top-level `(...)`
    group at the tail of `text`, or None if one doesn't exist.

    Handles nested parens — a `[^()]*` regex misses
    `(Jan 22, ...(2026-01-22))` because the inner parens break its
    character class. Walk right-to-left, matching `)` with balance.
    """
    s = text.rstrip()
    if not s.endswith(")"):
        return None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        c = s[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                return i
    return None


def _strip_trailing_dated_parens(title: str) -> tuple[str, str]:
    """Peel trailing dated parenthetical groups off the title.

    Returns (clean_title, stripped_text). Alternates between stripping
    an editorial annotation (" — superseded") and a dated trailing
    `(...)` group so combinations like
    `Original rules (v1 — Jan 16, 2026) — superseded` collapse to
    `Original rules`. Non-dated trailing parens (`(v2)`) are left
    alone — only dated ones get peeled. Balance-aware so nested
    parens like `(Jan 22, ...(2026-01-22))` strip as a single unit.
    """
    stripped_parts: list[str] = []
    current = title.rstrip()

    while True:
        # Peel any trailing editorial annotation first — otherwise
        # the trailing "(dated)" won't be at end-of-string.
        new = _RE_TRAILING_ANNOTATION.sub("", current).rstrip()
        if new != current:
            current = new
            continue

        start = _find_trailing_paren_start(current)
        if start is None:
            break
        group = current[start:].strip()
        if not _is_dated_title(group):
            break
        stripped_parts.append(group)
        current = current[:start].rstrip()

    return current.rstrip(), " ".join(stripped_parts)


_STRUCTURED_BLOCK_RE = re.compile(r"^(\||>|#|```|- |\* |\+ |\d+[.)] )")


def _starts_structured_block(lstripped_line: str) -> bool:
    """True if `line` (already lstripped) starts a markdown block whose
    grammar breaks if you prepend arbitrary text to it.

    Covers: tables (`|`), blockquotes (`>`), headings (`#`), code
    fences (```), unordered lists (`-`, `*`, `+` followed by space),
    and ordered lists (`1.` / `1)` followed by space). Inline italics
    like `*word*` won't match because we require a space after the
    list marker.
    """
    return bool(_STRUCTURED_BLOCK_RE.match(lstripped_line))


def _process_page(path: Path) -> PageResult:
    """Walk a page's body, rewriting dated H2s and prepending date
    sentences where a specific date was recovered.

    Body content is preserved byte-for-byte: only the H2 line changes
    and (when applicable) a new `As of YYYY-MM-DD:` sentence is
    prepended in front of the first non-blank content line of the
    section. The previous first-line text is kept verbatim.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return PageResult(new_body="")

    fm_text, body = split_frontmatter(content)
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    rewrites: list[Rewrite] = []

    # Track fenced code blocks so we don't rewrite a `## x` inside a
    # ```markdown``` example — it's not a real heading.
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        heading_match = _RE_HEADING.match(line) if not in_fence else None
        if heading_match is None or not _is_dated_title(heading_match.group(2)):
            out.append(line)
            i += 1
            continue

        prefix = heading_match.group(1)  # "## "
        original = heading_match.group(2)
        clean_title, stripped_text = _strip_trailing_dated_parens(original)
        parsed = _extract_date(stripped_text) or _extract_date(original)

        # No-op guard: _is_dated_title matches a wider set (parens-wrapped
        # month/year) than _strip_trailing_dated_parens can peel — e.g.
        # `## Rollout (Jan 16, 2026) — deprecated` triggers the dated
        # check but the unlisted `deprecated` annotation blocks the peel.
        # Without this guard we'd emit a Rewrite with new_title==original
        # (silent no-op on disk, misleading in the summary).
        if clean_title == original:
            out.append(line)
            i += 1
            continue

        # Preserve the trailing newline — last line of a file may lack one.
        newline = "\n" if line.endswith("\n") else ""
        out.append(f"{prefix}{clean_title}{newline}")
        rewrites.append(
            Rewrite(
                page=path,
                original_title=original,
                new_title=clean_title,
                parsed_date=parsed,
            )
        )
        i += 1

        if parsed is None:
            continue

        # Walk forward past blank lines to find the first content
        # line of the section. Prepend `As of ...:` to it; leave the
        # rest untouched. An H3/blockquote/list item counts as body
        # content — the prefix goes before whatever the author wrote.
        while i < len(lines) and lines[i].strip() == "":
            out.append(lines[i])
            i += 1
        if i >= len(lines):
            # Section is empty / runs to EOF with only blanks. Drop an
            # `As of` stub so the date isn't silently lost.
            out.append(f"As of {parsed}.\n")
            continue

        first_content = lines[i]
        # Only inline-prepend into a prose paragraph. Tables (`|`),
        # lists (`-`, `*`, `1.`), blockquotes (`>`), sub-headings
        # (`#`), code fences/blocks all break their own grammar if
        # we splice `As of YYYY-MM-DD: ` in front — emit a standalone
        # `As of YYYY-MM-DD.` paragraph instead.
        rest = first_content.lstrip()
        if _starts_structured_block(rest):
            out.append(f"As of {parsed}.\n\n")
            out.append(first_content)
        else:
            indent = first_content[: len(first_content) - len(rest)]
            out.append(f"{indent}As of {parsed}: {rest}")
        i += 1

    new_body = "".join(out)
    if fm_text:
        # lstrip leading newlines on the body then re-emit exactly one
        # blank line between frontmatter and content. split_frontmatter
        # already strips them today, but lstripping here keeps the
        # reconstruction idempotent (and safe against future changes).
        new_body = f"---\n{fm_text}---\n\n{new_body.lstrip(chr(10))}"
    return PageResult(new_body=new_body, rewrites=rewrites)


def fix_wiki(
    wiki_dir: Path,
    *,
    commit: bool,
    limit: int | None = None,
) -> list[Rewrite]:
    """Walk the wiki, apply (or preview) title rewrites.

    --limit N caps at N pages changed, not N rewrites — so the caller
    can cherry-pick a small batch for manual review before rolling
    out the full ~74-page fix.
    """
    all_rewrites: list[Rewrite] = []
    pages_changed = 0
    for category in CATEGORIES:
        cat = wiki_dir / category
        if not cat.exists():
            continue
        for path in sorted(cat.glob("*.md")):
            if limit is not None and pages_changed >= limit:
                break
            result = _process_page(path)
            if not result.rewrites:
                continue
            all_rewrites.extend(result.rewrites)
            pages_changed += 1
            if commit:
                path.write_text(result.new_body, encoding="utf-8")
    return all_rewrites


def _print_summary(rewrites: list[Rewrite], *, commit: bool) -> None:
    """Human-readable stdout: per-page title changes + grand total."""
    by_page: dict[Path, list[Rewrite]] = {}
    for r in rewrites:
        by_page.setdefault(r.page, []).append(r)

    verb = "Rewrote" if commit else "Would rewrite"
    click.echo(f"{verb} {len(rewrites)} H2 title(s) across {len(by_page)} page(s):")
    for page in sorted(by_page, key=lambda p: str(p)):
        click.echo(f"  {page}:")
        for r in by_page[page]:
            date_note = f" [+As of {r.parsed_date}]" if r.parsed_date else " [no date prefix]"
            click.echo(f"    - {r.original_title!r} → {r.new_title!r}{date_note}")


@click.command()
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Preview rewrites without modifying wiki/ files (default).",
)
@click.option(
    "--commit",
    "commit",
    is_flag=True,
    default=False,
    help="Apply rewrites to disk. Mutually exclusive with --dry-run.",
)
@click.option(
    "--limit",
    "limit",
    type=int,
    default=None,
    help="Cap at N pages changed (for staged rollout).",
)
def main(dry_run: bool, commit: bool, limit: int | None) -> None:
    """Fix Bug F dated H2 titles across topic/system/policy pages.

    Exit codes:
    - 0: success (preview or commit).
    - 2: misuse (both --dry-run and --commit, or wiki dir missing).
    """
    if dry_run and commit:
        click.echo("error: --dry-run and --commit are mutually exclusive", err=True)
        sys.exit(2)

    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"error: {wiki_dir} not found", err=True)
        sys.exit(2)

    rewrites = fix_wiki(wiki_dir, commit=commit, limit=limit)
    _print_summary(rewrites, commit=commit)
    sys.exit(0)


if __name__ == "__main__":
    main()
