"""Pull historical emails from a Gmail mailing list into raw/.

Usage:
    uv run python scripts/ingest_backlog.py --days 30
    uv run python scripts/ingest_backlog.py --after 2026-01-01 --before 2026-04-01
    uv run python scripts/ingest_backlog.py --days 30 --dry-run
    uv run python scripts/ingest_backlog.py --days 30 --skip-attachments
"""

from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import click
import structlog

# Add repo root to Python path so `src` imports work when run via `uv run`
REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.ingest.attachments import save_attachments  # noqa: E402
from src.ingest.gmail import GmailClient  # noqa: E402
from src.ingest.parser import parse_message  # noqa: E402
from src.ingest.parser import write_raw_email  # noqa: E402

# Configure structlog for pretty CLI output
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)


@click.command()
@click.option("--days", type=int, help="Fetch emails from last N days")
@click.option(
    "--after",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Fetch emails after this date (YYYY-MM-DD)",
)
@click.option(
    "--before",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Fetch emails before this date (YYYY-MM-DD)",
)
@click.option(
    "--mailing-list",
    default=None,
    help="Override mailing list address from .env",
)
@click.option(
    "--max-results",
    default=500,
    help="Maximum messages to fetch (default 500)",
)
@click.option("--dry-run", is_flag=True, help="List messages without saving")
@click.option("--skip-attachments", is_flag=True, help="Skip attachment downloads")
@click.option("--query", default="", help="Additional Gmail search query")
def main(
    days: int | None,
    after: datetime | None,
    before: datetime | None,
    mailing_list: str | None,
    max_results: int,
    dry_run: bool,
    skip_attachments: bool,
    query: str,
) -> None:
    """Fetch and store historical emails from a Gmail mailing list."""
    list_address = mailing_list or settings.mailing_list_address
    if not list_address and not query:
        click.echo(
            "ERROR: no mailing list address configured. "
            "Set MAILING_LIST_ADDRESS in .env or pass --mailing-list.",
            err=True,
        )
        sys.exit(1)

    # Resolve date range
    if days:
        after = datetime.now(UTC) - timedelta(days=days)
    if not after and not before and not days:
        click.echo("ERROR: specify --days or --after/--before date range", err=True)
        sys.exit(1)

    click.echo(f"Ingesting emails from: {list_address or '(query only)'}")
    if after:
        click.echo(f"  After:  {after.strftime('%Y-%m-%d')}")
    if before:
        click.echo(f"  Before: {before.strftime('%Y-%m-%d')}")
    if query:
        click.echo(f"  Extra query: {query}")
    click.echo(f"  Max results: {max_results}")
    click.echo(f"  Dry run: {dry_run}")
    click.echo()

    client = GmailClient(
        credentials_path=settings.gmail_credentials_path,
        token_path=settings.gmail_token_path,
    )

    click.echo("Authenticating with Gmail...")
    client.authenticate()

    click.echo("Listing messages...")
    stubs = client.list_messages(
        list_address=list_address,
        after=after,
        before=before,
        query=query,
        max_results=max_results,
    )
    click.echo(f"Found {len(stubs)} messages.")

    if dry_run:
        for stub in stubs[:20]:
            click.echo(f"  {stub.id} (thread {stub.thread_id})")
        if len(stubs) > 20:
            click.echo(f"  ... and {len(stubs) - 20} more")
        click.echo("\nDry run complete. No files written.")
        return

    settings.raw_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    skipped = 0
    errors = 0

    for i, stub in enumerate(stubs, 1):
        try:
            click.echo(f"[{i}/{len(stubs)}] Fetching {stub.id}...", nl=False)
            raw = client.get_message(stub.id)
            parsed = parse_message(raw)

            # Check for dup before doing work
            from src.ingest.parser import generate_filename

            filename = generate_filename(parsed)
            target = settings.raw_dir / filename
            if target.exists():
                click.echo(" skipped (exists)")
                skipped += 1
                continue

            # Save attachments
            attachment_paths: list[str] = []
            if parsed.attachments and not skip_attachments:
                attachment_paths = save_attachments(
                    client, parsed, settings.raw_dir, skip_download=False
                )

            write_raw_email(parsed, settings.raw_dir, attachment_paths=attachment_paths)
            click.echo(f" saved: {filename}")
            saved += 1
        except Exception as e:  # noqa: BLE001 - top-level per-message error isolation
            logger.error("ingest failed", message_id=stub.id, error=str(e))
            click.echo(f" ERROR: {e}")
            errors += 1

    click.echo()
    click.echo(f"Done. Saved: {saved}, Skipped: {skipped}, Errors: {errors}")


if __name__ == "__main__":
    main()
