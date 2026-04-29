"""Live mode: poll Gmail for new emails and compile them incrementally.

This is the simplest path to "live" — cheaper and easier to operate than
Gmail watch + Pub/Sub, and for email's natural cadence (minutes-granularity)
it's indistinguishable. No GCP topic setup needed.

Loop:
1. Fetch emails from the mailing list since the last seen historyId/date
2. Parse + save new ones to raw/
3. Compile any uncompiled raw files (small batch)
4. Sleep --interval seconds and repeat

State: stores the last-seen cursor in the `ingest_cursors` table
(cursor_name='gmail_history') so restarts resume. A legacy
`.watch_state.json` file is migrated once on first run and renamed to
`.watch_state.json.migrated`.

Usage:
    uv run python scripts/watch_and_compile.py                 # 5-min poll, 10-email compile batches
    uv run python scripts/watch_and_compile.py --interval 300 --compile-limit 10
    uv run python scripts/watch_and_compile.py --compile-only  # don't fetch, just compile backlog
    uv run python scripts/watch_and_compile.py --fetch-only    # don't compile, just pull new
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent.compiler_agent import run_compilation  # noqa: E402
from src.agent.tools.sources import list_uncompiled_emails  # noqa: E402
from src.config import settings  # noqa: E402
from src.db.cursors import read_cursor  # noqa: E402
from src.db.cursors import write_cursor  # noqa: E402
from src.ingest.attachments import save_attachments  # noqa: E402
from src.ingest.gmail import GmailClient  # noqa: E402
from src.ingest.parser import generate_filename  # noqa: E402
from src.ingest.parser import parse_message  # noqa: E402
from src.ingest.parser import write_raw_email  # noqa: E402
from src.wiki.landing import update_wiki_index  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

LEGACY_STATE_FILE = REPO_ROOT / ".watch_state.json"
CURSOR_NAME = "gmail_history"

_STOP = False


def _handle_sigterm(signum, frame):  # type: ignore[no-untyped-def]
    global _STOP
    _STOP = True
    logger.info("shutdown requested", signal=signum)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def load_last_seen() -> str | None:
    """Return the persisted cursor value (ISO timestamp), or None.

    Preference order:
    1. `ingest_cursors` row for CURSOR_NAME.
    2. One-time migration from `.watch_state.json` — if the DB cursor is
       empty and the legacy file exists, copy its `last_seen` into the DB
       and rename the file to `.watch_state.json.migrated` so we don't
       re-migrate on the next start.
    """
    row = read_cursor(CURSOR_NAME)
    if row is not None:
        return row["history_id"]

    if LEGACY_STATE_FILE.exists():
        legacy = json.loads(LEGACY_STATE_FILE.read_text())
        last_seen = legacy.get("last_seen")
        if last_seen:
            write_cursor(CURSOR_NAME, last_seen)
            LEGACY_STATE_FILE.rename(LEGACY_STATE_FILE.with_suffix(".json.migrated"))
            logger.info(
                "migrated .watch_state.json to ingest_cursors",
                cursor_name=CURSOR_NAME,
                history_id=last_seen,
            )
            return last_seen

    return None


def fetch_new_emails(
    client: GmailClient,
    list_address: str,
    since: datetime,
    skip_attachments: bool = True,
) -> int:
    """Fetch any emails from list_address since `since`. Returns count saved."""
    stubs = client.list_messages(list_address=list_address, after=since.date(), max_results=500)
    if not stubs:
        return 0

    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for stub in stubs:
        try:
            raw = client.get_message(stub.id)
            parsed = parse_message(raw)
            if (settings.raw_dir / generate_filename(parsed)).exists():
                continue
            attachment_paths: list[str] = []
            if parsed.attachments and not skip_attachments:
                attachment_paths = save_attachments(
                    client, parsed, settings.raw_dir, skip_download=False
                )
            write_raw_email(parsed, settings.raw_dir, attachment_paths=attachment_paths)
            saved += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch failed", message_id=stub.id, error=str(e))
    return saved


def compile_some(limit: int, batch_size: int) -> int:
    """Compile up to `limit` uncompiled emails (oldest first). Returns processed."""
    import time

    from src.coordinator.post_batch import _backfill_references_on_touched_pages
    from src.coordinator.post_batch import _iter_touched_content_pages
    from src.wiki.references import clear_raw_index_cache

    uncompiled = list_uncompiled_emails.invoke({"raw_dir": str(settings.raw_dir)})
    if not uncompiled:
        return 0

    # The watch loop fetches new emails into raw/ between iterations, so the
    # raw-index cache built last tick is stale by definition this tick. Drop
    # it once at the start so the backfill sees the new files.
    clear_raw_index_cache()

    to_do = uncompiled[:limit]
    processed = 0
    for i in range(0, len(to_do), batch_size):
        if _STOP:
            break
        batch = to_do[i : i + batch_size]
        paths = [b["path"] if isinstance(b, dict) else b for b in batch]
        instruction = (
            f"Compile the following {len(batch)} uncompiled raw emails into wiki pages. "
            f"Process chronologically. Mark each as compiled.\n\n"
            f"Files:\n" + "\n".join(f"- {p}" for p in paths)
        )
        batch_start = time.time()
        try:
            run_compilation(
                instruction=instruction,
                raw_dir=str(settings.raw_dir),
                wiki_dir=str(settings.wiki_dir),
            )
            processed += len(batch)
        except Exception as e:  # noqa: BLE001
            logger.error("compile batch failed", error=str(e))
            continue
        # Mirror compile_all.py's deterministic ## References backfill so
        # live-mode pages don't ship with dangling [^msg-x] refs. Without
        # this, the agent's prompt rule "stop authoring References" leaves
        # cited-but-unverifiable pages in non-coordinator flows.
        wiki_path = Path(settings.wiki_dir)
        _backfill_references_on_touched_pages(
            _iter_touched_content_pages(batch_start, wiki_path), wiki_path
        )

    if processed:
        update_wiki_index.invoke({"wiki_dir": str(settings.wiki_dir)})
    return processed


@click.command()
@click.option("--interval", default=300, help="Seconds between polls (default 300 = 5 min)")
@click.option("--compile-limit", default=10, help="Max emails to compile per loop tick")
@click.option("--compile-batch-size", default=5, help="Emails per agent invocation")
@click.option("--lookback-days", default=2, help="How far back to look on first start")
@click.option("--fetch-only", is_flag=True, help="Only fetch; don't compile")
@click.option("--compile-only", is_flag=True, help="Only compile backlog; don't fetch")
@click.option("--once", is_flag=True, help="Run a single tick then exit")
def main(
    interval: int,
    compile_limit: int,
    compile_batch_size: int,
    lookback_days: int,
    fetch_only: bool,
    compile_only: bool,
    once: bool,
) -> None:
    """Poll Gmail + compile loop."""
    list_address = settings.mailing_list_address
    if not list_address and not compile_only:
        click.echo("ERROR: MAILING_LIST_ADDRESS not set in .env", err=True)
        sys.exit(1)

    client: GmailClient | None = None
    if not compile_only:
        client = GmailClient(
            credentials_path=settings.gmail_credentials_path,
            token_path=settings.gmail_token_path,
        )
        client.authenticate()

    last_seen = load_last_seen()
    since = (
        datetime.fromisoformat(last_seen)
        if last_seen
        else datetime.now(UTC) - timedelta(days=lookback_days)
    )

    click.echo(f"Watching mailing list: {list_address}")
    click.echo(f"Interval: {interval}s  compile_limit/tick: {compile_limit}")
    click.echo(f"Last seen: {since.isoformat()}")
    click.echo()

    while not _STOP:
        loop_start = datetime.now(UTC)
        try:
            fetched = 0
            if not compile_only and client is not None:
                fetched = fetch_new_emails(client, list_address, since)
                click.echo(f"[{loop_start.isoformat()}] fetched {fetched} new email(s)")

            processed = 0
            if not fetch_only:
                processed = compile_some(compile_limit, compile_batch_size)
                if processed:
                    click.echo(f"[{loop_start.isoformat()}] compiled {processed} email(s)")

            # Advance since cursor so next tick doesn't re-scan same window
            since = loop_start
            write_cursor(CURSOR_NAME, since.isoformat())

        except Exception as e:  # noqa: BLE001
            logger.error("tick failed", error=str(e))

        if once or _STOP:
            break

        # Sleep but remain responsive to SIGINT
        for _ in range(interval):
            if _STOP:
                break
            time.sleep(1)

    click.echo("Watcher stopped.")


if __name__ == "__main__":
    main()
