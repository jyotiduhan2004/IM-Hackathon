"""Smoke test for the explicit MkDocs navigation.

Builds the synthetic fixture under ``tests/fixtures/nav_fixture/`` with
``uv run mkdocs build`` and asserts:

* the build completes without raising
* the generated home page references the ``Products & Platforms`` nav
  label (verifying the UI rename of ``Systems``)
* the ``about/`` landing page is emitted

The fixture mirrors the real ``mkdocs.yml`` nav tree but with a self-
contained ``docs_dir`` so the test doesn't depend on the (gitignored) real
wiki corpus.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_CONFIG = REPO_ROOT / "tests" / "fixtures" / "nav_fixture" / "mkdocs.yml"


def _run_mkdocs_build(build_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "mkdocs", "build", "-f", str(FIXTURE_CONFIG), "-d", str(build_dir)],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_fixture_build_succeeds(tmp_path: Path) -> None:
    """The synthetic fixture builds cleanly."""
    build_dir = tmp_path / "build-nav"
    result = _run_mkdocs_build(build_dir)
    assert build_dir.exists(), result.stderr


def test_home_renders_products_and_platforms_label(tmp_path: Path) -> None:
    """The UI rename from 'Systems' to 'Products & Platforms' is visible.

    MkDocs Material emits the literal ``&`` in the rendered sidebar (it
    does not HTML-encode it), so we accept either form for robustness in
    case a future theme switch starts escaping.
    """
    build_dir = tmp_path / "build-nav"
    _run_mkdocs_build(build_dir)
    home_html = (build_dir / "home" / "index.html").read_text(encoding="utf-8")
    assert "Products & Platforms" in home_html or "Products &amp; Platforms" in home_html


def test_about_page_is_emitted(tmp_path: Path) -> None:
    """``About`` landing page reaches the generated site."""
    build_dir = tmp_path / "build-nav"
    _run_mkdocs_build(build_dir)
    assert (build_dir / "about" / "index.html").exists()


def test_all_top_level_landing_pages_emitted(tmp_path: Path) -> None:
    """Every top-level nav entry has its landing page rendered."""
    build_dir = tmp_path / "build-nav"
    _run_mkdocs_build(build_dir)
    expected = [
        build_dir / "home" / "index.html",
        build_dir / "topics" / "index.html",
        build_dir / "systems" / "index.html",
        build_dir / "policies" / "index.html",
        build_dir / "entities" / "index.html",
        build_dir / "log" / "index.html",
        build_dir / "about" / "index.html",
    ]
    missing = [p for p in expected if not p.exists()]
    assert not missing, f"missing rendered pages: {missing}"
