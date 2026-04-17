"""Bug F validator check: H2 section titles must not bake in dates,
person names, or email-subject attribution. Parallel H2s per email are
the failure mode the `<section_titles>` prompt rule + Cycle 6/7
measurements are trying to drive down."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_validator():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "validate_wiki", REPO_ROOT / "scripts" / "validate_wiki.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_wiki"] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()
is_dated = validator._is_dated_h2


class TestIsDatedH2:
    def test_iso_date_in_parens_flagged(self) -> None:
        assert is_dated("Bug Status (as of 2026-01-13)")

    def test_attribution_with_iso_date_flagged(self) -> None:
        assert is_dated("QA Testing Results (Rucha Patil, 2026-01-13)")

    def test_month_name_full_flagged(self) -> None:
        assert is_dated("Decision: Scale to 100% (January 7, 2026)")

    def test_month_name_abbreviated_flagged(self) -> None:
        assert is_dated("A/B Test Results (Jan 13, 2026)")

    def test_parens_just_year_flagged(self) -> None:
        assert is_dated("Testing Results (2026)")

    def test_date_range_with_month_flagged(self) -> None:
        assert is_dated("A/B Test Results (14 Dec - 3 Jan)")

    def test_plain_canonical_h2_not_flagged(self) -> None:
        assert not is_dated("Testing results")
        assert not is_dated("Current state")
        assert not is_dated("Recent changes")

    def test_quarter_reference_not_flagged(self) -> None:
        # "Q4 2025 results" is an intentional slice, not a per-email
        # parallel section. No parens → no flag.
        assert not is_dated("Q4 2025 results")

    def test_version_in_parens_not_flagged(self) -> None:
        # "(v2)" has parens but no date/month → not flagged.
        assert not is_dated("Current rules (v2)")


class TestCheckDatedH2Sections:
    def _make_wiki(self, tmp_path: Path, page_body: str, category: str = "topics") -> Path:
        wiki = tmp_path / "wiki"
        (wiki / category).mkdir(parents=True)
        (wiki / category / "page.md").write_text(
            "---\ntitle: Test\npage_type: topic\nstatus: active\n---\n\n" + page_body,
            encoding="utf-8",
        )
        return wiki

    def test_warns_on_dated_h2_in_topic(self, tmp_path: Path) -> None:
        wiki = self._make_wiki(
            tmp_path,
            "## Testing Results (Rucha Patil, 2026-01-13)\n\nfoo\n",
        )
        warnings = validator.check_dated_h2_sections(wiki)
        assert len(warnings) == 1
        assert warnings[0].check == "dated-h2-section"
        assert "Rucha Patil" in warnings[0].reason

    def test_silent_on_canonical_h2(self, tmp_path: Path) -> None:
        wiki = self._make_wiki(tmp_path, "## Testing results\n\n- **2026-01-13** — foo\n")
        assert validator.check_dated_h2_sections(wiki) == []

    def test_fenced_code_block_does_not_false_positive(self, tmp_path: Path) -> None:
        # An H2 inside a ``` fence is not a real page heading.
        wiki = self._make_wiki(
            tmp_path,
            "## Testing results\n\n```\n## Bug Report (Jan 16, 2026)\n```\n",
        )
        assert validator.check_dated_h2_sections(wiki) == []

    def test_multiple_dated_h2s_reported_together(self, tmp_path: Path) -> None:
        wiki = self._make_wiki(
            tmp_path,
            "## Findings (Dec 30, 2025)\n\na\n\n## Feedback (Jan 12, 2026)\n\nb\n",
        )
        warnings = validator.check_dated_h2_sections(wiki)
        assert len(warnings) == 1
        # Both H2s appear in the same warning so an operator sees the
        # cluster, not N parallel warnings.
        assert "Findings (Dec 30, 2025)" in warnings[0].reason
        assert "Feedback (Jan 12, 2026)" in warnings[0].reason
