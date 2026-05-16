"""Tests for per-tool-call logging.

Covers:
  - `ToolCallLogHandler` captures on_tool_start / on_tool_end / on_tool_error.
  - `insert_many` issues one INSERT per record with the right column mapping.
  - `summarize` rolls up rows into the shape the coordinator expects.
  - `fallback_to_jsonl` writes one JSON line per record under `docs/audits/`.

Strategy: the callback + JSONL tests are pure in-memory / tmp_path tests. The
DB-side tests mock `src.db.tool_call_log.connect` with a small fake that
records every SQL statement + params it was given, so we don't depend on a
live Postgres here (`conftest.py`'s schema fixture is irrelevant for this
module).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

import pytest
from src.db import tool_call_log as repo
from src.observability.tool_call_log import ToolCallLogHandler

# ---------------------------------------------------------------------------
# ToolCallLogHandler — callback plumbing
# ---------------------------------------------------------------------------


def _start(
    handler: ToolCallLogHandler,
    run_id: UUID,
    tool_name: str = "read_file",
    inputs: dict[str, Any] | None = None,
) -> None:
    handler.on_tool_start(
        {"name": tool_name},
        "",
        run_id=run_id,
        inputs=inputs or {"path": "raw/foo.md"},
    )


def test_start_then_end_records_success() -> None:
    h = ToolCallLogHandler()
    rid = uuid4()
    _start(h, rid, tool_name="read_file", inputs={"path": "raw/foo.md"})
    h.on_tool_end("file contents here", run_id=rid)

    records = h.records()
    assert len(records) == 1
    rec = records[0]
    assert rec["tool_name"] == "read_file"
    assert rec["status"] == "ok"
    assert rec["output_preview"] == "file contents here"
    assert rec["output_bytes"] == len("file contents here")
    assert rec["inputs_json"] is not None and "raw/foo.md" in rec["inputs_json"]
    assert rec["latency_ms"] is not None and rec["latency_ms"] >= 0
    assert rec["error_message"] is None
    assert rec["finished_at"] is not None
    assert rec["finished_at"] >= rec["started_at"]


def test_start_then_error_records_error() -> None:
    h = ToolCallLogHandler()
    rid = uuid4()
    _start(h, rid, tool_name="edit_file")
    h.on_tool_error(ValueError("boom — disk full"), run_id=rid)

    records = h.records()
    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "error"
    assert rec["error_message"] == "boom — disk full"
    assert rec["output_preview"] is None
    assert rec["output_bytes"] is None
    assert rec["latency_ms"] is not None


def test_long_output_is_truncated_to_300_chars() -> None:
    h = ToolCallLogHandler()
    rid = uuid4()
    _start(h, rid)
    long_output = "x" * 5000
    h.on_tool_end(long_output, run_id=rid)

    rec = h.records()[0]
    assert rec["output_preview"] is not None
    assert len(rec["output_preview"]) == 300
    assert rec["output_bytes"] == 5000


def test_output_bytes_counts_utf8_bytes_not_chars() -> None:
    """Multi-byte output (emoji / non-ASCII) must count BYTES, not code points.

    `len("🚀")` == 1 but it encodes to 4 UTF-8 bytes. Previous impl used
    `len(str)` which silently undercounted multi-byte payloads — Claude
    review on PR #62 flagged this.
    """
    h = ToolCallLogHandler()
    rid = uuid4()
    _start(h, rid)
    emoji_output = "🚀" * 10  # 10 chars, 40 bytes
    h.on_tool_end(emoji_output, run_id=rid)

    rec = h.records()[0]
    assert rec["output_bytes"] == 40
    assert rec["output_bytes"] != len(emoji_output)


def test_flush_all_captures_abandoned_in_flight_records() -> None:
    """In-flight tool calls (agent crashed mid-call) surface as abandoned."""
    h = ToolCallLogHandler()
    completed_rid = uuid4()
    abandoned_rid = uuid4()

    # One completes normally.
    _start(h, completed_rid, tool_name="read_file")
    h.on_tool_end("ok", run_id=completed_rid)

    # One stays in-flight (never gets on_tool_end/error).
    _start(h, abandoned_rid, tool_name="write_file")

    out = h.flush_all()
    assert len(out) == 2
    statuses = sorted(r["status"] for r in out)
    assert statuses == ["abandoned", "ok"]

    abandoned = next(r for r in out if r["status"] == "abandoned")
    assert abandoned["tool_name"] == "write_file"
    assert abandoned["finished_at"] is not None
    assert abandoned["latency_ms"] is not None

    # flush_all also empties internal state.
    assert h.records() == []
    assert h.flush_all() == []


def test_long_error_is_truncated_to_500_chars() -> None:
    h = ToolCallLogHandler()
    rid = uuid4()
    _start(h, rid)
    h.on_tool_error(RuntimeError("e" * 1000), run_id=rid)

    rec = h.records()[0]
    assert rec["error_message"] is not None
    assert len(rec["error_message"]) == 500


def test_end_without_matching_start_is_ignored() -> None:
    h = ToolCallLogHandler()
    # No start for this run_id — on_tool_end should silently drop, not crash.
    h.on_tool_end("stray", run_id=uuid4())
    assert h.records() == []


def test_non_serializable_inputs_become_null_payload() -> None:
    # json.dumps falls back to default=str for non-JSON types. We only hit the
    # None branch when even that raises — simulate with a __str__ that blows up.
    class _RaisesOnStr:
        def __str__(self) -> str:
            raise TypeError("nope")

        def __repr__(self) -> str:
            raise TypeError("nope")

    h = ToolCallLogHandler()
    rid = uuid4()
    h.on_tool_start(
        {"name": "write_file"},
        "",
        run_id=rid,
        inputs={"obj": _RaisesOnStr()},
    )
    h.on_tool_end("ok", run_id=rid)

    rec = h.records()[0]
    assert rec["inputs_json"] is None
    assert rec["status"] == "ok"


def test_clear_empties_buffers() -> None:
    h = ToolCallLogHandler()
    rid = uuid4()
    _start(h, rid)
    h.on_tool_end("ok", run_id=rid)
    assert len(h.records()) == 1
    h.clear()
    assert h.records() == []


def test_concurrent_tool_calls_are_tracked_separately() -> None:
    h = ToolCallLogHandler()
    rid_a = uuid4()
    rid_b = uuid4()
    _start(h, rid_a, tool_name="read_file", inputs={"path": "a"})
    _start(h, rid_b, tool_name="write_file", inputs={"path": "b"})
    h.on_tool_end("A-out", run_id=rid_a)
    h.on_tool_error(OSError("bad-b"), run_id=rid_b)

    names = sorted(r["tool_name"] for r in h.records())
    assert names == ["read_file", "write_file"]
    by_name = {r["tool_name"]: r for r in h.records()}
    assert by_name["read_file"]["status"] == "ok"
    assert by_name["write_file"]["status"] == "error"


# ---------------------------------------------------------------------------
# insert_many — DB mock
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor stand-in that records executemany calls."""

    def __init__(self, parent: _FakeConn) -> None:
        self._parent = parent

    def executemany(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        self._parent.executemany_calls.append((sql, list(params_seq)))


class _FakeConn:
    """Minimal stand-in for a psycopg connection used by `insert_many`."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.committed = False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _FakeConn:
        self.executed.append((sql, params or ()))
        return self

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return []

    # context-manager shims so `with conn.transaction():` works
    def transaction(self) -> _FakeConn:
        return self

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *args: Any) -> None:
        self.committed = True


def _fake_connect_ctx(conn: _FakeConn) -> Any:
    """Return a context-manager factory yielding `conn` when entered."""

    class _CM:
        def __enter__(self) -> _FakeConn:
            return conn

        def __exit__(self, *args: Any) -> None:
            return None

    def _factory() -> _CM:
        return _CM()

    return _factory


def _sample_records() -> list[dict[str, Any]]:
    return [
        {
            "tool_name": "read_file",
            "inputs_json": '{"path": "raw/a.md"}',
            "output_preview": "abc",
            "output_bytes": 3,
            "latency_ms": 12,
            "status": "ok",
            "error_message": None,
            "started_at": 1_700_000_000.0,
            "finished_at": 1_700_000_000.012,
        },
        {
            "tool_name": "edit_file",
            "inputs_json": '{"path": "wiki/x.md"}',
            "output_preview": None,
            "output_bytes": None,
            "latency_ms": 50,
            "status": "error",
            "error_message": "boom",
            "started_at": 1_700_000_001.0,
            "finished_at": 1_700_000_001.05,
        },
    ]


def test_insert_many_returns_zero_on_empty_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(repo, "connect", _fake_connect_ctx(conn))
    assert repo.insert_many(uuid4(), []) == 0
    assert conn.executed == []


def test_insert_many_uses_executemany_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One executemany() call ships all records in a single round-trip."""
    conn = _FakeConn()
    monkeypatch.setattr(repo, "connect", _fake_connect_ctx(conn))
    records = _sample_records()
    run_uuid = uuid4()

    count = repo.insert_many(run_uuid, records)

    assert count == 2
    # One executemany call (not N loop iterations).
    assert len(conn.executemany_calls) == 1
    sql, params_seq = conn.executemany_calls[0]
    assert "INSERT INTO compile_tool_calls" in sql
    assert "::jsonb" in sql  # inputs_json cast
    assert len(params_seq) == 2
    # Parameter order follows the column list in the SQL.
    params0 = params_seq[0]
    assert params0[0] == run_uuid
    assert params0[1] == records[0]["tool_name"]
    assert params0[2] == records[0]["inputs_json"]
    assert params0[3] == records[0]["output_preview"]
    assert params0[6] == records[0]["status"]
    assert params0[8] == records[0]["started_at"]
    assert params0[9] == records[0]["finished_at"]


# ---------------------------------------------------------------------------
# summarize — DB mock
# ---------------------------------------------------------------------------


class _FakeSummaryConn(_FakeConn):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:  # type: ignore[override]
        return self._rows


def test_summarize_returns_expected_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"tool_name": "read_file", "calls": 47, "avg_ms": 30, "errors": 0},
        {"tool_name": "edit_file", "calls": 20, "avg_ms": 120, "errors": 2},
        {"tool_name": "write_file", "calls": 8, "avg_ms": 200, "errors": 0},
    ]
    conn = _FakeSummaryConn(rows)
    monkeypatch.setattr(repo, "connect", _fake_connect_ctx(conn))

    result = repo.summarize(uuid4())

    assert result["total_calls"] == 75
    assert result["total_errors"] == 2
    assert result["top_by_count"] == [
        ("read_file", 47),
        ("edit_file", 20),
        ("write_file", 8),
    ]
    # avg_ms descending
    assert result["top_by_latency"][0] == ("write_file", 200)
    assert result["top_by_latency"][1] == ("edit_file", 120)
    assert result["top_by_latency"][2] == ("read_file", 30)


def test_summarize_includes_zero_ms_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """avg(latency_ms)::int can round to 0 for very fast tools; they must still surface."""
    rows = [
        {"tool_name": "fast_tool", "calls": 10, "avg_ms": 0, "errors": 0},
        {"tool_name": "slow_tool", "calls": 5, "avg_ms": 200, "errors": 0},
    ]
    conn = _FakeSummaryConn(rows)
    monkeypatch.setattr(repo, "connect", _fake_connect_ctx(conn))

    result = repo.summarize(uuid4())
    assert ("fast_tool", 0) in result["top_by_latency"]


def test_summarize_handles_empty_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeSummaryConn([])
    monkeypatch.setattr(repo, "connect", _fake_connect_ctx(conn))

    result = repo.summarize(uuid4())
    assert result == {
        "top_by_count": [],
        "top_by_latency": [],
        "total_calls": 0,
        "total_errors": 0,
    }


# ---------------------------------------------------------------------------
# fallback_to_jsonl — filesystem
# ---------------------------------------------------------------------------


def test_fallback_to_jsonl_writes_one_line_per_record(
    tmp_path: Path,
) -> None:
    records = _sample_records()
    run_uuid = uuid4()

    out_path = repo.fallback_to_jsonl(run_uuid, records, base_dir=tmp_path)

    assert out_path == tmp_path / "docs" / "audits" / f"tool_calls-{run_uuid}.jsonl"
    assert out_path.exists()
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)

    import json as _json

    first = _json.loads(lines[0])
    assert first["tool_name"] == "read_file"
    assert first["status"] == "ok"


def test_fallback_to_jsonl_defaults_resolve_repo_root_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without base_dir, path resolves off repo root — not CWD.

    Patches `Path(__file__).resolve().parents[2]` indirection by giving a
    fake module file path whose `parents[2]` is tmp_path. Avoids writing
    into the real repo tree during tests.
    """
    import src.db.tool_call_log as module

    # Force the default branch to resolve under tmp_path instead of the repo.
    fake_module_file = tmp_path / "src" / "db" / "tool_call_log.py"
    fake_module_file.parent.mkdir(parents=True, exist_ok=True)
    fake_module_file.touch()
    monkeypatch.setattr(module, "__file__", str(fake_module_file))

    # CWD does not matter — the default should anchor on the patched __file__.
    monkeypatch.chdir(tmp_path / "src")

    records = _sample_records()[:1]
    run_uuid = uuid4()
    out_path = repo.fallback_to_jsonl(run_uuid, records)

    assert out_path == tmp_path / "docs" / "audits" / f"tool_calls-{run_uuid}.jsonl"
    assert out_path.exists()


def test_fallback_to_jsonl_appends_on_repeat(tmp_path: Path) -> None:
    records = _sample_records()
    run_uuid = uuid4()

    repo.fallback_to_jsonl(run_uuid, records[:1], base_dir=tmp_path)
    repo.fallback_to_jsonl(run_uuid, records[1:], base_dir=tmp_path)

    out_path = tmp_path / "docs" / "audits" / f"tool_calls-{run_uuid}.jsonl"
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)
