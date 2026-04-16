"""One-shot migration: flip legacy `status` values to the new ontology.

Tier A made the live compiler emit the new ontology (`active` /
`superseded` / `archived`). This script brings the existing catalog
into line:

    status='current'    → 'active'
    status='contested'  → 'archived'

DB and disk are flipped independently:

    * DB flip is driven by a `UPDATE ... WHERE status = ANY(...)` across
      `wiki_pages` — every legacy row moves in one statement.
    * File rewrites are driven by a filesystem walk of `wiki/**/*.md`
      that finds any file whose frontmatter `status:` is still legacy.

The fs-driven file source is what makes the script idempotent. After a
partial failure where the DB flipped but some file write blew up, the
DB row is no longer legacy — but the file still is, so the fs scan
surfaces it on the next `--commit`. This also covers orphan files with
no matching DB row, keeping disk and DB converging even when they've
drifted.

Flags:
    --dry-run     Preview only. Prints counts + writes a plan file to
                  docs/audits/status-backfill-plan-<ISO>.md. Default.
    --commit      Apply the DB UPDATE + rewrite every matching .md file.
                  DB is flipped first; if file rewrites fail midway,
                  re-run `--commit` to finish — the fs scan picks up
                  stuck files even after their DB rows have migrated.

Usage:
    uv run python scripts/backfill_status_active.py --dry-run
    uv run python scripts/backfill_status_active.py --commit
"""

from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

# Legacy → new ontology. The DB CHECK constraint + prompts accept both
# during the transition window; this script is what actually retires
# the legacy values.
_STATUS_FLIP: dict[str, str] = {
    "current": "active",
    "contested": "archived",
}
_LEGACY_STATUSES = list(_STATUS_FLIP)


def _scan_filesystem_for_legacy_files(root: Path) -> list[tuple[Path, str]]:
    """Walk `root/wiki/**/*.md` and return `(path, legacy_status)` for
    every file whose frontmatter `status:` is still on a legacy value.

    This is the sole file-rewrite source. Deriving it from DB rows
    would break idempotent recovery: once a DB row has flipped, a
    subsequent re-run would not surface the file if its earlier write
    failed. Walking disk guarantees we always find every file that
    still needs rewriting, DB state notwithstanding.

    Unreadable files, malformed YAML, and files missing a frontmatter
    block are skipped silently — we only rewrite files we can prove
    are legitimate legacy targets.
    """
    wiki_dir = root / "wiki"
    if not wiki_dir.exists():
        return []
    found: list[tuple[Path, str]] = []
    for path in wiki_dir.rglob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        status = extract_frontmatter(content).get("status")
        if isinstance(status, str) and status in _STATUS_FLIP:
            found.append((path, status))
    return found


def _rewrite_file_status(file_path: Path, new_status: str) -> None:
    """Overwrite `status:` in the file's frontmatter. Preserves other
    fields + body via yaml.safe_dump round-trip."""
    content = file_path.read_text(encoding="utf-8")
    fm = extract_frontmatter(content)
    body = extract_body(content)
    fm["status"] = new_status
    file_path.write_text(render_with_frontmatter(fm, body), encoding="utf-8")


def _fetch_legacy_rows(conn: Any) -> list[dict[str, Any]]:
    """Pull every wiki_pages row whose status is still on the old ontology."""
    cur = conn.execute(
        """
        SELECT page_id, slug, path, status
          FROM wiki_pages
         WHERE status = ANY(%s)
         ORDER BY path
        """,
        (_LEGACY_STATUSES,),
    )
    return list(cur.fetchall())


def _write_plan_file(
    plan_dir: Path,
    rows: list[dict[str, Any]],
    file_rewrites: list[tuple[Path, str, str]],
) -> Path:
    """Dump a human-readable plan to docs/audits/ for audit trail."""
    plan_dir.mkdir(parents=True, exist_ok=True)
    iso = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    plan_path = plan_dir / f"status-backfill-plan-{iso}.md"

    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1

    lines: list[str] = []
    lines.append("# Status backfill plan")
    lines.append("")
    lines.append(f"Generated: {iso}")
    lines.append("")
    lines.append("## DB rows to flip")
    lines.append("")
    lines.append(f"Total: {len(rows)}")
    for old, n in sorted(by_status.items()):
        lines.append(f"  - {old} → {_STATUS_FLIP[old]}: {n}")
    lines.append("")
    lines.append("## File frontmatter rewrites")
    lines.append("")
    lines.append(f"Total: {len(file_rewrites)}")
    lines.append("")
    lines.append("| file | old | new |")
    lines.append("|---|---|---|")
    for path, old, new in file_rewrites[:200]:
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            rel = path
        lines.append(f"| {rel} | {old} | {new} |")
    lines.append("")
    if len(file_rewrites) > 200:
        lines.append(f"*({len(file_rewrites) - 200} more rows elided)*")
        lines.append("")

    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan_path


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    help="Preview only (default). Writes plan to docs/audits/.",
)
@click.option(
    "--commit",
    is_flag=True,
    help="Apply DB UPDATE + rewrite .md frontmatter.",
)
@click.option(
    "--repo-root",
    default=None,
    help="Override repo root for the wiki/ scan (tests).",
)
def main(dry_run: bool, commit: bool, repo_root: str | None) -> None:
    # Click's default-on `--dry-run` flag stays True unless we flip it
    # ourselves when `--commit` is present — there's no `--no-dry-run`.
    if commit:
        dry_run = False

    root = Path(repo_root).resolve() if repo_root else REPO_ROOT

    with connect() as conn:
        rows = _fetch_legacy_rows(conn)

        file_rewrites: list[tuple[Path, str, str]] = [
            (path, old, _STATUS_FLIP[old]) for path, old in _scan_filesystem_for_legacy_files(root)
        ]

        db_current = sum(1 for r in rows if r["status"] == "current")
        db_contested = sum(1 for r in rows if r["status"] == "contested")

        click.echo(
            f"{db_current} rows would flip current→active · "
            f"{db_contested} rows would flip contested→archived · "
            f"{len(file_rewrites)} .md files would be rewritten"
        )

        if dry_run:
            plan_path = _write_plan_file(root / "docs" / "audits", rows, file_rewrites)
            click.echo(f"plan: {plan_path.relative_to(root)}")
            return

        # DB first, files second. Re-running `--commit` after a partial
        # failure reliably picks up stuck files: the DB CASE is a no-op
        # on already-migrated rows, and the fs scan re-reads disk on
        # every run so files still on legacy values surface even after
        # their DB rows have flipped.
        if rows:
            conn.execute(
                """
                UPDATE wiki_pages
                   SET status = CASE
                       WHEN status = 'current' THEN 'active'
                       WHEN status = 'contested' THEN 'archived'
                   END
                 WHERE status = ANY(%s)
                """,
                (_LEGACY_STATUSES,),
            )
            conn.commit()

        rewritten = 0
        for path, _old, new in file_rewrites:
            try:
                _rewrite_file_status(path, new)
                rewritten += 1
            except (OSError, UnicodeDecodeError) as exc:
                click.echo(f"  WARN rewrite failed for {path}: {exc}", err=True)

        click.echo(f"committed: {len(rows)} DB rows flipped · {rewritten} files rewritten")


if __name__ == "__main__":
    main()
