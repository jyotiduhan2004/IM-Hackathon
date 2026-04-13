"""Flag (and optionally relocate) humans miscategorized as systems.

Live-viewer feedback (see issue #43): `wiki/systems/` accumulated ~10+
human-named pages that belong in `wiki/entities/` — e.g. `alok-kumar2.md`,
`deepak-yadav01.md`. Per `CLAUDE.md`, systems = products / services / URLs
/ mailing lists; entities = humans. This script audits `wiki/systems/*.md`
and moves misclassified pages to `wiki/entities/`.

A page is flagged as "probably human" if ANY of these fire:

- Frontmatter `email:` is set to a plausible RFC-ish address.
- Frontmatter `page_type:` is literally `entity` (someone set it wrong
  but the file is still in systems/).
- Slug/title shape looks like a person — two title-case tokens, no digits
  (optional trailing digits are common for numeric duplicates like
  `alok-kumar2`), no system-ish words (`api`, `tool`, `service`, …).

Wikilinks in this repo are directory-agnostic: `mkdocs-roamlinks-plugin`
resolves `[[alok-kumar2]]` by walking the whole `docs_dir` and matching on
filename, so moving a page between `wiki/systems/` and `wiki/entities/`
does NOT require rewriting inbound wikilinks (verified in
`.venv/lib/python3.13/site-packages/mkdocs_roamlinks_plugin/plugin.py`).
We therefore skip link rewrites entirely.

Usage:
    uv run python scripts/audit_systems_entities.py              # dry-run
    uv run python scripts/audit_systems_entities.py --confirm    # mutate

Exit code: 1 when the dry-run found issues; 0 otherwise (and 0 after
--confirm moves files).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.entities import is_valid_email  # noqa: E402
from src.config import settings  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

# Words that make a slug obviously a system/product/tool rather than a
# person. Matched case-insensitively as whole tokens in slug `-` splits.
# Kept short on purpose — expand only when a false positive appears.
SYSTEM_WORDS = frozenset(
    {
        "api",
        "tool",
        "service",
        "system",
        "portal",
        "pipeline",
        "dashboard",
        "platform",
        "agent",
        "bot",
        "team",
        "program",
        "feature",
        "module",
        "plugin",
        "framework",
        "app",
        "ui",
        "server",
        "db",
        "database",
        "queue",
        "cli",
        "sdk",
        "website",
        "site",
        "com",
        "net",
        "org",
        "io",
        "ai",
    }
)


@dataclass
class Flag:
    path: Path
    reason: str


def _is_plausible_email(value: object) -> bool:
    """True when the YAML value parses as a plausible RFC-ish address."""
    return isinstance(value, str) and is_valid_email(value)


def _slug_looks_human(slug: str) -> bool:
    """Heuristic: does the slug look like `firstname-lastname[digits]`?

    - Exactly two tokens after splitting on `-`.
    - Neither token is a known system word.
    - Both tokens are all-letters (possibly with trailing digits on the
      second token — e.g. `alok-kumar2`, `deepak-yadav01`).
    - No leading digits in either token.
    """
    tokens = slug.split("-")
    if len(tokens) != 2:
        return False
    first, second = tokens
    if not first or not second:
        return False
    if first.lower() in SYSTEM_WORDS or second.lower() in SYSTEM_WORDS:
        return False
    if not first.isalpha():
        return False
    # Second token: letters optionally followed by trailing digits.
    return re.match(r"^[a-zA-Z]+\d*$", second) is not None


def audit_systems(systems_dir: Path) -> list[Flag]:
    """Return Flags for every `wiki/systems/*.md` that looks like a human."""
    flags: list[Flag] = []
    if not systems_dir.exists():
        return flags
    for path in sorted(systems_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = extract_frontmatter(content)
        reasons: list[str] = []

        if _is_plausible_email(fm.get("email")):
            reasons.append("has email field")
        if fm.get("page_type") == "entity":
            reasons.append("page_type=entity")
        if _slug_looks_human(path.stem):
            reasons.append("slug looks human")

        if reasons:
            flags.append(Flag(path, ", ".join(reasons)))
    return flags


def _is_git_tracked(path: Path) -> bool:
    """True when `path` sits inside a git worktree and git knows about it."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=path.parent,
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _move(src: Path, dst: Path) -> None:
    """Move `src` to `dst`, preferring `git mv` when `src` is tracked."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _is_git_tracked(src):
        try:
            subprocess.run(
                ["git", "mv", str(src), str(dst)],
                cwd=src.parent,
                check=True,
                capture_output=True,
            )
            return
        except subprocess.CalledProcessError:
            # Fall through to plain move — git may refuse (e.g. target
            # outside the repo). shutil.move handles cross-filesystem.
            pass
    shutil.move(str(src), str(dst))


def relocate(flags: list[Flag], entities_dir: Path) -> list[tuple[Path, Path]]:
    """Move each flagged page into `entities_dir/`. Returns (src, dst) pairs.

    Skips any page whose destination already exists — manual merge then.
    """
    moves: list[tuple[Path, Path]] = []
    for flag in flags:
        dst = entities_dir / flag.path.name
        if dst.exists():
            click.echo(
                f"skip: {flag.path} → {dst} (target exists; manual merge needed)",
                err=True,
            )
            continue
        _move(flag.path, dst)
        moves.append((flag.path, dst))
    return moves


def _pretty(path: Path, base: Path) -> Path:
    """Show `path` relative to `base` when possible, else absolute."""
    try:
        return path.relative_to(base)
    except ValueError:
        return path


@click.command()
@click.option(
    "--confirm",
    is_flag=True,
    help="Mutate files. Without this flag, runs in dry-run mode (default).",
)
@click.option(
    "--wiki-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Override wiki/ directory (default: settings.wiki_dir).",
)
def main(confirm: bool, wiki_dir: Path | None) -> None:
    """Audit wiki/systems/ for misclassified human pages; relocate on --confirm."""
    wiki = (wiki_dir or settings.wiki_dir).resolve()
    systems_dir = wiki / "systems"
    entities_dir = wiki / "entities"

    flags = audit_systems(systems_dir)
    if not flags:
        click.echo("No misclassified pages found.")
        sys.exit(0)

    cwd = Path.cwd()
    for flag in flags:
        dst = entities_dir / flag.path.name
        click.echo(f"move: {_pretty(flag.path, cwd)} → {_pretty(dst, cwd)} (reason: {flag.reason})")

    if confirm:
        moves = relocate(flags, entities_dir)
        click.echo(f"\nMoved {len(moves)} page(s) to {entities_dir}.")
        sys.exit(0)

    click.echo(f"\n{len(flags)} misclassified page(s). Re-run with --confirm to move.")
    sys.exit(1)


if __name__ == "__main__":
    main()
