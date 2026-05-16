"""Tests for get_thread_context response_format variants (U8 + v10-U6 + v11-U2).

Covers:
- `"detailed"` preserves the pre-U8 shape exactly — callers that opt
  into detailed must see a full per-message list and the legacy
  `cutoff_date` key.
- `"concise"` (the v10-U6 default) returns the navigable shape
  `{thread_id, message_count, first_subject, latest_date,
  applied_cutoff_date, note_on_cutoff, truncated, messages_summary}`.
  v11-U2 added `messages_summary` (per-message `raw_path` for one-hop
  navigation) and renamed `cutoff_date` → `applied_cutoff_date` to make
  the cutoff semantics unambiguous; `note_on_cutoff` is the human-
  readable companion.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from src.agent.run_state import _current_batch_cutoff_date
from src.agent.tools.raw_access import _cite_key_from_raw_path
from src.agent.tools.raw_access import _cutoff_to_date
from src.agent.tools.raw_access import get_thread_context


class _FakeCursor:
    """Minimal stand-in for a psycopg cursor returning a preset row list.

    `fetchone` dispatches on the last executed SQL — the MAX(date) query
    introduced by v10 followup P1-4 (#196) expects a single-row
    `{"max_date": ...}` shape, whereas the page query's primary result
    is fetched via `fetchall`.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.last_sql = sql
        self.last_params = params
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        max_date = None
        for r in self._rows:
            d = r.get("date")
            if d and (max_date is None or d > max_date):
                max_date = d
        return {"max_date": max_date}


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def __enter__(self) -> _FakeCursor:
        return self._cur

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        return self._cur.execute(*args, **kwargs)


def _seed_raw(tmp_path: Path, name: str, body: str) -> str:
    """Write a raw email with minimal frontmatter + body; return absolute path."""
    path = tmp_path / name
    path.write_text(
        f"---\nmessage_id: {name}\n---\n\n{body}",
        encoding="utf-8",
    )
    return str(path)


class TestCutoffToDate:
    """Pure helper behind the corrected `note_on_cutoff` prose."""

    def test_iso_with_time(self) -> None:
        assert _cutoff_to_date("2026-01-14T08:30:12+00:00") == "2026-01-14"

    def test_iso_with_space(self) -> None:
        assert _cutoff_to_date("2026-01-14 08:30:12") == "2026-01-14"

    def test_date_only(self) -> None:
        assert _cutoff_to_date("2026-01-14") == "2026-01-14"

    def test_none_returns_none(self) -> None:
        assert _cutoff_to_date(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _cutoff_to_date("") is None


class TestCiteKeyFromRawPath:
    """Pure helper that precomputes the footnote target the agent
    would otherwise derive via `raw_path.stem.rsplit("_", 1)[-1]`."""

    def test_typical_raw_path(self) -> None:
        assert _cite_key_from_raw_path("raw/2026-01-08_subject_cda09a3d.md") == "cda09a3d"

    def test_absolute_path(self) -> None:
        assert _cite_key_from_raw_path("/repo/raw/2026-01-08_subject_4f78488c.md") == "4f78488c"

    def test_empty_returns_empty(self) -> None:
        assert _cite_key_from_raw_path("") == ""

    def test_no_underscore_returns_empty(self) -> None:
        assert _cite_key_from_raw_path("raw/m1.md") == ""


@pytest.fixture(autouse=True)
def _reset_cutoff() -> Any:
    """Each test runs with no chronological cutoff unless it sets one."""
    token = _current_batch_cutoff_date.set(None)
    try:
        yield
    finally:
        _current_batch_cutoff_date.reset(token)


def test_get_thread_context_detailed_unchanged(tmp_path: Path) -> None:
    """Explicit `detailed` mode must return the full per-message shape."""
    raw1 = _seed_raw(tmp_path, "m1.md", "First message body with plenty of context.")
    raw2 = _seed_raw(tmp_path, "m2.md", "Second message body.")
    rows = [
        {
            "message_id": "msg-001",
            "raw_path": raw1,
            "subject": "Sample thread",
            "from_address": "alice@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "compiled",
        },
        {
            "message_id": "msg-002",
            "raw_path": raw2,
            "subject": "Re: Sample thread",
            "from_address": "bob@indiamart.com",
            "date": datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke(
            {"thread_id": "thread-abc", "response_format": "detailed"}
        )

    assert set(result.keys()) == {"thread_id", "messages", "truncated", "cutoff_date"}
    assert result["thread_id"] == "thread-abc"
    assert result["truncated"] is False
    assert result["cutoff_date"] is None
    assert len(result["messages"]) == 2

    m1 = result["messages"][0]
    assert set(m1.keys()) == {
        "message_id",
        "subject",
        "from_addr",
        "date",
        "raw_path",
        "first_200_chars",
        "compile_state",
    }
    assert m1["message_id"] == "msg-001"
    assert m1["subject"] == "Sample thread"
    assert m1["from_addr"] == "alice@indiamart.com"
    assert m1["date"] == "2026-04-10T12:00:00+00:00"
    assert m1["compile_state"] == "compiled"
    assert m1["first_200_chars"].startswith("First message body")


def test_get_thread_context_default_is_concise_aggregate(tmp_path: Path) -> None:
    """Default (no arg) returns the navigable concise shape (v11-U2)."""
    raw1 = _seed_raw(tmp_path, "m1.md", "Launch plan: ship MVP Friday.")
    raw2 = _seed_raw(tmp_path, "m2.md", "Second message body — later in thread.")
    rows = [
        {
            "message_id": "msg-001",
            "raw_path": raw1,
            "subject": "Launch",
            "from_address": "alice@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "compiled",
        },
        {
            "message_id": "msg-002",
            "raw_path": raw2,
            "subject": "Re: Launch",
            "from_address": "bob@indiamart.com",
            "date": datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke({"thread_id": "thread-abc"})

    assert set(result.keys()) == {
        "thread_id",
        "message_count",
        "first_subject",
        "latest_date",
        "applied_cutoff_date",
        "note_on_cutoff",
        "truncated",
        "messages_summary",
    }
    assert result["thread_id"] == "thread-abc"
    assert result["message_count"] == 2
    assert result["first_subject"] == "Launch"
    assert result["latest_date"] == "2026-04-11T09:00:00+00:00"
    assert result["applied_cutoff_date"] is None
    assert result["note_on_cutoff"] is None
    assert result["truncated"] is False
    # v11-U2: per-message stub with `raw_path` for one-hop navigation.
    assert len(result["messages_summary"]) == 2
    first = result["messages_summary"][0]
    assert set(first.keys()) == {
        "message_id",
        "raw_path",
        "cite_key",
        "date",
        "from_addr",
    }
    assert first["message_id"] == "msg-001"
    assert first["raw_path"] == raw1
    assert first["from_addr"] == "alice@indiamart.com"
    assert first["date"] == "2026-04-10T12:00:00+00:00"
    # `cite_key` is the 8-char hash suffix of the raw filename. The
    # test fixtures use synthetic `m1.md` / `m2.md` paths with no `_`
    # separator, so the helper returns `""` — covered explicitly by
    # the dedicated cite_key tests below.
    assert first["cite_key"] == ""
    # Concise must NOT include per-message bodies / preview / compile_state.
    assert "messages" not in result
    assert "summary_lines" not in result
    assert "first_200_chars" not in first
    assert "compile_state" not in first
    # Renamed away from the bare `cutoff_date` to make semantics explicit.
    assert "cutoff_date" not in result


def test_get_thread_context_concise_empty_thread() -> None:
    """Concise mode on an unknown thread returns message_count=0 + empty subject."""
    cur = _FakeCursor([])

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke(
            {"thread_id": "thread-nope", "response_format": "concise"}
        )

    assert result["thread_id"] == "thread-nope"
    assert result["message_count"] == 0
    assert result["first_subject"] == ""
    assert result["latest_date"] is None
    assert result["truncated"] is False
    assert result["messages_summary"] == []
    assert result["applied_cutoff_date"] is None
    assert result["note_on_cutoff"] is None


def test_get_thread_context_concise_respects_cutoff() -> None:
    """Concise mode honours _current_batch_cutoff_date and surfaces it."""
    cur = _FakeCursor([])
    token = _current_batch_cutoff_date.set("2026-01-09T00:00:00+00:00")
    try:
        with patch("src.db.connect", return_value=_FakeConn(cur)):
            result = get_thread_context.invoke(
                {"thread_id": "thread-abc", "response_format": "concise"}
            )
    finally:
        _current_batch_cutoff_date.reset(token)

    assert result["applied_cutoff_date"] == "2026-01-09T00:00:00+00:00"
    # `note_on_cutoff` uses the date-only form of the cutoff because the
    # SQL is `date::date <= cutoff::date` — messages dated on `cutoff`
    # at any time ARE visible. The prior prose implied otherwise.
    assert result["note_on_cutoff"] == (
        "Messages dated after 2026-01-09 are hidden per chronological scope; "
        "messages on 2026-01-09 at any time remain visible."
    )
    assert "date::date <= %s::date" in (cur.last_sql or "")


class _TruncatedFakeCursor:
    """Fake cursor that returns the windowed slice on fetchall + the real
    thread-wide MAX(date) on fetchone. Exercises the split-query shape
    introduced by v10 followup P1-4 (#196): a truncated thread's
    ``latest_date`` must reflect the newest message in the DB, not the
    newest message that fit in the ``limit``-sized window.
    """

    def __init__(
        self,
        windowed_rows: list[dict[str, Any]],
        thread_max_date: datetime,
    ) -> None:
        self._rows = windowed_rows
        self._max_date = thread_max_date

    def execute(self, sql: str, params: tuple[Any, ...]) -> _TruncatedFakeCursor:
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any]:
        return {"max_date": self._max_date}


def test_concise_latest_date_reflects_thread_max_not_window_tail(tmp_path: Path) -> None:
    """P1-4 (#196): truncated concise response reports thread's real latest_date.

    The tool issues `LIMIT limit + 1` under the hood so a returned row
    count > `limit` flags truncation. To simulate that we return 3 rows
    for `limit=2`; the tool slices to 2 and flips `truncated=True`,
    which is the branch that hits the MAX(date) query.
    """
    raw1 = _seed_raw(tmp_path, "m1.md", "Body 1.")
    raw2 = _seed_raw(tmp_path, "m2.md", "Body 2.")
    raw3 = _seed_raw(tmp_path, "m3.md", "Body 3.")
    windowed_rows = [
        {
            "message_id": "msg-001",
            "raw_path": raw1,
            "subject": "Launch",
            "from_address": "alice@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "compiled",
        },
        {
            "message_id": "msg-002",
            "raw_path": raw2,
            "subject": "Re: Launch",
            "from_address": "bob@indiamart.com",
            "date": datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
        {
            "message_id": "msg-003",
            "raw_path": raw3,
            "subject": "Re: Launch",
            "from_address": "carol@indiamart.com",
            "date": datetime(2026, 4, 12, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]
    # MAX(date) for the full thread is much newer than anything in the
    # returned window — must still surface through `latest_date`.
    cur = _TruncatedFakeCursor(
        windowed_rows,
        thread_max_date=datetime(2026, 4, 20, 15, 30, 0, tzinfo=UTC),
    )

    with patch("src.db.connect", return_value=_FakeConn(cur)):  # type: ignore[arg-type]
        result = get_thread_context.invoke(
            {"thread_id": "long-thread", "limit": 2, "response_format": "concise"}
        )

    assert result["truncated"] is True
    # `latest_date` reflects the thread's true MAX(date), not the window tail.
    assert result["latest_date"] == "2026-04-20T15:30:00+00:00"
    assert result["message_count"] == 2


def test_concise_caps_messages_summary_at_20(tmp_path: Path) -> None:
    """P2 (#202 followup): `messages_summary` is capped at 20 entries.

    A 50-message thread used to emit 50 per-row stubs in concise mode,
    defeating the token-budget story. `message_count` + `truncated`
    still reflect the full DB window so the agent knows there's more.
    """
    rows = [
        {
            "message_id": f"msg-{i:03d}",
            "raw_path": _seed_raw(tmp_path, f"m{i}.md", "body"),
            "subject": "Topic",
            "from_address": f"user{i}@indiamart.com",
            "date": datetime(2026, 4, i % 28 + 1, 12, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        }
        for i in range(1, 51)
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        result = get_thread_context.invoke(
            {"thread_id": "long", "response_format": "concise", "limit": 50}
        )

    # `message_count` is accurate (DB returned 50 rows, limit matched).
    assert result["message_count"] == 50
    # But `messages_summary` tops out at 20 stubs.
    assert len(result["messages_summary"]) == 20


def test_concise_date_is_none_when_missing(tmp_path: Path) -> None:
    """P2 (#202 followup): unify missing-date to `None` (not `""`)."""
    rows = [
        {
            "message_id": "msg-dateless",
            "raw_path": _seed_raw(tmp_path, "dateless.md", "body"),
            "subject": "No date row",
            "from_address": "nobody@indiamart.com",
            "date": None,
            "compile_state": "pending",
        }
    ]
    cur = _FakeCursor(rows)

    with patch("src.db.connect", return_value=_FakeConn(cur)):
        concise = get_thread_context.invoke({"thread_id": "no-date", "response_format": "concise"})
        detailed = get_thread_context.invoke(
            {"thread_id": "no-date", "response_format": "detailed"}
        )

    assert concise["messages_summary"][0]["date"] is None
    assert concise["latest_date"] is None
    assert detailed["messages"][0]["date"] is None


def test_detailed_payload_larger_than_concise(tmp_path: Path) -> None:
    """P2 (#202 followup): restore size-ratio canary.

    Detailed mode adds subject, first_200_chars body preview, and
    compile_state per message — for any non-trivial thread it should
    weigh >1.3x concise. Missing this ratio means one of the two
    modes grew feature-parity with the other, collapsing the point
    of having two response formats.
    """
    rows = [
        {
            "message_id": f"msg-{i:03d}",
            "raw_path": _seed_raw(tmp_path, f"m{i}.md", "A reasonably full body." * 5),
            "subject": f"Subject {i} — has meaningful length",
            "from_address": f"user{i}@indiamart.com",
            "date": datetime(2026, 4, i + 1, 12, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        }
        for i in range(5)
    ]
    cur = _FakeCursor(rows)
    with patch("src.db.connect", return_value=_FakeConn(cur)):
        concise = get_thread_context.invoke({"thread_id": "sized", "response_format": "concise"})
        detailed = get_thread_context.invoke({"thread_id": "sized", "response_format": "detailed"})

    concise_size = len(json.dumps(concise))
    detailed_size = len(json.dumps(detailed))
    assert detailed_size >= 1.3 * concise_size, (
        f"detailed={detailed_size} should be >= 1.3x concise={concise_size} "
        "— if the two modes have collapsed in size, concise's savings are gone."
    )
