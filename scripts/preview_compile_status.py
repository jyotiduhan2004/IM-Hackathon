"""Print the compile-status block to stdout against the live DB.

Use this to eyeball the rendered Markdown locally before deploy — handy
because `wiki/compile-status.md` is gitignored and only the deployed
MkDocs site shows the rendered charts. Pipe to a file if you want to
preview in a Markdown viewer.

Usage:
    uv run python scripts/preview_compile_status.py
    uv run python scripts/preview_compile_status.py > /tmp/preview.md
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.wiki.landing import _compile_progress_block  # noqa: E402


def main() -> None:
    lines = _compile_progress_block()
    if not lines:
        print("(empty — DB unreachable or no messages rows)", file=sys.stderr)
        sys.exit(1)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
