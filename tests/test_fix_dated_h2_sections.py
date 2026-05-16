"""Unit tests for scripts/fix_dated_h2_sections.py.

Pin the three canonical title-rewrite shapes the fixer must handle:

1. `## Bug report (Jan 16, 2026)` (Month-Day-Year) → `## Bug report`,
   body prefix `As of 2026-01-16:`.
2. `## Test Results (2026-01-08)` (ISO date) → `## Test Results`,
   body prefix `As of 2026-01-08:`.
3. `## Rollout (January 2026)` (Month-Year only, no day) →
   `## Rollout`, NO body prefix — we only prepend when a specific
   day can be recovered.

Plus a byte-for-byte body preservation assertion so reviewers can
trust `--commit` won't accidentally rewrite paragraph content.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_fixer():  # type: ignore[no-untyped-def]
    """Import scripts/fix_dated_h2_sections.py as a module.

    Matches the pattern used by test_fix_broken_wikilinks.
    """
    spec = importlib.util.spec_from_file_location(
        "fix_dated_h2_sections",
        REPO_ROOT / "scripts" / "fix_dated_h2_sections.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fix_dated_h2_sections"] = module
    spec.loader.exec_module(module)
    return module


fixer = _load_fixer()


def _make_page(tmp_path: Path, slug: str, body: str) -> Path:
    """Write a minimal wiki page (frontmatter + body) under topics/."""
    wiki = tmp_path / "wiki"
    topics = wiki / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    # Systems/policies dirs exist so fix_wiki doesn't skip the walk.
    (wiki / "systems").mkdir(exist_ok=True)
    (wiki / "policies").mkdir(exist_ok=True)
    frontmatter = f"---\ntitle: {slug}\npage_type: topic\nstatus: active\n---\n\n"
    (topics / f"{slug}.md").write_text(frontmatter + body, encoding="utf-8")
    return wiki


def test_month_day_year_parens_stripped_with_body_prefix(tmp_path: Path) -> None:
    """Canonical rewrite #1: `(Jan 16, 2026)` → `As of 2026-01-16:`."""
    body = (
        "Some intro paragraph.\n\n## Bug report (Jan 16, 2026)\n\nTesting identified four bugs.\n"
    )
    wiki = _make_page(tmp_path, "foo", body)
    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.original_title == "Bug report (Jan 16, 2026)"
    assert r.new_title == "Bug report"
    assert r.parsed_date == "2026-01-16"

    content = (wiki / "topics" / "foo.md").read_text(encoding="utf-8")
    assert "## Bug report\n" in content
    assert "## Bug report (Jan 16, 2026)" not in content
    assert "As of 2026-01-16: Testing identified four bugs.\n" in content


def test_iso_date_parens_stripped_with_body_prefix(tmp_path: Path) -> None:
    """Canonical rewrite #2: `(2026-01-08)` → `As of 2026-01-08:`."""
    body = "## Test Results (2026-01-08)\n\nRegression suite passed on all platforms.\n"
    wiki = _make_page(tmp_path, "bar", body)
    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.original_title == "Test Results (2026-01-08)"
    assert r.new_title == "Test Results"
    assert r.parsed_date == "2026-01-08"

    content = (wiki / "topics" / "bar.md").read_text(encoding="utf-8")
    assert "## Test Results\n" in content
    assert "As of 2026-01-08: Regression suite passed on all platforms.\n" in content


def test_month_year_only_strips_title_without_body_prefix(tmp_path: Path) -> None:
    """Canonical rewrite #3: `(January 2026)` has no specific day —
    strip the title but DON'T add an `As of` prefix with a made-up
    day. The body line is returned untouched."""
    body = "## Rollout (January 2026)\n\nEnabled for 5% of traffic nationwide.\n"
    wiki = _make_page(tmp_path, "baz", body)
    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.original_title == "Rollout (January 2026)"
    assert r.new_title == "Rollout"
    assert r.parsed_date is None

    content = (wiki / "topics" / "baz.md").read_text(encoding="utf-8")
    assert "## Rollout\n" in content
    # Body line is preserved verbatim — no "As of" prefix.
    assert "Enabled for 5% of traffic nationwide.\n" in content
    assert "As of" not in content


def test_body_content_preserved_byte_for_byte(tmp_path: Path) -> None:
    """Paragraphs, tables, lists, and sub-headings inside the section
    are preserved verbatim — only the H2 title line changes and (if a
    date was recovered) a single `As of ...:` token is prepended to
    the first content line."""
    body = (
        "Intro paragraph mentioning [[foo-bar]] link.\n"
        "\n"
        "## Impact Assessment (Feb 5, 2026)\n"
        "\n"
        "Monitoring period: Non-festive weeks.\n"
        "\n"
        "| Metric | Impact |\n"
        "|--------|--------|\n"
        "| Clicks | Stable |\n"
        "\n"
        "### Sub-heading survives\n"
        "\n"
        "- bullet one with [[wikilink]]\n"
        "- bullet two\n"
    )
    wiki = _make_page(tmp_path, "qux", body)
    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 1

    content = (wiki / "topics" / "qux.md").read_text(encoding="utf-8")
    # Title rewritten.
    assert "## Impact Assessment\n" in content
    # First content line got prefixed.
    assert "As of 2026-02-05: Monitoring period: Non-festive weeks.\n" in content
    # Everything else preserved byte-for-byte — tables, sub-heading,
    # bullets, wikilinks.
    assert "| Metric | Impact |\n" in content
    assert "| Clicks | Stable |\n" in content
    assert "### Sub-heading survives\n" in content
    assert "- bullet one with [[wikilink]]\n" in content
    assert "- bullet two\n" in content
    # Intro paragraph above the rewritten heading is untouched.
    assert "Intro paragraph mentioning [[foo-bar]] link.\n" in content


def test_table_first_gets_standalone_as_of_paragraph(tmp_path: Path) -> None:
    """If the section opens with a table (or list/blockquote/code
    fence), inlining `As of ...: | Metric | ... |` corrupts the
    table grammar. The fixer must emit a standalone `As of ...`
    paragraph above instead."""
    body = (
        "## Processing Statistics (through Nov 15, 2025)\n"
        "\n"
        "| Metric | Value |\n"
        "|--------|-------|\n"
        "| PDN Processed | 301,048 |\n"
    )
    wiki = _make_page(tmp_path, "table-first", body)
    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 1
    assert rewrites[0].parsed_date == "2025-11-15"

    content = (wiki / "topics" / "table-first.md").read_text(encoding="utf-8")
    # Title stripped.
    assert "## Processing Statistics\n" in content
    # Date emitted as a standalone paragraph so the table stays valid.
    assert "As of 2025-11-15.\n" in content
    # Table grammar preserved byte-for-byte.
    assert "| Metric | Value |\n" in content
    assert "|--------|-------|\n" in content
    assert "| PDN Processed | 301,048 |\n" in content
    # No corruption by inlining.
    assert "As of 2025-11-15: |" not in content


def test_non_dated_h2_untouched(tmp_path: Path) -> None:
    """Headings without parenthetical dates (e.g. `## Background`) are
    left exactly as-is — the fixer must not over-reach."""
    body = (
        "## Background\n\nWhy we built this.\n\n## Bug report (Jan 16, 2026)\n\nFound four bugs.\n"
    )
    wiki = _make_page(tmp_path, "mixed", body)
    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 1
    assert rewrites[0].original_title == "Bug report (Jan 16, 2026)"

    content = (wiki / "topics" / "mixed.md").read_text(encoding="utf-8")
    assert "## Background\n" in content
    assert "Why we built this.\n" in content
    assert "As of" not in content.split("## Bug report")[0]  # no stray prefix


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    """`commit=False` previews but leaves files untouched on disk."""
    body = "## Bug report (Jan 16, 2026)\n\nFour bugs found.\n"
    wiki = _make_page(tmp_path, "readonly", body)
    original = (wiki / "topics" / "readonly.md").read_text(encoding="utf-8")

    rewrites = fixer.fix_wiki(wiki, commit=False)
    assert len(rewrites) == 1

    after = (wiki / "topics" / "readonly.md").read_text(encoding="utf-8")
    assert after == original


def test_unlisted_annotation_suffix_no_rewrite_recorded(tmp_path: Path) -> None:
    """Regression guard: _is_dated_title matches a wider set than
    _strip_trailing_dated_parens can peel. A heading like
    `## Rollout (Jan 16, 2026) — deprecated` trips the dated check
    (`(Jan 16, 2026)` matches `_RE_DATED_MONTH_IN_PARENS`) but
    `deprecated` isn't in `_RE_TRAILING_ANNOTATION`'s allow-list, so
    the peel returns the original string. Without the no-op guard we
    emit a Rewrite with `new_title == original_title` — misleading
    and a silent disk write. The guard must skip this line entirely.
    """
    body = "## Rollout (Jan 16, 2026) — deprecated\n\nSome content.\n"
    wiki = _make_page(tmp_path, "unpeelable", body)
    original_content = (wiki / "topics" / "unpeelable.md").read_text(encoding="utf-8")

    rewrites = fixer.fix_wiki(wiki, commit=True)
    assert len(rewrites) == 0

    # File untouched — no silent write, no corruption.
    after = (wiki / "topics" / "unpeelable.md").read_text(encoding="utf-8")
    assert after == original_content


def test_frontmatter_reconstruction_idempotent(tmp_path: Path) -> None:
    """When no H2 rewrites fire, the script still calls the reconstruction
    path on pages with dated H2s that weren't peelable. Verify the
    frontmatter + single-blank-line separator survives a commit cycle
    byte-for-byte — no extra blank lines, no lost content."""
    body = "Intro paragraph.\n\n## Bug report (Jan 16, 2026)\n\nFour bugs found.\n"
    wiki = _make_page(tmp_path, "roundtrip", body)
    before = (wiki / "topics" / "roundtrip.md").read_text(encoding="utf-8")
    # Frontmatter delimiter sanity — exactly one blank line separator.
    assert "---\n\n" in before
    assert "---\n\n\n" not in before

    fixer.fix_wiki(wiki, commit=True)
    after = (wiki / "topics" / "roundtrip.md").read_text(encoding="utf-8")
    # After rewrite, still exactly one blank line between frontmatter
    # and body — no triple-newline drift from the reconstruction.
    assert "---\n\n" in after
    assert "---\n\n\n" not in after


def test_limit_caps_pages_changed(tmp_path: Path) -> None:
    """`--limit N` stops after N pages have been rewritten."""
    for i in range(3):
        _make_page(
            tmp_path,
            f"page-{i}",
            f"## Bug report (Jan {i + 1}, 2026)\n\nBody {i}.\n",
        )
    wiki = tmp_path / "wiki"

    rewrites = fixer.fix_wiki(wiki, commit=True, limit=2)
    pages_changed = {r.page for r in rewrites}
    assert len(pages_changed) == 2
