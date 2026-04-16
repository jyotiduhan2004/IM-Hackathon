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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CONFIG = REPO_ROOT / "tests" / "fixtures" / "nav_fixture" / "mkdocs.yml"


def _run_mkdocs_build(build_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "mkdocs", "build", "-f", str(FIXTURE_CONFIG), "-d", str(build_dir)],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the fixture once per module — each test just asserts over the output."""
    build_dir = tmp_path_factory.mktemp("nav-build")
    _run_mkdocs_build(build_dir)
    return build_dir


def test_fixture_build_succeeds(built_site: Path) -> None:
    assert built_site.exists()


def test_home_renders_products_and_platforms_label(built_site: Path) -> None:
    """The UI rename from 'Systems' to 'Products & Platforms' is visible.

    MkDocs Material emits the literal ``&`` in the rendered sidebar (it
    does not HTML-encode it), so we accept either form for robustness in
    case a future theme switch starts escaping.
    """
    home_html = (built_site / "home" / "index.html").read_text(encoding="utf-8")
    assert "Products & Platforms" in home_html or "Products &amp; Platforms" in home_html


def test_about_page_is_emitted(built_site: Path) -> None:
    assert (built_site / "about" / "index.html").exists()


def test_all_top_level_landing_pages_emitted(built_site: Path) -> None:
    expected = [
        built_site / "home" / "index.html",
        built_site / "topics" / "index.html",
        built_site / "systems" / "index.html",
        built_site / "policies" / "index.html",
        built_site / "log" / "index.html",
        built_site / "about" / "index.html",
    ]
    missing = [p for p in expected if not p.exists()]
    assert not missing, f"missing rendered pages: {missing}"
