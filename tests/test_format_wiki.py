"""Tests for scripts/format_wiki.py — the light-format normalizer.

The formatter has two responsibilities tested here:
1. Strip agent-written nav sections (`## Related`, `## People`, `## Team`,
   `## Related Topics`, etc.) while preserving `## Related Work` (content).
2. Regenerate a single `## Related` section at the page end from the union
   of frontmatter `related:` + inline `[[slug]]` links that resolve to real
   pages.

Each test uses a tmp_path-backed mini-wiki so we never touch real wiki/.
The formatter is imported via importlib because `scripts/` isn't on
PYTHONPATH by default.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_formatter() -> ModuleType:
    """Import scripts/format_wiki.py as a module.

    Register in sys.modules before exec so dataclass() can resolve forward
    refs — same pattern as test_validate_wiki_entity_checks.py.
    """
    spec = importlib.util.spec_from_file_location(
        "format_wiki", REPO_ROOT / "scripts" / "format_wiki.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["format_wiki"] = module
    spec.loader.exec_module(module)
    return module


formatter = _load_formatter()


@pytest.fixture
def mini_wiki(tmp_path: Path) -> Path:
    """Wiki tree with topics/, entities/, systems/ subdirectories ready to fill."""
    wiki = tmp_path / "wiki"
    for cat in ("topics", "entities", "systems", "policies", "timelines", "conflicts"):
        (wiki / cat).mkdir(parents=True)
    return wiki


def _write_page(
    path: Path,
    *,
    title: str,
    page_type: str,
    related: list[str] | None = None,
    body: str,
) -> None:
    """Write a minimal valid wiki page with YAML frontmatter + body."""
    related_block = ""
    if related:
        related_block = "related:\n" + "\n".join(f"  - '[[{r}]]'" for r in related) + "\n"
    frontmatter = (
        "---\n"
        f"title: {title}\n"
        f"page_type: {page_type}\n"
        "status: current\n"
        f"{related_block}"
        "---\n\n"
        f"{body}"
    )
    path.write_text(frontmatter, encoding="utf-8")


def test_strips_agent_related_section(mini_wiki: Path) -> None:
    """Agent-written `## Related` + `## People` + `## Related Topics` are all removed.

    Regenerated into ONE canonical `## Related` at the end. `## Details` (real
    content) must survive untouched.
    """
    # `other-topic.md` must exist so the regenerated ## Related has a valid
    # target — otherwise it'd be filtered out as a broken link.
    _write_page(
        mini_wiki / "topics" / "other-topic.md",
        title="Other Topic",
        page_type="topic",
        body="Other topic lead sentence. Second sentence.\n",
    )
    _write_page(
        mini_wiki / "entities" / "jane-doe.md",
        title="Jane Doe",
        page_type="entity",
        body="Jane is a person. She does work.\n",
    )
    page = mini_wiki / "topics" / "main.md"
    _write_page(
        page,
        title="Main",
        page_type="topic",
        body=(
            "Main is a project that does stuff. It has two sentences.\n\n"
            "## Details\n\n"
            "Some details here.\n\n"
            "## Related\n\n"
            "- [[other-topic]] — a thing\n\n"
            "## People\n\n"
            "- [[jane-doe]] — owner\n\n"
            "## Related Topics\n\n"
            "- [[other-topic]]\n"
        ),
    )

    result = formatter.format_file(page, formatter._known_slugs(mini_wiki))
    assert result.changed is True
    assert result.skipped_reason is None

    formatter.run(mini_wiki, confirm=True)
    content = page.read_text(encoding="utf-8")
    # Exactly one ## Related heading remains; ## People and ## Related Topics
    # were stripped.
    assert content.count("## Related") == 1
    assert "## People" not in content
    assert "## Related Topics" not in content
    # Inline-dropped links still show up in the regenerated Related section
    # because they were referenced somewhere in the original body.
    assert "[[other-topic]]" in content
    assert "[[jane-doe]]" in content
    # Real content (## Details) survives.
    assert "## Details" in content
    assert "Main is a project" in content


def test_preserves_related_work(mini_wiki: Path) -> None:
    """`## Related Work` is a content section and must not be stripped."""
    page = mini_wiki / "topics" / "research.md"
    _write_page(
        page,
        title="Research",
        page_type="topic",
        body=(
            "Research is a topic covering prior art. Second sentence.\n\n"
            "## Related Work\n\n"
            "Smith et al. 2023 — foundational study on the approach.\n"
        ),
    )
    formatter.run(mini_wiki, confirm=True)
    content = page.read_text(encoding="utf-8")
    assert "## Related Work" in content
    assert "Smith et al." in content


def test_regenerates_from_inline_links(mini_wiki: Path) -> None:
    """Inline [[foo]] + empty related: frontmatter → Related section lists [[foo]]."""
    # Make `foo` resolve.
    _write_page(
        mini_wiki / "topics" / "foo.md",
        title="Foo",
        page_type="topic",
        body="Foo is a thing. It exists.\n",
    )
    page = mini_wiki / "topics" / "bar.md"
    _write_page(
        page,
        title="Bar",
        page_type="topic",
        related=[],
        body=(
            "Bar is a thing that references [[foo]]. Second sentence.\n\n"
            "## Context\n\n"
            "More on [[foo]] here.\n"
        ),
    )
    formatter.run(mini_wiki, confirm=True)
    content = page.read_text(encoding="utf-8")
    # Regenerated Related section exists with [[foo]] even though the
    # page's `related:` frontmatter was empty and no `## Related` was
    # hand-written.
    assert "## Related" in content
    assert "[[foo]]" in content.split("## Related", 1)[1]


def test_idempotent_on_clean_page(mini_wiki: Path) -> None:
    """Running the formatter twice produces identical output (byte-exact)."""
    _write_page(
        mini_wiki / "topics" / "foo.md",
        title="Foo",
        page_type="topic",
        body="Foo is a thing. It exists.\n",
    )
    page = mini_wiki / "topics" / "bar.md"
    _write_page(
        page,
        title="Bar",
        page_type="topic",
        body=(
            "Bar is a thing that references [[foo]]. Second sentence.\n\n"
            "## Context\n\n"
            "More details.\n"
        ),
    )
    formatter.run(mini_wiki, confirm=True)
    after_first = page.read_text(encoding="utf-8")

    formatter.run(mini_wiki, confirm=True)
    after_second = page.read_text(encoding="utf-8")
    assert after_first == after_second


def test_skips_on_broken_frontmatter(mini_wiki: Path) -> None:
    """Unparseable YAML → FileResult with a skipped_reason; no crash."""
    page = mini_wiki / "topics" / "broken.md"
    # YAML with unmatched quotes + a stray tab makes this unparseable.
    page.write_text(
        '---\ntitle: "Broken\npage_type: topic\nstatus: current\n---\n\nBody.\n',
        encoding="utf-8",
    )

    summary = formatter.run(mini_wiki, confirm=True)
    assert summary.pages_with_errors == 1
    assert summary.pages_changed == 0
    # The broken file was not mutated.
    remaining = page.read_text(encoding="utf-8")
    assert remaining.startswith('---\ntitle: "Broken')
    # Per-file result carries the skip reason.
    broken_results = [r for r in summary.results if r.path == page]
    assert len(broken_results) == 1
    assert broken_results[0].skipped_reason is not None
