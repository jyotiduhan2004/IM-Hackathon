"""Tests for src.agent.critique — pre-mark_as_compiled quality gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.agent.critique import CritiqueResult
from src.agent.critique import critique_pages
from src.agent.critique import find_touched_pages
from src.agent.critique import write_audit


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
        "## Overview\nBody.\n\n## Recent changes\n- 2026-04-20 — something.\n\n"
        "## Related\n- [[bar]]\n",
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
    from src.agent.critique import Issue

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
    `warning` — reviewer takes the final call.

    PR2 (2026-04-28 prompt-review Q7.1, Q7.2): the universal H2
    floor dropped `Summary` (lead paragraph IS the summary) and
    `Key decisions` (decisions are lazy + linked-from). Replaced
    the asserted slot names accordingly."""
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
    assert "Why it matters" in msg
    assert "Current state" in msg
    assert "missing" in msg
    # Severity is warning — never blocker.
    assert all(b.check != "missing_suggested_h2s" for b in result.blockers)


def test_suggested_h2s_complete_no_warning(tmp_path: Path) -> None:
    """A topic page that hits the floor does NOT warn.

    PR2 (Q7.1): the new floor is 5 slots — `Why it matters`,
    `Current state`, `Recent changes`, `Open questions`, `Related`
    — and the threshold is ≥4/5."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "complete.md"
    _write_page(
        page,
        "Complete",
        ["raw/x.md"],
        "\nLead paragraph here.\n\n## Why it matters\nA.\n\n"
        "## Current state\nB.\n\n## Recent changes\nC.\n\n"
        "## Open questions\nD.\n\n## Related\nE.\n",
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


# --- #169 — find_touched_pages scope ---------------------------------------


def test_find_touched_pages_does_not_include_unrelated_recent_mtime(
    tmp_path: Path,
) -> None:
    """Without a ``batch_touched`` scope, the broken-frontmatter fallback
    MUST NOT slurp unrelated recently-modified pages (index.md, merge
    candidates). Regression coverage for #169."""
    import os

    wiki = tmp_path / "wiki"
    # `a-touched` is a legitimate citation by this raw email.
    _write_page(
        wiki / "topics" / "a-touched.md",
        "A Touched",
        ["raw/2026-04-15_x_abc12345.md"],
        "\n## Overview\nbody\n\n## Recent changes\n- 2026-04-20 — y.\n",
    )
    # `b-generated` is a freshly-written unparseable file (e.g. index.md
    # rewritten between batches). Without scope, the old fallback would
    # pull this in.
    b = wiki / "b-generated.md"
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("no frontmatter at all, just text\n", encoding="utf-8")
    # `c-old` is an old unparseable file — mtime well outside the 600s
    # window — so it never qualifies regardless of scope.
    c = wiki / "c-old.md"
    c.write_text("also no frontmatter\n", encoding="utf-8")
    old = tmp_path.stat().st_mtime - 3600
    os.utime(c, (old, old))

    # No batch_touched scope → broken-page fallback is disabled; only
    # citation-matching pages come back.
    touched = find_touched_pages("raw/2026-04-15_x_abc12345.md", wiki)
    names = [p.name for p in touched]
    assert "a-touched.md" in names
    assert "b-generated.md" not in names
    assert "c-old.md" not in names

    # With a batch scope that includes a-touched ONLY, the broken b-
    # generated file is still excluded because it's not in the scope.
    touched_scoped = find_touched_pages(
        "raw/2026-04-15_x_abc12345.md", wiki, batch_touched={"a-touched"}
    )
    names_scoped = [p.name for p in touched_scoped]
    assert "a-touched.md" in names_scoped
    assert "b-generated.md" not in names_scoped
    assert "c-old.md" not in names_scoped


def test_find_touched_pages_scope_includes_batch_broken_page(tmp_path: Path) -> None:
    """When ``batch_touched`` names a page that happens to have unparseable
    frontmatter AND was touched within the 600s window, it IS surfaced —
    the agent just corrupted a page and the critique should report it."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    broken = wiki / "topics" / "just-broke.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("---\nbroken YAML: : :\n", encoding="utf-8")

    touched = find_touched_pages(
        "raw/2026-04-15_x_abc12345.md",
        wiki,
        batch_touched={"just-broke"},
    )
    names = [p.name for p in touched]
    assert "just-broke.md" in names


# --- #167 — legacy people-slug → warning, not blocker ---------------------


def test_broken_legacy_people_slug_is_warning_not_blocker(tmp_path: Path) -> None:
    """``[[ishan-tomar-indiamart-com]]`` resolves to a non-existent people
    page; critique should WARN (auto-stub fires elsewhere) rather than
    BLOCK the agent. Regression for #167."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nLead paragraph.\n\n## Overview\nAsked [[ishan-tomar-indiamart-com]].\n\n"
        "## Recent changes\n- 2026-04-20 — logged.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    # warning fires on the legacy slug
    matches = [w for w in result.warnings if w.check == "broken-people-slug"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    assert "ishan-tomar-indiamart-com" in matches[0].message
    # must NOT be a blocker
    assert all(b.check != "broken-people-slug" for b in result.blockers)
    assert all(b.check != "broken-wikilink" for b in result.blockers)


def test_broken_concept_slug_stays_blocker(tmp_path: Path) -> None:
    """Non-people broken wikilinks keep their blocker severity — the
    people-slug demotion is narrow, scoped to email-shaped slugs only."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\n## Overview\nSee [[some-missing-concept]].\n\n"
        "## Recent changes\n- 2026-04-20 — logged.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert any(b.check == "broken-wikilink" for b in result.blockers), result.issues


# --- footnote def-vs-use safety net (warning severity) -------------------
# Primary fix: deterministic backfill in
# `src/coordinator/post_batch.py::_backfill_references_on_touched_pages`.
# The check below remains as a `warning` for non-coordinator entrypoints
# (`watch_and_compile.py`, `compile_parallel.py`) which skip the hook chain.


def test_footnote_usage_without_def_warns_not_blocks(tmp_path: Path) -> None:
    """Missing def is now a warning, not a blocker — the deterministic
    backfill is the primary fix; the agent must not loop on this."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nLead paragraph.\n\n"
        "## Recent changes\n- 2026-04-20 — shipped [^msg-bff57907]\n\n"
        "## References\n- source\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [w for w in result.warnings if w.check == "footnote-missing-def"]
    assert len(matches) == 1, [w.message for w in result.warnings]
    assert "msg-bff57907" in matches[0].message
    assert all(b.check != "footnote-missing-def" for b in result.blockers)


def test_footnote_usage_with_def_no_warning(tmp_path: Path) -> None:
    """Matching def present — no warning."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nLead paragraph.\n\n"
        "## Recent changes\n- 2026-04-20 — shipped [^msg-bff57907]\n\n"
        "## References\n[^msg-bff57907]: email from dev team\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(w.check != "footnote-missing-def" for w in result.warnings)


# --- #158 — Recent-changes H2 required on topic pages ---------------------


def test_topic_without_recent_changes_h2_is_blocker(tmp_path: Path) -> None:
    """Topic pages MUST have ``## Recent changes``. Regression for
    bl-notification-timing-optimization / pns-call-summary findings."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nLead paragraph.\n\n## Overview\nBody.\n\n## Related\n- [[bar]]\n",
    )
    _write_page(
        wiki / "topics" / "bar.md",
        "Bar",
        ["raw/y.md"],
        "\n## Overview\n\n## Recent changes\n- 2026-04-20 — x.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [b for b in result.blockers if b.check == "missing-recent-changes-h2"]
    assert len(matches) == 1, [b.message for b in result.blockers]


def test_topic_with_recent_changes_h2_no_blocker(tmp_path: Path) -> None:
    """Topic page with ``## Recent changes`` — rule stays quiet."""
    wiki = tmp_path / "wiki"
    page = wiki / "topics" / "foo.md"
    _write_page(
        page,
        "Foo",
        ["raw/x.md"],
        "\nLead paragraph.\n\n## Overview\nBody.\n\n"
        "## Recent changes\n- 2026-04-20 — rolled out.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(b.check != "missing-recent-changes-h2" for b in result.blockers)


def test_non_topic_missing_recent_changes_no_blocker(tmp_path: Path) -> None:
    """Decision / policy / system pages are not subject to this rule."""
    wiki = tmp_path / "wiki"
    page = wiki / "decisions" / "scale-foo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: Scale Foo\npage_type: decision\nstatus: active\n"
        "sources:\n- raw/x.md\n---\n\n"
        "Lead paragraph.\n\n## Overview\nBody.\n",
        encoding="utf-8",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(b.check != "missing-recent-changes-h2" for b in result.blockers)


# --- #182 — Summary-stale-date (blocker) ----------------------------------


def test_summary_stale_when_recent_changes_has_newer_entry(tmp_path: Path) -> None:
    """``## Recent changes`` bullet on 2026-04-20; Summary says "launched
    on 2026-01-15". Blocker fires because the Summary is demonstrably
    out of sync with the page's own history."""
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
        "\nLaunched on 2026-01-15 — initial rollout.\n\n"
        "## Recent changes\n- 2026-04-20 — scaled to 100%.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    matches = [b for b in result.blockers if b.check == "summary-stale-date"]
    assert len(matches) == 1, [b.message for b in result.blockers]
    assert "2026-04-20" in matches[0].message
    assert "2026-01-15" in matches[0].message


def test_summary_stale_date_quiet_when_summary_has_newest_date(tmp_path: Path) -> None:
    """Summary explicitly cites 2026-04-20 → no date disagreement."""
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
        "\nScaled to 100% on 2026-04-20.\n\n## Recent changes\n- 2026-04-20 — scaled to 100%.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(b.check != "summary-stale-date" for b in result.blockers)


def test_summary_stale_date_quiet_when_no_date_in_summary(tmp_path: Path) -> None:
    """The blocker needs explicit date evidence on BOTH sides — Summary
    without a date is handled by the existing warning-level rule."""
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
        "\nThe system is currently in beta.\n\n## Recent changes\n- 2026-04-20 — scaled to 100%.\n",
    )
    result = critique_pages([page], wiki, tmp_path)
    assert all(b.check != "summary-stale-date" for b in result.blockers)
