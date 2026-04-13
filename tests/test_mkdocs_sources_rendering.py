"""Tests for the collapsed Sources section + entity cap in on_page_markdown.

Verifies:
- Entity pages with >20 sources render the newest 10 plus an "+N older" hint.
- Entity pages with <=20 sources render all of them, no older hint.
- Pages with 0 sources get no Sources section at all.
- Topic pages are never capped (only entities are capped).
- The wrapper always uses `<details markdown="1">` so inner markdown renders.

Fixture pages under `tests/fixtures/sources_fixture/wiki/` carry the
frontmatter shape we expect at compile time (page_type + sources list);
tests parse that frontmatter and hand it to the hook directly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mkdocs_hooks import _extract_frontmatter  # noqa: E402
from mkdocs_hooks import on_page_markdown  # noqa: E402

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sources_fixture" / "wiki"


@dataclass
class _FakeFile:
    src_path: str


@dataclass
class _FakePage:
    file: _FakeFile
    meta: dict = field(default_factory=dict)


def _page(src_path: str, meta: dict) -> _FakePage:
    return _FakePage(file=_FakeFile(src_path=src_path), meta=meta)


def _load_fixture(rel_path: str) -> tuple[dict, str]:
    """Return (meta, body) parsed from a fixture markdown file."""
    raw = (FIXTURE_ROOT / rel_path).read_text(encoding="utf-8")
    meta, body = _extract_frontmatter(raw)
    assert meta, f"fixture {rel_path} missing frontmatter"
    return meta, body


def _render(rel_path: str) -> str:
    meta, body = _load_fixture(rel_path)
    return on_page_markdown(body, page=_page(rel_path, meta), config={}, files=[])


def _count_inner_source_items(out: str) -> int:
    """Count sources rendered (each is either a `<details markdown="1">` block
    for a present raw file or a `*(file missing)*` bullet for a missing one).

    The outermost Sources wrapper is also `<details markdown="1">` — subtract
    one for it. All fixture raw files are missing on disk so each source in
    the emitted output is a bullet line.
    """
    inner_details = out.count('<details markdown="1">') - 1
    missing = out.count("*(file missing)*")
    return inner_details + missing


def test_entity_with_30_sources_is_capped_to_10_newest() -> None:
    out = _render("entities/jane-with-many.md")

    # Summary shows total count (30), not the capped count.
    assert "<summary>📚 Sources (30)</summary>" in out

    # Older-sources hint uses (30 - 10) = 20.
    assert "+20 older sources" in out

    # Only 10 source entries rendered inside the collapsed block.
    assert _count_inner_source_items(out) == 10

    # The 20 oldest sources (msg-001..msg-020) must not appear; the 10
    # newest (msg-021..msg-030) must.
    assert "raw/msg-001.md" not in out
    assert "raw/msg-020.md" not in out
    assert "raw/msg-021.md" in out
    assert "raw/msg-030.md" in out


def test_entity_with_five_sources_renders_all() -> None:
    out = _render("entities/jane-with-few.md")

    assert "<summary>📚 Sources (5)</summary>" in out
    assert "older sources" not in out
    assert _count_inner_source_items(out) == 5


def test_entity_with_zero_sources_has_no_sources_section() -> None:
    out = _render("entities/jane-with-none.md")

    assert "## Sources" not in out
    assert "<summary>" not in out
    assert "📚 Sources" not in out


def test_topic_with_ten_sources_is_not_capped() -> None:
    out = _render("topics/cool-topic.md")

    assert "<summary>📚 Sources (10)</summary>" in out
    # Topics never get the "older sources" hint — only entities do.
    assert "older sources" not in out
    assert _count_inner_source_items(out) == 10


def test_details_wrapper_uses_markdown_attr_when_sources_present() -> None:
    # Both an entity and a topic page should have the markdown-enabling
    # wrapper so inner content keeps rendering as markdown.
    for rel in ("entities/jane-with-many.md", "entities/jane-with-few.md", "topics/cool-topic.md"):
        out = _render(rel)
        assert '<details markdown="1">' in out, f"missing markdown attr in {rel}"
