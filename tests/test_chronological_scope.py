"""Tests for the chronological-scope middleware and get_thread_context cutoff.

Bug H — Cycle 4 Case 1 (SEO Rework recursion):
    Agent compiled 2026-01-09 email but read 12 raws spanning 2026-01-09 to
    2026-01-19 via get_thread_context. Those future replies named Amarinder
    (a person only in the later replies) → agent wikilinked him → reviewer
    blocked → `create_entities` rejected him because his email isn't in the
    current batch's raw. Recursion spiral.

Systemic fix:
    1. Coordinator sets `_current_batch_cutoff_date` to the batch's latest
       message date.
    2. `get_thread_context` auto-clips to date <= cutoff.
    3. `read_file` on a future-dated raw is rejected by
       `ChronologicalScopeMiddleware` as belt-and-suspenders.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from src.compile.compiler import _current_batch_cutoff_date
from src.compile.middleware.chronological_scope import _check_future_raw
from src.compile.middleware.chronological_scope import _raw_file_date


class TestRawFileDateParse:
    def test_standard_raw_filename(self) -> None:
        d = _raw_file_date("raw/2026-01-09_mplaunchim-seo-rework_e674.md")
        assert d == date(2026, 1, 9)

    def test_virtual_path(self) -> None:
        assert _raw_file_date("/raw/2026-04-15_foo_abc.md") == date(2026, 4, 15)

    def test_non_raw_path_returns_none(self) -> None:
        assert _raw_file_date("wiki/topics/foo.md") is None

    def test_malformed_date_returns_none(self) -> None:
        assert _raw_file_date("raw/2026-13-99_garbage_xyz.md") is None


class TestCheckFutureRaw:
    def test_no_cutoff_means_pass_through(self) -> None:
        token = _current_batch_cutoff_date.set(None)
        try:
            result = _check_future_raw("read_file", {"file_path": "raw/2099-01-01_future_xyz.md"})
        finally:
            _current_batch_cutoff_date.reset(token)
        assert result is None

    def test_past_or_same_date_passes(self) -> None:
        token = _current_batch_cutoff_date.set("2026-01-09T00:00:00+00:00")
        try:
            past = _check_future_raw("read_file", {"file_path": "raw/2026-01-05_earlier_abc.md"})
            same = _check_future_raw("read_file", {"file_path": "raw/2026-01-09_today_def.md"})
        finally:
            _current_batch_cutoff_date.reset(token)
        assert past is None
        assert same is None

    def test_future_date_is_rejected_with_guidance(self) -> None:
        token = _current_batch_cutoff_date.set("2026-01-09T12:00:00+00:00")
        try:
            rej = _check_future_raw(
                "read_file",
                {"file_path": "raw/2026-01-14_amarinder_reply_ef56.md"},
            )
        finally:
            _current_batch_cutoff_date.reset(token)
        assert rej is not None
        assert rej["ok"] is False
        assert rej["reason"] == "future_dated_raw"
        assert rej["file_date"] == "2026-01-14"
        assert rej["cutoff_date"] == "2026-01-09"
        assert "future replies" in rej["guidance"] or "out of scope" in rej["guidance"]

    def test_non_read_file_tool_ignored(self) -> None:
        token = _current_batch_cutoff_date.set("2026-01-09T00:00:00+00:00")
        try:
            assert (
                _check_future_raw("write_file", {"file_path": "raw/2026-12-31_future.md"}) is None
            )
        finally:
            _current_batch_cutoff_date.reset(token)

    def test_tz_boundary_filename_date_treated_as_authoritative(self) -> None:
        # Regression for Codex/Claude P1 on PR #139: the cutoff used to be
        # MAX(messages.date).isoformat() — a UTC timestamptz. A near-midnight
        # IST email with filename `raw/2026-01-10_...md` would have DB date
        # 2026-01-09T19:00:00+00:00. Old code truncated to UTC calendar
        # date `2026-01-09`, then the middleware rejected the batch's own
        # `2026-01-10` filename as "future".
        #
        # Fix: _compute_batch_cutoff_date now derives the cutoff from
        # filename prefixes, so batch raws always pass self-check.
        token = _current_batch_cutoff_date.set("2026-01-09T19:00:00+00:00")
        try:
            # Raw filename stamp is IST-local 2026-01-10 — same moment,
            # different calendar date. Old code false-rejected this.
            rej = _check_future_raw(
                "read_file",
                {"file_path": "raw/2026-01-10_same_moment_ist_xyz.md"},
            )
        finally:
            _current_batch_cutoff_date.reset(token)
        # Post-fix (using filename-derived cutoff), this is out-of-scope
        # and still rejected — we're verifying the middleware's comparison
        # hasn't drifted. The real fix is in _compute_batch_cutoff_date's
        # derivation path, exercised by the unit test below.
        assert rej is not None

    def test_compute_batch_cutoff_date_from_filenames(self) -> None:
        from src.compile.compiler import _compute_batch_cutoff_date

        # Filenames-only derivation, no DB round-trip, no TZ drift.
        assert (
            _compute_batch_cutoff_date(
                [
                    "raw/2026-01-09_foo_abc.md",
                    "raw/2026-01-10_bar_def.md",
                    "raw/2026-01-05_baz_ghi.md",
                ]
            )
            == "2026-01-10"
        )

    def test_compute_batch_cutoff_date_no_parseable_paths(self) -> None:
        from src.compile.compiler import _compute_batch_cutoff_date

        assert _compute_batch_cutoff_date([]) is None
        assert _compute_batch_cutoff_date(["wiki/topics/foo.md"]) is None
        assert _compute_batch_cutoff_date(["raw/no-date-prefix.md"]) is None

    def test_wiki_read_not_gated(self) -> None:
        token = _current_batch_cutoff_date.set("2026-01-09T00:00:00+00:00")
        try:
            assert _check_future_raw("read_file", {"file_path": "wiki/topics/seo.md"}) is None
        finally:
            _current_batch_cutoff_date.reset(token)


class TestGetThreadContextCutoff:
    def test_cutoff_applied_sql_branch(self) -> None:
        # Exercise the code path that applies the cutoff filter. We don't
        # need a real DB — a mocked connect + execute proves the query
        # shape changes when the ContextVar is set.
        from src.compile.compiler import get_thread_context

        class _FakeCursor:
            def __init__(self) -> None:
                self.last_sql: str | None = None
                self.last_params: tuple | None = None

            def execute(self, sql: str, params: tuple) -> _FakeCursor:
                self.last_sql = sql
                self.last_params = params
                return self

            def fetchall(self) -> list:
                return []

        cur = _FakeCursor()

        class _FakeConn:
            def __enter__(self) -> _FakeCursor:
                return cur

            def __exit__(self, *_: object) -> None:
                return None

            def execute(self, *args: object) -> _FakeCursor:
                return cur.execute(*args)  # type: ignore[arg-type]

        token = _current_batch_cutoff_date.set("2026-01-09T00:00:00+00:00")
        try:
            with patch("src.db.connect", return_value=_FakeConn()):
                result = get_thread_context.invoke({"thread_id": "abc123def4567890"})
        finally:
            _current_batch_cutoff_date.reset(token)

        assert result["cutoff_date"] == "2026-01-09T00:00:00+00:00"
        assert "date::date <= %s::date" in (cur.last_sql or "")

    def test_no_cutoff_no_filter(self) -> None:
        from src.compile.compiler import get_thread_context

        class _FakeCursor:
            last_sql: str | None = None

            def execute(self, sql: str, params: tuple) -> _FakeCursor:
                _FakeCursor.last_sql = sql
                return self

            def fetchall(self) -> list:
                return []

        cur = _FakeCursor()

        class _FakeConn:
            def __enter__(self) -> _FakeCursor:
                return cur

            def __exit__(self, *_: object) -> None:
                return None

            def execute(self, *args: object) -> _FakeCursor:
                return cur.execute(*args)  # type: ignore[arg-type]

        token = _current_batch_cutoff_date.set(None)
        try:
            with patch("src.db.connect", return_value=_FakeConn()):
                result = get_thread_context.invoke({"thread_id": "abc123def4567890"})
        finally:
            _current_batch_cutoff_date.reset(token)

        assert result["cutoff_date"] is None
        assert "date <= " not in (_FakeCursor.last_sql or "")
