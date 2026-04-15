"""Tests for the post-batch catalog sync (scripts/compile_all.py::_sync_wiki_catalog).

Covers the gap surfaced by the 2026-04-15 trace audit: the compile agent
created wiki pages but `wiki_pages` stayed empty, so `resolve_page`
couldn't find anything and the agent burned calls on silent misses.

These tests exercise the sync function directly against a tmp wiki + the
shared test DB, covering:

- topic / entity / system pages upsert
- entity's canonical email auto-inserts into `users`
- files outside a known category folder are ignored
- missing / malformed frontmatter fallbacks to filename-based title
- re-running is idempotent (row count doesn't grow)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_compile_all():  # type: ignore[no-untyped-def]
    """Import scripts/compile_all.py as a module (not on PYTHONPATH)."""
    spec = importlib.util.spec_from_file_location(
        "compile_all", REPO_ROOT / "scripts" / "compile_all.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["compile_all"] = module
    spec.loader.exec_module(module)
    return module


compile_all = _load_compile_all()


def _write_page(path: Path, frontmatter: dict[str, str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def test_sync_upserts_topic_entity_system(
    tmp_path: Path, db_conn: psycopg.Connection
) -> None:
    wiki = tmp_path / "wiki"
    topic = wiki / "topics" / "sync-test-topic.md"
    entity = wiki / "entities" / "sync-test-entity.md"
    system = wiki / "systems" / "sync-test-system.md"
    _write_page(topic, {"title": "Sync Test Topic", "page_type": "topic"}, "body")
    _write_page(
        entity,
        {"title": "Sync Test Person", "page_type": "entity", "email": "sync-test@example.com"},
        "body",
    )
    _write_page(system, {"title": "Sync Test System", "page_type": "system"}, "body")

    synced = compile_all._sync_wiki_catalog([topic, entity, system], wiki)

    assert synced == 3
    from src.db.wiki_pages import find_by_slug

    assert find_by_slug("sync-test-topic")["page_type"] == "topic"
    assert find_by_slug("sync-test-entity")["page_type"] == "entity"
    assert find_by_slug("sync-test-entity")["canonical_user_email"] == "sync-test@example.com"
    assert find_by_slug("sync-test-system")["page_type"] == "system"
    # Entity's canonical email was auto-inserted into users so the FK resolved.
    from src.db.users import find_by_email

    assert find_by_email("sync-test@example.com") is not None


def test_sync_ignores_out_of_category_files(
    tmp_path: Path, db_conn: psycopg.Connection
) -> None:
    wiki = tmp_path / "wiki"
    # Top-level files (no category folder) are not catalog entries.
    top = wiki / "sync-test-home.md"
    _write_page(top, {"title": "Home"}, "")
    # Unknown folder is also ignored.
    unknown = wiki / "drafts" / "sync-test-draft.md"
    _write_page(unknown, {"title": "Draft"}, "")

    synced = compile_all._sync_wiki_catalog([top, unknown], wiki)
    assert synced == 0


def test_sync_falls_back_to_stem_when_title_missing(
    tmp_path: Path, db_conn: psycopg.Connection
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "sync-test-no-title.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    # No frontmatter at all — must not crash; title falls back to stem.
    page.write_text("just body, no frontmatter\n", encoding="utf-8")

    synced = compile_all._sync_wiki_catalog([page], wiki)
    assert synced == 1

    from src.db.wiki_pages import find_by_slug

    row = find_by_slug("sync-test-no-title")
    assert row is not None
    assert "Sync Test No Title" in row["title"]


def test_sync_does_not_cascade_on_bad_row(
    tmp_path: Path, db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locks in the SAVEPOINT fix: a DB error on one page must not abort
    the whole batch's sync. Without per-page savepoints, psycopg3 leaves
    the connection in an aborted-transaction state after any constraint
    violation, and every subsequent `conn.execute()` raises
    `InFailedSqlTransaction` — which the broad `except Exception` in
    this helper would silently swallow, zeroing the entire batch's sync.

    Setup: three pages. The middle one triggers a constraint violation
    by stubbing `upsert_wiki_page` to raise on a specific slug. The
    other two must still sync.
    """
    wiki = tmp_path / "wiki"
    good_a = wiki / "topics" / "cascade-test-good-a.md"
    bad = wiki / "topics" / "cascade-test-bad.md"
    good_b = wiki / "topics" / "cascade-test-good-b.md"
    _write_page(good_a, {"title": "Good A", "page_type": "topic"}, "body")
    _write_page(bad, {"title": "Bad", "page_type": "topic"}, "body")
    _write_page(good_b, {"title": "Good B", "page_type": "topic"}, "body")

    import src.db.wiki_pages as repo

    real_upsert = repo.upsert_wiki_page

    def flaky_upsert(conn: psycopg.Connection, *, slug: str, **kw: Any) -> int:
        if slug == "cascade-test-bad":
            # Simulate a constraint violation from inside the DB.
            conn.execute("SELECT 1 / 0")
            return 0
        return real_upsert(conn, slug=slug, **kw)

    monkeypatch.setattr(compile_all, "upsert_wiki_page", flaky_upsert)

    synced = compile_all._sync_wiki_catalog([good_a, bad, good_b], wiki)

    # 2 of 3 must have synced (the good ones), not 0 or 1.
    assert synced == 2

    from src.db.wiki_pages import find_by_slug

    assert find_by_slug("cascade-test-good-a") is not None
    assert find_by_slug("cascade-test-bad") is None
    assert find_by_slug("cascade-test-good-b") is not None


def test_sync_stores_repo_relative_paths(
    tmp_path: Path, db_conn: psycopg.Connection
) -> None:
    """Locks in Claude review Bug 2: touched pages under REPO_ROOT must
    store repo-relative paths, matching scripts/backfill_wiki_pages.py.
    Otherwise the next compile overwrites backfilled relatives with
    absolutes via ON-CONFLICT UPDATE."""
    # Put a page under REPO_ROOT so the relative-path branch fires.
    wiki = compile_all.REPO_ROOT / "tests" / "fixtures" / "tmp_sync_rel"
    page = wiki / "topics" / "sync-test-rel-path.md"
    try:
        _write_page(page, {"title": "Rel Path", "page_type": "topic"}, "body")
        compile_all._sync_wiki_catalog([page], wiki)

        from src.db.wiki_pages import find_by_slug

        row = find_by_slug("sync-test-rel-path")
        assert row is not None
        assert not row["path"].startswith("/"), (
            f"expected repo-relative, got absolute: {row['path']}"
        )
        assert "tests/fixtures/tmp_sync_rel" in row["path"]
    finally:
        # Clean up the fixture even if the assertion fails.
        import shutil

        if wiki.exists():
            shutil.rmtree(wiki)


def test_sync_is_idempotent(tmp_path: Path, db_conn: psycopg.Connection) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "sync-test-idempotent.md"
    _write_page(page, {"title": "Idem", "page_type": "topic"}, "body")

    compile_all._sync_wiki_catalog([page], wiki)
    compile_all._sync_wiki_catalog([page], wiki)  # second call must not fail

    from src.db.wiki_pages import find_by_slug

    assert find_by_slug("sync-test-idempotent") is not None
