"""Advisory check: surface one-shot scripts past their delete-by date.

Scripts meant to run once (backfills, migrations) should include a
`Safe to delete after: YYYY-MM-DD` marker. This walker prints any
script whose marker is more than 7 days in the past — a nudge to
remove the file, not a hard failure.

Exit code is always 0.

Usage:
    uv run python scripts/check_one_shot_expiry.py
"""

from __future__ import annotations

import re
import sys
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
GRACE = timedelta(days=7)

_MARKER_RE = re.compile(r"Safe to delete after:\s*(\d{4}-\d{2}-\d{2})")


def _find_markers() -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not SCRIPTS_DIR.exists():
        return out
    for py_file in sorted(SCRIPTS_DIR.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = _MARKER_RE.search(text)
        if not match:
            continue
        try:
            parsed = datetime.strptime(match.group(1), "%Y-%m-%d").date()  # noqa: DTZ007 — date-only, tz irrelevant
        except ValueError:
            continue
        out.append((py_file, parsed))
    return out


def main() -> int:
    today = datetime.now(UTC).date()
    expired: list[tuple[Path, date]] = []
    for path, marker_date in _find_markers():
        if today > marker_date + GRACE:
            expired.append((path, marker_date))

    if not expired:
        print("no expired one-shot scripts.")
        return 0

    print(f"{len(expired)} one-shot script(s) past expiry (+{GRACE.days}d grace):")
    for path, marker_date in expired:
        rel = path.relative_to(REPO_ROOT)
        print(f"  {rel}  (marked {marker_date.isoformat()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
