"""Tests for the post-batch normalization + validation hooks in compile_all.py.

Context: the compile agent still writes pages that fail validators (duplicate
``## Related`` headings, broken wikilinks, malformed frontmatter) despite a
prompt rule against it. ``_normalize_touched_pages`` runs the idempotent
formatter in-process over any wiki page whose mtime advanced during a batch,
catching the most common format drift before the next batch sees it.
``_validate_touched_pages`` then surfaces anything the formatter couldn't
auto-fix so operators notice immediately instead of hours later.

These tests cover the `mtime >= batch_start` filter, the formatter actually
firing on touched pages, and the "validator failure doesn't crash the batch"
guarantee.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_compile_all():
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = REPO_ROOT / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_post_batch_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_post_batch_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module():
    return _load_compile_all()


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    """Build an empty wiki tree with the standard category subdirs."""
    wiki = tmp_path / "wiki"
    for cat in ("topics", "entities", "systems", "policies", "timelines", "conflicts"):
        (wiki / cat).mkdir(parents=True)
    return wiki


def _write_page(path: Path, title: str, page_type: str, body: str) -> None:
    """Write a minimal valid wiki page with frontmatter + body."""
    path.write_text(
        f"---\ntitle: {title}\npage_type: {page_type}\nstatus: current\n---\n\n{body}",
        encoding="utf-8",
    )


def _backdate(path: Path, seconds: int) -> None:
    """Push a file's mtime backwards by ``seconds``."""
    target = time.time() - seconds
    os.utime(path, (target, target))


def test_normalize_touched_pages_skips_untouched(compile_all_module, wiki_dir: Path) -> None:
    """Pages whose mtime predates ``batch_start`` must be left alone.

    Guards the contract: the hook should only normalize pages touched DURING
    the batch, not every page in the wiki. A page with agent-written ``##
    Related`` from a previous batch would get re-written if we didn't filter.
    """
    mod = compile_all_module
    # A pre-existing page with an agent-written ## Related that the formatter
    # would strip if given the chance.
    stale = wiki_dir / "topics" / "stale.md"
    _write_page(
        stale,
        title="Stale",
        page_type="topic",
        body=("Stale is an old topic. Two sentences required.\n\n## Related\n\n- [[nowhere]]\n"),
    )
    _backdate(stale, 60)
    original = stale.read_text(encoding="utf-8")

    # batch_start is after the file's mtime — hook should skip it.
    batch_start = time.time() - 1
    normalized = mod._normalize_touched_pages(batch_start, wiki_dir)

    assert normalized == []
    assert stale.read_text(encoding="utf-8") == original


def test_normalize_touched_pages_processes_new(compile_all_module, wiki_dir: Path) -> None:
    """A page whose mtime advanced after ``batch_start`` gets formatted."""
    mod = compile_all_module
    page = wiki_dir / "topics" / "fresh.md"
    _write_page(
        page,
        title="Fresh",
        page_type="topic",
        body=(
            "Fresh is a topic the agent just wrote. Two sentences.\n\n"
            "## Related\n\n- [[other]]\n\n"
            "## People\n\n- [[jane-doe]]\n"
        ),
    )
    _backdate(page, 60)

    # Capture batch_start BEFORE touching the file so it qualifies as "new".
    batch_start = time.time()
    time.sleep(0.05)
    page.touch()

    normalized = mod._normalize_touched_pages(batch_start, wiki_dir)

    # The formatter stripped the agent-written nav sections (and the
    # regenerated Related block has no valid targets to emit because the
    # referenced pages don't exist). So the page changed.
    assert page in normalized
    content = page.read_text(encoding="utf-8")
    assert "## People" not in content


def test_normalize_ignores_policies_category(compile_all_module, wiki_dir: Path) -> None:
    """The formatter scope is topics/entities/systems only; policies keep
    their own template (History, Supersedes, etc.) and must not be touched."""
    mod = compile_all_module
    policy = wiki_dir / "policies" / "p.md"
    _write_page(
        policy,
        title="P",
        page_type="policy",
        body=("Policy P covers a rule. Two sentences.\n\n## Related\n\n- [[whatever]]\n"),
    )

    batch_start = time.time() - 60  # policy is newer than batch_start
    normalized = mod._normalize_touched_pages(batch_start, wiki_dir)

    assert policy not in normalized


def test_normalize_returns_empty_when_wiki_missing(compile_all_module, tmp_path: Path) -> None:
    """Missing wiki dir → empty list, no exception (matches stamp helper)."""
    mod = compile_all_module
    result = mod._normalize_touched_pages(time.time(), tmp_path / "does-not-exist")
    assert result == []


def test_validate_hook_returns_error_map(compile_all_module, wiki_dir: Path) -> None:
    """``_validate_touched_pages`` returns a page→errors dict for bad pages,
    and omits clean pages entirely."""
    mod = compile_all_module
    clean = wiki_dir / "topics" / "clean.md"
    _write_page(
        clean,
        title="Clean",
        page_type="topic",
        body="Clean is a fine page. It has enough frontmatter.\n",
    )
    bad = wiki_dir / "topics" / "bad.md"
    # Malformed frontmatter: three fences instead of two — the "extra ---"
    # pattern that's one of the top-three agent errors we're trying to catch.
    bad.write_text(
        "---\ntitle: Bad\npage_type: topic\nstatus: current\n---\n"
        "---\nstray fence above body\n---\n\nBody.\n",
        encoding="utf-8",
    )

    errors_by_page = mod._validate_touched_pages([clean, bad], wiki_dir)

    assert clean not in errors_by_page
    assert bad in errors_by_page
    assert any("fence" in e.reason.lower() for e in errors_by_page[bad])


def test_validate_hook_skips_crashes_without_raising(
    compile_all_module, wiki_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A buggy validator must not tear down the batch — exceptions are
    swallowed and logged, and the affected page is simply omitted from the
    result map."""
    mod = compile_all_module

    def _boom(path: Path) -> list:
        del path  # unused — test stub raises regardless
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(mod, "validate_page", _boom)
    page = wiki_dir / "topics" / "any.md"
    _write_page(page, title="Any", page_type="topic", body="body\n")

    # Must not raise.
    errors_by_page = mod._validate_touched_pages([page], wiki_dir)
    assert errors_by_page == {}


def test_validate_hook_logs_errors_but_does_not_fail_batch(
    compile_all_module,
    wiki_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end post-batch flow on a broken page: the formatter runs, the
    validator flags the page, a structured warning is logged, and the call
    returns cleanly so the batch can continue.

    Simulates the user's ship scenario: the compile agent wrote a
    three-fence page (top-three error class); the hook must catch and
    surface it without rolling back the commit. structlog uses
    ``ConsoleRenderer`` which writes to stdout/stderr directly, so we
    assert on ``capsys`` rather than the stdlib ``caplog`` bridge.
    """
    mod = compile_all_module
    page = wiki_dir / "topics" / "three-fence.md"
    page.write_text(
        "---\ntitle: Three\npage_type: topic\nstatus: current\n---\n"
        "---\nstray fence\n---\n\nBody text.\n",
        encoding="utf-8",
    )
    _backdate(page, 60)
    batch_start = time.time()
    time.sleep(0.05)
    page.touch()  # advance mtime past batch_start

    normalized = mod._normalize_touched_pages(batch_start, wiki_dir)
    errors_by_page = mod._validate_touched_pages(normalized, wiki_dir)

    assert page in errors_by_page
    # The batch would mark "compiled" at the call site; validator errors
    # don't flip the outcome, they only surface in logs + batch notes.
    # Mimic the production call to confirm the structlog renderer emits.
    mod.logger.warning(
        "batch touched pages have validator errors",
        batch_index=1,
        errors=[
            {"page": str(p), "reasons": [e.reason for e in errs]}
            for p, errs in errors_by_page.items()
        ],
    )
    captured = capsys.readouterr()
    # The rendered line carries the message verbatim + the error reasons.
    combined = captured.out + captured.err
    assert "validator errors" in combined
    assert "fence" in combined  # reason-level detail surfaced for the operator
