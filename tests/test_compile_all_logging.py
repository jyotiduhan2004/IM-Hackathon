"""Tests for the coordinator-owned audit log helper in compile_all.py.

Covers the bug fix where the coordinator now writes one structured row to
`wiki/log.md` after every batch (success or failure), rather than trusting
the LLM to call `append_to_log`. The agent silently dropped log rows
roughly half the time, leaving holes in the audit trail.
"""

from __future__ import annotations

import importlib.util
import sys
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


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_first_call_writes_header_and_row(compile_all_module, tmp_path):
    mod = compile_all_module
    wiki_dir = tmp_path / "wiki"

    batch = [{"path": "raw/a.md", "thread_id": "t1"}]
    mod._append_batch_log(1, batch, "compiled", str(wiki_dir))

    log_path = wiki_dir / "log.md"
    assert log_path.exists()
    lines = _read_lines(log_path)
    # Header (4 lines: title, blank, columns, separator) + 1 data row = 5
    assert lines[0] == "# Compilation Log"
    assert lines[1] == ""
    assert lines[2] == "| Timestamp | Batch | N Emails | Thread ID | Outcome | Notes |"
    assert lines[3] == "|---|---|---|---|---|---|"
    assert len(lines) == 5
    assert lines[4].startswith("| ")
    assert "| 1 | 1 | t1 | compiled |" in lines[4]


def test_three_calls_produce_three_rows_in_order(compile_all_module, tmp_path):
    mod = compile_all_module
    wiki_dir = tmp_path / "wiki"

    batches = [
        (1, [{"path": "raw/a.md", "thread_id": "t1"}], "compiled", ""),
        (2, [{"path": "raw/b.md", "thread_id": "t2"}, {"path": "raw/c.md", "thread_id": "t2"}],
         "failed", "recursion limit hit"),
        (3, [{"path": "raw/d.md", "thread_id": "t3"}], "partial", "1 of 2 done"),
    ]
    for idx, batch, outcome, notes in batches:
        mod._append_batch_log(idx, batch, outcome, str(wiki_dir), notes=notes)

    lines = _read_lines(wiki_dir / "log.md")
    # 4 header lines + 3 data rows = 7
    assert len(lines) == 7

    row_compiled, row_failed, row_partial = lines[4], lines[5], lines[6]
    assert "| 1 | 1 | t1 | compiled |" in row_compiled
    assert "| 2 | 2 | t2 | failed | recursion limit hit |" in row_failed
    assert "| 3 | 1 | t3 | partial | 1 of 2 done |" in row_partial


def test_header_written_only_on_first_call(compile_all_module, tmp_path):
    """Subsequent calls must append rows without re-writing the header."""
    mod = compile_all_module
    wiki_dir = tmp_path / "wiki"

    for idx in range(1, 4):
        mod._append_batch_log(
            idx, [{"path": f"raw/{idx}.md", "thread_id": f"t{idx}"}],
            "compiled", str(wiki_dir),
        )

    lines = _read_lines(wiki_dir / "log.md")
    # Exactly one header occurrence
    assert lines.count("# Compilation Log") == 1
    assert lines.count("|---|---|---|---|---|---|") == 1
    # 4 header lines + 3 data rows
    assert len(lines) == 7


def test_pipes_in_notes_are_escaped(compile_all_module, tmp_path):
    """Pipe characters in notes would break markdown table parsing."""
    mod = compile_all_module
    wiki_dir = tmp_path / "wiki"

    mod._append_batch_log(
        1, [{"path": "raw/a.md", "thread_id": "t1"}], "failed",
        str(wiki_dir), notes="error: a|b|c crashed",
    )

    row = _read_lines(wiki_dir / "log.md")[4]
    # Raw pipes inside the notes column must be escaped so the row still parses
    # as a 6-column table.
    assert "a\\|b\\|c" in row


def test_newlines_in_notes_collapsed(compile_all_module, tmp_path):
    """Multi-line errors must collapse to a single row."""
    mod = compile_all_module
    wiki_dir = tmp_path / "wiki"

    mod._append_batch_log(
        1, [{"path": "raw/a.md", "thread_id": "t1"}], "failed",
        str(wiki_dir), notes="line1\nline2\nline3",
    )

    lines = _read_lines(wiki_dir / "log.md")
    # 4 header lines + 1 data row, no extra rows from the embedded newlines
    assert len(lines) == 5


def test_empty_batch_writes_row_with_empty_thread(compile_all_module, tmp_path):
    """Defensive: an empty batch should still produce a row, not crash."""
    mod = compile_all_module
    wiki_dir = tmp_path / "wiki"

    mod._append_batch_log(1, [], "compiled", str(wiki_dir))

    row = _read_lines(wiki_dir / "log.md")[4]
    assert "| 1 | 0 |  | compiled |" in row


def test_creates_wiki_dir_if_missing(compile_all_module, tmp_path):
    """First call must create the wiki dir if it doesn't exist yet."""
    mod = compile_all_module
    wiki_dir = tmp_path / "fresh-wiki"
    assert not wiki_dir.exists()

    mod._append_batch_log(
        1, [{"path": "raw/a.md", "thread_id": "t1"}], "compiled", str(wiki_dir),
    )

    assert wiki_dir.exists()
    assert (wiki_dir / "log.md").exists()
