"""Tests for the ``--deploy`` / ``--deploy-force`` flags in compile_all.py.

After a compile run completes cleanly, the coordinator can optionally fire
``make publish`` (or ``make publish-force``) so operators don't have to
remember the separate deploy step. The guardrails are:

* Deploy runs only when ``run_status == "completed"`` — a killed/failed run
  must never ship a half-compiled wiki to Cloud Run.
* ``--deploy-force`` maps to ``make publish-force`` (skips the validation
  gate); plain ``--deploy`` maps to ``make publish`` (gate enforced).
* Neither flag → subprocess not touched by the deploy branch.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


def _load_compile_all():
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = Path(__file__).parent.parent / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_deploy_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_deploy_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module():
    return _load_compile_all()


def _seed_raw(raw_dir: Path, name: str = "a.md") -> Path:
    """Write a minimal raw email the tool will pick up as uncompiled."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(
        "---\n"
        "message_id: m1\n"
        "thread_id: t1\n"
        "date: 2026-04-13T00:00:00Z\n"
        "subject: test\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    return path


class _FakeResult:
    """Stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_main_dependencies(
    mod,
    monkeypatch: pytest.MonkeyPatch,
    raw_dir: Path,
    wiki_dir: Path,
    run_compilation_impl,
    subprocess_calls: list[tuple],
) -> None:
    """Stub every external side effect so ``main`` runs in-process without
    touching Postgres, the LLM, or the real ``make``.

    ``subprocess_calls`` is the mutable list we append each ``subprocess.run``
    invocation to — tests assert against it.
    """
    monkeypatch.setattr(mod.settings, "raw_dir", raw_dir)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki_dir)

    class _ListStub:
        @staticmethod
        def invoke(_args):
            return [
                {"path": str(raw_dir / "a.md"), "thread_id": "t1", "date": "2026-04-13"},
            ]

    monkeypatch.setattr(mod, "list_uncompiled_emails", _ListStub)

    class _IndexStub:
        @staticmethod
        def invoke(_args):
            return "index regenerated (stubbed)"

    monkeypatch.setattr(mod, "update_wiki_index", _IndexStub)
    monkeypatch.setattr(mod, "run_compilation", run_compilation_impl)

    # DB + outside-world no-ops.
    monkeypatch.setattr(mod, "start_run", lambda **_: "run-id-test")
    monkeypatch.setattr(mod, "finish_run", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "fetch_budget", lambda: None)
    monkeypatch.setattr(mod, "_mark_batch_compiled", lambda *_a, **_kw: (1, 0, 0))
    monkeypatch.setattr(mod, "_mark_batch_failed", lambda *_a, **_kw: 1)
    monkeypatch.setattr(mod, "_stamp_recently_modified_pages", lambda *a, **kw: (0, 0))
    monkeypatch.setattr(mod, "_flush_tool_calls", lambda *_a, **_kw: "")

    def _fake_run(cmd, *args, **kwargs):
        subprocess_calls.append((list(cmd), kwargs))
        return _FakeResult(returncode=0, stdout="stub", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)


def _run_main(mod, args: list[str]):
    return CliRunner().invoke(mod.main, args, catch_exceptions=False)


def test_deploy_flag_calls_make_publish(compile_all_module, monkeypatch, tmp_path):
    """``--deploy`` on a completed run invokes ``make publish``."""
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)
    calls: list[tuple] = []

    def fast(**_kwargs):
        return {"messages": []}

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, fast, calls)

    result = _run_main(mod, ["--batch-size", "1", "--batch-timeout", "10", "--deploy"])
    assert result.exit_code == 0, result.output

    make_calls = [c for c in calls if c[0] and c[0][0] == "make"]
    assert make_calls, f"expected `make publish` call, got {calls}"
    assert make_calls[0][0] == ["make", "publish"]


def test_deploy_force_flag_calls_make_publish_force(compile_all_module, monkeypatch, tmp_path):
    """``--deploy-force`` on a completed run invokes ``make publish-force``."""
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)
    calls: list[tuple] = []

    def fast(**_kwargs):
        return {"messages": []}

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, fast, calls)

    result = _run_main(mod, ["--batch-size", "1", "--batch-timeout", "10", "--deploy-force"])
    assert result.exit_code == 0, result.output

    make_calls = [c for c in calls if c[0] and c[0][0] == "make"]
    assert make_calls, f"expected `make publish-force` call, got {calls}"
    assert make_calls[0][0] == ["make", "publish-force"]


def test_no_deploy_by_default(compile_all_module, monkeypatch, tmp_path):
    """Without either flag the coordinator must NOT call ``make``."""
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)
    calls: list[tuple] = []

    def fast(**_kwargs):
        return {"messages": []}

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, fast, calls)

    result = _run_main(mod, ["--batch-size", "1", "--batch-timeout", "10"])
    assert result.exit_code == 0, result.output

    make_calls = [c for c in calls if c[0] and c[0][0] == "make"]
    assert make_calls == [], f"expected no `make` invocation, got {make_calls}"


def test_deploy_skipped_on_failed_run(compile_all_module, monkeypatch, tmp_path):
    """``--deploy`` + Ctrl+C mid-run must NOT fire ``make publish``.

    Deploy is guarded by ``run_status == 'completed'``; KeyboardInterrupt
    re-raises out of the ``finally:`` block before the deploy branch is
    reached. The invariant we care about: a killed/failed run never ships
    a half-compiled wiki to Cloud Run.

    (The other "run_status != completed" path — an uncaught Exception
    escaping the loop — also bubbles past the deploy block via the same
    finally-rethrow mechanism, so this test covers both failure modes.)
    """
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)
    calls: list[tuple] = []

    def fast(**_kwargs):
        return {"messages": []}

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, fast, calls)

    def ctrl_c(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "run_compilation", ctrl_c)

    result = CliRunner().invoke(
        mod.main,
        ["--batch-size", "1", "--batch-timeout", "10", "--deploy"],
        catch_exceptions=True,  # KeyboardInterrupt bubbles out
    )
    # CliRunner surfaces the KeyboardInterrupt on result.exception.
    assert isinstance(result.exception, KeyboardInterrupt) or result.exit_code != 0, result.output

    make_calls = [c for c in calls if c[0] and c[0][0] == "make"]
    assert make_calls == [], f"deploy must not run after KeyboardInterrupt, got {make_calls}"


def test_deploy_failure_propagates_exit_code(compile_all_module, monkeypatch, tmp_path):
    """When ``make publish`` exits non-zero, compile_all must exit with the
    same code so CI / operators see the failure rather than a success.
    """
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)
    calls: list[tuple] = []

    def fast(**_kwargs):
        return {"messages": []}

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, fast, calls)

    # Override subprocess.run so the make call fails but validator passes.
    def _fake_run(cmd, *args, **kwargs):
        calls.append((list(cmd), kwargs))
        if cmd and cmd[0] == "make":
            return _FakeResult(returncode=7, stdout="", stderr="gsutil blew up")
        return _FakeResult(returncode=0, stdout="validator stub", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = CliRunner().invoke(
        mod.main,
        ["--batch-size", "1", "--batch-timeout", "10", "--deploy"],
        catch_exceptions=False,
    )
    assert result.exit_code == 7, result.output
    make_calls = [c for c in calls if c[0] and c[0][0] == "make"]
    assert make_calls and make_calls[0][0] == ["make", "publish"]
