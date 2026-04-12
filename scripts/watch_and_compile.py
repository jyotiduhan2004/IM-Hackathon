"""Live mode: poll Gmail for new emails and compile them incrementally.

This is the simplest path to "live" — cheaper and easier to operate than
Gmail watch + Pub/Sub, and for email's natural cadence (minutes-granularity)
it's indistinguishable. No GCP topic setup needed.

Loop:
1. Fetch emails from the mailing list since the last seen historyId/date
2. Parse + save new ones to raw/
3. Compile any uncompiled raw files (small batch)
4. Sleep --interval seconds and repeat

State: stores last-seen timestamp in `.watch_state.json` so restarts resume.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.compiler import (  # noqa: E402
    list_uncompiled_emails,
    run_compilation,
    update_wiki_index,
)
from src.config import settings  # noqa: E402
from src.ingest.attachments import save_attachments  # noqa: E402
from src.ingest.gmail import GmailClient  # noqa: E402
from src.ingest.parser import generate_filename, parse_message, write_raw_email  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

STATE_FILE = REPO_ROOT / ".watch_state.json"

_STOP = False


def _handle_sigterm(signum, frame):  # type: ignore[no-untyped-def]
    global _STOP
    _STOP = True
    logger.info("shutdown requested", signal=signum)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


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
    uncompiled = list_uncompiled_emails.invoke({"raw_dir": str(settings.raw_dir)})
    if not uncompiled:
        return 0

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
        try:
            run_compilation(
                instruction=instruction,
                raw_dir=str(settings.raw_dir),
                wiki_dir=str(settings.wiki_dir),
            )
            processed += len(batch)
        except Exception as e:  # noqa: BLE001
            logger.error("compile batch failed", error=str(e))

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

    state = load_state()
    last_seen = state.get("last_seen")
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
                click.echo(
                    f"[{loop_start.isoformat()}] fetched {fetched} new email(s)"
                )

            processed = 0
            if not fetch_only:
                processed = compile_some(compile_limit, compile_batch_size)
                if processed:
                    click.echo(
                        f"[{loop_start.isoformat()}] compiled {processed} email(s)"
                    )

            # Advance since cursor so next tick doesn't re-scan same window
            since = loop_start
            state["last_seen"] = since.isoformat()
            state["last_fetched"] = fetched
            state["last_processed"] = processed
            save_state(state)

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
