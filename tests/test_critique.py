"""Tests for src.compile.critique — pre-mark_as_compiled quality gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.compile.critique import CritiqueResult
from src.compile.critique import critique_pages
from src.compile.critique import find_touched_pages
from src.compile.critique import write_audit


def _write_page(path: Path, title: str, sources: list[str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    src_lines = "\n".join(f"- {s}" for s in sources)
    fm = f"---\ntitle: {title}\npage_type: topic\nstatus: current\nsources:\n{src_lines}\n---\n"
    path.write_text(fm + body, encoding="utf-8")


def test_find_touched_pages_matches_by_basename(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    _write_page(
        wiki / "topics" / "foo.md",
        "Foo",
        ["raw/2026-04-15_test_abc12345.md"],
        "\n## Overview\nBody.\n",
    )
    _write_page(
        wiki / "topics" / "bar.md",
        "Bar",
        ["raw/2026-04-15_other_xyz99999.md"],
        "\n## Overview\nBody.\n",
    )

    touched = find_touched_pages("raw/2026-04-15_test_abc12345.md", wiki)
    assert len(touched) == 1
    assert touched[0].name == "foo.md"


def test_critique_flags_duplicate_h2(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\n## Known Issues\nFirst.\n\n## Known Issues\nSecond.\n",
    )

    result = critique_pages([page], wiki, tmp_path)
    assert any(b.check == "duplicate-h2" for b in result.blockers), result.issues


def test_critique_flags_broken_wikilink(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\n## Overview\nSee [[nonexistent-page]].\n",
    )

    result = critique_pages([page], wiki, tmp_path)
    assert any(b.check == "broken-wikilink" for b in result.blockers), result.issues


def test_critique_flags_stray_bracket(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\n## Overview\nText.\n\n## Related\n\n- [[bar]]\n]\n- [[baz]]\n",
    )
    # Also make bar and baz real so broken-wikilink doesn't fire
    _write_page(wiki / "topics" / "bar.md", "Bar", ["raw/y.md"], "\n## Overview\nB.\n")
    _write_page(wiki / "topics" / "baz.md", "Baz", ["raw/z.md"], "\n## Overview\nB.\n")

    result = critique_pages([page], wiki, tmp_path)
    assert any(b.check == "stray-bracket" for b in result.blockers), result.issues


def test_critique_warns_on_h1_in_body(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\n# Foo Page Title\n\n## Overview\nBody.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert any(w.check == "h1-in-body" for w in result.warnings), result.issues


def test_critique_clean_page(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nThis is a clean lead paragraph. It has two sentences.\n\n"
        "## Overview\nBody.\n\n## Related\n- [[bar]]\n",
    )
    _write_page(wiki / "topics" / "bar.md", "Bar", ["raw/y.md"], "\n## Overview\nB.\n")

    result = critique_pages([page], wiki, tmp_path)
    assert len(result.blockers) == 0, [b.message for b in result.blockers]


def test_write_audit_creates_iso_dated_file(tmp_path: Path) -> None:
    audit_dir = tmp_path / "docs" / "audits"
    result = CritiqueResult(issues=[], pages_critiqued=["wiki/topics/foo.md"])
    out = write_audit(result, "raw/2026-04-15_test_abc12345.md", "compiled", audit_dir)

    assert out.exists()
    assert out.parent == audit_dir
    assert out.name.startswith("critique-")
    assert out.name.endswith("-abc12345.md")
    content = out.read_text(encoding="utf-8")
    assert "action: compiled" in content
    assert "raw/2026-04-15_test_abc12345.md" in content


def test_write_audit_includes_blockers_and_ack(tmp_path: Path) -> None:
    from src.compile.critique import Issue

    audit_dir = tmp_path / "docs" / "audits"
    issues = [
        Issue(
            id="aaaa0001",
            severity="blocker",
            check="duplicate-h2",
            page="wiki/topics/foo.md",
            message="dup",
        ),
        Issue(
            id="bbbb0002",
            severity="blocker",
            check="broken-wikilink",
            page="wiki/topics/foo.md",
            message="broken",
        ),
    ]
    result = CritiqueResult(issues=issues, pages_critiqued=["wiki/topics/foo.md"])
    out = write_audit(
        result,
        "raw/2026-04-15_x_deadbeef.md",
        "compiled",
        audit_dir,
        acknowledged_ids={"aaaa0001"},
    )
    content = out.read_text(encoding="utf-8")
    assert "[aaaa0001]" in content
    assert "[bbbb0002]" in content
    assert "_acknowledged_" in content  # marker on aaaa0001


def test_critique_handles_malformed_frontmatter(tmp_path: Path) -> None:
    """Genuinely malformed (missing closing fence) must flag fence-count.

    A page with a valid 2-fence frontmatter PLUS a `---` horizontal rule
    in the body is NOT malformed — see `cae7f4c` (P0 fix), which moved
    the check from a raw `---` count to `split_frontmatter`'s result.
    The fixture here is a genuinely broken page: opening fence, no
    closing fence, so `split_frontmatter` returns empty frontmatter text.
    """
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "bad.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    # Opening fence only — no closing `---`
    page.write_text(
        "---\ntitle: Bad\npage_type: topic\nstatus: current\n\nbody with no closing fence\n",
        encoding="utf-8",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert any(b.check == "fence-count" for b in result.blockers), result.issues


@pytest.mark.parametrize("missing_field", ["title", "page_type", "status"])
def test_critique_flags_missing_required_fields(tmp_path: Path, missing_field: str) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    fields = {"title": "Foo", "page_type": "topic", "status": "current"}
    del fields[missing_field]
    fm = (
        "---\n"
        + "\n".join(f"{k}: {v}" for k, v in fields.items())
        + "\n---\n\n## Overview\nBody.\n"
    )
    page.write_text(fm, encoding="utf-8")

    result = critique_pages([page], wiki, tmp_path)
    assert any(
        b.check == "required-field" and missing_field in b.message for b in result.blockers
    ), result.issues


def test_missing_suggested_h2s_warns(tmp_path: Path) -> None:
    """v11-U7: a topic page with thread-subject-templated H2s and
    zero canonical sections must surface a `missing_suggested_h2s`
    warning naming the present + missing slots. Severity is always
    `warning` — reviewer takes the final call."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "launch-foo.md"
    _write_page(
        page,
        "Launch Foo",
        ["raw/x.md"],
        "\nLead paragraph.\n\n## Launch Announcement\nDetails.\n\n"
        "## Bug report\nThings.\n\n## QA Testing Results\nResults.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [w for w in result.warnings if w.check == "missing_suggested_h2s"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    msg = matches[0].message
    assert "Summary" in msg
    assert "Current state" in msg
    assert "missing" in msg
    # Severity is warning — never blocker.
    assert all(b.check != "missing_suggested_h2s" for b in result.blockers)


def test_suggested_h2s_complete_no_warning(tmp_path: Path) -> None:
    """A topic page that hits the floor (≥4/8) does NOT warn."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "complete.md"
    _write_page(
        page,
        "Complete",
        ["raw/x.md"],
        "\nLead paragraph here.\n\n## Summary\nA.\n\n"
        "## Current state\nB.\n\n## Why it matters\nC.\n\n"
        "## Key decisions\nD.\n\n## Recent changes\nE.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "missing_suggested_h2s" for w in result.warnings)


def test_suggested_h2s_skips_decision_pages(tmp_path: Path) -> None:
    """Decision pages have no canonical shape — the rule must not fire
    on `page_type: decision`."""
    wiki = tmp_path / "wiki"
    page = wiki / "decisions" / "scale-foo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: Scale Foo\npage_type: decision\nstatus: active\n"
        "sources:\n- raw/x.md\n---\n\n"
        "Lead paragraph.\n\n## Random Heading\nBody.\n",
        encoding="utf-8",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "missing_suggested_h2s" for w in result.warnings)


# --- anti-pattern H2 (V12 50-compile deep audit fix-A) --------------------


def _write_page_with_fm(path: Path, fm_lines: list[str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = "---\n" + "\n".join(fm_lines) + "\n---\n"
    path.write_text(fm + body, encoding="utf-8")


def test_anti_pattern_h2_fires_on_qa_testing_results(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nCurrently live for 50% of traffic.\n\n## QA Testing Results\nAll green.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [w for w in result.warnings if w.check == "anti-pattern-h2"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    assert "QA Testing Results" in matches[0].message


def test_anti_pattern_h2_fires_on_business_requirements(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nCurrently in beta.\n\n## Business Requirements\nDoc text.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [w for w in result.warnings if w.check == "anti-pattern-h2"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    assert "Business Requirements" in matches[0].message


def test_anti_pattern_h2_fires_on_decision_prefix(tmp_path: Path) -> None:
    """``## Decision: Scale to 100%`` matches via the decision-prefix rule."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nCurrently live.\n\n## Decision: Scale to 100%\nWe shipped.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [w for w in result.warnings if w.check == "anti-pattern-h2"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    assert "Decision: Scale to 100%" in matches[0].message


def test_anti_pattern_h2_does_not_fire_on_clean_shape(tmp_path: Path) -> None:
    """``## Current state`` + ``## Why it matters`` must not trigger."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nCurrently live at 50%.\n\n"
        "## Current state\nRolling out to 50%.\n\n"
        "## Why it matters\nImproves signal.\n\n"
        "## Key decisions\nShip it.\n\n## Recent changes\nSome history.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "anti-pattern-h2" for w in result.warnings)


def test_anti_pattern_h2_is_warning_not_blocker(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nCurrently live.\n\n## QA Testing Results\nAll green.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(b.check != "anti-pattern-h2" for b in result.blockers)


# --- summary staleness (V12 50-compile deep audit fix-A) ------------------


def test_summary_staleness_fires_when_summary_lacks_current_markers(
    tmp_path: Path,
) -> None:
    """``Recent changes`` has a 2026-04-20 date but Summary says "was
    launched in January" — no current-state marker, so warn.

    ``last_compiled`` is set just after the recent-change date so the
    90-day window includes it (rules out "old page, recent event"
    legitimate cases)."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page_with_fm(
        page,
        [
            "title: Foo",
            "page_type: topic",
            "status: active",
            "last_compiled: 2026-04-21T00:00:00Z",
            "sources:",
            "- raw/x.md",
        ],
        "\nThe system was launched in January.\n\n"
        "## Current state\nSomething.\n\n"
        "## Recent changes\n- 2026-04-20: rolled out to 50%.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [w for w in result.warnings if w.check == "summary-staleness"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    assert "2026-04-20" in matches[0].message


def test_summary_staleness_does_not_fire_when_summary_has_current_marker(
    tmp_path: Path,
) -> None:
    """Summary saying "Currently rolled out to 50% since 2026-04-20" has
    both a current-state token and an ISO date — the rule must stay
    quiet."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page_with_fm(
        page,
        [
            "title: Foo",
            "page_type: topic",
            "status: active",
            "last_compiled: 2026-04-21T00:00:00Z",
            "sources:",
            "- raw/x.md",
        ],
        "\nCurrently rolled out to 50% since 2026-04-20.\n\n"
        "## Current state\nDetails.\n\n"
        "## Recent changes\n- 2026-04-20: rolled out to 50%.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "summary-staleness" for w in result.warnings)


def test_summary_staleness_skips_when_no_recent_changes_section(
    tmp_path: Path,
) -> None:
    """No ``## Recent changes`` section — nothing to diff, skip cleanly."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page_with_fm(
        page,
        [
            "title: Foo",
            "page_type: topic",
            "status: active",
            "last_compiled: 2026-04-21T00:00:00Z",
            "sources:",
            "- raw/x.md",
        ],
        "\nThe system was launched in January.\n\n## Current state\nThings.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "summary-staleness" for w in result.warnings)


def test_summary_staleness_skips_when_recent_changes_has_only_old_dates(
    tmp_path: Path,
) -> None:
    """Recent changes dated only from > 90 days before ``last_compiled``
    — no recency signal, rule stays quiet."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page_with_fm(
        page,
        [
            "title: Foo",
            "page_type: topic",
            "status: active",
            "last_compiled: 2026-04-21T00:00:00Z",
            "sources:",
            "- raw/x.md",
        ],
        "\nThe system was launched in January.\n\n"
        "## Recent changes\n- 2025-01-01: initial release.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "summary-staleness" for w in result.warnings)


def test_summary_staleness_is_warning_not_blocker(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page_with_fm(
        page,
        [
            "title: Foo",
            "page_type: topic",
            "status: active",
            "last_compiled: 2026-04-21T00:00:00Z",
            "sources:",
            "- raw/x.md",
        ],
        "\nThe system was launched in January.\n\n"
        "## Recent changes\n- 2026-04-20: rolled out to 50%.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(b.check != "summary-staleness" for b in result.blockers)
