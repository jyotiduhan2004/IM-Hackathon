"""Shared test helper: load a script module by name for white-box testing.

The underscore prefix keeps pytest from trying to collect this as a test file.
Individual scripts under scripts/ are not on sys.path; tests that exercise
their internals reach in via importlib. Centralising that boilerplate avoids
having the same spec_from_file_location incantation copy-pasted across 10+
test files.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str) -> ModuleType:
    """Load scripts/<name>.py as a fresh module.

    No sys.modules caching — each call returns a new module object so tests
    that monkeypatch module state stay isolated.

    The module IS registered in sys.modules during exec to support dataclass
    forward-ref resolution and any submodule relative imports the script does,
    but it's popped on teardown via a try/finally so the next call re-execs
    from scratch.
    """
    path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    # Don't pop on success: the module must remain in sys.modules for pickling,
    # dataclass __module__ resolution, etc. The NEXT load_script call will
    # overwrite it with a fresh instance.
    return module
