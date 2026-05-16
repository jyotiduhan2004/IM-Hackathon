"""Unit tests for scripts/fix_broken_wikilinks.py.

One test per rule in the spec (raw target, dotted domain, exact-match,
fuzzy match >0.95, unknown → manual). Plus CLI sanity tests to ensure
exit codes line up with what `make publish-gate` expects.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_fixer():  # type: ignore[no-untyped-def]
    """Import scripts/fix_broken_wikilinks.py as a module.

    Not on PYTHONPATH by default — match the pattern used by
    test_validate_wiki_sections.
    """
    spec = importlib.util.spec_from_file_location(
        "fix_broken_wikilinks", REPO_ROOT / "scripts" / "fix_broken_wikilinks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fix_broken_wikilinks"] = module
    spec.loader.exec_module(module)
    return module


fixer = _load_fixer()


def _make_wiki(tmp_path: Path, pages: dict[str, str]) -> Path:
    """Build a tiny wiki/ tree in tmp_path.

    Keys are `<category>/<slug>` (e.g. `topics/foo`) and values are
    the page body. Frontmatter isn't required — the fixer doesn't read
    it. Returns the wiki/ root path.
    """
    wiki = tmp_path / "wiki"
    for key, body in pages.items():
        category, _, slug = key.partition("/")
        (wiki / category).mkdir(parents=True, exist_ok=True)
        (wiki / category / f"{slug}.md").write_text(body, encoding="utf-8")
    return wiki


def test_raw_target_stripped_with_date(tmp_path: Path) -> None:
    """Rule 1: `[[raw/YYYY-MM-DD_..._hash.md]]` → `see raw source (DATE)`."""
    wiki = _make_wiki(
        tmp_path,
        {
            "topics/foo": "See [[raw/2026-01-14_subject_abc123.md]] for context.",
        },
    )
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    assert fixes[0].rule == "raw-target"
    assert manual == []
    content = (wiki / "topics" / "foo.md").read_text(encoding="utf-8")
    assert "see raw source (2026-01-14)" in content
    assert "[[raw/" not in content


def test_raw_target_without_date_still_stripped(tmp_path: Path) -> None:
    """If we can't parse a date, fall back to generic `see raw source`."""
    wiki = _make_wiki(
        tmp_path,
        {"topics/foo": "See [[raw/weird_filename.md]]."},
    )
    fixes, _ = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    content = (wiki / "topics" / "foo.md").read_text(encoding="utf-8")
    assert "see raw source" in content
    assert "[[raw/" not in content


def test_dotted_domain_stripped_to_literal(tmp_path: Path) -> None:
    """Rule 2: `[[component.intermesh.net]]` → literal `component.intermesh.net`."""
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/alice": "Rolled via [[component.intermesh.net]] on launch day.",
        },
    )
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    assert fixes[0].rule == "dotted-domain"
    assert manual == []
    content = (wiki / "entities" / "alice.md").read_text(encoding="utf-8")
    # The wrapper is gone, the literal string remains.
    assert "[[component.intermesh.net]]" not in content
    assert "component.intermesh.net" in content


def test_dotted_domain_preserves_alias(tmp_path: Path) -> None:
    """`[[foo.com|Foo Inc]]` → `Foo Inc` (alias wins over the raw target)."""
    wiki = _make_wiki(
        tmp_path,
        {"topics/foo": "Partnered with [[foo.com|Foo Inc]] last quarter."},
    )
    fixes, _ = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    content = (wiki / "topics" / "foo.md").read_text(encoding="utf-8")
    assert "Foo Inc" in content
    assert "[[foo.com" not in content


def test_exact_match_rewrites_casefold(tmp_path: Path) -> None:
    """Rule 3: `[[Alice-Smith]]` → `[[alice-smith]]` when that page exists."""
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/alice-smith": "Alice page body.",
            "topics/hello": "We met [[Alice-Smith]] today.",
        },
    )
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    assert fixes[0].rule == "exact-match"
    assert manual == []
    content = (wiki / "topics" / "hello.md").read_text(encoding="utf-8")
    assert "[[alice-smith]]" in content
    assert "[[Alice-Smith]]" not in content


def test_exact_match_strips_category_prefix(tmp_path: Path) -> None:
    """`[[entities/alice]]` → `[[alice]]` when `entities/alice.md` exists."""
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/alice": "body",
            "topics/foo": "See [[entities/alice]].",
        },
    )
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    assert fixes[0].rule == "exact-match"
    assert manual == []
    content = (wiki / "topics" / "foo.md").read_text(encoding="utf-8")
    assert "[[alice]]" in content


def test_fuzzy_match_above_threshold(tmp_path: Path) -> None:
    """Rule 4: close-match (ratio >0.95) with a single candidate auto-rewrites.

    `alice-smithh` vs `alice-smith` — one-char typo, ratio ~0.956.
    """
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/alice-smith": "Alice page body.",
            "topics/hello": "Hi [[alice-smithh]].",
        },
    )
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert len(fixes) == 1
    assert fixes[0].rule == "fuzzy-match"
    assert manual == []
    content = (wiki / "topics" / "hello.md").read_text(encoding="utf-8")
    assert "[[alice-smith]]" in content


def test_fuzzy_match_below_threshold_goes_manual(tmp_path: Path) -> None:
    """Distant typo (e.g. `alice-jones`) → manual even if a close-ish slug exists."""
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/alice-smith": "Alice page body.",
            "topics/hello": "Hi [[alice-jones]].",
        },
    )
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert fixes == []
    assert len(manual) == 1
    assert manual[0].target == "alice-jones"
    # Manual path should still surface a suggestion ("alice-smith") for
    # the operator — even if it was below the auto-fix threshold.
    assert "alice-smith" in manual[0].suggestions
    # Content unchanged.
    content = (wiki / "topics" / "hello.md").read_text(encoding="utf-8")
    assert "[[alice-jones]]" in content


def test_literal_word_like_system_goes_manual(tmp_path: Path) -> None:
    """`[[system]]` with no matching page → manual review."""
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/sprinters": "List is now a [[system]] page, see [[sprinters-new]].",
            "entities/sprinters-new": "New page.",
        },
    )
    _fixes, manual = fixer.fix_wiki(wiki, commit=True)
    # `[[sprinters-new]]` resolves, only `[[system]]` is manual.
    assert len(manual) == 1
    assert manual[0].target == "system"
    # And no wrong auto-fix on `[[system]]`.
    content = (wiki / "entities" / "sprinters.md").read_text(encoding="utf-8")
    assert "[[system]]" in content


def test_existing_valid_links_untouched(tmp_path: Path) -> None:
    """Pages with all-valid wikilinks must not be modified at all."""
    wiki = _make_wiki(
        tmp_path,
        {
            "entities/alice": "body",
            "entities/bob": "body",
            "topics/hello": "Between [[alice]] and [[bob]] we ship.",
        },
    )
    original = (wiki / "topics" / "hello.md").read_text(encoding="utf-8")
    fixes, manual = fixer.fix_wiki(wiki, commit=True)
    assert fixes == []
    assert manual == []
    # Byte-for-byte identical.
    assert (wiki / "topics" / "hello.md").read_text(encoding="utf-8") == original


def test_dry_run_does_not_modify_disk(tmp_path: Path) -> None:
    """`commit=False` must record the fixes we'd apply but leave files alone."""
    wiki = _make_wiki(
        tmp_path,
        {"topics/foo": "See [[raw/2026-01-14_foo_abc.md]]."},
    )
    original = (wiki / "topics" / "foo.md").read_text(encoding="utf-8")
    fixes, _ = fixer.fix_wiki(wiki, commit=False)
    assert len(fixes) == 1
    assert (wiki / "topics" / "foo.md").read_text(encoding="utf-8") == original


def test_manual_review_file_written(tmp_path: Path) -> None:
    """`_write_manual_review` produces a valid markdown file with line numbers."""
    wiki = _make_wiki(tmp_path, {"topics/foo": "[[unknown-slug]]"})
    _, manual = fixer.fix_wiki(wiki, commit=False)
    assert len(manual) == 1

    audit = tmp_path / "audit.md"
    fixer._write_manual_review(manual, audit, "20260416T000000Z")
    body = audit.read_text(encoding="utf-8")
    assert "unknown-slug" in body
    assert "Line 1" in body
    assert "20260416T000000Z" in body


def test_line_number_reporting(tmp_path: Path) -> None:
    """Manual items should report the 1-based line where the link appeared."""
    wiki = _make_wiki(
        tmp_path,
        {"topics/foo": "line one\nline two with [[unknown]]\nline three"},
    )
    _, manual = fixer.fix_wiki(wiki, commit=False)
    assert len(manual) == 1
    assert manual[0].line_no == 2


def _run_cli(
    *args: str,
    wiki_dir: Path,
) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI against a temp wiki/ via WIKI_DIR env."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "fix_broken_wikilinks.py"), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "WIKI_DIR": str(wiki_dir)},
        check=False,
    )


def test_cli_dry_run_exits_zero_even_with_manual(tmp_path: Path) -> None:
    """Dry-run is informational; exit 0 no matter how many manual items exist."""
    wiki = _make_wiki(tmp_path, {"topics/foo": "[[unknown]]"})
    result = _run_cli("--dry-run", wiki_dir=wiki)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_dry_run_does_not_write_audit_file(tmp_path: Path) -> None:
    """Dry-run must NOT touch docs/audits/*.md — it just previews to stdout."""
    audits_dir = REPO_ROOT / "docs" / "audits"
    before = set(audits_dir.glob("broken-wikilinks-*.md")) if audits_dir.exists() else set()
    wiki = _make_wiki(tmp_path, {"topics/foo": "[[unknown-slug-xyz]]"})
    result = _run_cli("--dry-run", wiki_dir=wiki)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    after = set(audits_dir.glob("broken-wikilinks-*.md")) if audits_dir.exists() else set()
    assert before == after, f"dry-run wrote new audit file(s): {after - before}"
    # Proposed manual-review body should be on stdout.
    assert "proposed manual-review" in result.stdout
    assert "unknown-slug-xyz" in result.stdout


def test_cli_commit_exits_nonzero_when_manual_remain(tmp_path: Path) -> None:
    """--commit with remaining manual items must exit 1 so publish-gate fails."""
    wiki = _make_wiki(tmp_path, {"topics/foo": "[[truly-unknown-slug]]"})
    result = _run_cli("--commit", wiki_dir=wiki)
    assert result.returncode == 1, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_cli_commit_exits_zero_when_all_auto_fixed(tmp_path: Path) -> None:
    """--commit with clean post-state exits 0 → unblocks publish-gate."""
    wiki = _make_wiki(
        tmp_path,
        {"topics/foo": "[[raw/2026-01-14_foo_abc.md]]"},
    )
    result = _run_cli("--commit", wiki_dir=wiki)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_cli_rejects_both_flags(tmp_path: Path) -> None:
    """`--dry-run --commit` is misuse; exit 2."""
    wiki = _make_wiki(tmp_path, {"topics/foo": "body"})
    result = _run_cli("--dry-run", "--commit", wiki_dir=wiki)
    assert result.returncode == 2


@pytest.mark.parametrize(
    ("target", "expected_rule"),
    [
        ("raw/2026-01-01_foo_bar.md", "raw-target"),
        ("component.intermesh.net", "dotted-domain"),
        ("indiamart.com", "dotted-domain"),
        ("foo.bar.co.in", "dotted-domain"),
        ("unknown-literal", "manual"),
    ],
)
def test_classify_rule_parametrized(
    target: str,
    expected_rule: str,
) -> None:
    """Sanity: the classifier routes targets to the rule we expect."""
    rule, _, _ = fixer._classify_rule(target, alias=None, known_by_norm={}, all_slugs=[])
    assert rule == expected_rule
