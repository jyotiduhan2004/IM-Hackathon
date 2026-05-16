"""Detect duplicate @pytest.fixture definitions across tests/ files.

Fixtures that appear in 2+ non-conftest test files are a sign the
fixture should be promoted to conftest.py. This tool surfaces those.

Enforcement is loose today: exit 0 even when duplicates exist, so CI
goes green on HEAD. After Unit 2 lands and the corpus is cleaned,
flip STRICT=True below to make this a hard gate.

Usage:
    uv run python scripts/check_duplicate_fixtures.py
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# After Unit 2 lands, set STRICT = True so duplicates fail the check.
STRICT = False


def _is_pytest_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        # Match `@pytest.fixture` or `@pytest.fixture(...)` or bare `@fixture`.
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "fixture"
            and isinstance(target.value, ast.Name)
            and target.value.id == "pytest"
        ):
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


def _fixture_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    # Normalize: hash the body only (skip leading docstring) so cosmetic
    # tweaks — docstrings, decorator args — don't hide real duplication.
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


def _scan(path: Path) -> list[tuple[str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_pytest_fixture(node):
            out.append((node.name, _fixture_signature(node)))
    return out


def main() -> int:
    if not TESTS_DIR.exists():
        print(f"tests/ not found at {TESTS_DIR}", file=sys.stderr)
        return 0

    fixtures: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for py_file in sorted(TESTS_DIR.rglob("*.py")):
        if py_file.name == "conftest.py":
            continue
        for name, body in _scan(py_file):
            fixtures[name].append((py_file, body))

    dupes: list[str] = []
    for name, entries in sorted(fixtures.items()):
        if len(entries) < 2:
            continue
        # Group entries by body so identical-body dupes are reported together.
        by_body: dict[str, list[Path]] = defaultdict(list)
        for path, body in entries:
            by_body[body].append(path)
        for paths in by_body.values():
            if len(paths) >= 2:
                rels = ", ".join(str(p.relative_to(REPO_ROOT)) for p in paths)
                dupes.append(f"{name}: {len(paths)} copies in {rels}")

    if not dupes:
        print("no duplicate fixtures detected.")
        return 0

    print(f"{len(dupes)} duplicate fixture group(s) found:")
    for line in dupes:
        print(f"  {line}")
    if STRICT:
        print("\nMove these into tests/conftest.py.", file=sys.stderr)
        return 1
    print("\nAdvisory only (STRICT=False). Flip STRICT=True after Unit 2 lands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
