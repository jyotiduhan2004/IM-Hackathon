"""Entity-page WARN-level checks in scripts/validate_wiki.py.

These checks must never contribute to the exit code — they exist to surface
legacy pages (display-name slugs, missing `email:`) without blocking the
compile pipeline. A tmp_path-backed mini-wiki keeps the assertions self-contained.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_validator():
    """Import scripts/validate_wiki.py as a module (not on PYTHONPATH by default).

    The module must be registered in sys.modules before exec so dataclass()
    can look up the owning module to resolve forward references.
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


def _write_entity(
    entities_dir: Path,
    slug: str,
    *,
    email: str | None = None,
    extra_fm: str = "",
) -> Path:
    """Write a minimal valid entity page. Pass email=None to omit the field."""
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        "page_type: entity",
        "status: current",
    ]
    if email is not None:
        lines.append(f"email: {email}")
    if extra_fm:
        lines.append(extra_fm.rstrip())
    lines += ["---", "", f"Body for {slug}.", ""]
    path = entities_dir / f"{slug}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture
def mini_wiki(tmp_path: Path) -> Path:
    """A wiki/ dir with just an entities/ subdirectory ready for fixture pages."""
    wiki = tmp_path / "wiki"
    (wiki / "entities").mkdir(parents=True)
    return wiki


def _run_entity_checks(wiki: Path) -> list:
    return validator.check_entity_identity(wiki)


def test_canonical_entity_produces_no_warning(mini_wiki: Path) -> None:
    """Valid email + canonical slug (matching email_to_slug) → zero warnings."""
    if not validator._HAS_ENTITY_HELPERS:
        pytest.skip("src.compile.entities not importable — slug-canonicality unknown")
    from src.compile.entities import email_to_slug

    email = "amit@indiamart.com"
    _write_entity(mini_wiki / "entities", email_to_slug(email), email=email)
    assert _run_entity_checks(mini_wiki) == []


def test_missing_email_produces_warning(mini_wiki: Path) -> None:
    _write_entity(mini_wiki / "entities", "amit-agarwal", email=None)
    warnings = _run_entity_checks(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "entity-missing-email"
    assert warnings[0].page.name == "amit-agarwal.md"


def test_invalid_email_produces_warning(mini_wiki: Path) -> None:
    _write_entity(mini_wiki / "entities", "bad-entity", email="not-an-email")
    warnings = _run_entity_checks(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "entity-invalid-email"
    assert "not-an-email" in warnings[0].reason


def test_display_name_slug_produces_mismatch_warning(mini_wiki: Path) -> None:
    """Legacy `amit-agarwal.md` with email set → entity-slug-mismatch.

    Only fires when src.compile.entities is importable (i.e. W0 shipped).
    Otherwise the validator can't compute the canonical slug; skip cleanly.
    """
    if not validator._HAS_ENTITY_HELPERS:
        pytest.skip("src.compile.entities not importable — slug check is a no-op")

    _write_entity(mini_wiki / "entities", "amit-agarwal", email="amit@indiamart.com")
    warnings = _run_entity_checks(mini_wiki)
    checks = {w.check for w in warnings}
    assert "entity-slug-mismatch" in checks
    mismatch = next(w for w in warnings if w.check == "entity-slug-mismatch")
    assert mismatch.page.name == "amit-agarwal.md"


def test_warnings_do_not_contribute_to_errors(mini_wiki: Path) -> None:
    """run() splits errors from warnings — warnings must never bleed into errors.

    A `status: current` page in `wiki/entities/` with `page_type: entity` is
    legacy on every axis, so Phase 0 fires entity-missing-email plus the
    three legacy-shape warnings. The important invariant here is that NONE
    of them contribute to `errors`.
    """
    _write_entity(mini_wiki / "entities", "amit-agarwal", email=None)
    # Other categories exist in real wiki but are empty here; run() should
    # still execute fine and report one warning, zero errors.
    for cat in ("topics", "systems", "policies", "timelines", "conflicts"):
        (mini_wiki / cat).mkdir()
    errors, warnings = validator.run(mini_wiki)
    assert errors == []
    checks = {w.check for w in warnings}
    assert "entity-missing-email" in checks


# ---------------------------------------------------------------------------
# people/ directory acceptance (C1 migration prerequisite)
# ---------------------------------------------------------------------------
#
# `wiki/people/` is the target directory for person pages; during the
# transition it's accepted alongside `wiki/entities/` so neither side of
# the migration breaks the compiler.


def test_people_directory_accepts_person_page_type(tmp_path: Path) -> None:
    """A page in wiki/people/ with `page_type: person` validates clean."""
    wiki = tmp_path / "wiki"
    people = wiki / "people"
    people.mkdir(parents=True)
    page = people / "test-person.md"
    page.write_text(
        '---\ntitle: "Test Person"\npage_type: person\nstatus: active\nsources: []\n'
        "---\n\nTest person body.\n",
        encoding="utf-8",
    )
    errs = validator.validate_page(page)
    assert errs == [], f"expected clean, got: {[(e.page.name, e.reason) for e in errs]}"


def test_people_directory_rejects_wrong_page_type(tmp_path: Path) -> None:
    """A page in wiki/people/ but `page_type: entity` mismatches the folder."""
    wiki = tmp_path / "wiki"
    people = wiki / "people"
    people.mkdir(parents=True)
    page = people / "wrong-type.md"
    page.write_text(
        '---\ntitle: "Wrong Type"\npage_type: entity\nstatus: active\n---\n\nBody.\n',
        encoding="utf-8",
    )
    errs = validator.validate_page(page)
    assert any("expected 'person'" in e.reason for e in errs), (
        f"expected mismatch error, got: {[(e.page.name, e.reason) for e in errs]}"
    )
