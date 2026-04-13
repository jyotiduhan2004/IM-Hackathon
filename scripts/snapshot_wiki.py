"""Snapshot/restore the wiki directory for safe experimentation.

Usage:
    uv run python scripts/snapshot_wiki.py save               # snapshot current wiki
    uv run python scripts/snapshot_wiki.py save --label v1    # named snapshot
    uv run python scripts/snapshot_wiki.py list               # list available snapshots
    uv run python scripts/snapshot_wiki.py restore v1         # restore a snapshot
    uv run python scripts/snapshot_wiki.py clean              # clear wiki/ content (no delete)
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

SNAPSHOT_ROOT = REPO_ROOT / ".snapshots"


@click.group()
def cli() -> None:
    """Snapshot / restore / clean the wiki directory."""


@cli.command()
@click.option("--label", default=None, help="Optional label (default: timestamp)")
@click.option("--include-raw-flags", is_flag=True, help="Also snapshot compiled flags in raw/")
def save(label: str | None, include_raw_flags: bool) -> None:
    """Snapshot the current wiki/ into .snapshots/."""
    if not settings.wiki_dir.exists():
        click.echo(f"ERROR: {settings.wiki_dir} does not exist", err=True)
        sys.exit(1)

    SNAPSHOT_ROOT.mkdir(exist_ok=True)
    name = label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = SNAPSHOT_ROOT / name
    if dest.exists():
        click.echo(f"ERROR: snapshot '{name}' already exists at {dest}", err=True)
        sys.exit(1)

    dest.mkdir()
    shutil.copytree(settings.wiki_dir, dest / "wiki")
    click.echo(f"Snapshot saved: {dest / 'wiki'}")

    if include_raw_flags:
        # Only the frontmatter compiled state matters for raw — copy all .md files
        raw_backup = dest / "raw-frontmatter"
        raw_backup.mkdir()
        for md in settings.raw_dir.glob("*.md"):
            shutil.copy2(md, raw_backup / md.name)
        click.echo(f"Also saved raw frontmatter: {raw_backup}")


@cli.command("list")
def list_snapshots() -> None:
    """List available snapshots."""
    if not SNAPSHOT_ROOT.exists():
        click.echo("No snapshots yet.")
        return
    entries = sorted(SNAPSHOT_ROOT.iterdir())
    if not entries:
        click.echo("No snapshots yet.")
        return
    click.echo("Available snapshots:")
    for d in entries:
        if d.is_dir():
            wiki_count = len(list((d / "wiki").rglob("*.md"))) if (d / "wiki").exists() else 0
            click.echo(f"  {d.name}  ({wiki_count} wiki pages)")


@cli.command()
@click.argument("label")
def restore(label: str) -> None:
    """Restore a snapshot into wiki/ (wiping current content first)."""
    src = SNAPSHOT_ROOT / label / "wiki"
    if not src.exists():
        click.echo(f"ERROR: snapshot '{label}' not found at {src}", err=True)
        sys.exit(1)

    if settings.wiki_dir.exists():
        # Delete current content, keep structure
        for md in settings.wiki_dir.rglob("*.md"):
            md.unlink()

    shutil.copytree(src, settings.wiki_dir, dirs_exist_ok=True)
    click.echo(f"Restored wiki from snapshot '{label}' → {settings.wiki_dir}")


@cli.command()
@click.option("--confirm", is_flag=True, help="Actually delete; otherwise dry-run")
def clean(confirm: bool) -> None:
    """Delete all .md content from wiki/ (keeps structure and .gitkeep)."""
    count = 0
    for md in settings.wiki_dir.rglob("*.md"):
        count += 1
        if confirm:
            md.unlink()
    if confirm:
        click.echo(f"Deleted {count} wiki .md files.")
    else:
        click.echo(f"Would delete {count} wiki .md files. Pass --confirm to proceed.")


@cli.command()
@click.option("--confirm", is_flag=True, help="Actually reset; otherwise dry-run")
def reset_raw_compiled(confirm: bool) -> None:
    """Reset compiled: true → false on all raw emails. Useful after clearing wiki."""
    count = 0
    for p in settings.raw_dir.glob("*.md"):
        content = p.read_text(encoding="utf-8")
        fm = extract_frontmatter(content)
        if not fm:
            continue
        if fm.get("compiled") is True:
            count += 1
            if confirm:
                fm["compiled"] = False
                fm.pop("compiled_at", None)
                body = extract_body(content)
                p.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
    if confirm:
        click.echo(f"Reset compiled flag on {count} raw emails.")
    else:
        click.echo(f"Would reset {count} raw emails. Pass --confirm to proceed.")


if __name__ == "__main__":
    cli()
