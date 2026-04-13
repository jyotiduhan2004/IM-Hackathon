"""Unit tests for scripts/audit_systems_entities.py.

A synthetic wiki fixture lives in `tests/fixtures/audit_wiki/` with three
systems/ pages (one legit, two misclassified humans) and one correctly
placed entity. The audit must flag exactly the two humans in dry-run and,
after --confirm, move them to entities/ without touching `legit-tool.md`.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "audit_wiki" / "wiki"


def _load_audit_module():
    """Import scripts/audit_systems_entities.py directly.

    Mirrors the approach used by test_validate_wiki_entity_checks — the
    `scripts/` directory isn't on PYTHONPATH, so we load the file
    explicitly.
    """
    spec = importlib.util.spec_from_file_location(
        "audit_systems_entities",
        REPO_ROOT / "scripts" / "audit_systems_entities.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_systems_entities"] = module
    spec.loader.exec_module(module)
    return module


audit_mod = _load_audit_module()


def _load_validator():
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
def wiki_copy(tmp_path: Path) -> Path:
    """Copy the fixture wiki into tmp_path so mutations don't touch the repo."""
    dest = tmp_path / "wiki"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest


def test_audit_flags_exactly_two_systems_pages(wiki_copy: Path) -> None:
    """Dry-run: the two humans are flagged, the legit tool is not."""
    flags = audit_mod.audit_systems(wiki_copy / "systems")
    flagged_names = sorted(f.path.name for f in flags)
    assert flagged_names == ["alok-kumar2.md", "deepak-yadav01.md"]
    assert "legit-tool.md" not in flagged_names


def test_audit_reason_identifies_email_field(wiki_copy: Path) -> None:
    flags = audit_mod.audit_systems(wiki_copy / "systems")
    by_name = {f.path.name: f.reason for f in flags}
    assert "has email field" in by_name["alok-kumar2.md"]


def test_audit_reason_identifies_human_slug(wiki_copy: Path) -> None:
    flags = audit_mod.audit_systems(wiki_copy / "systems")
    by_name = {f.path.name: f.reason for f in flags}
    assert "slug looks human" in by_name["deepak-yadav01.md"]


def test_dry_run_exits_nonzero_when_flags_present(wiki_copy: Path) -> None:
    """CLI dry-run should exit 1 to fail CI until someone reviews + moves."""
    runner = CliRunner()
    result = runner.invoke(audit_mod.main, ["--wiki-dir", str(wiki_copy)])
    assert result.exit_code == 1
    assert "alok-kumar2.md" in result.output
    assert "deepak-yadav01.md" in result.output
    # Legit tool must not appear in the flagged output.
    assert "move:" in result.output
    assert "legit-tool.md" not in result.output
    # Files must still be where we put them.
    assert (wiki_copy / "systems" / "alok-kumar2.md").exists()
    assert (wiki_copy / "systems" / "deepak-yadav01.md").exists()


def test_confirm_moves_files_to_entities(wiki_copy: Path) -> None:
    """With --confirm, flagged pages are moved and exit code is 0."""
    runner = CliRunner()
    result = runner.invoke(audit_mod.main, ["--wiki-dir", str(wiki_copy), "--confirm"])
    assert result.exit_code == 0
    # Moved.
    assert not (wiki_copy / "systems" / "alok-kumar2.md").exists()
    assert not (wiki_copy / "systems" / "deepak-yadav01.md").exists()
    assert (wiki_copy / "entities" / "alok-kumar2.md").exists()
    assert (wiki_copy / "entities" / "deepak-yadav01.md").exists()
    # Legit tool stays put.
    assert (wiki_copy / "systems" / "legit-tool.md").exists()


def test_no_flags_when_systems_is_clean(tmp_path: Path) -> None:
    """Baseline: a wiki with only clean systems/entity pages returns []."""
    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir()
    (tmp_path / "wiki" / "systems" / "sonarqube.md").write_text(
        "---\ntitle: SonarQube\npage_type: system\nstatus: current\n---\n\nbody\n",
        encoding="utf-8",
    )
    flags = audit_mod.audit_systems(tmp_path / "wiki" / "systems")
    assert flags == []


def test_validator_hard_errors_on_systems_with_email(wiki_copy: Path) -> None:
    """The extended validator treats email-in-systems as a blocking error."""
    # Ensure minimum sibling dirs exist so validate_wiki.run() can walk.
    for cat in ("topics", "policies", "timelines", "conflicts"):
        (wiki_copy / cat).mkdir(exist_ok=True)

    errors, _warnings = validator.run(wiki_copy)
    # `alok-kumar2.md` has an email field — must be flagged.
    offenders = [e for e in errors if e.page.name == "alok-kumar2.md" and "email:" in e.reason]
    assert offenders, f"expected email-in-systems error, got: {errors}"


def test_git_mv_used_when_repo_tracked(tmp_path: Path) -> None:
    """Sanity check: `git mv` path is taken in a real git worktree.

    Not strictly required for correctness (shutil.move is the fallback),
    but protects against a regression where we forget to shell out to
    git and break `git log --follow`.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.st"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    systems = repo / "wiki" / "systems"
    entities = repo / "wiki" / "entities"
    systems.mkdir(parents=True)
    entities.mkdir()
    src = systems / "alok-kumar2.md"
    src.write_text(
        "---\ntitle: Alok\npage_type: system\nstatus: current\nemail: alok@indiamart.com\n---\n\nx\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    runner = CliRunner()
    result = runner.invoke(audit_mod.main, ["--wiki-dir", str(repo / "wiki"), "--confirm"])
    assert result.exit_code == 0
    assert not src.exists()
    assert (entities / "alok-kumar2.md").exists()
    # `git status` should show the rename, not add+delete.
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # Renames in porcelain start with "R" or "RM".
    assert any(line.startswith("R") for line in status.splitlines()), status
