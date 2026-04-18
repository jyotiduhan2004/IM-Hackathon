"""Domain-slug WARN-level checks in scripts/validate_wiki.py.

Tier A's prompt rewrite teaches the agent to emit `domain:` on topic
and system pages. This validator surfaces pages that are still missing
the field (`domain-missing`) or carry an unknown slug (`domain-unknown`)
without blocking the build — warnings must never contribute to the
exit code.
"""

from __future__ import annotations

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
    page_type: str,
    *,
    domain: str | None = None,
    omit_domain: bool = False,
) -> Path:
    """Write a minimal valid topic/system page.

    `omit_domain=True` leaves the field out entirely. `domain=None` with
    `omit_domain=False` writes `domain: null` (explicit unknown value).
    """
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        f"page_type: {page_type}",
        "status: current",
    ]
    if not omit_domain:
        lines.append(f"domain: {domain if domain is not None else 'null'}")
    lines += ["---", "", f"Body for {slug}.", ""]
    path = cat_dir / f"{slug}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_canonical_domain_produces_no_warning(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "buyer-app-launch", "topic", domain="buyer-experience")
    _write_page(mini_wiki / "systems", "search-service", "system", domain="marketplace-discovery")
    assert validator.check_missing_domain(mini_wiki) == []


def test_missing_domain_field_produces_warning(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "legacy-topic", "topic", omit_domain=True)
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "domain-missing"
    assert warnings[0].page.name == "legacy-topic.md"
    assert "buyer-experience" in warnings[0].reason


def test_missing_domain_on_system_page_produces_warning(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "systems", "legacy-system", "system", omit_domain=True)
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "domain-missing"
    assert warnings[0].page.name == "legacy-system.md"


def test_unknown_domain_slug_produces_warning(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "odd-topic", "topic", domain="invalid-slug")
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "domain-unknown"
    assert "'invalid-slug'" in warnings[0].reason


def test_index_page_skipped(mini_wiki: Path) -> None:
    """topics/index.md is nav-only — don't demand a domain on it."""
    (mini_wiki / "topics" / "index.md").write_text(
        "---\ntitle: Topics\npage_type: index\nstatus: current\n---\n\nIndex body.\n",
        encoding="utf-8",
    )
    assert validator.check_missing_domain(mini_wiki) == []


def test_all_eight_canonical_domains_accepted(mini_wiki: Path) -> None:
    """Every slug in CANONICAL_DOMAINS should satisfy the check."""
    for i, slug in enumerate(sorted(validator.CANONICAL_DOMAINS)):
        _write_page(mini_wiki / "topics", f"page-{i}", "topic", domain=slug)
    assert validator.check_missing_domain(mini_wiki) == []


def test_canonical_domains_has_eight_entries() -> None:
    """North-star spec calls for exactly eight domains — guard against drift."""
    assert len(validator.CANONICAL_DOMAINS) == 8


def test_warnings_do_not_contribute_to_errors(mini_wiki: Path) -> None:
    """run() must split errors from warnings — warnings never bleed into errors."""
    _write_page(mini_wiki / "topics", "no-domain-topic", "topic", omit_domain=True)
    errors, warnings = validator.run(mini_wiki)
    assert errors == []
    checks = {w.check for w in warnings}
    assert "domain-missing" in checks
