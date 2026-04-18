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
        result = get_page_summary.invoke(
            {"slug": "fully-migrated", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
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
        result = get_page_summary.invoke(
            {"slug": "transitional", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
        assert result["source_count"] == 2
        assert result["source_thread_count"] == 1
        assert result["is_cited"] is True

    def test_empty_page_not_cited(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "topics" / "empty.md",
            {"title": "Empty", "page_type": "topic", "status": "active"},
        )
        result = get_page_summary.invoke(
            {"slug": "empty", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
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
        result = get_page_summary.invoke(
            {"slug": "statusless", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
        assert result["status"] == "active"


class TestGetPageSummaryTldr:
    """V11-U5: surface `## TL;DR` content so agents skip a re-read.

    151/234 (~64.5%) of `get_page_summary` calls were followed by
    `read_file` on the same slug — the agent needed more than headings
    + first paragraph. A durable `## TL;DR` section now appears as the
    `tldr` field on both concise and detailed responses.
    """

    def test_concise_surfaces_tldr_block(self, tmp_path: Path) -> None:
        body = (
            "Lead paragraph here.\n\n"
            "## TL;DR\n\n"
            "Scaled buyer trust to 50% after A/B win. "
            "Engagement +8.4%, NPS +1.2 vs. control.\n\n"
            "## Current state\n\nDetails follow.\n"
        )
        _write(
            tmp_path / "topics" / "with-tldr.md",
            {"title": "With TLDR", "page_type": "topic", "status": "active"},
            body=body,
        )
        result = get_page_summary.invoke(
            {"slug": "with-tldr", "wiki_dir": str(tmp_path), "response_format": "concise"}
        )
        assert result["found"] is True
        assert "tldr" in result
        tldr = result["tldr"]
        assert tldr is not None
        assert "Scaled buyer trust to 50%" in tldr
        # The TL;DR ends at the next H2 — `Current state` body must NOT leak.
        assert "Details follow" not in tldr

    def test_detailed_surfaces_tldr_block(self, tmp_path: Path) -> None:
        body = "Lead.\n\n## TL;DR\nOne sentence summary.\n\n## Background\nMore body.\n"
        _write(
            tmp_path / "topics" / "with-tldr-d.md",
            {"title": "With TLDR D", "page_type": "topic", "status": "active"},
            body=body,
        )
        result = get_page_summary.invoke(
            {"slug": "with-tldr-d", "wiki_dir": str(tmp_path), "response_format": "detailed"}
        )
        assert result["tldr"] == "One sentence summary."

    def test_no_tldr_returns_none_field(self, tmp_path: Path) -> None:
        body = "Just a lead paragraph.\n\n## Current state\n\nBody.\n"
        _write(
            tmp_path / "topics" / "no-tldr.md",
            {"title": "No TLDR", "page_type": "topic", "status": "active"},
            body=body,
        )
        for fmt in ("concise", "detailed"):
            result = get_page_summary.invoke(
                {"slug": "no-tldr", "wiki_dir": str(tmp_path), "response_format": fmt}
            )
            assert "tldr" in result, f"{fmt} response must include tldr key"
            assert result["tldr"] is None, f"{fmt} should report tldr=None"

    def test_tldr_variant_no_semicolon(self, tmp_path: Path) -> None:
        # `## TLDR` (no semicolon) is a valid heading variant.
        body = "Lead.\n\n## TLDR\n\nNo-semicolon variant works too.\n"
        _write(
            tmp_path / "topics" / "tldr-variant.md",
            {"title": "Variant", "page_type": "topic", "status": "active"},
            body=body,
        )
        result = get_page_summary.invoke(
            {"slug": "tldr-variant", "wiki_dir": str(tmp_path), "response_format": "concise"}
        )
        assert result["tldr"] == "No-semicolon variant works too."

    def test_tldr_case_insensitive(self, tmp_path: Path) -> None:
        body = "Lead.\n\n## tl;dr\nLowercase heading is fine.\n"
        _write(
            tmp_path / "topics" / "tldr-lower.md",
            {"title": "Lower", "page_type": "topic", "status": "active"},
            body=body,
        )
        result = get_page_summary.invoke(
            {"slug": "tldr-lower", "wiki_dir": str(tmp_path), "response_format": "concise"}
        )
        assert result["tldr"] == "Lowercase heading is fine."

    def test_tldr_at_end_of_file(self, tmp_path: Path) -> None:
        # No subsequent H2 — collection runs to EOF.
        body = "Lead.\n\n## TL;DR\nLast section in the doc.\n"
        _write(
            tmp_path / "topics" / "tldr-end.md",
            {"title": "End", "page_type": "topic", "status": "active"},
            body=body,
        )
        result = get_page_summary.invoke(
            {"slug": "tldr-end", "wiki_dir": str(tmp_path), "response_format": "concise"}
        )
        assert result["tldr"] == "Last section in the doc."


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
