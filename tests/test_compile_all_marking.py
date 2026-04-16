"""Tests for the deterministic batch marking helpers in compile_all.py.

Covers the catalog-truth v7 Phase A U1 rework: the coordinator now reads
`message_touched_pages` (not wiki frontmatter) to decide which batch
emails to flip to `compiled` / `skipped` / kept pending. Entity/person
stubs no longer count as "compiled" evidence (Bug C is dead).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

from src.db.wiki_pages import upsert_wiki_page


def _load_compile_all():
    """Load scripts/compile_all.py as a module so we can test its helpers."""
    path = Path(__file__).parent.parent / "scripts" / "compile_all.py"
    spec = importlib.util.spec_from_file_location("_compile_all_for_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_compile_all_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def compile_all_module():
    return _load_compile_all()


def _insert_message(conn, *, message_id: str, raw_path: str, state: str = "pending") -> None:
    conn.execute(
        """
        INSERT INTO messages (
          message_id, raw_path, thread_id, subject, from_address, date, compile_state
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (message_id, raw_path, "t1", "subj", "a@b.c", datetime.now(UTC), state),
    )


def _insert_page(conn, *, slug: str, page_type: str) -> int:
    """Upsert a wiki_pages row and return its page_id.

    Goes through the repo's upsert so the test exercises the same
    code path the compile loop uses for catalog sync.
    """
    return upsert_wiki_page(
        conn,
        slug=slug,
        path=f"wiki/{page_type}s/{slug}.md",
        title=slug.replace("-", " ").title(),
        page_type=page_type,
        status="active",
    )


def _insert_touch(conn, *, message_id: str, page_id: int) -> None:
    conn.execute(
        """
        INSERT INTO message_touched_pages (message_id, page_id)
        VALUES (%s, %s)
        """,
        (message_id, page_id),
    )


def _insert_run(conn) -> uuid.UUID:
    """Minimal ``compile_runs`` row — insights FK `run_id` needs it."""
    row = conn.execute(
        "INSERT INTO compile_runs (model, notes) VALUES (%s, %s) RETURNING run_id",
        ("test-model", "test"),
    ).fetchone()
    return row["run_id"]


def _insert_insight(conn, *, run_id: uuid.UUID, category: str, email_path: str) -> None:
    conn.execute(
        """
        INSERT INTO compile_insights (run_id, category, message, email_path)
        VALUES (%s, %s, %s, %s)
        """,
        (run_id, category, "test insight", email_path),
    )


def _state(conn, message_id: str) -> str:
    row = conn.execute(
        "SELECT compile_state FROM messages WHERE message_id = %s", (message_id,)
    ).fetchone()
    assert row is not None
    return row["compile_state"]


def _trivial_skip_category_accepted(conn) -> bool:
    """Probe the test-schema ``compile_insights`` CHECK for `trivial_skip`.

    The source-schema CHECK is widened by migration
    202604160500_compile_insights_trivial_skip.sql but the test-schema
    mirror in tests/conftest.py may lag for a commit. When the CHECK
    rejects ``trivial_skip`` we skip the skipped-insight tests instead
    of failing — mirrors the CLAUDE.md guardrail gating rule.
    """
    try:
        conn.execute(
            """
            INSERT INTO compile_insights (category, message, email_path)
            VALUES ('trivial_skip', 'probe', 'raw/probe.md')
            """
        )
    except Exception:  # noqa: BLE001 — this is a feature probe
        conn.rollback()
        return False
    conn.rollback()
    return True


def test_batch_paths_handles_dicts_and_strings(compile_all_module):
    mod = compile_all_module
    assert mod._batch_paths(["a", "b"]) == ["a", "b"]
    assert mod._batch_paths([{"path": "a"}, {"path": "b"}]) == ["a", "b"]
    assert mod._batch_paths(["a", {"path": "b"}]) == ["a", "b"]


def test_mark_batch_compiled_only_flips_content_touched(
    compile_all_module, db_conn, tmp_path
):
    """Messages with a touch on a content-type page flip to compiled;
    messages touched only on a person/entity stub stay pending (Bug C dead)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    _insert_message(db_conn, message_id="m3", raw_path="raw/c.md")
    # m1 → topic page (content)  → compiled
    # m2 → no touches             → not cited (pending)
    # m3 → person stub only       → Bug C: stays pending, NOT compiled
    topic_id = _insert_page(db_conn, slug="topic-a", page_type="topic")
    person_id = _insert_page(db_conn, slug="person-c", page_type="person")
    _insert_touch(db_conn, message_id="m1", page_id=topic_id)
    _insert_touch(db_conn, message_id="m3", page_id=person_id)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}, {"path": "raw/c.md"}]
    compiled, skipped, not_cited, missing = mod._mark_batch_compiled(batch, tmp_path)
    assert set(compiled) == {"m1"}
    assert skipped == []
    assert not_cited == 2  # m2 (no touches) + m3 (person stub only)
    assert missing == 0
    assert _state(db_conn, "m1") == "compiled"
    assert _state(db_conn, "m2") == "pending"
    assert _state(db_conn, "m3") == "pending"


def test_mark_batch_compiled_reports_missing(compile_all_module, db_conn, tmp_path):
    """Paths with no `messages` row count as missing (backfill drift)."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    topic_id = _insert_page(db_conn, slug="topic-a", page_type="topic")
    _insert_touch(db_conn, message_id="m1", page_id=topic_id)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/not-in-db.md"}]
    compiled, skipped, not_cited, missing = mod._mark_batch_compiled(batch, tmp_path)
    assert compiled == ["m1"]
    assert skipped == []
    assert not_cited == 0
    assert missing == 1
    assert _state(db_conn, "m1") == "compiled"


def test_mark_batch_compiled_all_uncited_keeps_all_pending(
    compile_all_module, db_conn, tmp_path
):
    """No touches in the catalog → every batch email stays pending."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}]
    compiled, skipped, not_cited, _missing = mod._mark_batch_compiled(batch, tmp_path)
    assert compiled == []
    assert skipped == []
    assert not_cited == 2
    assert _state(db_conn, "m1") == "pending"
    assert _state(db_conn, "m2") == "pending"


def test_mark_batch_compiled_skips_on_trivial_insight(
    compile_all_module, db_conn, tmp_path
):
    """A message with a `trivial_skip` insight logged in the current run
    flips to ``skipped`` instead of staying pending."""
    mod = compile_all_module
    if not _trivial_skip_category_accepted(db_conn):
        pytest.skip("test-schema CHECK lags production — trivial_skip not yet accepted")
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    run_id = _insert_run(db_conn)
    _insert_insight(db_conn, run_id=run_id, category="trivial_skip", email_path="raw/a.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, skipped, not_cited, missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=run_id
    )
    assert compiled == []
    assert skipped == ["m1"]
    assert not_cited == 0
    assert missing == 0
    assert _state(db_conn, "m1") == "skipped"


def test_mark_batch_compiled_skip_insight_from_other_run_ignored(
    compile_all_module, db_conn, tmp_path
):
    """A trivial_skip insight from a prior run must not flip this run's
    messages — skip-insight lookup is scoped by run_id."""
    mod = compile_all_module
    if not _trivial_skip_category_accepted(db_conn):
        pytest.skip("test-schema CHECK lags production — trivial_skip not yet accepted")
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    old_run = _insert_run(db_conn)
    _insert_insight(db_conn, run_id=old_run, category="trivial_skip", email_path="raw/a.md")
    current_run = _insert_run(db_conn)
    db_conn.commit()

    batch = [{"path": "raw/a.md"}]
    compiled, skipped, not_cited, _missing = mod._mark_batch_compiled(
        batch, tmp_path, run_id=current_run
    )
    assert compiled == []
    assert skipped == []  # the prior-run insight does not reach across runs
    assert not_cited == 1
    assert _state(db_conn, "m1") == "pending"


def test_mark_batch_failed_flips_to_failed(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}]
    marked = mod._mark_batch_failed(batch, "recursion limit hit")
    assert marked == 2
    assert _state(db_conn, "m1") == "failed"
    assert _state(db_conn, "m2") == "failed"
    row = db_conn.execute("SELECT last_error FROM messages WHERE message_id = 'm1'").fetchone()
    assert row["last_error"] == "recursion limit hit"


def test_mark_batch_failed_truncates_long_error(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    db_conn.commit()

    long_err = "x" * 10_000
    mod._mark_batch_failed([{"path": "raw/a.md"}], long_err)
    row = db_conn.execute("SELECT last_error FROM messages WHERE message_id = 'm1'").fetchone()
    assert len(row["last_error"]) == 500


def test_write_touch_catalog_filters_to_content_pages(
    compile_all_module, db_conn, tmp_path
):
    """``_write_touch_catalog`` writes a row for each (batch message,
    touched content-type page) pair and silently ignores entity/person
    stubs — core of the Bug C fix."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    topic_id = _insert_page(db_conn, slug="my-topic", page_type="topic")
    person_id = _insert_page(db_conn, slug="alice", page_type="person")
    db_conn.commit()

    # Simulate the on-disk "touched pages" list: a topic page + a person
    # stub. Only the topic should land in the catalog.
    topic_path = tmp_path / "topics" / "my-topic.md"
    person_path = tmp_path / "people" / "alice.md"
    topic_path.parent.mkdir(parents=True, exist_ok=True)
    person_path.parent.mkdir(parents=True, exist_ok=True)
    topic_path.write_text("stub", encoding="utf-8")
    person_path.write_text("stub", encoding="utf-8")

    inserted = mod._write_touch_catalog([topic_path, person_path], ["m1"])
    assert inserted == 1

    touches = db_conn.execute(
        "SELECT message_id, page_id FROM message_touched_pages ORDER BY page_id"
    ).fetchall()
    assert len(touches) == 1
    assert touches[0]["message_id"] == "m1"
    assert touches[0]["page_id"] == topic_id
    # Person stub row is absent — Bug C stays dead.
    assert person_id not in {t["page_id"] for t in touches}


def test_write_touch_catalog_idempotent(compile_all_module, db_conn, tmp_path):
    """Re-running the hook over the same (message, page) pair is a no-op
    thanks to the ON CONFLICT guard in ``insert_touch``."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_page(db_conn, slug="my-topic", page_type="topic")
    db_conn.commit()

    topic_path = tmp_path / "topics" / "my-topic.md"
    topic_path.parent.mkdir(parents=True, exist_ok=True)
    topic_path.write_text("stub", encoding="utf-8")

    first = mod._write_touch_catalog([topic_path], ["m1"])
    second = mod._write_touch_catalog([topic_path], ["m1"])
    assert first == 1
    assert second == 0  # second call is a pure ON CONFLICT DO NOTHING

    count = db_conn.execute("SELECT COUNT(*)::int AS c FROM message_touched_pages").fetchone()
    assert count["c"] == 1


def test_collect_content_cited_message_ids_filters_by_page_type(
    compile_all_module, db_conn
):
    """The catalog query joins ``wiki_pages`` and filters to
    ``CONTENT_PAGE_TYPES`` — entity/person touches are excluded."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    topic_id = _insert_page(db_conn, slug="t", page_type="topic")
    person_id = _insert_page(db_conn, slug="alice", page_type="person")
    _insert_touch(db_conn, message_id="m1", page_id=topic_id)
    _insert_touch(db_conn, message_id="m2", page_id=person_id)
    db_conn.commit()

    cited = mod._collect_content_cited_message_ids(["m1", "m2"])
    assert cited == {"m1"}  # m2's person-stub touch is filtered out
