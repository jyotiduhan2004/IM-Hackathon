"""One-shot cleanup of auto-stub wiki pages.

Background: `scripts/lint_wiki.py::create_missing_stubs` used to auto-write
skeleton pages under wiki/entities/ and wiki/systems/ for every unresolved
`[[target]]` it encountered. These pages have empty `sources:` and a
boilerplate body like:

    *Stub page auto-created because [[target]] was referenced but no
    page existed.*

They're pure noise — no evidence, no content, just bracket-matching. Now
that auto-stub creation is gated behind `--create-stubs` (see
`scripts/lint_wiki.py` CLI), this script is the one-shot cleanup for the
pages that leaked in before the gate landed.

IMPORTANT: We do NOT match on empty `sources:` alone. `src/compile/entities.py
::create_entity_page` legitimately writes entity stubs with empty sources
that get filled in by the agent on subsequent mentions. We signature-match
on the boilerplate body string so we never nuke a legit page.

Usage:
    uv run python scripts/cleanup_auto_stubs.py              # dry-run: list matches
    uv run python scripts/cleanup_auto_stubs.py --confirm    # delete via `git rm`

Exit codes:
    0 — clean (nothing matched)
    1 — dry-run found matches (so CI could in theory catch regressions)
    0 — --confirm deletes succeeded
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

# Only walk category folders known to be stub targets from the legacy
# lint_wiki heuristic (entities/systems split on hyphen-count).
_CANDIDATE_DIRS = ("entities", "systems")

# Signature strings the legacy create_missing_stubs emitted. Either match
# is enough — the bodies look like:
#   *Stub page auto-created because [[foo]] was referenced but no page existed.*
#   Referenced from: [[bar]]
_SIGNATURE_SUBSTRINGS = (
    "Stub page auto-created because",
    "Referenced from: [[",
)

# How many non-empty body lines to scan for the signature. Generous enough
# that a stub page with a blank line between heading and signature still
# matches, tight enough that a real page with "Referenced from:" buried
# in its middle doesn't.
_SIGNATURE_WINDOW_LINES = 5


def _sources_is_empty_or_missing(fm: dict[str, object]) -> bool:
    """Return True if `sources:` is an empty list or missing entirely.

    Pages with one or more sources are real compiled content — never touch.
    """
    if "sources" not in fm:
        return True
    sources = fm.get("sources")
    if sources is None:
        return True
    return isinstance(sources, list) and len(sources) == 0


def _body_has_auto_stub_signature(body: str) -> bool:
    """Scan the first few non-empty lines of `body` for the auto-stub marker.

    We look near the top because the legacy stub template put the marker
    in the first two non-empty lines after the H1. A page that happens to
    mention "Referenced from:" deep inside real content will NOT match.
    """
    seen = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        seen += 1
        if seen > _SIGNATURE_WINDOW_LINES:
            break
        for sig in _SIGNATURE_SUBSTRINGS:
            if sig in line:
                return True
    return False


def matches_auto_stub(path: Path) -> bool:
    """Return True if `path` is an auto-stub page that should be cleaned up.

    Signature (ALL must match):
    - frontmatter `sources:` is empty list or missing, AND
    - body contains one of the auto-stub marker strings within the first
      few non-empty lines.

    The AND is deliberate: some legit entity pages have empty sources but
    a real body (e.g. fresh `create_entity_page` output), and we must
    leave those alone.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm = extract_frontmatter(content)
    if not _sources_is_empty_or_missing(fm):
        return False
    body = extract_body(content)
    return _body_has_auto_stub_signature(body)


def find_auto_stubs(wiki_dir: Path) -> list[Path]:
    """Walk `wiki/{entities,systems}/*.md` and return auto-stub matches."""
    hits: list[Path] = []
    for folder in _CANDIDATE_DIRS:
        cat_dir = wiki_dir / folder
        if not cat_dir.exists():
            continue
        for md in sorted(cat_dir.glob("*.md")):
            if matches_auto_stub(md):
                hits.append(md)
    return hits


def _delete_via_git_or_unlink(path: Path) -> tuple[bool, str]:
    """Remove `path` preferring `git rm`, falling back to `os.unlink`.

    Returns (ok, how). `how` is "git rm" when git handled it, "unlink"
    when we fell back, or "error: ..." on failure.
    """
    try:
        result = subprocess.run(
            ["git", "rm", "--quiet", str(path)],
            cwd=path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True, "git rm"
    except (FileNotFoundError, OSError):
        pass  # git missing or path odd — fall through

    try:
        path.unlink()
        return True, "unlink"
    except FileNotFoundError:
        return True, "already gone"
    except OSError as exc:
        return False, f"error: {exc}"


def _run_backfill_wiki_pages() -> bool:
    """Run `scripts/backfill_wiki_pages.py` to sync catalog after deletes.

    Returns True when backfill ran cleanly, False otherwise. We swallow
    all exceptions and just print a warning — the delete already happened,
    and the operator can re-run backfill by hand. Don't crash the caller.
    """
    try:
        from scripts import backfill_wiki_pages  # noqa: F401
    except ImportError as exc:
        click.echo(
            f"WARNING: could not import scripts/backfill_wiki_pages.py ({exc}).",
            err=True,
        )
        click.echo(
            "  Catalog may be out of sync. Run manually: "
            "  uv run python scripts/backfill_wiki_pages.py",
            err=True,
        )
        return False

    try:
        result = subprocess.run(
            ["uv", "run", "python", "scripts/backfill_wiki_pages.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        click.echo(
            f"WARNING: failed to invoke backfill_wiki_pages.py ({exc}).",
            err=True,
        )
        click.echo(
            "  Catalog may be out of sync. Run manually: "
            "  uv run python scripts/backfill_wiki_pages.py",
            err=True,
        )
        return False

    if result.returncode != 0:
        click.echo(
            "WARNING: backfill_wiki_pages.py exited non-zero. Catalog may be out of sync.",
            err=True,
        )
        if result.stderr:
            click.echo(result.stderr.rstrip(), err=True)
        return False

    click.echo("Ran backfill_wiki_pages.py — catalog synced.")
    return True


@click.command()
@click.option(
    "--confirm",
    is_flag=True,
    help="Actually delete matched files (via `git rm`, fallback os.unlink). Default is dry-run.",
)
@click.option(
    "--wiki-dir",
    default=None,
    help="wiki/ root (default settings.wiki_dir)",
)
def main(confirm: bool, wiki_dir: str | None) -> None:
    """Find (and optionally delete) auto-created stub pages."""
    wiki_root = Path(wiki_dir) if wiki_dir else settings.wiki_dir
    if not wiki_root.is_absolute():
        wiki_root = (REPO_ROOT / wiki_root).resolve()
    if not wiki_root.exists():
        click.echo(f"ERROR: wiki directory not found: {wiki_root}", err=True)
        sys.exit(2)

    matches = find_auto_stubs(wiki_root)

    if not matches:
        click.echo("No auto-stub pages found.")
        sys.exit(0)

    click.echo(f"Found {len(matches)} auto-stub page(s):")
    for p in matches:
        try:
            rel = p.resolve().relative_to(REPO_ROOT)
        except ValueError:
            rel = p
        click.echo(f"  {rel}")

    if not confirm:
        click.echo()
        click.echo("Dry-run only. Re-run with --confirm to delete.")
        sys.exit(1)

    click.echo()
    click.echo(f"Deleting {len(matches)} auto-stub page(s)...")
    deleted = 0
    failed: list[tuple[Path, str]] = []
    for p in matches:
        ok, how = _delete_via_git_or_unlink(p)
        if ok:
            deleted += 1
            click.echo(f"  [{how}] {p.name}")
        else:
            failed.append((p, how))
            click.echo(f"  [FAIL] {p.name}: {how}", err=True)

    click.echo()
    click.echo(f"Deleted {deleted} / {len(matches)}.")

    if deleted > 0:
        _run_backfill_wiki_pages()

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
