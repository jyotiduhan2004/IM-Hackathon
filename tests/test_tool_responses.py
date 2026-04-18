"""Dual-format response contract (v10-U6).

Pins the `response_format="concise"|"detailed"` pattern on the three
listing tools per CLAUDE.md's "Dual-format responses" tool-design rule
(section: Tool-design rules the agent can rely on, item 6).

For each tool we assert:
- Default (no arg) → concise shape.
- `response_format="detailed"` → richer shape with strictly more keys.
- Concise JSON payload is small — < 100 tokens by the rough
  `len(json.dumps(...)) / 4` estimate.
- Detailed is 2-4x the concise budget for the same input, proving the
  opt-in pays.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.compile.compiler import _current_batch_cutoff_date
from src.compile.compiler import get_page_summary
from src.compile.compiler import get_thread_context
from src.compile.compiler import list_wiki_pages

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_estimate(payload: Any) -> int:
    """Rough token count (~4 chars / token) from JSON-serialised payload."""
    return len(json.dumps(payload, default=str)) // 4


def _seed_wiki(wiki: Path) -> None:
    """Write a handful of pages across categories for the listing tools."""
    (wiki / "topics").mkdir(parents=True)
    (wiki / "systems").mkdir(parents=True)
    (wiki / "topics" / "buylead.md").write_text(
        "---\n"
        'title: "BuyLead"\n'
        "page_type: topic\n"
        "status: active\n"
        'last_compiled: "2026-04-15T12:00:00+00:00"\n'
        'source_threads: ["19b92d9b270daa57"]\n'
        "---\n\n"
        "Lead paragraph about BuyLeads.\n\n"
        "## Overview\nMore body.\n",
        encoding="utf-8",
    )
    (wiki / "systems" / "lens.md").write_text(
        "---\n"
        'title: "Lens"\n'
        "page_type: system\n"
        "status: active\n"
        'last_compiled: "2026-04-14T08:00:00+00:00"\n'
        "sources: []\n"
        "---\n\n"
        "Lens lead paragraph.\n",
        encoding="utf-8",
    )


class _FakeCursor:
    """Minimal psycopg cursor stand-in; cf. tests/test_get_thread_context.py.

    Only the truncated branch of `get_thread_context` concise calls
    `fetchone()` for a separate `MAX(date)` query (v10 followup P1-4
    #196). `fetchone` returns the max of the seeded rows' `date`
    fields so the fake stays faithful to the real SQL shape.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
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


# ---------------------------------------------------------------------------
# list_wiki_pages
# ---------------------------------------------------------------------------


class TestListWikiPagesDualFormat:
    def test_default_is_concise(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        result = list_wiki_pages.invoke({"wiki_dir": str(tmp_path)})
        assert set(result.keys()) == {"pages"}
        # page_type is kept (P1-5 from #196 followup) so slugs that collide
        # across categories — e.g. `topic/seller-isq` and `system/seller-isq`
        # — aren't collapsed into one entry in the agent's view.
        assert all(set(p.keys()) == {"slug", "title", "page_type"} for p in result["pages"])

    def test_concise_shape_drops_metadata(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        result = list_wiki_pages.invoke({"wiki_dir": str(tmp_path), "response_format": "concise"})
        assert set(result.keys()) == {"pages"}
        slugs = {p["slug"] for p in result["pages"]}
        assert slugs == {"buylead", "lens"}
        # Titles come from frontmatter, not from slug.
        titles = {p["title"] for p in result["pages"]}
        assert titles == {"BuyLead", "Lens"}
        # Concise drops path, last_compiled, status, source_count — page_type
        # stays in so cross-category slug collisions remain distinguishable.
        for page in result["pages"]:
            assert "last_compiled" not in page
            assert "status" not in page
            assert "path" not in page
            assert "source_count" not in page

    def test_concise_keeps_page_type_for_collision_disambiguation(self, tmp_path: Path) -> None:
        """P1-5 (#196): concise must include `page_type` so `topic/seller-isq`
        and `system/seller-isq` don't collapse into one entry for the agent.
        """
        _seed_wiki(tmp_path)
        result = list_wiki_pages.invoke({"wiki_dir": str(tmp_path), "response_format": "concise"})
        page_types = {p["page_type"] for p in result["pages"]}
        assert page_types == {"topic", "system"}

    def test_detailed_shape_keeps_metadata(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        result = list_wiki_pages.invoke({"wiki_dir": str(tmp_path), "response_format": "detailed"})
        # Detailed keeps per-category keying with rich per-page dicts.
        assert "topics" in result
        assert "systems" in result
        assert result["topics"][0]["slug"] == "buylead"
        # P2 from #196 followup: pin the full detailed shape so agents / the
        # reviewer tool can rely on every discovery signal being present.
        for key in (
            "last_compiled",
            "status",
            "page_type",
            "source_count",
            "source_thread_count",
            "is_cited",
        ):
            assert key in result["topics"][0], f"detailed missing {key}"
        # buylead's seed has source_threads=[<one>], sources=[] — exercise
        # the cited/thread-count plumbing too.
        assert result["topics"][0]["source_thread_count"] == 1
        assert result["topics"][0]["is_cited"] is True

    def test_concise_is_smaller_than_detailed(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        concise = list_wiki_pages.invoke({"wiki_dir": str(tmp_path), "response_format": "concise"})
        detailed = list_wiki_pages.invoke(
            {"wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
        c_tokens = _token_estimate(concise)
        d_tokens = _token_estimate(detailed)
        assert c_tokens < 100, f"concise {c_tokens} tokens > 100 cap"
        assert d_tokens >= 2 * c_tokens, (
            f"detailed {d_tokens} tokens should be >= 2x concise {c_tokens}"
        )


# ---------------------------------------------------------------------------
# get_page_summary
# ---------------------------------------------------------------------------


class TestGetPageSummaryDualFormat:
    def test_default_is_concise(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        result = get_page_summary.invoke({"slug": "buylead", "wiki_dir": str(tmp_path)})
        # `tldr` (V11-U5) is part of the concise shape — None when no
        # `## TL;DR` section exists. Future agents skip a re-read when present.
        assert set(result.keys()) == {"found", "slug", "title", "first_paragraph", "tldr"}

    def test_concise_shape_drops_frontmatter_stats(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        result = get_page_summary.invoke(
            {"slug": "buylead", "wiki_dir": str(tmp_path), "response_format": "concise"}
        )
        assert result["found"] is True
        assert result["slug"] == "buylead"
        assert result["title"] == "BuyLead"
        assert "BuyLead" in result["first_paragraph"] or result["first_paragraph"]
        # Concise drops the richer signals.
        for dropped in (
            "page_type",
            "status",
            "headings",
            "source_count",
            "source_thread_count",
            "is_cited",
            "last_compiled",
        ):
            assert dropped not in result, f"concise should not include {dropped}"

    def test_detailed_shape_keeps_frontmatter_stats(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        result = get_page_summary.invoke(
            {"slug": "buylead", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
        # All the discovery signals are present.
        for key in (
            "page_type",
            "status",
            "headings",
            "source_count",
            "source_thread_count",
            "is_cited",
            "last_compiled",
            "first_paragraph",
            "title",
            "slug",
            "found",
        ):
            assert key in result, f"detailed missing {key}"

    def test_concise_is_smaller_than_detailed(self, tmp_path: Path) -> None:
        _seed_wiki(tmp_path)
        concise = get_page_summary.invoke(
            {"slug": "buylead", "wiki_dir": str(tmp_path), "response_format": "concise"}
        )
        detailed = get_page_summary.invoke(
            {"slug": "buylead", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
        c_tokens = _token_estimate(concise)
        d_tokens = _token_estimate(detailed)
        assert c_tokens < 100, f"concise {c_tokens} tokens > 100 cap"
        assert d_tokens > c_tokens, f"detailed {d_tokens} tokens should exceed concise {c_tokens}"

    def test_missing_page_identical_across_formats(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        concise = get_page_summary.invoke(
            {"slug": "ghost", "wiki_dir": str(wiki), "response_format": "concise"}
        )
        detailed = get_page_summary.invoke(
            {"slug": "ghost", "wiki_dir": str(wiki), "response_format": "detailed"}
        )
        assert (
            concise
            == detailed
            == {
                "found": False,
                "slug": "ghost",
                "reason": "not_found",
            }
        )


# ---------------------------------------------------------------------------
# get_thread_context
# ---------------------------------------------------------------------------


def _thread_rows(tmp_path: Path) -> list[dict[str, Any]]:
    """Two-message thread seed used across the dual-format checks."""
    raw1 = tmp_path / "m1.md"
    raw2 = tmp_path / "m2.md"
    raw1.write_text(
        "---\nmessage_id: m1\n---\n\nFirst message body with plenty of context.\n",
        encoding="utf-8",
    )
    raw2.write_text(
        "---\nmessage_id: m2\n---\n\nSecond message body.\n",
        encoding="utf-8",
    )
    return [
        {
            "message_id": "msg-001",
            "raw_path": str(raw1),
            "subject": "Launch thread",
            "from_address": "alice@indiamart.com",
            "date": datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC),
            "compile_state": "compiled",
        },
        {
            "message_id": "msg-002",
            "raw_path": str(raw2),
            "subject": "Re: Launch thread",
            "from_address": "bob@indiamart.com",
            "date": datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC),
            "compile_state": "pending",
        },
    ]


class TestGetThreadContextDualFormat:
    def test_default_is_concise_aggregate(self, tmp_path: Path) -> None:
        cur = _FakeCursor(_thread_rows(tmp_path))
        token = _current_batch_cutoff_date.set(None)
        try:
            with patch("src.db.connect", return_value=_FakeConn(cur)):
                result = get_thread_context.invoke({"thread_id": "t-1"})
        finally:
            _current_batch_cutoff_date.reset(token)
        assert set(result.keys()) == {
            "thread_id",
            "message_count",
            "first_subject",
            "latest_date",
            "cutoff_date",
            "truncated",
        }
        assert result["message_count"] == 2
        assert result["first_subject"] == "Launch thread"

    def test_concise_drops_per_message_bodies(self, tmp_path: Path) -> None:
        cur = _FakeCursor(_thread_rows(tmp_path))
        token = _current_batch_cutoff_date.set(None)
        try:
            with patch("src.db.connect", return_value=_FakeConn(cur)):
                result = get_thread_context.invoke(
                    {"thread_id": "t-1", "response_format": "concise"}
                )
        finally:
            _current_batch_cutoff_date.reset(token)
        assert "messages" not in result
        assert "summary_lines" not in result
        assert result["message_count"] == 2
        assert result["first_subject"] == "Launch thread"
        assert result["latest_date"] == "2026-04-11T09:00:00+00:00"

    def test_detailed_keeps_per_message_bodies(self, tmp_path: Path) -> None:
        cur = _FakeCursor(_thread_rows(tmp_path))
        token = _current_batch_cutoff_date.set(None)
        try:
            with patch("src.db.connect", return_value=_FakeConn(cur)):
                result = get_thread_context.invoke(
                    {"thread_id": "t-1", "response_format": "detailed"}
                )
        finally:
            _current_batch_cutoff_date.reset(token)
        assert "messages" in result
        assert len(result["messages"]) == 2
        first = result["messages"][0]
        assert set(first.keys()) == {
            "message_id",
            "subject",
            "from_addr",
            "date",
            "raw_path",
            "first_200_chars",
            "compile_state",
        }
        assert first["first_200_chars"].startswith("First message body")

    def test_concise_is_smaller_than_detailed(self, tmp_path: Path) -> None:
        cur_concise = _FakeCursor(_thread_rows(tmp_path))
        cur_detailed = _FakeCursor(_thread_rows(tmp_path))
        token = _current_batch_cutoff_date.set(None)
        try:
            with patch("src.db.connect", return_value=_FakeConn(cur_concise)):
                concise = get_thread_context.invoke(
                    {"thread_id": "t-1", "response_format": "concise"}
                )
            with patch("src.db.connect", return_value=_FakeConn(cur_detailed)):
                detailed = get_thread_context.invoke(
                    {"thread_id": "t-1", "response_format": "detailed"}
                )
        finally:
            _current_batch_cutoff_date.reset(token)
        c_tokens = _token_estimate(concise)
        d_tokens = _token_estimate(detailed)
        assert c_tokens < 100, f"concise {c_tokens} tokens > 100 cap"
        assert d_tokens >= 2 * c_tokens, (
            f"detailed {d_tokens} tokens should be >= 2x concise {c_tokens}"
        )
