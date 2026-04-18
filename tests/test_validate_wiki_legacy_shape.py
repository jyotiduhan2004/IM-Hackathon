"""Legacy-shape checks in scripts/validate_wiki.py.

Phase 0 surfaces pages still on pre-north-star ontology as warnings (tolerant
on reads) and exposes a `--strict-new-ontology` flag that promotes them to
errors (strict on writes, wired into compile_all's post-batch hook in Phase
3). Both paths are exercised here:

- Default mode: warn-only. A legacy `status: current` / `page_type: entity` /
  `wiki/entities/` page passes validation but emits a warning.
- Strict mode: errors. Same inputs now fail validation.
- North-star shape (`status: active` + `page_type: person` under
  `wiki/people/`) must stay silent in both modes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests._script_loader import load_script

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


validator = load_script("validate_wiki")


def _write_page(
    cat_dir: Path,
    slug: str,
    *,
    page_type: str = "topic",
    status: str = "active",
    extra_fm: str = "",
) -> Path:
    """Write a minimal wiki page with the requested frontmatter.

    Keeps bodies non-empty so `validate_page`'s empty-body ERROR doesn't
    contaminate the legacy-shape assertions.
    """
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        f"page_type: {page_type}",
        f"status: {status}",
    ]
    if extra_fm:
        lines.append(extra_fm.rstrip())
    lines += ["---", "", f"Body for {slug}.", ""]
    path = cat_dir / f"{slug}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Task 1 — warn on legacy shape by default
# ---------------------------------------------------------------------------


def test_legacy_status_current_warns_by_default(mini_wiki: Path) -> None:
    """status=current → warning, but page passes validation (zero errors)."""
    _write_page(mini_wiki / "topics", "legacy-topic", page_type="topic", status="current")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=False)
    assert errors == []
    checks = [w.check for w in warnings]
    assert checks == ["legacy-status"]
    only = warnings[0]
    assert only.page.name == "legacy-topic.md"
    assert "active/archived/superseded" in only.reason


def test_legacy_status_contested_warns_by_default(mini_wiki: Path) -> None:
    """status=contested is legacy too — north-star uses active/archived/superseded."""
    _write_page(mini_wiki / "topics", "contested-topic", page_type="topic", status="contested")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=False)
    assert errors == []
    checks = [w.check for w in warnings]
    assert checks == ["legacy-status"]


def test_legacy_page_type_entity_warns_by_default(mini_wiki: Path) -> None:
    """page_type=entity → warning; page in entities/ also fires legacy-entities-path."""
    _write_page(mini_wiki / "entities", "legacy-person", page_type="entity", status="active")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=False)
    assert errors == []
    checks = {w.check for w in warnings}
    # An entities/ page with page_type=entity triggers both signals — legacy
    # on two axes (frontmatter + directory).
    assert checks == {"legacy-page-type-entity", "legacy-entities-path"}


def test_legacy_entities_path_warns_by_default(mini_wiki: Path) -> None:
    """A page under wiki/entities/ fires legacy-entities-path even when page_type=person."""
    _write_page(mini_wiki / "entities", "already-migrated-fm", page_type="person", status="active")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=False)
    assert errors == []
    checks = [w.check for w in warnings]
    assert checks == ["legacy-entities-path"]
    assert "wiki/people/" in warnings[0].reason


# ---------------------------------------------------------------------------
# Task 2 — strict mode promotes warnings to errors
# ---------------------------------------------------------------------------


def test_strict_new_ontology_promotes_status_to_error(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "legacy-topic", page_type="topic", status="current")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=True)
    assert warnings == []
    assert len(errors) == 1
    assert errors[0].page.name == "legacy-topic.md"
    assert "legacy-status" in errors[0].reason


def test_strict_new_ontology_promotes_page_type_to_error(mini_wiki: Path) -> None:
    # Put it under topics/ so we don't also collect legacy-entities-path.
    _write_page(mini_wiki / "topics", "wrong-type", page_type="entity", status="active")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=True)
    assert warnings == []
    # Only the frontmatter axis fires here.
    assert len(errors) == 1
    assert "legacy-page-type-entity" in errors[0].reason


def test_strict_new_ontology_promotes_entities_path_to_error(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "entities", "fm-ok-path-legacy", page_type="person", status="active")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=True)
    assert warnings == []
    assert len(errors) == 1
    assert "legacy-entities-path" in errors[0].reason


def test_strict_mode_run_returns_nonempty_errors(mini_wiki: Path) -> None:
    """Full run() with strict_new_ontology=True surfaces legacy pages as errors.

    Verifies the flag plumbs through the top-level entry point, not just the
    leaf check. Guards against the regression where strict mode warnings
    still got appended to warnings only (and thus didn't affect exit code).
    """
    _write_page(mini_wiki / "topics", "legacy-topic", page_type="topic", status="current")
    errors, warnings = validator.run(mini_wiki, strict_new_ontology=True)
    reasons = [e.reason for e in errors if "legacy-status" in e.reason]
    assert reasons, f"expected a legacy-status error, got errors={errors!r} warnings={warnings!r}"


# ---------------------------------------------------------------------------
# Task 3 — north-star shape passes clean in both modes
# ---------------------------------------------------------------------------


def test_north_star_person_page_has_no_legacy_warnings(mini_wiki: Path) -> None:
    """Clean shape: page_type=person + status=active under wiki/people/."""
    _write_page(mini_wiki / "people", "jane-doe", page_type="person", status="active")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=False)
    assert errors == []
    assert warnings == []


def test_north_star_person_page_passes_strict(mini_wiki: Path) -> None:
    """Strict mode must not flag north-star pages either."""
    _write_page(mini_wiki / "people", "jane-doe", page_type="person", status="active")
    errors, warnings = validator.check_legacy_shape(mini_wiki, strict=True)
    assert errors == []
    assert warnings == []


def test_north_star_topic_page_passes_both_modes(mini_wiki: Path) -> None:
    """Topics on status=active/archived/superseded with page_type=topic stay silent."""
    for slug, status in (
        ("alpha", "active"),
        ("bravo", "archived"),
        ("charlie", "superseded"),
    ):
        _write_page(mini_wiki / "topics", slug, page_type="topic", status=status)
    for strict in (False, True):
        errors, warnings = validator.check_legacy_shape(mini_wiki, strict=strict)
        assert errors == [], f"strict={strict}: unexpected errors {errors!r}"
        assert warnings == [], f"strict={strict}: unexpected warnings {warnings!r}"


# ---------------------------------------------------------------------------
# CLI integration — flag surface must stay stable and additive
# ---------------------------------------------------------------------------


def _run_cli(wiki_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke scripts/validate_wiki.py against `wiki_dir` via subprocess."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_wiki.py"), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "WIKI_DIR": str(wiki_dir)},
        check=False,
    )


def test_cli_default_exits_zero_on_legacy_only(mini_wiki: Path) -> None:
    """A wiki with ONLY legacy-shape violations exits 0 by default (warn-only)."""
    _write_page(mini_wiki / "topics", "legacy-topic", page_type="topic", status="current")
    result = _run_cli(mini_wiki)
    assert result.returncode == 0, (
        f"expected exit 0 (warn-only), got {result.returncode}\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "legacy-status" in combined


def test_cli_strict_new_ontology_exits_nonzero(mini_wiki: Path) -> None:
    """`--strict-new-ontology` must exit 1 when legacy pages exist."""
    _write_page(mini_wiki / "topics", "legacy-topic", page_type="topic", status="current")
    result = _run_cli(mini_wiki, "--strict-new-ontology")
    assert result.returncode == 1, (
        f"expected exit 1 under --strict-new-ontology, got {result.returncode}\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "legacy-topic.md" in combined
    assert "legacy-status" in combined


def test_cli_strict_flag_default_off(mini_wiki: Path) -> None:
    """Sanity — the flag's default (OFF) must not change existing behavior.

    A clean wiki with just a north-star page must exit 0 whether or not the
    flag is mentioned.
    """
    _write_page(mini_wiki / "people", "jane-doe", page_type="person", status="active")
    assert _run_cli(mini_wiki).returncode == 0
    assert _run_cli(mini_wiki, "--strict-new-ontology").returncode == 0
