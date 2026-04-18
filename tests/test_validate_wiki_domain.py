"""Multi-value `domains:` schema coverage for scripts/validate_wiki.py.

v10-U2 extends the domain check to accept either the singular `domain:`
string or the plural `domains:` list, each value drawn from the eight
canonical north-star slugs. Unknown values surface as
`unknown-domain-value` in the warning reason so operators can grep the
log; the check itself stays under `domain-unknown` for backward compat
with the existing warning pipeline.

See `tests/test_validate_wiki_domain_warning.py` for singular-form
coverage. This file pins the multi-value behaviour end-to-end — valid
singular, valid list, invalid singular, invalid list, and the
both-set conflict case.
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
    domains: list[str] | None = None,
) -> Path:
    """Write a minimal topic/system page with the requested domain shape.

    Pass `domain=` for the singular form, `domains=` for the list form,
    or both to exercise the precedence rule (plural wins). Omitting both
    skips the field entirely so the caller can test `domain-missing`.
    """
    lines = [
        "---",
        f"title: {slug.replace('-', ' ').title()}",
        f"page_type: {page_type}",
        "status: active",
    ]
    if domain is not None:
        lines.append(f"domain: {domain}")
    if domains is not None:
        inline = ", ".join(domains)
        lines.append(f"domains: [{inline}]")
    lines += ["---", "", f"Body for {slug}.", ""]
    path = cat_dir / f"{slug}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_valid_singular_domain_passes(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "seller-topic", "topic", domain="seller-experience")
    assert validator.check_missing_domain(mini_wiki) == []


def test_valid_multi_domains_passes(mini_wiki: Path) -> None:
    """A topic that legitimately spans two canonical domains can list both."""
    _write_page(
        mini_wiki / "topics",
        "payment-fraud-sweep",
        "topic",
        domains=["trust-safety", "growth-monetization"],
    )
    assert validator.check_missing_domain(mini_wiki) == []


def test_invalid_singular_domain_errors_with_unknown_domain_value(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "odd-topic", "topic", domain="foo")
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "domain-unknown"
    assert "unknown-domain-value" in warnings[0].reason
    assert "'foo'" in warnings[0].reason


def test_invalid_multi_domains_errors_with_unknown_domain_value(mini_wiki: Path) -> None:
    """One bad slug among valid ones — emit one warning naming the bad value."""
    _write_page(
        mini_wiki / "topics",
        "half-bogus",
        "topic",
        domains=["trust-safety", "bogus"],
    )
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "domain-unknown"
    assert "unknown-domain-value" in warnings[0].reason
    assert "'bogus'" in warnings[0].reason


def test_multi_domains_all_invalid_reports_each_value(mini_wiki: Path) -> None:
    """Each bad value gets its own warning so operators see the full picture."""
    _write_page(
        mini_wiki / "topics",
        "fully-bogus",
        "topic",
        domains=["foo", "bar"],
    )
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 2
    reasons = " | ".join(w.reason for w in warnings)
    assert "'foo'" in reasons
    assert "'bar'" in reasons
    assert all(w.check == "domain-unknown" for w in warnings)


def test_both_domain_and_domains_set_prefers_plural(mini_wiki: Path) -> None:
    """Precedence: `domains:` wins over `domain:` when both are declared.

    Rationale: the plural form is strictly more expressive — a single
    value in a list equals the singular form, so picking plural gives
    the viewer + validator a uniform code path. The singular field is
    kept readable but effectively ignored. Mirrors mkdocs_hooks
    `_render_domain_badges` so both produce the same list. Keeps the
    failure mode simple: pages only need to be invalid under ONE field.
    """
    # Plural is canonical; singular is legacy/stale — no warnings expected.
    _write_page(
        mini_wiki / "topics",
        "both-set-valid-plural",
        "topic",
        domain="foo",  # would be domain-unknown on its own
        domains=["seller-experience"],
    )
    # Inverse: plural has the bad value; singular is canonical but ignored.
    _write_page(
        mini_wiki / "topics",
        "both-set-bad-plural",
        "topic",
        domain="seller-experience",
        domains=["bogus"],
    )
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].page.name == "both-set-bad-plural.md"
    assert "'bogus'" in warnings[0].reason


def test_missing_both_fields_produces_domain_missing(mini_wiki: Path) -> None:
    _write_page(mini_wiki / "topics", "legacy-topic", "topic")
    warnings = validator.check_missing_domain(mini_wiki)
    assert len(warnings) == 1
    assert warnings[0].check == "domain-missing"


def test_empty_domains_list_falls_back_to_singular(mini_wiki: Path) -> None:
    """`domains: []` is treated as not-set so the singular field still wins."""
    page = mini_wiki / "topics" / "empty-list.md"
    page.write_text(
        "---\n"
        "title: Empty List\n"
        "page_type: topic\n"
        "status: active\n"
        "domain: platform-reliability\n"
        "domains: []\n"
        "---\n"
        "\n"
        "Body.\n",
        encoding="utf-8",
    )
    assert validator.check_missing_domain(mini_wiki) == []


def test_warnings_do_not_bleed_into_errors(mini_wiki: Path) -> None:
    """Multi-value invalid slugs stay in the warning channel; never exit-1."""
    _write_page(
        mini_wiki / "topics",
        "bad-plural",
        "topic",
        domains=["nope"],
    )
    errors, warnings = validator.run(mini_wiki)
    assert errors == []
    assert any(w.check == "domain-unknown" for w in warnings)
