"""Populate users / threads / message_participants from raw/*.md frontmatter.

Idempotent (`ON CONFLICT DO NOTHING` everywhere). Reads the same raw tree
as scripts/backfill_messages.py, parses from/to/cc headers, and writes:

    users                  : one row per distinct email
    threads                : one row per Gmail thread_id
    message_participants   : one row per (message, user, role)

Order matters — users must exist before participants reference them via
FK, and a thread row helps when we later attach derived aggregates. We
walk the raw files once and queue inserts in the right order per file.

Usage:
    uv run python scripts/backfill_users_threads_participants.py

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
from src.db.participants import count_participants_by_role  # noqa: E402
from src.db.participants import insert_participant  # noqa: E402
from src.db.threads import count_threads  # noqa: E402
from src.db.threads import update_thread_aggregates  # noqa: E402
from src.db.threads import upsert_thread  # noqa: E402
from src.db.users import count_users  # noqa: E402
from src.db.users import upsert_user  # noqa: E402
from src.ingest.email_parse import parse_email_address  # noqa: E402
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


def _as_address_list(val: object) -> list[str]:
    """Normalize the to/cc field — YAML gives list[str] | str | None."""
    if isinstance(val, list):
        return [v for v in val if isinstance(v, str) and v.strip()]
    if isinstance(val, str) and val.strip():
        return [val]
    return []


@click.command()
@click.option("--raw-dir", default=None, help="raw/ root (default settings.raw_dir)")
def main(raw_dir: str | None) -> None:
    raw_root = Path(raw_dir) if raw_dir else settings.raw_dir
    if not raw_root.is_absolute():
        raw_root = (REPO_ROOT / raw_root).resolve()
    if not raw_root.exists():
        click.echo(f"ERROR: {raw_root} not found", err=True)
        sys.exit(2)

    users_inserted = 0
    threads_inserted = 0
    participants_inserted = 0
    skipped_no_id = 0
    skipped_unknown_message = 0
    thread_ids: set[str] = set()

    with connect() as conn:
        # Build the message_id → date index up front so we can fan out
        # per-file work without re-querying the DB for every participant.
        existing = conn.execute("SELECT message_id, thread_id, date FROM messages").fetchall()
        message_index: dict[str, dict[str, object]] = {
            r["message_id"]: {"thread_id": r["thread_id"], "date": r["date"]} for r in existing
        }
        click.echo(f"messages already in catalog: {len(message_index)}")

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
            row = message_index.get(mid)
            if row is None:
                # The messages backfill hasn't seen this raw file — skip
                # rather than violate the participants FK. Re-run after
                # backfill_messages.py covers it.
                skipped_unknown_message += 1
                continue

            # ---- threads ---------------------------------------------------
            tid = row["thread_id"]
            msg_date = row["date"] if isinstance(row["date"], datetime) else None
            if isinstance(tid, str) and tid:
                if upsert_thread(
                    conn,
                    thread_id=tid,
                    first_message_at=msg_date,
                    last_message_at=msg_date,
                ):
                    threads_inserted += 1
                thread_ids.add(tid)

            # ---- users + participants -------------------------------------
            triples: list[tuple[str, object]] = [
                ("from", fm.get("from")),
                *(("to", v) for v in _as_address_list(fm.get("to"))),
                *(("cc", v) for v in _as_address_list(fm.get("cc"))),
            ]
            # `from` is a single string in the frontmatter; treat anything
            # else as missing rather than as a list.
            from_raw = fm.get("from")
            if not isinstance(from_raw, str) or not from_raw.strip():
                triples = [t for t in triples if t[0] != "from"]

            for role, header in triples:
                if not isinstance(header, str) or not header.strip():
                    continue
                display_name, email = parse_email_address(header)
                if not email:
                    continue
                if upsert_user(conn, email=email, display_name=display_name):
                    users_inserted += 1
                if insert_participant(
                    conn,
                    message_id=mid,
                    user_email=email,
                    role=role,
                    display_name=display_name,
                ):
                    participants_inserted += 1

        conn.commit()

        # Recompute aggregates for every thread we touched. This is cheap
        # enough at our scale (~thousands of threads), and it gives the
        # message_count column the source-of-truth refresh it needs.
        for tid in sorted(thread_ids):
            update_thread_aggregates(tid)

    click.echo(f"users inserted:        {users_inserted}")
    click.echo(f"threads inserted:      {threads_inserted}")
    click.echo(f"participants inserted: {participants_inserted}")
    click.echo(f"skipped (no message_id):       {skipped_no_id}")
    click.echo(f"skipped (message not in DB):   {skipped_unknown_message}")
    click.echo("\ntotals:")
    click.echo(f"  users:        {count_users()}")
    click.echo(f"  threads:      {count_threads()}")
    for role, n in count_participants_by_role().items():
        click.echo(f"  participants[{role}]: {n}")


if __name__ == "__main__":
    main()
