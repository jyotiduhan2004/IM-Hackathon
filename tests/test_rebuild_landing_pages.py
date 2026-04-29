"""Tests for `rebuild_landing_pages` + `_page_summary`.

Regression coverage for the post-Phase-A breakage: `_page_summary` used
to call a page a "stub" when its `sources:` list was empty. Phase A
(PRs #130/#131) moved provenance to `source_threads:`, so every
freshly-compiled system page looked like a stub and the systems
landing listed "No pages compiled yet" despite 90+ real pages.
"""

from __future__ import annotations

from pathlib import Path

from src.wiki.landing import rebuild_landing_pages
from src.wiki.pages import _page_summary


def _write_page(path: Path, frontmatter: str, body: str = "Body content.\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")


class TestPageSummaryProvenance:
    def test_sources_only_is_not_stub(self, tmp_path: Path) -> None:
        page = tmp_path / "topics" / "legacy.md"
        _write_page(
            page,
            "title: Legacy page\nsources:\n- raw/2026-01-01_foo_abc.md\nstatus: current\n"
            "last_compiled: '2026-04-15T12:00:00+00:00'",
        )
        summary = _page_summary(page)
        assert summary is not None
        assert summary["is_stub"] is False
        assert summary["sources_count"] == 1

    def test_source_threads_only_is_not_stub(self, tmp_path: Path) -> None:
        # Post-Phase-A shape: no `sources:`, only `source_threads:`.
        page = tmp_path / "systems" / "seller-im.md"
        _write_page(
            page,
            "title: Seller.IM\npage_type: system\nstatus: active\nsources: []\n"
            "source_threads:\n- 19b6ef4139a1b8b6\nlast_compiled: '2026-04-16T10:50:12+00:00'",
        )
        summary = _page_summary(page)
        assert summary is not None
        assert summary["is_stub"] is False
        assert summary["sources_count"] == 1

    def test_both_fields_empty_is_stub(self, tmp_path: Path) -> None:
        page = tmp_path / "entities" / "orphan.md"
        _write_page(
            page,
            "title: Orphan stub\npage_type: entity\nstatus: active\n"
            "sources: []\nsource_threads: []\nlast_compiled: ''",
        )
        summary = _page_summary(page)
        assert summary is not None
        assert summary["is_stub"] is True

    def test_stub_marker_still_wins(self, tmp_path: Path) -> None:
        # Even with provenance, `last_compiled: stub` marks this as a stub.
        page = tmp_path / "entities" / "backfilled.md"
        _write_page(
            page,
            "title: Backfilled stub\npage_type: entity\nsources:\n- raw/x.md\nlast_compiled: stub",
        )
        summary = _page_summary(page)
        assert summary is not None
        assert summary["is_stub"] is True

    def test_missing_fields_graceful(self, tmp_path: Path) -> None:
        page = tmp_path / "topics" / "minimal.md"
        _write_page(page, "title: Minimal page")
        summary = _page_summary(page)
        assert summary is not None
        assert summary["is_stub"] is True
        assert summary["sources_count"] == 0

    def test_missing_status_defaults_to_active(self, tmp_path: Path) -> None:
        # v8 vocabulary shift: `active` is the new canonical default, not
        # the retired legacy `current`. Pages with no status in frontmatter
        # must fall back to `active` so landing listings don't stamp every
        # such page with a `*(current)*` "stale legacy" suffix.
        page = tmp_path / "topics" / "no-status.md"
        _write_page(
            page,
            "title: Page without status\nsource_threads:\n- abc123\n"
            "last_compiled: '2026-04-17T00:00:00+00:00'",
        )
        summary = _page_summary(page)
        assert summary is not None
        assert summary["status"] == "active"


class TestWriteSectionIndexStatusSuffix:
    """PR #159 regression: suffix rule must treat both `active` (new
    canonical) and `current` (legacy) as no-suffix states. Only deviant
    states (`superseded`, `archived`, `contested`) should get the suffix.
    """

    def _seed_and_render(self, tmp_path: Path, status: str) -> str:
        _write_page(
            tmp_path / "topics" / f"demo-{status}.md",
            f"title: Demo {status}\npage_type: topic\nstatus: {status}\n"
            "sources:\n- raw/2026-01-01_foo_abc.md\n"
            "last_compiled: '2026-04-10T08:00:00+00:00'",
            body="Demo body.\n",
        )
        rebuild_landing_pages(str(tmp_path))
        return (tmp_path / "topics" / "index.md").read_text(encoding="utf-8")

    def test_active_has_no_suffix(self, tmp_path: Path) -> None:
        index = self._seed_and_render(tmp_path, "active")
        assert "*(active)*" not in index
        assert "[[demo-active]] — Demo active" in index

    def test_current_legacy_has_no_suffix(self, tmp_path: Path) -> None:
        # Legacy pages still read correctly — no retroactive flagging.
        index = self._seed_and_render(tmp_path, "current")
        assert "*(current)*" not in index
        assert "[[demo-current]] — Demo current" in index

    def test_superseded_gets_suffix(self, tmp_path: Path) -> None:
        index = self._seed_and_render(tmp_path, "superseded")
        assert "[[demo-superseded]] — Demo superseded *(superseded)*" in index

    def test_archived_gets_suffix(self, tmp_path: Path) -> None:
        index = self._seed_and_render(tmp_path, "archived")
        assert "[[demo-archived]] — Demo archived *(archived)*" in index

    def test_contested_legacy_gets_suffix(self, tmp_path: Path) -> None:
        # Legacy `contested` still renders with suffix so humans notice.
        index = self._seed_and_render(tmp_path, "contested")
        assert "[[demo-contested]] — Demo contested *(contested)*" in index


class TestRebuildLandingPagesShowsSourceThreadsPages:
    def test_section_index_sorts_mixed_timezone_offsets_by_absolute_time(
        self, tmp_path: Path
    ) -> None:
        _write_page(
            tmp_path / "topics" / "older-ist.md",
            "title: Older IST\npage_type: topic\nstatus: active\n"
            "sources:\n- raw/2026-01-01_older_abc.md\n"
            "last_compiled: '2026-04-29T10:00:00+05:30'",
            body="Older absolute timestamp.\n",
        )
        _write_page(
            tmp_path / "topics" / "newer-utc.md",
            "title: Newer UTC\npage_type: topic\nstatus: active\n"
            "sources:\n- raw/2026-01-01_newer_def.md\n"
            "last_compiled: '2026-04-29T06:00:00+00:00'",
            body="Newer absolute timestamp.\n",
        )

        rebuild_landing_pages(str(tmp_path))

        index = (tmp_path / "topics" / "index.md").read_text(encoding="utf-8")
        assert index.index("[[newer-utc]]") < index.index("[[older-ist]]")

    def test_systems_page_with_source_threads_appears_in_index(self, tmp_path: Path) -> None:
        # Seed ONE system page using the post-Phase-A contract.
        _write_page(
            tmp_path / "systems" / "seller-im.md",
            "title: Seller.IM\npage_type: system\nstatus: active\nsources: []\n"
            "source_threads:\n- 19b6ef4139a1b8b6\n"
            "last_compiled: '2026-04-16T10:50:12+00:00'",
            body="Seller.IM is the seller-facing dashboard.\n",
        )
        # Also seed a legacy-shape topic so both code paths fire.
        _write_page(
            tmp_path / "topics" / "legacy.md",
            "title: Legacy topic\npage_type: topic\nstatus: current\n"
            "sources:\n- raw/2026-01-01_foo_abc.md\n"
            "last_compiled: '2026-04-10T08:00:00+00:00'",
            body="Legacy topic content.\n",
        )

        summary = rebuild_landing_pages(str(tmp_path))
        assert "systems=1" in summary
        assert "topics=1" in summary

        systems_index = (tmp_path / "systems" / "index.md").read_text(encoding="utf-8")
        assert "No pages compiled yet" not in systems_index
        assert "Seller.IM" in systems_index
        assert "[[seller-im]]" in systems_index

        topics_index = (tmp_path / "topics" / "index.md").read_text(encoding="utf-8")
        assert "Legacy topic" in topics_index
