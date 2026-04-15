"""Tests for the deterministic batch marking helpers in compile_all.py.

Covers the bug fix where the coordinator now flips `compile_state` in
Postgres after run_compilation returns, rather than trusting the LLM
to call `mark_as_compiled` correctly.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest


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
        INSERT INTO messages (message_id, raw_path, thread_id, subject, from_address, date, compile_state)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (message_id, raw_path, "t1", "subj", "a@b.c", datetime.now(UTC), state),
    )


def _state(conn, message_id: str) -> str:
    row = conn.execute(
        "SELECT compile_state FROM messages WHERE message_id = %s", (message_id,)
    ).fetchone()
    assert row is not None
    return row["compile_state"]


def test_batch_paths_handles_dicts_and_strings(compile_all_module):
    mod = compile_all_module
    assert mod._batch_paths(["a", "b"]) == ["a", "b"]
    assert mod._batch_paths([{"path": "a"}, {"path": "b"}]) == ["a", "b"]
    # Mixed is fine:
    assert mod._batch_paths(["a", {"path": "b"}]) == ["a", "b"]


def _make_wiki_page(wiki_dir: Path, category: str, slug: str, sources: list[str]) -> None:
    (wiki_dir / category).mkdir(parents=True, exist_ok=True)
    src_yaml = "\n".join(f"  - {s}" for s in sources)
    (wiki_dir / category / f"{slug}.md").write_text(
        f"---\n"
        f"title: {slug}\n"
        f"page_type: {category[:-1]}\n"
        f"status: current\n"
        f"sources:\n{src_yaml}\n"
        f"---\n\nbody\n",
        encoding="utf-8",
    )


def test_mark_batch_compiled_only_flips_cited_emails(compile_all_module, db_conn, tmp_path):
    """If a wiki page cites the email's raw_path, the agent demonstrably did
    the work — flip to compiled. If not, leave pending."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    _insert_message(db_conn, message_id="m3", raw_path="raw/c.md")
    db_conn.commit()

    # m1 and m3 are cited by wiki pages; m2 is not (agent skipped it).
    _make_wiki_page(tmp_path, "topics", "one", sources=["raw/a.md"])
    _make_wiki_page(tmp_path, "entities", "two", sources=["raw/c.md", "raw/a.md"])

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}, {"path": "raw/c.md"}]
    marked, not_cited, missing = mod._mark_batch_compiled(batch, tmp_path)
    assert set(marked) == {"m1", "m3"}
    assert not_cited == 1
    assert missing == 0
    assert _state(db_conn, "m1") == "compiled"
    assert _state(db_conn, "m2") == "pending"
    assert _state(db_conn, "m3") == "compiled"


def test_mark_batch_compiled_reports_missing(compile_all_module, db_conn, tmp_path):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    db_conn.commit()

    _make_wiki_page(tmp_path, "topics", "one", sources=["raw/a.md", "raw/not-in-db.md"])

    batch = [{"path": "raw/a.md"}, {"path": "raw/not-in-db.md"}]
    marked, not_cited, missing = mod._mark_batch_compiled(batch, tmp_path)
    assert marked == ["m1"]
    assert not_cited == 0
    assert missing == 1
    assert _state(db_conn, "m1") == "compiled"


def test_mark_batch_compiled_all_uncited_keeps_all_pending(compile_all_module, db_conn, tmp_path):
    """If the agent didn't touch the wiki at all (no pages cite any batch
    email), NOTHING gets flipped — matches the 'agent gave up early'
    failure mode the user flagged."""
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    db_conn.commit()

    # No wiki pages cite either email.
    (tmp_path / "topics").mkdir()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}]
    marked, not_cited, _missing = mod._mark_batch_compiled(batch, tmp_path)
    assert marked == []
    assert not_cited == 2
    assert _state(db_conn, "m1") == "pending"
    assert _state(db_conn, "m2") == "pending"


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
