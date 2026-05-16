"""Read raw/*.md frontmatter and populate the messages table.

Idempotent (`ON CONFLICT DO NOTHING`). Map raw frontmatter:
  compiled: true  → compile_state='compiled' (carries compiled_at if set)
  compiled: false → compile_state='pending'

After this PR ships, the raw `compiled:` field becomes legacy state —
the DB is the source of truth. Do not rewrite 6,759 raw files to erase it.

Usage:
    uv run python scripts/backfill_messages.py

Lifecycle: bootstrap-recovery — not on hot path, but required to rebuild from scratch. Do NOT delete.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.db.messages import count_by_state  # noqa: E402
from src.db.messages import insert_message  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402


def _coerce_date(val: object) -> datetime | None:
    """Parse the raw frontmatter date — YAML may give str OR datetime."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None
    return None


def _str_or_none(val: object) -> str | None:
    return val if isinstance(val, str) and val.strip() else None


@click.command()
@click.option("--raw-dir", default=None, help="raw/ root (default settings.raw_dir)")
def main(raw_dir: str | None) -> None:
    raw_root = Path(raw_dir) if raw_dir else settings.raw_dir
    if not raw_root.is_absolute():
        raw_root = (REPO_ROOT / raw_root).resolve()
    if not raw_root.exists():
        click.echo(f"ERROR: {raw_root} not found", err=True)
        sys.exit(2)

    inserted = 0
    skipped_existing = 0
    skipped_no_id = 0
    seen_ids: set[str] = set()
    duplicates: list[tuple[str, str]] = []

    with connect() as conn:
        for md in sorted(raw_root.glob("*.md")):
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = extract_frontmatter(content)
            mid = fm.get("message_id")
            if not isinstance(mid, str) or not mid.strip():
                skipped_no_id += 1
                continue

            rel_path = str(md.resolve().relative_to(REPO_ROOT))

            if mid in seen_ids:
                duplicates.append((mid, rel_path))
                continue
            seen_ids.add(mid)

            compiled = fm.get("compiled") is True
            ok = insert_message(
                conn,
                message_id=mid,
                raw_path=rel_path,
                thread_id=_str_or_none(fm.get("thread_id")),
                subject=_str_or_none(fm.get("subject")),
                from_address=_str_or_none(fm.get("from")),
                date=_coerce_date(fm.get("date")),
                compile_state="compiled" if compiled else "pending",
                compiled_at=_coerce_date(fm.get("compiled_at")) if compiled else None,
            )
            if ok:
                inserted += 1
            else:
                skipped_existing += 1
        conn.commit()

    click.echo(f"inserted: {inserted}")
    click.echo(f"skipped (already in DB): {skipped_existing}")
    click.echo(f"skipped (no message_id in frontmatter): {skipped_no_id}")
    if duplicates:
        click.echo(f"WARNING: {len(duplicates)} raw files share a message_id with another file:")
        for mid, path in duplicates[:5]:
            click.echo(f"  {path} → {mid}")
    click.echo("\nstate counts:")
    for state, n in sorted(count_by_state().items()):
        click.echo(f"  {state}: {n}")


if __name__ == "__main__":
    main()
