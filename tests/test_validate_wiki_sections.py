"""Section-template checks in scripts/validate_wiki.py.

Topic/system/policy pages benefit from the suggested H2 sections defined in
the Phase 1 wiki IA plan. Default: warn-only (informational);
`--strict-sections` promotes missing sections to errors so CI can enforce
drift. Entity and timeline pages are deliberately not checked — their
templates are out of scope for this validator.

v11-U7: error code is `suggested-sections-missing` (was `{topic,system,
policy}-sections`); `check_required_sections` was renamed to
`check_suggested_sections`. Vocabulary-only — behavior unchanged.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_validator():  # type: ignore[no-untyped-def]
    """Import scripts/validate_wiki.py as a module (not on PYTHONPATH by default)."""
    spec = importlib.util.spec_from_file_location(
        "validate_wiki", REPO_ROOT / "scripts" / "validate_wiki.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_wiki"] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()

FIXTURE_WIKI = REPO_ROOT / "tests" / "fixtures" / "validate_sections_fixture" / "wiki"


def test_warns_on_missing_sections_by_default() -> None:
    """Default mode: bad-topic + bad-system each produce 1 warning, 0 errors."""
    errors, warnings = validator.check_suggested_sections(FIXTURE_WIKI)
    assert errors == []
    assert len(warnings) == 2
    by_page = {w.page.name: w for w in warnings}
    assert "bad-topic.md" in by_page
    assert "bad-system.md" in by_page
    assert by_page["bad-topic.md"].check == "suggested-sections-missing"
    assert by_page["bad-system.md"].check == "suggested-sections-missing"
    # bad-topic omits "Open questions" and "References"
    assert "Open questions" in by_page["bad-topic.md"].reason
    assert "References" in by_page["bad-topic.md"].reason
    # bad-system omits "Related pages"
    assert "Related pages" in by_page["bad-system.md"].reason


def test_strict_promotes_warnings_to_errors() -> None:
    """`strict=True` flips both warnings into errors, none left warning."""
    errors, warnings = validator.check_suggested_sections(FIXTURE_WIKI, strict=True)
    assert warnings == []
    assert len(errors) == 2
    names = {e.page.name for e in errors}
    assert names == {"bad-topic.md", "bad-system.md"}


def test_entity_and_timeline_pages_are_never_checked() -> None:
    """Entity/timeline pages live in categories not in SUGGESTED_SECTIONS —
    no warning or error should mention them, ever."""
    errors, warnings = validator.check_suggested_sections(FIXTURE_WIKI)
    all_names = [e.page.name for e in errors] + [w.page.name for w in warnings]
    assert "jane-doe.md" not in all_names
    assert "migration.md" not in all_names


def test_case_insensitive_substring_match() -> None:
    """`good-topic.md` uses lowercase `## current state` — must still count.

    Suggested sections are checked via `substring.lower() in heading.lower()`,
    so renames like `## Current state (2026)` still satisfy `Current state`.
    """
    errors, warnings = validator.check_suggested_sections(FIXTURE_WIKI)
    flagged = {w.page.name for w in warnings} | {e.page.name for e in errors}
    assert "good-topic.md" not in flagged
    assert "good-system.md" not in flagged
    assert "good-policy.md" not in flagged


def test_headings_inside_fenced_code_blocks_do_not_count(tmp_path: Path) -> None:
    """Codex: `## Summary` in a code snippet must NOT satisfy the Summary check.

    Otherwise a template-drift page that embeds the old template inside a
    ```` ```md ```` block would pass `--strict-sections` despite having no
    real section headings.
    """
    topics = tmp_path / "topics"
    topics.mkdir(parents=True)
    (topics / "only-fenced.md").write_text(
        "---\ntitle: Fenced\npage_type: topic\nstatus: current\n---\n\n"
        "Intro paragraph.\n\n"
        "```md\n## Summary\n## Current state\n## Recent activity\n```\n",
        encoding="utf-8",
    )

    errors, _warnings = validator.check_suggested_sections(tmp_path, strict=True)
    flagged = {e.page.name for e in errors}
    assert "only-fenced.md" in flagged


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run validate_wiki.py against the fixture wiki via subprocess.

    Inherits parent env so PATH/PYTHONPATH flow through; WIKI_DIR points the
    validator at our fixture instead of the real `wiki/`.
    """
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_wiki.py"), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "WIKI_DIR": str(FIXTURE_WIKI)},
        check=False,
    )


def test_cli_strict_sections_flag_exits_nonzero() -> None:
    """Subprocess CLI test: `validate_wiki.py --strict-sections` must exit 1."""
    result = _run_cli("--strict-sections")
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\nSTDOUT: {result.stdout}\n"
        f"STDERR: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "bad-topic.md" in combined
    assert "bad-system.md" in combined


def test_cli_without_strict_flag_exits_zero() -> None:
    """Sanity check — no errors means exit 0 even when warnings exist."""
    result = _run_cli()
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\nSTDOUT: {result.stdout}\n"
        f"STDERR: {result.stderr}"
    )
