"""Tests for per-batch stall detection in compile_all.py.

Covers the ``--batch-timeout`` safeguard: an interactive
``uv run python scripts/compile_all.py`` used to have no per-batch
timeout, so a single hung batch (slow OTel export, stuck LLM provider,
rare deadlock) could freeze the whole compile loop. ``_run_with_timeout``
caps each ``run_compilation`` call; ``TimeoutError`` flows into the
existing ``except`` branch that marks the batch failed and moves on.
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Unit tests for the helper itself.
# ---------------------------------------------------------------------------


def test_run_with_timeout_returns_result_before_timeout(compile_all_module):
    mod = compile_all_module
    assert mod._run_with_timeout(lambda: 42, timeout_s=10) == 42


def test_run_with_timeout_raises_on_hang(compile_all_module):
    """A sleep(2) wrapped with timeout_s=0.3 must raise TimeoutError fast."""
    mod = compile_all_module
    start = time.monotonic()
    with pytest.raises(concurrent.futures.TimeoutError):
        mod._run_with_timeout(lambda: time.sleep(2), timeout_s=0.3)
    elapsed = time.monotonic() - start
    # Must raise within ~1s — well before the sleep(2) would return.
    assert elapsed < 1.5, f"timeout should fire at ~0.3s, took {elapsed:.2f}s"


def test_run_with_timeout_zero_means_disabled(compile_all_module):
    """timeout_s=0 runs the callable inline with no wrapping."""
    mod = compile_all_module
    assert mod._run_with_timeout(lambda: "ok", timeout_s=0) == "ok"


def test_run_with_timeout_none_means_disabled(compile_all_module):
    mod = compile_all_module
    assert mod._run_with_timeout(lambda: "ok", timeout_s=None) == "ok"


def test_run_with_timeout_zero_runs_inline_no_executor(compile_all_module, monkeypatch):
    """timeout_s=0 must call fn() directly — never instantiate a pool.

    Guards against a regression where a truthy check (e.g. ``if timeout_s:``)
    is replaced with one that treats ``0`` as "start the executor with a
    zero budget", which would raise TimeoutError immediately.
    """
    mod = compile_all_module
    created = []

    class _BoomExecutor:
        def __init__(self, *a, **kw):
            created.append((a, kw))
            raise AssertionError("executor must not be created when timeout_s=0")

    monkeypatch.setattr(mod.concurrent.futures, "ThreadPoolExecutor", _BoomExecutor)
    assert mod._run_with_timeout(lambda: "inline", timeout_s=0) == "inline"
    assert created == []


def test_negative_batch_timeout_rejected(compile_all_module):
    """Click's IntRange(min=0) must reject --batch-timeout=-1 with a
    non-zero exit and an error message mentioning the valid range.

    Without this guard a negative value slips past ``if timeout_s:`` as
    truthy, then ``future.result(timeout=-1)`` fires TimeoutError
    immediately — every batch would look hung within milliseconds.
    """
    mod = compile_all_module
    result = CliRunner().invoke(
        mod.main,
        ["--batch-timeout", "-1"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0, result.output
    # Click's IntRange emits a 'not in the range' style message.
    output = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "-1" in output
    assert "range" in output.lower() or "0" in output


def test_run_with_timeout_propagates_inner_exceptions(compile_all_module):
    """Exceptions raised by fn() must bubble up unchanged."""
    mod = compile_all_module

    def boom() -> None:
        raise ValueError("inner failure")

    with pytest.raises(ValueError, match="inner failure"):
        mod._run_with_timeout(boom, timeout_s=5)


# ---------------------------------------------------------------------------
# Integration tests: drive `main` via Click's CliRunner and verify the log row.
# ---------------------------------------------------------------------------


def _seed_raw(raw_dir: Path, name: str = "a.md") -> Path:
    """Write a minimal raw email file so list_uncompiled_emails returns it.

    The real tool scans ``raw_dir`` for ``*.md`` without ``compiled: true``
    in the frontmatter. Shape here only needs to satisfy that filter —
    we don't actually run_compilation for real.
    """
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


def _patch_main_dependencies(
    mod,
    monkeypatch: pytest.MonkeyPatch,
    raw_dir: Path,
    wiki_dir: Path,
    run_compilation_impl,
) -> None:
    """Stub every external side effect so ``main`` runs in-process without
    touching Postgres, the LLM, or the filesystem outside ``tmp_path``.

    We keep ``_append_batch_log`` real — it writes ``wiki/log.md`` into
    ``tmp_path`` and is the assertion surface for these tests.
    """
    # Satisfy the F3 preflight (wiki_dir needs topics/) and redirect
    # REPO_ROOT so each test's pre-compile snapshot lands in tmp_path.
    (wiki_dir / "topics").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "REPO_ROOT", wiki_dir.parent)

    # Redirect settings to tmp_path.
    monkeypatch.setattr(mod.settings, "raw_dir", raw_dir)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki_dir)

    # LangChain StructuredTool is a frozen pydantic model — can't monkeypatch
    # its ``.invoke`` directly. Replace the whole attribute on the module
    # with a stub object that exposes the same ``.invoke(args)`` contract.
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

    # The thing we're actually testing — swap in the caller's behaviour.
    monkeypatch.setattr(mod, "run_compilation", run_compilation_impl)

    # DB + outside-world no-ops.
    monkeypatch.setattr(mod, "start_run", lambda **_: "run-id-test")
    monkeypatch.setattr(mod, "finish_run", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "fetch_budget", lambda: None)
    # ``not_cited`` position now returns a list of raw paths (was a
    # count pre-V12 fix-C) so the coordinator can selectively flip the
    # terminal-decision-guard-exhausted subset. Stub matches shape.
    monkeypatch.setattr(mod, "_mark_batch_compiled", lambda *_a, **_kw: (["m1"], [], [], 0))
    monkeypatch.setattr(mod, "_write_touch_catalog", lambda *_a, **_kw: 0)
    monkeypatch.setattr(mod, "_mark_batch_failed", lambda *_a, **_kw: 1)
    monkeypatch.setattr(mod, "_stamp_recently_modified_pages", lambda *a, **kw: (0, 0))

    # Block the post-run validator subprocess.
    class _Result:
        returncode = 0
        stdout = "validator stubbed"
        stderr = ""

    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())


def test_batch_timeout_fires_and_logs_failed(compile_all_module, monkeypatch, tmp_path):
    """--batch-timeout=1 with a hung run_compilation must produce a 'failed'
    row in wiki/log.md whose notes include the TimeoutError marker.
    """
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)

    def hung(**_kwargs):
        time.sleep(2)

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, hung)

    result = CliRunner().invoke(
        mod.main,
        ["--batch-size", "1", "--batch-timeout", "1"],
        catch_exceptions=False,
    )

    # Main logs the error and moves on — exit should still be 0 (the
    # per-batch failure is handled inside the loop).
    assert result.exit_code == 0, result.output

    log_path = wiki_dir / "log.md"
    assert log_path.exists(), "coordinator must write wiki/log.md on failure"
    log_text = log_path.read_text(encoding="utf-8")
    assert "| failed |" in log_text
    # Coordinator synthesizes "TimeoutError: batch exceeded Ns" because
    # concurrent.futures.TimeoutError has an empty str() — without it,
    # the notes column would be blank and the failure would look silent.
    assert "TimeoutError" in log_text, f"expected TimeoutError in notes, got:\n{log_text}"


def test_batch_timeout_success_logs_compiled(compile_all_module, monkeypatch, tmp_path):
    """A fast run_compilation within the timeout budget must log 'compiled',
    not 'failed' — guards against the wrapper producing false positives.
    """
    mod = compile_all_module
    raw_dir = tmp_path / "raw"
    wiki_dir = tmp_path / "wiki"
    _seed_raw(raw_dir)

    def fast(**_kwargs):
        return {"messages": []}

    _patch_main_dependencies(mod, monkeypatch, raw_dir, wiki_dir, fast)

    result = CliRunner().invoke(
        mod.main,
        ["--batch-size", "1", "--batch-timeout", "10"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    log_text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert "| compiled |" in log_text
    assert "| failed |" not in log_text


# ---------------------------------------------------------------------------
# Per-`agent.ainvoke` wall-clock timeout (#163)
#
# The outer `--batch-timeout` caps the whole batch including model retries,
# so a single wedged LLM round can still hang for hours before the outer
# budget trips. `_ainvoke_with_timeout` caps each round directly.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_ainvoke_times_out_and_raises():
    """A `agent.ainvoke` that never returns must raise InvokeWallClockTimeout
    well before the outer batch-timeout would have noticed.
    """
    from src.compile.compiler import InvokeWallClockTimeout
    from src.compile.compiler import _ainvoke_with_timeout

    class _HungAgent:
        async def ainvoke(self, *_args, **_kwargs):
            # Sleep well past the test's timeout so we can verify
            # wait_for fires and raises, not the body returning early.
            await __import__("asyncio").sleep(10)
            return {"messages": []}

    start = time.monotonic()
    with pytest.raises(InvokeWallClockTimeout):
        await _ainvoke_with_timeout(_HungAgent(), "do work", {}, timeout_s=1)
    elapsed = time.monotonic() - start
    # Must raise within ~2s of the 1s budget (tight but tolerant of
    # CI jitter). If this takes 10s, wait_for didn't actually fire.
    assert elapsed < 3.0, f"timeout should fire at ~1s, took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_graph_ainvoke_completes_under_timeout():
    """A fast-returning `agent.ainvoke` must pass through without raising."""
    from src.compile.compiler import _ainvoke_with_timeout

    class _FastAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {"messages": [{"role": "assistant", "content": "ok"}]}

    result = await _ainvoke_with_timeout(_FastAgent(), "do work", {}, timeout_s=5)
    assert result == {"messages": [{"role": "assistant", "content": "ok"}]}


@pytest.mark.asyncio
async def test_graph_ainvoke_preserves_other_exceptions():
    """Exceptions raised inside ainvoke (not timeouts) must bubble up
    unchanged — we only translate asyncio.TimeoutError.
    """
    from src.compile.compiler import _ainvoke_with_timeout

    class _BoomAgent:
        async def ainvoke(self, *_args, **_kwargs):
            raise ValueError("inner failure")

    with pytest.raises(ValueError, match="inner failure"):
        await _ainvoke_with_timeout(_BoomAgent(), "do work", {}, timeout_s=5)


def test_invoke_timeout_s_has_default():
    """settings.invoke_timeout_s must have a sane default — a single round
    shouldn't need more than a few minutes, and uncapped is the current bug.
    """
    from src.config import settings

    assert isinstance(settings.invoke_timeout_s, int)
    assert 30 <= settings.invoke_timeout_s <= 600
