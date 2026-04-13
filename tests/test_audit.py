"""Unit tests for scripts/audit.py helpers.

Scope: we only test `count_entity_shapes` here — the reporting sections
hit the database and call `validate_wiki`, which are covered by the
end-to-end smoke (see PR description). These tests fake `email_to_slug`
with a simple lambda so they don't depend on W0 being merged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import count_entity_shapes  # noqa: E402


def _fake_email_to_slug(email: str) -> str:
    """Mimic src.compile.entities.email_to_slug just enough for tests.

    We don't want the real function here — the test should pass whether
    or not W0 has merged.
    """
    if "@" not in email:
        raise ValueError(f"bad email: {email!r}")
    local, _, domain = email.strip().lower().partition("@")
    return f"{local}-{domain}".replace(".", "-").replace("+", "-")


def _write_entity(
    entities_dir: Path, stem: str, frontmatter_lines: list[str], body: str = "body\n"
) -> Path:
    """Write a minimal entity page with the given frontmatter lines."""
    fm_block = "\n".join(frontmatter_lines)
    page = entities_dir / f"{stem}.md"
    page.write_text(f"---\n{fm_block}\n---\n\n{body}", encoding="utf-8")
    return page


@pytest.fixture
def entities_dir(tmp_path: Path) -> Path:
    d = tmp_path / "entities"
    d.mkdir(parents=True)
    return d


def test_count_entity_shapes_three_buckets(entities_dir: Path) -> None:
    """One of each shape — the happy-path counting contract."""
    # email-canonical: stem matches fake_email_to_slug("amit@indiamart.com")
    _write_entity(
        entities_dir,
        stem="amit-indiamart-com",
        frontmatter_lines=[
            "title: Amit",
            "page_type: entity",
            "status: current",
            "email: amit@indiamart.com",
        ],
    )
    # legacy-displayname: has email but stem doesn't match
    _write_entity(
        entities_dir,
        stem="abhinav-kaushik",
        frontmatter_lines=[
            "title: Abhinav Kaushik",
            "page_type: entity",
            "status: current",
            "email: abhinav.kaushik@indiamart.com",
        ],
    )
    # no-email-frontmatter: no `email:` field at all
    _write_entity(
        entities_dir,
        stem="some-org",
        frontmatter_lines=[
            "title: Some Org",
            "page_type: entity",
            "status: current",
        ],
    )

    counts = count_entity_shapes(entities_dir, _fake_email_to_slug)

    assert counts == {
        "email-canonical": 1,
        "legacy-displayname": 1,
        "no-email-frontmatter": 1,
    }


def test_count_entity_shapes_missing_dir_returns_zeros(tmp_path: Path) -> None:
    """If entities/ doesn't exist yet, return all-zero counts (don't crash)."""
    counts = count_entity_shapes(tmp_path / "nope", _fake_email_to_slug)
    assert counts == {
        "email-canonical": 0,
        "legacy-displayname": 0,
        "no-email-frontmatter": 0,
    }


def test_count_entity_shapes_email_is_not_string(entities_dir: Path) -> None:
    """`email: 123` — not a string — counted as no-email-frontmatter."""
    _write_entity(
        entities_dir,
        stem="weird",
        frontmatter_lines=[
            "title: Weird",
            "page_type: entity",
            "status: current",
            "email: 12345",
        ],
    )
    counts = count_entity_shapes(entities_dir, _fake_email_to_slug)
    # yaml parses `email: 12345` as int; counter treats non-str as missing.
    assert counts["no-email-frontmatter"] == 1
    assert counts["email-canonical"] == 0
    assert counts["legacy-displayname"] == 0


def test_count_entity_shapes_email_to_slug_raises(entities_dir: Path) -> None:
    """If email_to_slug throws (garbage email), bucket as legacy-displayname."""
    _write_entity(
        entities_dir,
        stem="garbage",
        frontmatter_lines=[
            "title: Garbage",
            "page_type: entity",
            "status: current",
            "email: not-an-email",
        ],
    )
    counts = count_entity_shapes(entities_dir, _fake_email_to_slug)
    assert counts["legacy-displayname"] == 1


def test_count_entity_shapes_fn_none_falls_back_to_legacy(entities_dir: Path) -> None:
    """If caller passes None for the slug fn, pages with emails still count
    as legacy-displayname (shouldn't happen in practice — caller gates)."""
    _write_entity(
        entities_dir,
        stem="amit-indiamart-com",
        frontmatter_lines=[
            "title: Amit",
            "page_type: entity",
            "status: current",
            "email: amit@indiamart.com",
        ],
    )
    counts = count_entity_shapes(entities_dir, None)
    assert counts == {
        "email-canonical": 0,
        "legacy-displayname": 1,
        "no-email-frontmatter": 0,
    }
