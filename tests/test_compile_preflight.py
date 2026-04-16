"""Tests for the F3 preflight mount-sanity guards.

Context — 2026-04-16: live Tier-A traces silently failed when the agent's
``/raw`` mount was empty (just ``.gitkeep`` + ``attachments/``). The DB
still returned valid raw_paths, but ``read_file("/raw/...md")`` produced
empty content, and traces looked like "0 content page attempts" when the
real problem was environmental (wrong cwd / empty worktree mount).

These tests pin the three-layer guard:

1. Startup preflight in ``scripts/compile_all.py::main`` — aborts nonzero
   before any LLM/DB work when ``raw_dir`` has 0 ``.md`` files or the wiki
   tree isn't a real wiki (missing ``topics/``).
2. Per-batch preflight in ``src/compile/compiler.py::run_compilation`` —
   raises ``FileNotFoundError`` if any batch raw_path is missing from
   disk BEFORE the agent is invoked.
3. Mounted-view sanity — raises ``RuntimeError`` if a batch path doesn't
   resolve inside the chroot view-root.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_compile_all() -> Any:
    """Load scripts/compile_all.py as a named module for helper tests."""
    path = REPO_ROOT / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_preflight_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_preflight_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module() -> Any:
    return _load_compile_all()


def _seed_wiki_tree(wiki_dir: Path) -> None:
    """Create a minimal wiki layout that passes the preflight (has topics/)."""
    (wiki_dir / "topics").mkdir(parents=True, exist_ok=True)
    (wiki_dir / "entities").mkdir(parents=True, exist_ok=True)


def _seed_real_raw(raw_dir: Path, n: int = 1) -> list[Path]:
    """Write ``n`` minimal raw emails under ``raw_dir``."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(n):
        p = raw_dir / f"2026-04-16_test_{i}.md"
        p.write_text(
            f"---\nmessage_id: m{i}\nthread_id: t{i}\nsubject: s{i}\n"
            "date: 2026-04-16T00:00:00Z\n---\n\nbody\n",
            encoding="utf-8",
        )
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Level 1 — startup preflight via ``scripts/compile_all.py::main``.
# ---------------------------------------------------------------------------


def test_compile_aborts_when_raw_dir_has_no_md_files(
    compile_all_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup preflight: raw_dir with only .gitkeep must abort nonzero.

    Reproduces the 2026-04-16 codex worktree failure mode — the DB has
    valid compile queue entries, but the mount the script reads from is
    empty. The guard fires before any LLM / DB call.
    """
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    raw_dir.mkdir()
    (raw_dir / ".gitkeep").write_text("", encoding="utf-8")
    # Attachments subtree should NOT count as "real .md files".
    (raw_dir / "attachments").mkdir()
    (raw_dir / "attachments" / "blob.bin").write_bytes(b"")
    _seed_wiki_tree(wiki_dir)

    monkeypatch.setattr(mod.settings, "raw_dir", raw_dir)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki_dir)

    result = CliRunner().invoke(mod.main, ["--batch-size", "1", "--batch-timeout", "10"])
    assert result.exit_code != 0, result.output
    assert "0 .md files" in result.output


def test_compile_aborts_when_raw_dir_missing(
    compile_all_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing raw_dir is a distinct early exit from the 0-md-files case."""
    mod = compile_all_module
    raw_dir = tmp_path / "raw"  # intentionally not created
    wiki_dir = tmp_path / "wiki"
    _seed_wiki_tree(wiki_dir)
    monkeypatch.setattr(mod.settings, "raw_dir", raw_dir)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki_dir)

    result = CliRunner().invoke(mod.main, ["--batch-size", "1", "--batch-timeout", "10"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_compile_aborts_when_wiki_tree_has_no_topics(
    compile_all_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wiki dir without ``topics/`` subdir is probably the wrong path."""
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_real_raw(raw_dir, n=2)
    wiki_dir.mkdir()  # exists but NO topics/ subdir — should trip the guard
    monkeypatch.setattr(mod.settings, "raw_dir", raw_dir)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki_dir)

    result = CliRunner().invoke(mod.main, ["--batch-size", "1", "--batch-timeout", "10"])
    assert result.exit_code != 0
    assert "topics" in result.output


def test_preflight_passes_on_populated_corpus(compile_all_module: Any, tmp_path: Path) -> None:
    """Direct helper test: happy path returns md count > 0 and no exception.

    Decoupled from ``main`` so future main() refactors don't break the
    unit-level guard coverage.
    """
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_real_raw(raw_dir, n=3)
    _seed_wiki_tree(wiki_dir)

    md_count = mod._preflight_mount_sanity(raw_dir, wiki_dir)
    assert md_count == 3


# ---------------------------------------------------------------------------
# Level 2 — per-batch preflight in ``run_compilation``.
# ---------------------------------------------------------------------------


def test_run_compilation_aborts_when_batch_raw_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch whose raw_path doesn't exist on disk must raise before invoke.

    Simulates the exact failure mode: DB returned a raw_path, the file
    has since been deleted (or was never synced to this worktree's
    mount). We must fail fast with a FileNotFoundError BEFORE the agent
    burns an LLM call.
    """
    from src.compile import compiler as compiler_mod

    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_wiki_tree(wiki_dir)
    raw_dir.mkdir()
    missing_path = str(raw_dir / "2026-04-16_deleted.md")

    # The agent must NEVER be created on this path.
    called: dict[str, bool] = {"agent": False}

    def _boom_create(*_args: Any, **_kwargs: Any) -> Any:
        called["agent"] = True
        raise AssertionError("run_compilation invoked the agent despite missing raw path")

    monkeypatch.setattr(compiler_mod, "create_compiler", _boom_create)
    monkeypatch.setattr(compiler_mod, "get_langfuse_handler", lambda **_k: None)

    with pytest.raises(FileNotFoundError, match="raw files missing"):
        compiler_mod.run_compilation(
            instruction="Compile these:\nraw/2026-04-16_deleted.md",
            raw_dir=str(raw_dir),
            wiki_dir=str(wiki_dir),
            raw_paths=[missing_path],
        )
    assert called["agent"] is False


def test_run_compilation_does_not_trip_on_empty_raw_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty batch shouldn't falsely trip either preflight.

    ``run_compilation`` is sometimes invoked without a batch list (e.g.
    free-form queries from operator scripts). The guard must fire only
    when the caller explicitly asked for specific raw paths.
    """
    from src.compile import compiler as compiler_mod

    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_wiki_tree(wiki_dir)
    raw_dir.mkdir()

    invoked: dict[str, Any] = {}

    class _StubAgent:
        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
            invoked["config"] = config
            return {"ok": True}

    monkeypatch.setattr(compiler_mod, "create_compiler", lambda **_k: _StubAgent())
    monkeypatch.setattr(compiler_mod, "get_langfuse_handler", lambda **_k: None)

    # instruction has no raw paths and raw_paths=[] → preflight is a no-op.
    result = compiler_mod.run_compilation(
        instruction="No paths referenced.",
        raw_dir=str(raw_dir),
        wiki_dir=str(wiki_dir),
        raw_paths=[],
    )
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Level 3 — mounted-view sanity (batch path outside view-root chroot).
# ---------------------------------------------------------------------------


def test_run_compilation_aborts_when_view_root_missing_expected_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch path that exists on disk but is outside view_root must abort.

    The FilesystemBackend chroots the agent's filesystem to view_root.
    If a batch path lives outside that subtree, every `read_file` for
    it silently returns empty. We catch this by mocking
    ``_build_compile_view`` to return an unrelated tempdir while the
    batch raw_paths live elsewhere.
    """
    from src.compile import compiler as compiler_mod

    raw_dir = tmp_path / "real-raw"
    wiki_dir = tmp_path / "real-wiki"
    _seed_wiki_tree(wiki_dir)
    real_paths = _seed_real_raw(raw_dir, n=1)

    # Force the view-root elsewhere so the real raw_paths fall outside it.
    outside_view = tmp_path / "elsewhere"
    outside_view.mkdir()
    monkeypatch.setattr(compiler_mod, "_build_compile_view", lambda *_: outside_view)

    def _boom_create(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("agent created despite view-root mismatch")

    monkeypatch.setattr(compiler_mod, "create_compiler", _boom_create)
    monkeypatch.setattr(compiler_mod, "get_langfuse_handler", lambda **_k: None)

    with pytest.raises(RuntimeError, match="view-root /raw is missing"):
        compiler_mod.run_compilation(
            instruction="Compile.",
            raw_dir=str(raw_dir),
            wiki_dir=str(wiki_dir),
            raw_paths=[str(p) for p in real_paths],
        )


def test_run_compilation_injects_mount_metadata_into_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trace metadata must gain cwd / raw_dir / view_root / mount counts.

    These keys let the Langfuse scorecard + audit surface the
    infra-vs-synthesis distinction. Verifies defaults are emitted even
    when the caller provides a small ``trace_metadata`` dict.
    """
    from src.compile import compiler as compiler_mod

    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_wiki_tree(wiki_dir)
    _seed_real_raw(raw_dir, n=2)

    captured: dict[str, Any] = {}

    class _StubAgent:
        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
            captured["config"] = config
            return {"ok": True}

    monkeypatch.setattr(compiler_mod, "create_compiler", lambda **_k: _StubAgent())
    monkeypatch.setattr(compiler_mod, "get_langfuse_handler", lambda **_k: None)

    compiler_mod.run_compilation(
        instruction="Compile.",
        raw_dir=str(raw_dir),
        wiki_dir=str(wiki_dir),
        raw_paths=[],
        trace_metadata={"compile_model": "test-model"},
    )
    md = captured["config"]["metadata"]
    assert md["compile_model"] == "test-model"
    assert "cwd" in md
    assert "raw_dir" in md
    assert "view_root" in md
    assert md["mounted_raw_file_count"] == 2
    assert md["missing_raw_paths_count"] == 0
