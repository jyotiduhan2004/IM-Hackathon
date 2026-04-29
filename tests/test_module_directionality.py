"""Enforce the architectural dependency direction.

Four structural rules, no allowlists. The dependency arrows go strictly:

    scripts → coordinator → agent → wiki + db + observability + utils
                                  ↘ db + observability + utils
       wiki → db + observability + utils

If a future change wants to violate a rule, it argues for it in code review,
not by adding an exemption here. The Codex 2026-04-29 plan review explicitly
rejected the day-1 allowlist approach as a debt registry.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"


def _walk_imports(py_file: Path) -> set[str]:
    """Return every module string from `from X import Y` and `import X`."""
    out: set[str] = set()
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return out


def test_wiki_does_not_import_agent_or_coordinator() -> None:
    """src/wiki/ is the data layer — agent and coordinator depend on it, not the other way."""
    for py in (SRC / "wiki").rglob("*.py"):
        for mod in _walk_imports(py):
            assert not mod.startswith("src.agent"), (
                f"{py.relative_to(REPO_ROOT)}: forbidden src.agent import — "
                f"wiki may not depend on agent"
            )
            assert not mod.startswith("src.coordinator"), (
                f"{py.relative_to(REPO_ROOT)}: forbidden src.coordinator import — "
                f"wiki may not depend on coordinator"
            )


def test_agent_does_not_import_coordinator() -> None:
    """src/agent/ is the thinking layer — coordinator wraps it, not vice versa."""
    for py in (SRC / "agent").rglob("*.py"):
        for mod in _walk_imports(py):
            assert not mod.startswith("src.coordinator"), (
                f"{py.relative_to(REPO_ROOT)}: forbidden src.coordinator import — "
                f"agent may not depend on coordinator"
            )


def test_no_compile_package_remains() -> None:
    """src/compile/ was the junk-drawer package; the 2026-04-29 refactor deleted it."""
    assert not (SRC / "compile").exists(), (
        "src/compile/ should not exist — its contents moved to src/{wiki,agent,coordinator}/"
    )


def test_scripts_do_not_import_src_compile() -> None:
    """src/compile/ is dead — no script should resurrect it.

    Scripts ARE allowed to reach into src.agent / src.wiki / etc. directly
    when they're single-purpose CLIs around a specific feature
    (judge_wiki, score_wiki, dump_agent_diagram). Forcing them through
    src.coordinator would create a re-export shim with no value. The
    only structural rule worth enforcing for scripts is the no-compile
    one.
    """
    for py in SCRIPTS.rglob("*.py"):
        for mod in _walk_imports(py):
            if mod.startswith("src.compile"):
                raise AssertionError(
                    f"{py.relative_to(REPO_ROOT)}: src.compile is deleted — "
                    f"retarget to src.{{wiki,agent,coordinator,observability}}"
                )


def test_coordinator_does_not_import_scripts() -> None:
    """src/coordinator/ wraps the agent for batch execution; it must not depend
    on scripts/. Scripts call coordinator, not the reverse."""
    for py in (SRC / "coordinator").rglob("*.py"):
        for mod in _walk_imports(py):
            if mod.startswith("scripts"):
                raise AssertionError(
                    f"{py.relative_to(REPO_ROOT)}: forbidden scripts.{mod[len('scripts.') :]} "
                    f"import — extract reusable code into src.wiki / src.coordinator"
                )
