"""Idempotent wiki-page formatter — enforce the light-format rules.

The compiler hand-writes `## Related` / `## People` / `## Team` sections that
drift out of sync with reality. Per the "North-star" + "Wiki quality audit
snapshot (2026-04-14)" entries in docs/BACKLOG.md, those sections should be
auto-generated from frontmatter `related:` + inline `[[slug]]` links, not
prose.

This script:
- Strips any H2 section whose title matches an "agent-written nav" pattern
  (Related / People / Team / Related Topics / Related Systems / Related
  Entities / Related People / Key Contributors / Contributors).
- Preserves `## Related Work` — that's content, not nav.
- Regenerates ONE `## Related` section at the end from the union of:
    * frontmatter `related:` list
    * inline `[[slug]]` links in the body that resolve to real wiki pages
- Writes back only when the body actually changed, so running twice is a
  no-op on a clean page.

Usage:
    uv run python scripts/format_wiki.py                      # dry-run (default)
    uv run python scripts/format_wiki.py --dry-run            # explicit dry-run
    uv run python scripts/format_wiki.py --confirm            # apply changes in-place
    uv run python scripts/format_wiki.py --paths a.md b.md    # scope to specific files
"""

from __future__ import annotations

import difflib
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
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

# Categories we scan for light-format normalization. Policies/timelines/
# conflicts keep their own templates (history tables, etc.) — out of scope
# for this pass.
CATEGORIES = ("topics", "entities", "people", "systems")

# H2 headings the agent tends to hand-write as navigation metadata. Matched
# case-insensitively on the trimmed heading text. "Related Work" is content
# (e.g. "prior research related to this") and is deliberately excluded.
AGENT_NAV_HEADINGS = frozenset(
    h.lower()
    for h in (
        "Related",
        "People",
        "Team",
        "Related Topics",
        "Related Systems",
        "Related Entities",
        "Related People",
        "Key Contributors",
        "Contributors",
    )
)

# `^## <text>$` — one H2 heading line.
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# `[[slug]]` or `[[slug|display]]`. Strip the `|display` portion; we only
# care about the link target.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


@dataclass
class FileResult:
    path: Path
    changed: bool = False
    skipped_reason: str | None = None
    diff: str = ""


@dataclass
class Summary:
    pages_changed: int = 0
    pages_untouched: int = 0
    pages_with_errors: int = 0
    results: list[FileResult] = field(default_factory=list)


def _is_agent_nav_heading(text: str) -> bool:
    """Case-insensitive, trimmed membership check against AGENT_NAV_HEADINGS."""
    return text.strip().lower() in AGENT_NAV_HEADINGS


def _split_into_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Split body into (preamble, [(heading, section_text), ...]).

    `preamble` is everything before the first H2 (lead paragraph + any H1).
    Each section in the list includes its own heading line at the top, so
    reconstructing the body is just `preamble + "".join(section for _, section)`.
    """
    matches = list(_H2_RE.finditer(body))
    if not matches:
        return body, []

    preamble = body[: matches[0].start()]
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        heading_text = m.group(1)
        section_text = body[start:end]
        sections.append((heading_text, section_text))
    return preamble, sections


def _known_slugs(wiki_dir: Path) -> set[str]:
    """Set of filename stems for every .md across wiki/ categories."""
    slugs: set[str] = set()
    for cat in (*CATEGORIES, "policies", "timelines", "conflicts"):
        cat_dir = wiki_dir / cat
        if cat_dir.exists():
            slugs.update(p.stem for p in cat_dir.glob("*.md"))
    return slugs


def _slug_from_related_entry(entry: str) -> str | None:
    """Turn a `related:` YAML entry into a slug.

    Accepts either `[[slug]]` wiki-link form or bare `slug`. Returns None for
    entries that don't look like a slug reference (e.g., stray comments).
    """
    if not isinstance(entry, str):
        return None
    s = entry.strip()
    m = _WIKILINK_RE.fullmatch(s)
    if m:
        s = m.group(1)
    # strip the `|display` tail if present
    s = s.split("|", 1)[0].strip()
    if not s:
        return None
    return s


def _collect_related_slugs(
    frontmatter: dict[str, object], body: str, known_slugs: set[str], self_slug: str
) -> list[str]:
    """Gather the union of frontmatter `related:` + inline `[[slug]]` links.

    - Dedupe.
    - Drop any slug that doesn't resolve to a real wiki page (avoid writing
      broken links into the regenerated Related section).
    - Drop the page's own slug (self-reference is noise).
    - Sort alphabetically for a stable idempotent output.
    """
    collected: set[str] = set()

    raw_related = frontmatter.get("related") or []
    if isinstance(raw_related, list):
        for entry in raw_related:
            slug = _slug_from_related_entry(entry)
            if slug:
                collected.add(slug)

    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).split("|", 1)[0].strip()
        if target:
            collected.add(target)

    # Filter to known pages and drop self-refs.
    return sorted(s for s in collected if s in known_slugs and s != self_slug)


def _render_related_section(slugs: list[str]) -> str:
    """Render a `## Related` section body. Empty slugs → empty string."""
    if not slugs:
        return ""
    lines = ["## Related", ""]
    lines.extend(f"- [[{s}]]" for s in slugs)
    lines.append("")  # trailing newline
    return "\n".join(lines)


def _normalize_body(
    body: str, frontmatter: dict[str, object], known_slugs: set[str], self_slug: str
) -> str:
    """Strip agent-nav H2 sections and append ONE regenerated Related section.

    Idempotency: the regenerated Related section comes from a deterministic
    sort over a deduped set, and the stripping removes every known-nav
    variant. Running again produces byte-identical output.
    """
    preamble, sections = _split_into_sections(body)

    # Keep every section that isn't one of the agent-nav variants.
    kept_sections = [
        (heading, text) for heading, text in sections if not _is_agent_nav_heading(heading)
    ]

    related_slugs = _collect_related_slugs(frontmatter, body, known_slugs, self_slug)
    related_block = _render_related_section(related_slugs)

    rebuilt = preamble
    for _, text in kept_sections:
        rebuilt += text

    # Ensure exactly one blank line between last kept content and the new
    # Related block (if any).
    if related_block:
        rebuilt = rebuilt.rstrip() + "\n\n" + related_block
    else:
        rebuilt = rebuilt.rstrip() + "\n"

    # Guarantee a single trailing newline, matching the render helper's
    # expectations.
    if not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt


def _format_diff(path: Path, old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff)


def _compose(original_content: str, new_body: str) -> str:
    """Re-attach the original frontmatter block verbatim to a new body.

    We intentionally avoid `render_with_frontmatter` here because it
    re-serializes YAML and reformats quotes/indentation — cosmetic churn
    we don't want the formatter to produce. Only the body region changes.
    """
    # Walk to the second `---` line to find the frontmatter boundary,
    # matching src.utils.split_frontmatter's definition. The leading block
    # (fences included) is taken verbatim from the original file.
    lines = original_content.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        # No frontmatter — caller already bailed out in this case, but
        # guard anyway.
        return new_body
    boundary = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            boundary = i
            break
    if boundary == -1:
        return new_body
    fm_block = "".join(lines[: boundary + 1])
    # Preserve a single blank line between the closing `---` and the body.
    return fm_block + "\n" + new_body


def format_file(path: Path, known_slugs: set[str]) -> FileResult:
    """Normalize a single wiki page. Returns a FileResult without writing."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return FileResult(path, skipped_reason=f"unreadable: {e}")

    frontmatter = extract_frontmatter(content)
    if not frontmatter:
        return FileResult(path, skipped_reason="unparseable frontmatter")

    body = extract_body(content)
    self_slug = path.stem

    new_body = _normalize_body(body, frontmatter, known_slugs, self_slug)
    new_content = _compose(content, new_body)
    if not new_content.endswith("\n"):
        new_content += "\n"

    if new_content == content:
        return FileResult(path, changed=False)

    return FileResult(path, changed=True, diff=_format_diff(path, content, new_content))


def format_page(path: Path, wiki_root: Path, confirm: bool = False) -> bool:
    """Normalize a single wiki page in-process. Returns True if it changed.

    Thin wrapper around `format_file` for callers that just want "normalize
    this one file". The compile_all post-batch hook uses this so it can run
    the formatter on newly-written pages without shelling out. Safe on
    unreadable/unparseable pages — they're skipped and return False rather
    than raising.
    """
    known_slugs = _known_slugs(wiki_root)
    result = format_file(path, known_slugs)
    if result.skipped_reason or not result.changed:
        return False

    if confirm:
        # Re-derive fresh content and write. Matches the pattern in `run()`.
        content = path.read_text(encoding="utf-8")
        fm = extract_frontmatter(content)
        body = extract_body(content)
        new_body = _normalize_body(body, fm, known_slugs, path.stem)
        new_content = _compose(content, new_body)
        if not new_content.endswith("\n"):
            new_content += "\n"
        path.write_text(new_content, encoding="utf-8")

    return True


def _iter_pages(wiki_dir: Path, explicit_paths: list[Path] | None) -> list[Path]:
    """Return the list of .md paths to process.

    `--paths` is taken verbatim (relative paths resolved against CWD, then
    REPO_ROOT as a fallback). Without it we walk wiki/topics, wiki/entities,
    wiki/systems.
    """
    if explicit_paths:
        resolved: list[Path] = []
        for p in explicit_paths:
            candidate = p if p.is_absolute() else Path.cwd() / p
            if not candidate.exists():
                # Try relative to repo root — common pattern for CLI usage.
                alt = REPO_ROOT / p
                if alt.exists():
                    candidate = alt
            resolved.append(candidate)
        return resolved

    pages: list[Path] = []
    for cat in CATEGORIES:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        pages.extend(sorted(cat_dir.glob("*.md")))
    return pages


def run(
    wiki_dir: Path, *, explicit_paths: list[Path] | None = None, confirm: bool = False
) -> Summary:
    """Walk the wiki (or explicit paths) and normalize each page.

    `confirm=False` is dry-run. Summary.results carries per-file detail for
    callers that want to print diffs.
    """
    summary = Summary()
    pages = _iter_pages(wiki_dir, explicit_paths)
    known_slugs = _known_slugs(wiki_dir)

    # Include pages we're about to create ourselves (no-op here because we
    # never add pages, but the reference is here for future parity with
    # the compile workflow).

    for path in pages:
        if not path.exists():
            summary.pages_with_errors += 1
            summary.results.append(FileResult(path, skipped_reason="does not exist"))
            continue

        result = format_file(path, known_slugs)
        summary.results.append(result)
        if result.skipped_reason:
            summary.pages_with_errors += 1
            continue
        if result.changed:
            summary.pages_changed += 1
            if confirm:
                # Re-derive new content and write. We did the work inside
                # format_file, but keeping the write path here keeps the
                # dry-run / confirm branches explicit.
                content = path.read_text(encoding="utf-8")
                fm = extract_frontmatter(content)
                body = extract_body(content)
                new_body = _normalize_body(body, fm, known_slugs, path.stem)
                new_content = _compose(content, new_body)
                if not new_content.endswith("\n"):
                    new_content += "\n"
                path.write_text(new_content, encoding="utf-8")
        else:
            summary.pages_untouched += 1

    return summary


@click.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": False},
)
@click.option(
    "--dry-run/--confirm",
    default=True,
    help="Dry-run prints diffs without writing (default). --confirm writes in-place.",
)
@click.option(
    "--paths",
    "explicit_paths",
    multiple=True,
    type=click.Path(path_type=Path),
    help=(
        "Specific .md files to format. May be repeated (e.g., --paths a.md "
        "--paths b.md) or listed together after a single --paths flag "
        "(e.g., --paths a.md b.md). Overrides the default wiki scan."
    ),
)
@click.pass_context
def main(ctx: click.Context, dry_run: bool, explicit_paths: tuple[Path, ...]) -> None:
    """Light-format normalizer — strip agent-nav sections, regenerate ## Related."""
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        click.echo(f"ERROR: {wiki_dir} not found", err=True)
        sys.exit(2)

    # Accept trailing positional paths after `--paths a b c …` — click treats
    # only the first as --paths's value, the rest as extra args. Fold them
    # back into explicit_paths so both `--paths a b` and `--paths a --paths b`
    # work.
    extras = [Path(a) for a in ctx.args]
    combined = [*explicit_paths, *extras]
    paths_list = combined or None
    summary = run(wiki_dir, explicit_paths=paths_list, confirm=not dry_run)

    if dry_run:
        for r in summary.results:
            if r.skipped_reason:
                click.echo(f"SKIP {r.path}: {r.skipped_reason}", err=True)
            elif r.changed and r.diff:
                click.echo(r.diff)
    else:
        for r in summary.results:
            if r.skipped_reason:
                click.echo(f"SKIP {r.path}: {r.skipped_reason}", err=True)
            elif r.changed:
                click.echo(f"UPDATED {r.path}")

    click.echo(
        f"pages_changed={summary.pages_changed} "
        f"pages_untouched={summary.pages_untouched} "
        f"pages_with_errors={summary.pages_with_errors}",
        err=True,
    )
    if dry_run and summary.pages_changed:
        click.echo("(dry-run — re-run with --confirm to apply)", err=True)


if __name__ == "__main__":
    main()
