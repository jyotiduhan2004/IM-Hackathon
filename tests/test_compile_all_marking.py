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


def test_mark_batch_compiled_flips_every_row(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    _insert_message(db_conn, message_id="m2", raw_path="raw/b.md")
    _insert_message(db_conn, message_id="m3", raw_path="raw/c.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/b.md"}, {"path": "raw/c.md"}]
    marked, missing = mod._mark_batch_compiled(batch)
    assert marked == 3
    assert missing == 0
    assert _state(db_conn, "m1") == "compiled"
    assert _state(db_conn, "m2") == "compiled"
    assert _state(db_conn, "m3") == "compiled"


def test_mark_batch_compiled_reports_missing(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    db_conn.commit()

    batch = [{"path": "raw/a.md"}, {"path": "raw/not-in-db.md"}]
    marked, missing = mod._mark_batch_compiled(batch)
    assert marked == 1
    assert missing == 1
    assert _state(db_conn, "m1") == "compiled"


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
    row = db_conn.execute(
        "SELECT last_error FROM messages WHERE message_id = 'm1'"
    ).fetchone()
    assert row["last_error"] == "recursion limit hit"


def test_mark_batch_failed_truncates_long_error(compile_all_module, db_conn):
    mod = compile_all_module
    _insert_message(db_conn, message_id="m1", raw_path="raw/a.md")
    db_conn.commit()

    long_err = "x" * 10_000
    mod._mark_batch_failed([{"path": "raw/a.md"}], long_err)
    row = db_conn.execute(
        "SELECT last_error FROM messages WHERE message_id = 'm1'"
    ).fetchone()
    assert len(row["last_error"]) == 500
