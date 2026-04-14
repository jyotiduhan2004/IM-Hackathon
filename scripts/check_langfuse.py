"""Smoke-test Langfuse health and trace export."""

from __future__ import annotations

import sys
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path

import click
import httpx
from langfuse.api.resources.commons.errors.not_found_error import NotFoundError

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402


def _health_url(host: str) -> str:
    return f"{host.rstrip('/')}/api/public/health"


@click.command()
@click.option(
    "--wait-seconds",
    default=3.0,
    show_default=True,
    help="How long to wait after flush() before fetching the trace back.",
)
def main(wait_seconds: float) -> None:
    """Verify the configured Langfuse host can ingest and return a trace."""
    if not settings.langfuse_enabled:
        raise click.ClickException("LANGFUSE_ENABLED=false")

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise click.ClickException("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required")

    health_url = _health_url(settings.langfuse_host)
    try:
        health_response = httpx.get(health_url, timeout=2)
        health_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"health check failed: {exc}") from exc

    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        timeout=2,
        flush_at=1,
        flush_interval=1,
    )

    trace_id: str | None = None
    try:
        with client.start_as_current_span(
            name="langfuse-smoke-test",
            input={
                "checked_at": datetime.now(UTC).isoformat(),
                "source": "scripts/check_langfuse.py",
            },
            metadata={"workspace": REPO_ROOT.name},
        ) as span:
            trace_id = client.get_current_trace_id()
            span.update(output={"status": "ok"})

        client.flush()
        time.sleep(wait_seconds)

        if trace_id is None:
            raise click.ClickException("Langfuse client did not expose a trace_id")

        try:
            trace = client.api.trace.get(trace_id)
        except NotFoundError as exc:
            raise click.ClickException(
                f"trace export did not land in Langfuse: {trace_id}"
            ) from exc

        payload = health_response.json()
        click.echo(
            f"health: {payload.get('status', 'unknown')} ({payload.get('version', 'unknown')})"
        )
        click.echo(f"trace_id: {trace_id}")
        click.echo(f"trace_name: {trace.name}")
        click.echo(f"observations: {len(trace.observations)}")
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
