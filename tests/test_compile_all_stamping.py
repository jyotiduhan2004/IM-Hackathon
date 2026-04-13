"""Tests for the deterministic stamping helper in compile_all.py.

Covers the bug fix where the coordinator now stamps `last_compiled` on every
wiki page touched during a run, rather than trusting the LLM to call
`stamp_page_compiled_at` per-page (it routinely forgot, leaving stale
timestamps on re-edited pages).
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

from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402


def _load_compile_all():
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = REPO_ROOT / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_stamp_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_stamp_test"] = mod
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


def _write_page(path: Path, frontmatter: dict, body: str = "Body.\n") -> None:
    """Write a wiki page with frontmatter + body using the production renderer."""
    path.write_text(render_with_frontmatter(frontmatter, body), encoding="utf-8")


def _backdate(path: Path, seconds: int) -> None:
    """Push a file's mtime backwards by `seconds`."""
    target = time.time() - seconds
    os.utime(path, (target, target))


def test_skips_pages_unchanged_since_run_start(compile_all_module, wiki_dir):
    """Pages whose mtime is older than `since_timestamp` must be left alone."""
    mod = compile_all_module
    page_a = wiki_dir / "entities" / "alice.md"
    page_b = wiki_dir / "entities" / "bob.md"
    _write_page(page_a, {"title": "Alice", "page_type": "entity"})
    _write_page(page_b, {"title": "Bob", "page_type": "entity"})
    # Backdate to 60s ago so they're well before any since_timestamp we pick.
    _backdate(page_a, 60)
    _backdate(page_b, 60)

    # since_timestamp = 1 second ago — both files are older, nothing to stamp.
    since = time.time() - 1
    stamped, skipped = mod._stamp_recently_modified_pages(
        str(wiki_dir), since, "test-model"
    )

    assert stamped == 0
    assert skipped == 0
    fm_a = extract_frontmatter(page_a.read_text(encoding="utf-8"))
    fm_b = extract_frontmatter(page_b.read_text(encoding="utf-8"))
    assert "last_compiled" not in fm_a
    assert "last_compiled" not in fm_b
    assert "updated_by" not in fm_a
    assert "update_count" not in fm_b


def test_stamps_pages_modified_after_run_start(compile_all_module, wiki_dir):
    """Pages touched after `since_timestamp` get last_compiled/updated_by/count."""
    mod = compile_all_module
    page_a = wiki_dir / "entities" / "alice.md"
    page_b = wiki_dir / "topics" / "foo.md"
    _write_page(page_a, {"title": "Alice", "page_type": "entity"})
    _write_page(page_b, {"title": "Foo", "page_type": "topic"})
    _backdate(page_a, 60)
    _backdate(page_b, 60)

    since = time.time()
    # Touch both files AFTER capturing `since` so they qualify as "modified".
    time.sleep(0.05)
    page_a.touch()
    page_b.touch()

    stamped, skipped = mod._stamp_recently_modified_pages(
        str(wiki_dir), since, "test-model"
    )

    assert stamped == 2
    assert skipped == 0
    fm_a = extract_frontmatter(page_a.read_text(encoding="utf-8"))
    fm_b = extract_frontmatter(page_b.read_text(encoding="utf-8"))
    for fm in (fm_a, fm_b):
        assert "last_compiled" in fm
        assert fm["updated_by"] == "test-model"
        assert fm["update_count"] == 1


def test_update_count_increments_on_restamp(compile_all_module, wiki_dir):
    """Re-stamping the same page bumps update_count from 1 (first stamp) to 2."""
    mod = compile_all_module
    page = wiki_dir / "policies" / "p1.md"
    _write_page(page, {"title": "P1", "page_type": "policy"})

    # First stamp pass — `since` is older than the page mtime so it qualifies.
    since1 = time.time() - 60
    stamped1, _ = mod._stamp_recently_modified_pages(
        str(wiki_dir), since1, "test-model"
    )
    assert stamped1 == 1
    fm1 = extract_frontmatter(page.read_text(encoding="utf-8"))
    assert fm1["update_count"] == 1

    # Touch the page to advance mtime past `since2`, then re-stamp.
    time.sleep(0.05)
    since2 = time.time()
    time.sleep(0.05)
    page.touch()
    stamped2, _ = mod._stamp_recently_modified_pages(
        str(wiki_dir), since2, "test-model"
    )
    assert stamped2 == 1
    fm2 = extract_frontmatter(page.read_text(encoding="utf-8"))
    assert fm2["update_count"] == 2


def test_skips_corrupt_frontmatter_pages(compile_all_module, wiki_dir):
    """Pages without title or page_type are treated as mangled and skipped."""
    mod = compile_all_module
    orphan = wiki_dir / "entities" / "orphan.md"
    # Frontmatter present but missing both title AND page_type — same guard
    # update_wiki_index uses to detect agent edit_file corruption.
    orphan.write_text(
        "---\nlast_compiled: '1999-01-01T00:00:00+00:00'\n---\n\nbody\n",
        encoding="utf-8",
    )
    healthy = wiki_dir / "entities" / "healthy.md"
    _write_page(healthy, {"title": "Healthy", "page_type": "entity"})

    since = time.time() - 1
    # Touch both so mtime is fresh — only healthy should be stamped.
    orphan.touch()
    healthy.touch()

    stamped, skipped = mod._stamp_recently_modified_pages(
        str(wiki_dir), since, "test-model"
    )

    assert stamped == 1
    assert skipped == 1
    # Orphan untouched (still has its original 1999 timestamp)
    fm_orphan = extract_frontmatter(orphan.read_text(encoding="utf-8"))
    assert fm_orphan["last_compiled"] == "1999-01-01T00:00:00+00:00"
    assert "updated_by" not in fm_orphan
    fm_healthy = extract_frontmatter(healthy.read_text(encoding="utf-8"))
    assert fm_healthy["update_count"] == 1


def test_returns_zero_for_missing_wiki_dir(compile_all_module, tmp_path):
    """No wiki/ at all → silent (0, 0), no exception."""
    mod = compile_all_module
    stamped, skipped = mod._stamp_recently_modified_pages(
        str(tmp_path / "does-not-exist"), time.time() - 1, "test-model"
    )
    assert (stamped, skipped) == (0, 0)
