"""Idempotent wiki-page formatter — enforce the light-format rules.

Thin Click CLI around `src.wiki.page_formatter`. The actual normalisation
logic (`format_page`, `format_file`, helpers) lives in the library so the
post-batch coordinator can call it without shelling out — see
`src/coordinator/post_batch.py`.

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

import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.wiki.page_formatter import CATEGORIES  # noqa: E402
from src.wiki.page_formatter import FileResult  # noqa: E402
from src.wiki.page_formatter import Summary  # noqa: E402
from src.wiki.page_formatter import _compose  # noqa: E402
from src.wiki.page_formatter import _known_slugs  # noqa: E402
from src.wiki.page_formatter import _normalize_body  # noqa: E402
from src.wiki.page_formatter import format_file  # noqa: E402
from src.wiki.page_formatter import format_page  # noqa: E402,F401  (re-export for tests)


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
