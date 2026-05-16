"""Tests for the pymarkdown wrapper check in validate_wiki.py.

Pymarkdown is a dev dep; we test the parsing + config wiring, not
the pymarkdown library itself. The real signal (MD024 catching
cross-level heading dups) is validated via a fixture page that
intentionally has an H3 + H2 with the same title (the photosearch
bug shape)."""

from __future__ import annotations

import importlib.util
import shutil
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


def _make_page(wiki: Path, category: str, slug: str, body: str) -> Path:
    (wiki / category).mkdir(parents=True, exist_ok=True)
    p = wiki / category / f"{slug}.md"
    p.write_text(
        "---\ntitle: T\npage_type: topic\nstatus: active\n---\n\n" + body,
        encoding="utf-8",
    )
    return p


class TestCheckMarkdownlint:
    def test_returns_empty_when_pymarkdown_not_installed(
        self,
        tmp_path: Path,
        monkeypatch,  # type: ignore[no-untyped-def]
    ) -> None:
        # Simulate a dev-env without pymarkdown: make shutil.which
        # return None for it. The check must silently no-op rather
        # than fail the validator.
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        _make_page(tmp_path, "topics", "a", "## Dup\n\n## Dup\n")
        warnings = validator.check_markdownlint(tmp_path)
        assert warnings == []

    def test_catches_cross_level_duplicate_heading(self, tmp_path: Path) -> None:
        # Skip if pymarkdown isn't on PATH in CI. The wrapper already
        # no-ops in that case; this test only runs when the dev dep is
        # actually installed (local dev + the ruff/mypy CI job).
        if shutil.which("pymarkdown") is None:
            import pytest

            pytest.skip("pymarkdown not installed in this env")
        # H3 and H2 with the same title — MD024 catches, our H2-only
        # `check_duplicate_headings` does not.
        body = (
            "## Key Stakeholder Feedback\n\n"
            "### Feedback Frequency Design (Jan 13, 2026)\n\n"
            "Some content about frequency.\n\n"
            "## Other section\n\n"
            "## Feedback Frequency Design (Jan 13, 2026)\n\n"
            "Duplicate content.\n"
        )
        _make_page(tmp_path, "topics", "photosearch-like", body)
        warnings = validator.check_markdownlint(tmp_path)
        md024 = [w for w in warnings if w.check == "mdlint-md024"]
        assert len(md024) >= 1, f"expected ≥1 MD024 warning, got: {warnings}"
        # The warning should name the duplicate line so the operator
        # can find it.
        assert (
            "frequency design" in md024[0].reason.lower()
            or "multiple headings" in md024[0].reason.lower()
        )

    def test_silent_on_clean_page(self, tmp_path: Path) -> None:
        if shutil.which("pymarkdown") is None:
            import pytest

            pytest.skip("pymarkdown not installed")
        _make_page(
            tmp_path,
            "topics",
            "clean",
            "## Current state\n\nsome prose\n\n## Recent changes\n\n- **2026-01-13** — foo\n",
        )
        warnings = validator.check_markdownlint(tmp_path)
        # MD024 / MD026 / MD029 should not fire on a clean canonical page.
        blockers = [
            w for w in warnings if w.check in {"mdlint-md024", "mdlint-md026", "mdlint-md029"}
        ]
        assert blockers == [], blockers

    def test_no_wiki_categories_returns_empty(self, tmp_path: Path) -> None:
        # Empty wiki dir → the check should return early without
        # subprocess overhead.
        warnings = validator.check_markdownlint(tmp_path)
        assert warnings == []
