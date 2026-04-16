"""Unit tests for the 4 north-star browse/patch/validate tools.

Covers:
- ``get_page_summary``: page found, missing, and zero-H2 shape.
- ``get_thread_context``: thread found (with preview), empty thread.
- ``patch_page``: replace existing section, create missing section, slug
  not found.
- ``validate_page_draft``: clean body, over-quoted body, missing TL;DR,
  person-page bare-mention, likely-duplicate.

DB-facing tests reuse the schema-isolation fixture in ``tests/conftest.py``
so we exercise real SQL without touching production data. Filesystem
tests use ``tmp_path`` fixtures — never the real ``wiki/`` tree.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from src.compile import compiler as compiler_mod
from src.compile.patch import replace_section
from src.db import messages as messages_repo


def _invoke(tool: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke a LangChain `@tool`-wrapped callable with kwargs."""
    result: dict[str, Any] = tool.invoke(kwargs)
    return result


def _write_page(
    wiki_dir: Path,
    category: str,
    slug: str,
    *,
    title: str,
    page_type: str,
    body: str,
    status: str = "current",
    sources: list[str] | None = None,
    last_compiled: str = "2026-04-15T12:00:00+00:00",
) -> Path:
    """Helper to seed a synthetic wiki page on disk."""
    path = wiki_dir / category / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f'title: "{title}"',
        f"page_type: {page_type}",
        f"status: {status}",
        f'last_compiled: "{last_compiled}"',
    ]
    src = sources or []
    if src:
        fm_lines.append("sources:")
        for s in src:
            fm_lines.append(f'  - "{s}"')
    else:
        fm_lines.append("sources: []")
    fm_lines.append("---")
    path.write_text("\n".join(fm_lines) + "\n\n" + body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# get_page_summary
# ---------------------------------------------------------------------------


class TestGetPageSummary:
    def test_found_returns_metadata_and_truncates_first_paragraph(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        long_intro = (
            "This is a deliberately long intro paragraph meant to push past "
            "the two-hundred character cap so that `get_page_summary` has "
            "something worth truncating — if not it would just return the "
            "full paragraph and we'd never exercise the ellipsis branch of "
            "the summariser, which is a bug I want to rule out."
        )
        body = f"{long_intro}\n\n## Overview\nbody\n\n## Recent Changes\nmore body\n"
        _write_page(
            wiki,
            "topics",
            "buylead",
            title="BuyLead",
            page_type="topic",
            body=body,
            sources=["raw/2026-04-11_foo.md", "raw/2026-04-12_bar.md"],
        )

        result = _invoke(compiler_mod.get_page_summary, slug="buylead", wiki_dir=str(wiki))

        assert result["found"] is True
        assert result["slug"] == "buylead"
        assert result["title"] == "BuyLead"
        assert result["page_type"] == "topic"
        assert result["status"] == "current"
        assert result["headings"] == ["Overview", "Recent Changes"]
        assert result["source_count"] == 2
        assert result["last_compiled"] == "2026-04-15T12:00:00+00:00"
        # Truncated to 200 chars with ellipsis suffix.
        assert len(result["first_paragraph"]) <= 200
        assert result["first_paragraph"].endswith("...")
        # And the FS path MUST NOT leak — the tool is deliberately path-free.
        assert "path" not in result

    def test_missing_page_returns_found_false(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        result = _invoke(compiler_mod.get_page_summary, slug="does-not-exist", wiki_dir=str(wiki))

        assert result == {
            "found": False,
            "slug": "does-not-exist",
            "reason": "not_found",
        }

    def test_page_with_zero_h2_sections_returns_empty_headings(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = "Just a lead paragraph and nothing else.\n"
        _write_page(
            wiki,
            "systems",
            "quiet-page",
            title="Quiet Page",
            page_type="system",
            body=body,
        )

        result = _invoke(compiler_mod.get_page_summary, slug="quiet-page", wiki_dir=str(wiki))

        assert result["found"] is True
        assert result["headings"] == []
        assert result["first_paragraph"].startswith("Just a lead paragraph")
        assert result["source_count"] == 0


# ---------------------------------------------------------------------------
# get_thread_context
# ---------------------------------------------------------------------------


class TestGetThreadContext:
    def test_thread_found_returns_messages_in_date_order_with_previews(
        self, tmp_path: Path, db_conn: psycopg.Connection
    ) -> None:
        # Seed two messages in the same thread, oldest last to prove sort.
        raw_new = tmp_path / "raw" / "2026-04-12_second.md"
        raw_old = tmp_path / "raw" / "2026-04-10_first.md"
        raw_new.parent.mkdir(parents=True, exist_ok=True)
        raw_new.write_text(
            "---\nsubject: s\n---\n\nSecond email body preview.\n",
            encoding="utf-8",
        )
        raw_old.write_text(
            "---\nsubject: s\n---\n\nFirst email body preview — older.\n",
            encoding="utf-8",
        )

        messages_repo.insert_message(
            db_conn,
            message_id="m_new",
            raw_path=str(raw_new),
            thread_id="t-1",
            subject="Re: hello",
            from_address="bob@example.com",
            date=datetime(2026, 4, 12, tzinfo=UTC),
            compile_state="pending",
        )
        messages_repo.insert_message(
            db_conn,
            message_id="m_old",
            raw_path=str(raw_old),
            thread_id="t-1",
            subject="hello",
            from_address="alice@example.com",
            date=datetime(2026, 4, 10, tzinfo=UTC),
            compile_state="compiled",
        )
        # Different thread — must not leak.
        messages_repo.insert_message(
            db_conn,
            message_id="other",
            raw_path=str(tmp_path / "raw" / "other.md"),
            thread_id="t-2",
            subject="unrelated",
            from_address="carol@example.com",
            date=datetime(2026, 4, 11, tzinfo=UTC),
            compile_state="pending",
        )
        db_conn.commit()

        result = _invoke(compiler_mod.get_thread_context, thread_id="t-1")

        assert result["thread_id"] == "t-1"
        assert [m["message_id"] for m in result["messages"]] == ["m_old", "m_new"]
        # Previews pulled from raw body (frontmatter stripped).
        assert result["messages"][0]["first_200_chars"].startswith("First email body")
        assert result["messages"][1]["first_200_chars"].startswith("Second email body")
        # Compile state from DB surfaces directly.
        assert result["messages"][0]["compile_state"] == "compiled"
        assert result["messages"][1]["compile_state"] == "pending"

    def test_unknown_thread_returns_empty_list(self) -> None:
        result = _invoke(compiler_mod.get_thread_context, thread_id="t-nope")
        assert result == {"thread_id": "t-nope", "messages": [], "truncated": False}


# ---------------------------------------------------------------------------
# patch_page
# ---------------------------------------------------------------------------


class TestPatchPage:
    def test_replace_existing_section(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = "Intro paragraph.\n\n## Current State\nold state\n\n## Recent Changes\nunchanged\n"
        page = _write_page(
            wiki,
            "topics",
            "patchable",
            title="Patchable",
            page_type="topic",
            body=body,
        )

        result = _invoke(
            compiler_mod.patch_page,
            slug="patchable",
            section="current state",  # case-insensitive match
            new_content="brand new state text",
            wiki_dir=str(wiki),
        )

        assert result["ok"] is True
        assert result["action"] == "replaced"
        assert result["section"] == "current state"
        assert result["bytes_written"] > 0

        updated = page.read_text(encoding="utf-8")
        assert "brand new state text" in updated
        assert "old state" not in updated
        # Heading retains original casing even though caller passed lowercase.
        assert "## Current State" in updated
        # Other section preserved verbatim.
        assert "## Recent Changes" in updated
        assert "unchanged" in updated
        # Frontmatter preserved.
        assert 'title: "Patchable"' in updated or "title: Patchable" in updated

    def test_create_missing_section_appends_to_end(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = "Intro.\n\n## Current State\na\n"
        page = _write_page(
            wiki,
            "topics",
            "appender",
            title="Appender",
            page_type="topic",
            body=body,
        )

        result = _invoke(
            compiler_mod.patch_page,
            slug="appender",
            section="History",
            new_content="- 2026-04-16: created",
            wiki_dir=str(wiki),
        )

        assert result["ok"] is True
        assert result["action"] == "created"

        updated = page.read_text(encoding="utf-8")
        assert "## History" in updated
        assert "- 2026-04-16: created" in updated
        # Previously existing section untouched.
        assert "## Current State\n\na\n" in updated or "## Current State\na\n" in updated

    def test_slug_not_found_returns_error(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        result = _invoke(
            compiler_mod.patch_page,
            slug="ghost",
            section="anything",
            new_content="x",
            wiki_dir=str(wiki),
        )

        assert result["ok"] is False
        assert result["slug"] == "ghost"
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# replace_section (pure helper)
# ---------------------------------------------------------------------------


class TestReplaceSection:
    def test_replaces_middle_section(self) -> None:
        body = "## A\na\n\n## B\nold\n\n## C\nc\n"
        new_body, action = replace_section(body, "B", "new")
        assert action == "replaced"
        assert "## B\n\nnew\n" in new_body
        assert "old" not in new_body
        assert "## C\nc" in new_body
        assert "## A\na" in new_body

    def test_appends_missing_section(self) -> None:
        body = "Intro.\n\n## A\na\n"
        new_body, action = replace_section(body, "B", "new")
        assert action == "created"
        assert new_body.rstrip().endswith("## B\n\nnew")

    def test_matches_case_insensitive_preserves_original_heading_case(self) -> None:
        body = "## Overview\nbody\n"
        new_body, action = replace_section(body, "  overview  ", "replaced content")
        assert action == "replaced"
        # Original heading casing preserved — caller's input is a lookup key,
        # not an overwrite of the heading text.
        assert "## Overview" in new_body
        assert "## overview" not in new_body
        assert "replaced content" in new_body
        assert "body" not in new_body


# ---------------------------------------------------------------------------
# validate_page_draft
# ---------------------------------------------------------------------------


class TestValidatePageDraft:
    def test_clean_body_produces_no_warnings(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = (
            "## TL;DR\nOne-line summary of what this is.\n\n"
            "## Details\nThree sentences of prose. Another sentence. "
            "And a third one to satisfy the person-page heuristic.\n"
        )
        result = _invoke(
            compiler_mod.validate_page_draft,
            slug="clean-draft",
            body=body,
            title="Clean Draft",
            page_type="topic",
            wiki_dir=str(wiki),
        )
        assert result == {"ok": True, "warnings": []}

    def test_missing_tldr_warns(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = "## Overview\nSome facts about the project.\n"
        result = _invoke(
            compiler_mod.validate_page_draft,
            slug="no-tldr",
            body=body,
            title="No Summary",
            page_type="topic",
            wiki_dir=str(wiki),
        )
        rules = {w["rule"] for w in result["warnings"]}
        assert "missing_tldr" in rules
        assert result["ok"] is True  # warning-level only

    def test_over_quoting_warns(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = (
            "## TL;DR\nsummary\n\n"
            "> quoted line one\n> quoted line two\n> quoted line three\n"
            "> quoted line four\n> quoted line five\n"
            "plain line\n"
        )
        result = _invoke(
            compiler_mod.validate_page_draft,
            slug="quotey",
            body=body,
            title="Quotey",
            page_type="topic",
            wiki_dir=str(wiki),
        )
        rules = {w["rule"] for w in result["warnings"]}
        assert "over_quoting" in rules

    def test_person_page_bare_mention_is_blocker(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        body = "## Mentions\n[[topic/foo]]\n"
        result = _invoke(
            compiler_mod.validate_page_draft,
            slug="cc-only-bob",
            body=body,
            title="Bob",
            page_type="entity",
            wiki_dir=str(wiki),
        )
        rules = {w["rule"] for w in result["warnings"]}
        severities = {w["rule"]: w["severity"] for w in result["warnings"]}
        assert "person_page_heuristic" in rules
        assert severities["person_page_heuristic"] == "blocker"
        assert result["ok"] is False

    def test_likely_duplicate_warns_when_title_collides(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        _write_page(
            wiki,
            "topics",
            "existing-slug",
            title="Shared Title",
            page_type="topic",
            body="## TL;DR\nexisting\n",
        )
        body = "## TL;DR\nsummary\n\n## Body\nSentence one. Sentence two.\n"
        result = _invoke(
            compiler_mod.validate_page_draft,
            slug="new-slug",
            body=body,
            title="shared title",
            page_type="topic",
            wiki_dir=str(wiki),
        )
        rules = {w["rule"] for w in result["warnings"]}
        assert "likely_duplicate" in rules
