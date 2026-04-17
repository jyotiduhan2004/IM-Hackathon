"""Discovery tools must be source_threads-aware + default status=`active`.

Codex audit P1 (2026-04-17): `get_page_summary` and `list_wiki_pages`
only counted legacy `sources:` and defaulted missing status to the
legacy `current`. A page that's fully migrated to `source_threads:` +
`status: active` looked uncited + legacy to the agent, leading to
bad merge-vs-new decisions mid-compile."""

from __future__ import annotations

from pathlib import Path

from src.compile.compiler import get_page_summary
from src.compile.compiler import list_wiki_pages


def _write(path: Path, frontmatter: dict, body: str = "Body text.") -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    fm_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{fm_block}\n---\n\n{body}\n", encoding="utf-8")


class TestGetPageSummarySourceThreads:
    def test_source_threads_only_page_counts_as_cited(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "topics" / "fully-migrated.md",
            {
                "title": "Fully Migrated",
                "page_type": "topic",
                "status": "active",
                "source_threads": ["19b92d9b270daa57", "19b59cdc863ac109"],
            },
        )
        result = get_page_summary.invoke({"slug": "fully-migrated", "wiki_dir": str(tmp_path)})
        assert result["found"] is True
        assert result["source_count"] == 0  # no legacy sources
        assert result["source_thread_count"] == 2
        assert result["is_cited"] is True  # THE load-bearing assertion

    def test_dual_field_page_counts_both(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "topics" / "transitional.md",
            {
                "title": "Transitional",
                "page_type": "topic",
                "status": "active",
                "sources": ["raw/a.md", "raw/b.md"],
                "source_threads": ["19b92d9b270daa57"],
            },
        )
        result = get_page_summary.invoke({"slug": "transitional", "wiki_dir": str(tmp_path)})
        assert result["source_count"] == 2
        assert result["source_thread_count"] == 1
        assert result["is_cited"] is True

    def test_empty_page_not_cited(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "topics" / "empty.md",
            {"title": "Empty", "page_type": "topic", "status": "active"},
        )
        result = get_page_summary.invoke({"slug": "empty", "wiki_dir": str(tmp_path)})
        assert result["source_count"] == 0
        assert result["source_thread_count"] == 0
        assert result["is_cited"] is False

    def test_missing_status_defaults_to_active_not_current(self, tmp_path: Path) -> None:
        # Codex P1: a frontmatter missing `status:` previously defaulted to
        # "current" — the legacy vocabulary the writer was told to avoid.
        _write(
            tmp_path / "topics" / "statusless.md",
            {"title": "Statusless", "page_type": "topic"},
        )
        result = get_page_summary.invoke({"slug": "statusless", "wiki_dir": str(tmp_path)})
        assert result["status"] == "active"


class TestListWikiPagesSourceThreads:
    def test_detailed_includes_source_thread_count_and_is_cited(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "topics" / "a.md",
            {
                "title": "A",
                "page_type": "topic",
                "status": "active",
                "source_threads": ["19b92d9b270daa57"],
            },
        )
        _write(
            tmp_path / "topics" / "b.md",
            {"title": "B", "page_type": "topic", "status": "active"},
        )
        result = list_wiki_pages.invoke({"response_format": "detailed", "wiki_dir": str(tmp_path)})
        by_slug = {p["slug"]: p for p in result["topics"]}
        assert by_slug["a"]["source_thread_count"] == 1
        assert by_slug["a"]["is_cited"] is True
        assert by_slug["b"]["source_thread_count"] == 0
        assert by_slug["b"]["is_cited"] is False
