"""Enforce per-file LOC ceilings across src/ and scripts/.

Ratchet-down guardrail against the "one-file-grows-forever" failure
mode. Hot files that already exist get a grandfathered ceiling slightly
above their current size; every other .py file in src/ and scripts/
shares a 1500-LOC default. New hot files should not be added to CEILINGS
without discussion — they belong under 1500.

Usage:
    uv run python scripts/check_file_sizes.py
    uv run python scripts/check_file_sizes.py path/to/extra_file.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Grandfathered ceilings (file path → max LOC, chosen as current + small buffer).
# snapshot as of 2026-04-18; ratchet down over time
CEILINGS: dict[str, int] = {
    "src/compile/compiler.py": 3500,
    "scripts/compile_all.py": 2580,
    "scripts/validate_wiki.py": 1500,
    "scripts/trace_scorecard.py": 1500,
}

# Default ceiling for any .py file not in CEILINGS.
DEFAULT_CEILING = 1500

# Warning (not failure) threshold for any file.
WARN_THRESHOLD = 1000

SCAN_DIRS = ("src", "scripts")


def _count_lines(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def _iter_py_files(extra: list[Path]) -> list[Path]:
    files: list[Path] = []
    for rel in SCAN_DIRS:
        root = REPO_ROOT / rel
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.py")))
    for p in extra:
        resolved = p if p.is_absolute() else (REPO_ROOT / p)
        if resolved.is_file() and resolved not in files:
            files.append(resolved)
    return files


def _ceiling_for(rel_path: str) -> int:
    return CEILINGS.get(rel_path, DEFAULT_CEILING)


def main(argv: list[str]) -> int:
    extra = [Path(a) for a in argv[1:]]
    files = _iter_py_files(extra)

    violations: list[str] = []
    warnings: list[str] = []
    for path in files:
        try:
            rel = str(path.relative_to(REPO_ROOT))
        except ValueError:
            rel = str(path)
        try:
            loc = _count_lines(path)
        except OSError as exc:
            violations.append(f"{rel}: could not read ({exc})")
            continue
        ceiling = _ceiling_for(rel)
        if loc > ceiling:
            violations.append(f"{rel}: {loc} LOC exceeds ceiling {ceiling}")
        elif loc > WARN_THRESHOLD and rel not in CEILINGS:
            warnings.append(f"{rel}: {loc} LOC (warn threshold {WARN_THRESHOLD})")

    for line in warnings:
        print(f"warn: {line}")
    for line in violations:
        print(f"fail: {line}")

    if violations:
        print(f"\n{len(violations)} file(s) over ceiling.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
