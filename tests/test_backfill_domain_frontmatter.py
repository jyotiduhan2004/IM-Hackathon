"""Tests for scripts/backfill_domain_frontmatter.py (v9-U3).

Verifies the filesystem walk + keyword-scoring heuristic + ambiguity
detection on the `mini_wiki` fixture. The script doesn't touch the DB,
so we don't need the schema fixtures — just `mini_wiki` from
`tests/conftest.py` and the shared `load_script` helper.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest
from click.testing import CliRunner

from tests._script_loader import load_script


@pytest.fixture
def script_mod() -> ModuleType:
    """scripts/backfill_domain_frontmatter.py loaded for white-box testing."""
    return load_script("backfill_domain_frontmatter")


def _write_page(path: Path, *, title: str, body: str, domain: str | None = None) -> None:
    """Drop a minimal topic page at `path`. `domain` omitted == missing frontmatter."""
    lines = [
        "---",
        f"title: {title}",
        "page_type: topic",
        "status: active",
    ]
    if domain is not None:
        lines.append(f"domain: {domain}")
    lines += ["---", "", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scoring (unit-level)
# ---------------------------------------------------------------------------


def test_score_domains_returns_single_winner_for_clear_match(script_mod: ModuleType) -> None:
    """A title loaded with buyer-experience keywords beats all other domains."""
    verdict = script_mod._score_domains(
        title="Buymer WhatsApp buyer onboarding",
        body="Feature for buymer app improving search ux for first-time buyers.",
    )
    assert verdict.winner == "buyer-experience"
    assert verdict.candidates == []  # clean win, no ambiguity
    assert verdict.scores["buyer-experience"] >= 2


def test_score_domains_returns_none_when_zero_keywords_match(script_mod: ModuleType) -> None:
    """No keyword hits → None winner → caller records as unresolved."""
    verdict = script_mod._score_domains(
        title="Completely Off-Topic Memo",
        body="This page mentions nothing from any canonical domain list.",
    )
    assert verdict.winner is None
    assert verdict.scores == {}
    assert verdict.candidates == []


def test_score_domains_flags_ambiguous_runner_up(script_mod: ModuleType) -> None:
    """Title hits multiple domains with close scores → candidates populated.

    `buymer` hits buyer-experience and `auditmate` hits seller-experience;
    both have 1 keyword hit → within the 20% ambiguity threshold.
    """
    verdict = script_mod._score_domains(
        title="Buymer auditmate rollout",
        body="",
    )
    assert verdict.winner is not None
    # Both candidates present because the runner-up is within 20% (equal).
    assert set(verdict.candidates) >= {"buyer-experience", "seller-experience"}
    assert len(verdict.candidates) >= 2


def test_score_domains_drops_low_runner_up(script_mod: ModuleType) -> None:
    """Strong buyer-experience leader with a single-hit seller-experience runner-up → no ambiguity.

    The page stuffs many buyer-experience keywords into the title while the
    body only name-drops `auditmate` once. The runner-up's score is far
    below the 20% ambiguity ratio threshold (_AMBIGUITY_RATIO = 0.8 on the
    winner), so `candidates` must come back empty — a clean win.
    """
    verdict = script_mod._score_domains(
        title="Buymer buylead buyer app search ux lens WhatsApp buyer",
        body="Auditmate is mentioned once here.",
    )
    assert verdict.winner == "buyer-experience"
    # Runner-up's 1 hit sits well below 0.8 * winner_score → dropped.
    assert verdict.candidates == []


# ---------------------------------------------------------------------------
# Backfill walk (integration-level, via CliRunner)
# ---------------------------------------------------------------------------


def test_dry_run_preserves_existing_frontmatter(script_mod: ModuleType, mini_wiki: Path) -> None:
    """Dry-run never writes. Pages on disk stay byte-identical after invocation."""
    topics = mini_wiki / "topics"
    _write_page(
        topics / "alpha.md",
        title="Buymer onboarding",
        body="Buymer app search ux improvements.",
    )

    before = (topics / "alpha.md").read_text(encoding="utf-8")

    result = CliRunner().invoke(
        script_mod.main,
        ["--dry-run", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "1 would change, 0 ambiguous, 0 still-unresolved" in result.output

    after = (topics / "alpha.md").read_text(encoding="utf-8")
    assert before == after  # no mutation in dry-run


def test_commit_writes_single_domain_for_clean_match(
    script_mod: ModuleType, mini_wiki: Path
) -> None:
    """Clear keyword match → `domain: <slug>` lands in frontmatter."""
    topics = mini_wiki / "topics"
    _write_page(
        topics / "alpha.md",
        title="Buymer onboarding",
        body="Buymer app search ux improvements for the WhatsApp buyer journey.",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "1 changed, 0 ambiguous" in result.output

    content = (topics / "alpha.md").read_text(encoding="utf-8")
    assert "domain: buyer-experience" in content
    # No ambiguity marker on a single-domain page.
    assert "domain_candidates" not in content
    assert "ambiguous" not in content


def test_commit_writes_domain_candidates_for_ambiguous(
    script_mod: ModuleType, mini_wiki: Path
) -> None:
    """Two domains within 20% → `domain_candidates: [...]` + inline comment."""
    topics = mini_wiki / "topics"
    _write_page(
        topics / "dual.md",
        title="Buymer auditmate rollout",
        body="",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "1 changed, 1 ambiguous" in result.output

    content = (topics / "dual.md").read_text(encoding="utf-8")
    # A `domain:` line landed, plus a `domain_candidates:` list, plus the
    # comment nudge on the list-header line.
    assert "domain:" in content
    assert "domain_candidates:" in content
    assert "# ambiguous: review manually" in content


def test_commit_respects_limit(script_mod: ModuleType, mini_wiki: Path) -> None:
    """`--limit 1` mutates only the first page; the second one stays on disk untouched."""
    topics = mini_wiki / "topics"
    _write_page(topics / "a-first.md", title="Buymer onboarding", body="Buymer app.")
    _write_page(topics / "b-second.md", title="MCAT categorization", body="MCAT ranking.")

    before_second = (topics / "b-second.md").read_text(encoding="utf-8")

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--limit", "1", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    assert "domain: buyer-experience" in (topics / "a-first.md").read_text(encoding="utf-8")
    # Second page is still on disk unchanged — limit cut off the walk.
    assert (topics / "b-second.md").read_text(encoding="utf-8") == before_second


def test_commit_skips_pages_that_already_have_domain(
    script_mod: ModuleType, mini_wiki: Path
) -> None:
    """Already-tagged pages aren't re-scored — walker filters them out upfront."""
    topics = mini_wiki / "topics"
    _write_page(
        topics / "alpha.md",
        title="Some unrelated title",
        body="Buymer auditmate both mentioned to prove ambiguity isn't triggered.",
        domain="seller-experience",  # pre-existing, trusted verbatim
    )

    before = (topics / "alpha.md").read_text(encoding="utf-8")

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "0 changed, 0 ambiguous, 0 still-unresolved" in result.output

    assert (topics / "alpha.md").read_text(encoding="utf-8") == before


def test_commit_reports_unresolved_for_no_keyword_hits(
    script_mod: ModuleType, mini_wiki: Path
) -> None:
    """A page with zero keyword matches doesn't get a domain — just counted."""
    topics = mini_wiki / "topics"
    _write_page(
        topics / "orphan.md",
        title="Off-Topic Memo",
        body="Nothing here lines up with any canonical domain list.",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "0 changed, 0 ambiguous, 1 still-unresolved" in result.output

    # Unresolved pages stay untouched — no `domain:` stamped in.
    content = (topics / "orphan.md").read_text(encoding="utf-8")
    assert "domain:" not in content


def test_commit_walks_systems_directory(script_mod: ModuleType, mini_wiki: Path) -> None:
    """Systems/ pages are also covered — not just topics/."""
    systems = mini_wiki / "systems"
    _write_page(
        systems / "lens.md",
        title="Lens",
        body="Lens is a buyer app feature for visual search.",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    assert "domain: buyer-experience" in (systems / "lens.md").read_text(encoding="utf-8")


def test_commit_skips_index_md(script_mod: ModuleType, mini_wiki: Path) -> None:
    """Section index.md files are generated listings — never rewritten."""
    topics = mini_wiki / "topics"
    _write_page(
        topics / "index.md",
        title="Topics Index",
        body="Buymer auditmate — keywords present but generated.",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "0 changed" in result.output

    # index.md stays unchanged.
    assert "domain:" not in (topics / "index.md").read_text(encoding="utf-8")


def test_commit_preserves_existing_frontmatter_fields(
    script_mod: ModuleType, mini_wiki: Path
) -> None:
    """title, status, source_threads etc. round-trip through yaml.safe_dump."""
    topics = mini_wiki / "topics"
    alpha = topics / "alpha.md"
    alpha.write_text(
        """---
title: Alpha Page
page_type: topic
status: active
source_threads:
  - 19b92d9b270daa57
related:
  - '[[beta]]'
last_compiled: '2026-01-01T00:00:00+00:00'
---

# Alpha Page

Buymer app content — buyer-experience should win.
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    rewritten = alpha.read_text(encoding="utf-8")
    # Original fields survive.
    assert "title: Alpha Page" in rewritten
    assert "19b92d9b270daa57" in rewritten
    assert "[[beta]]" in rewritten
    assert "last_compiled:" in rewritten
    # New field landed.
    assert "domain: buyer-experience" in rewritten
    # Body preserved.
    assert "Buymer app content" in rewritten


def test_commit_is_idempotent(script_mod: ModuleType, mini_wiki: Path) -> None:
    """Running --commit twice in a row: second run is a no-op."""
    topics = mini_wiki / "topics"
    _write_page(topics / "alpha.md", title="Buymer onboarding", body="Buymer app.")

    first = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert first.exit_code == 0, first.output
    after_first = (topics / "alpha.md").read_text(encoding="utf-8")

    second = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(mini_wiki.parent)],
        catch_exceptions=False,
    )
    assert second.exit_code == 0, second.output
    assert "0 changed, 0 ambiguous, 0 still-unresolved" in second.output
    assert (topics / "alpha.md").read_text(encoding="utf-8") == after_first


def test_annotate_ambiguity_comment_only_touches_header_line(
    script_mod: ModuleType,
) -> None:
    """The inline-comment splicer lands only on `domain_candidates:` — not on
    `domain:`, `title:`, or anywhere else."""
    rendered = "title: foo\ndomain: a\ndomain_candidates:\n- a\n- b\n"
    result = script_mod._annotate_ambiguity_comment(rendered)
    assert "domain_candidates:  # ambiguous: review manually" in result
    # Exactly one comment marker — not accidentally re-applied.
    assert result.count("# ambiguous") == 1
    # The `domain:` line stays bare (no spurious comment).
    assert "\ndomain: a\n" in result
