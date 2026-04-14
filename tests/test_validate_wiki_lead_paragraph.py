"""Tests for the new validator checks added alongside the light-format work:

- **Fence count** (ERROR): `---` must appear exactly twice as a line.
  Catches the `tech-security-team.md` corruption pattern from the
  2026-04-14 wiki quality audit.
- **Lead paragraph** (WARN): topic/policy pages should open with ≥2
  sentences before the first H2. Warning only — legacy pages need a
  grace window so CI doesn't break on day one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_validator():  # type: ignore[no-untyped-def]
    """Import scripts/validate_wiki.py as a module.

    Mirrors the pattern in tests/test_validate_wiki_sections.py —
    register in sys.modules before exec so dataclasses resolve forward
    references when the module isn't on PYTHONPATH.
    """
    spec = importlib.util.spec_from_file_location(
        "validate_wiki", REPO_ROOT / "scripts" / "validate_wiki.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_wiki"] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


@pytest.fixture
def mini_wiki(tmp_path: Path) -> Path:
    """A wiki/ with topics/ + policies/ subdirectories for fixture pages."""
    wiki = tmp_path / "wiki"
    for cat in ("topics", "entities", "systems", "policies", "timelines", "conflicts"):
        (wiki / cat).mkdir(parents=True)
    return wiki


# --- Fence count check -------------------------------------------------


def test_fence_count_error_on_triple_fence(mini_wiki: Path) -> None:
    """A page with 3 `---` lines errors — mirrors tech-security-team.md corruption.

    The first block parses as valid YAML, so the old validator missed this
    failure mode. The new fence count check catches it.
    """
    page = mini_wiki / "topics" / "corrupt.md"
    page.write_text(
        "---\n"
        "title: Corrupt\n"
        "page_type: topic\n"
        "status: current\n"
        "---\n\n"
        "Body line.\n\n"
        "---\n",  # stray third fence, mirroring the audit case
        encoding="utf-8",
    )
    errors = validator.validate_page(page)
    reasons = [e.reason for e in errors]
    assert any(
        "expected 2 --- fences" in r for r in reasons
    ), f"fence-count error missing from {reasons!r}"
    # Specifically mentions the actual count found (3).
    assert any("found 3" in r for r in reasons)


def test_fence_count_ok_on_two_fences(mini_wiki: Path) -> None:
    """Normal two-fence page must NOT produce a fence-count error."""
    page = mini_wiki / "topics" / "normal.md"
    page.write_text(
        "---\n"
        "title: Normal\n"
        "page_type: topic\n"
        "status: current\n"
        "---\n\n"
        "A normal topic page. Second sentence for completeness.\n\n"
        "## Summary\n\nBody.\n",
        encoding="utf-8",
    )
    errors = validator.validate_page(page)
    reasons = [e.reason for e in errors]
    assert not any(
        "fences" in r for r in reasons
    ), f"unexpected fence-count error on a valid page: {reasons!r}"


# --- Lead paragraph check ----------------------------------------------


def test_lead_paragraph_warn_when_missing(mini_wiki: Path) -> None:
    """Topic page that opens with `## Overview` (no lead) produces a warning."""
    page = mini_wiki / "topics" / "no-lead.md"
    page.write_text(
        "---\n"
        "title: No Lead\n"
        "page_type: topic\n"
        "status: current\n"
        "---\n\n"
        "## Overview\n\nBody starts here.\n",
        encoding="utf-8",
    )
    warnings = validator.check_lead_paragraph(mini_wiki)
    flagged = {w.page.name for w in warnings if w.check.endswith("-lead-paragraph")}
    assert "no-lead.md" in flagged
    # Warning (not error) — verified by the type.
    only = next(w for w in warnings if w.page.name == "no-lead.md")
    assert only.check == "topic-lead-paragraph"
    assert "lead paragraph" in only.reason


def test_lead_paragraph_ok_when_present(mini_wiki: Path) -> None:
    """Topic page with ≥2 sentences before the first H2 passes — no warning."""
    page = mini_wiki / "topics" / "good-lead.md"
    page.write_text(
        "---\n"
        "title: Good Lead\n"
        "page_type: topic\n"
        "status: current\n"
        "---\n\n"
        "Good Lead is a topic that explains a thing. It has two sentences "
        "before the first heading.\n\n"
        "## Summary\n\nContent.\n",
        encoding="utf-8",
    )
    warnings = validator.check_lead_paragraph(mini_wiki)
    flagged = {w.page.name for w in warnings}
    assert "good-lead.md" not in flagged


def test_lead_paragraph_never_contributes_to_errors(mini_wiki: Path) -> None:
    """run() must keep the lead-paragraph check warning-only."""
    (mini_wiki / "topics" / "no-lead.md").write_text(
        "---\ntitle: No Lead\npage_type: topic\nstatus: current\n---\n\n## Overview\n\nBody.\n",
        encoding="utf-8",
    )
    errors, warnings = validator.run(mini_wiki)
    # No fence errors, no frontmatter errors, etc. — only the lead-paragraph
    # warning should come through.
    assert errors == [], f"lead-paragraph check leaked into errors: {errors!r}"
    lead_warnings = [w for w in warnings if w.check == "topic-lead-paragraph"]
    assert len(lead_warnings) == 1


def test_policy_page_lead_paragraph_check(mini_wiki: Path) -> None:
    """Policy pages are checked too (per the check's page_type loop)."""
    page = mini_wiki / "policies" / "no-lead-policy.md"
    page.write_text(
        "---\n"
        "title: No Lead Policy\n"
        "page_type: policy\n"
        "status: current\n"
        "---\n\n"
        "## Current policy\n\nText.\n",
        encoding="utf-8",
    )
    warnings = validator.check_lead_paragraph(mini_wiki)
    checks = [w.check for w in warnings if w.page.name == "no-lead-policy.md"]
    assert "policy-lead-paragraph" in checks
