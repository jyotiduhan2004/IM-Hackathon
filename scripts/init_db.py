"""Apply src/db/schema.sql to the configured DATABASE_URL. Idempotent.

Usage:
    uv run python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402


@click.command()
def main() -> None:
    schema_path = REPO_ROOT / "src" / "db" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(sql)
        conn.commit()
    click.echo(f"applied {schema_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
