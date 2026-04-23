"""Unit tests for ``src.compile.scoring`` + ``scripts/score_wiki.py`` CSV shape.

All tests use in-memory strings / ``tmp_path``. No Postgres, no live wiki.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.score_wiki import CSV_COLUMNS  # noqa: E402
from scripts.score_wiki import _select_topic_paths  # noqa: E402
from scripts.score_wiki import _write_csv  # noqa: E402
from src.compile.scoring import build_wikilink_incoming_index  # noqa: E402
from src.compile.scoring import score_concept_shape  # noqa: E402
from src.compile.scoring import score_graph_health  # noqa: E402
from src.compile.scoring import score_source_density  # noqa: E402
from src.compile.scoring import score_structural_smells  # noqa: E402
from src.compile.scoring import score_summary_currency  # noqa: E402

# --- concept_shape ---------------------------------------------------------


def test_concept_shape_three_thread_subject_h2s_scores_7() -> None:
    """3 bad H2s x -1 = 7 under the 2026-04-23 softened weight (was 4 under -2)."""
    body = (
        "Summary paragraph.\n\n"
        "## Bug Report\n\nX.\n\n"
        "## Final Decision\n\nY.\n\n"
        "## Launch Announcement\n\nZ.\n\n"
    )
    score, dbg = score_concept_shape(body)
    assert score == 7
    assert dbg["count_bad"] == 3
    assert set(dbg["bad_matches"]) == {"Bug Report", "Final Decision", "Launch Announcement"}


def test_concept_shape_two_bad_h2s_scores_8() -> None:
    """Regression for the -1 weight: 2 bad H2s → 8, not 6 (old -2 weight)."""
    body = "## Bug Report\n\nx.\n\n## Final Decision\n\ny.\n"
    score, dbg = score_concept_shape(body)
    assert score == 8
    assert dbg["count_bad"] == 2


def test_concept_shape_only_good_h2s_scores_10() -> None:
    body = "## Current state\n\nWords.\n\n## How it works\n\nMore.\n\n## Related\n\n- [[other]]\n"
    score, dbg = score_concept_shape(body)
    assert score == 10
    assert dbg["count_bad"] == 0
    assert dbg["h2_titles"] == ["Current state", "How it works", "Related"]


def test_concept_shape_clamps_at_zero() -> None:
    """Under -1 weight, clamping needs ≥ 10 bad H2s. We use all 12 titles."""
    body = "\n\n".join(
        f"## {h}\n\nx."
        for h in [
            "Bug Report",
            "Final Decision",
            "Launch Announcement",
            "Testing Results",
            "QA Results",
            "Release Notes",
            "Business Objective",
            "Announcement",
            "Issue",
            "Thread Summary",
            "Email Summary",
            "Discussion",
        ]
    )
    score, dbg = score_concept_shape(body)
    assert dbg["count_bad"] == 12
    assert score == 0


# --- summary_currency ------------------------------------------------------


def test_summary_currency_present_tense_scores_high() -> None:
    body = (
        "This system is the canonical ledger for seller onboarding. "
        "It provides the API that downstream consumers call.\n\n"
        "## Current state\n"
    )
    score, dbg = score_summary_currency(body)
    assert score >= 6
    assert dbg["good_count"] >= 1


def test_summary_currency_narrative_scores_low() -> None:
    body = (
        "Originally we tried inlining the call, then we switched to a queue. "
        "Later the team was asked to revert because latency was worse than before.\n\n"
        "## History\n"
    )
    score, dbg = score_summary_currency(body)
    assert score < 3
    assert dbg["bad_count"] >= 2


def test_summary_currency_skips_h1_line() -> None:
    body = (
        "# Page Title\n\nThis is the canonical summary and it handles the problem.\n\n## Section\n"
    )
    score, dbg = score_summary_currency(body)
    assert "canonical summary" in dbg["first_paragraph"]
    assert score >= 6


def test_summary_currency_neutral_paragraph_scores_five() -> None:
    body = "A short neutral paragraph with no loaded tokens.\n\n## Section\n"
    score, _ = score_summary_currency(body)
    assert score == 5


# --- source_density --------------------------------------------------------


def test_source_density_one_ref_in_short_body_scores_high() -> None:
    body = "word " * 100 + " [^msg-abc123]"
    score, dbg = score_source_density(body)
    # 101 words, target = 101/200 ≈ 0.5 → sources/target ≈ 2 → score 10 (capped).
    assert score >= 10
    assert dbg["sources"] == 1


def test_source_density_long_body_no_refs_scores_zero() -> None:
    body = "word " * 300
    score, dbg = score_source_density(body)
    assert score == 0
    assert dbg["sources"] == 0


def test_source_density_stub_body_returns_zero() -> None:
    body = "too short"
    score, dbg = score_source_density(body)
    assert score == 0
    assert dbg["body_words"] == 2


def test_source_density_counts_raw_bullets() -> None:
    body = "word " * 300 + "\n\n- raw/2026-01-01_foo.md\n- raw/2026-01-02_bar.md\n"
    score, dbg = score_source_density(body)
    assert dbg["sources"] == 2
    assert score > 0


def test_source_density_counts_frontmatter_sources() -> None:
    """``sources:`` list from frontmatter is the load-bearing signal today.

    Compiled topic pages populate ``frontmatter['sources']`` as a list of
    ``raw/<file>.md`` paths. Those count alongside inline footnotes + raw
    bullets — for the current corpus they're the only signal that fires.
    """
    body = "word " * 300
    frontmatter = {
        "sources": [
            "raw/2026-01-01_foo.md",
            "raw/2026-01-02_bar.md",
            "raw/2026-01-03_baz.md",
        ]
    }
    score, dbg = score_source_density(body, frontmatter)
    assert dbg["frontmatter_sources"] == 3
    assert dbg["sources"] == 3
    assert score > 0


def test_source_density_frontmatter_none_is_safe() -> None:
    """Missing frontmatter dict shouldn't explode — caller may still be wiring up."""
    body = "word " * 300 + " [^msg-abc123]"
    score, dbg = score_source_density(body, None)
    assert dbg["frontmatter_sources"] == 0
    assert dbg["sources"] == 1
    assert score > 0


def test_source_density_non_list_sources_treated_as_zero() -> None:
    """Frontmatter with malformed ``sources`` (string not list) mustn't crash."""
    body = "word " * 300
    frontmatter: dict[str, object] = {"sources": "raw/foo.md"}
    score, dbg = score_source_density(body, frontmatter)
    assert dbg["frontmatter_sources"] == 0
    assert score == 0


# --- graph_health ----------------------------------------------------------


def test_graph_health_five_incoming_no_broken_scores_ten() -> None:
    score, dbg = score_graph_health(
        slug="foo",
        body="## Related\n\n- [[bar]]\n- [[topic/baz]]\n",
        wikilink_index={"foo": 5},
        known_slugs={"foo", "bar", "baz"},
    )
    # 5 + 5 - 0 = 10 — saturates the clamp under the 2026-04-23 formula.
    assert score == 10
    assert dbg["incoming"] == 5
    assert dbg["broken_outgoing"] == 0


def test_graph_health_zero_incoming_zero_broken_scores_five() -> None:
    """An isolated but clean page earns the neutral baseline (was 0 under old formula)."""
    score, dbg = score_graph_health(
        slug="lonely",
        body="No outgoing links.\n",
        wikilink_index={},
        known_slugs={"lonely"},
    )
    assert score == 5
    assert dbg["incoming"] == 0
    assert dbg["outgoing_count"] == 0


def test_graph_health_three_incoming_one_broken_scores_five() -> None:
    """5 + 3 - 3 = 5 — incoming gains are tempered by broken outgoing links."""
    score, dbg = score_graph_health(
        slug="foo",
        body="## Related\n\n- [[missing-1]]\n- [[topic/bar]]\n",
        wikilink_index={"foo": 3},
        known_slugs={"foo", "bar"},
    )
    assert score == 5
    assert dbg["incoming"] == 3
    assert dbg["broken_outgoing"] == 1


def test_graph_health_zero_incoming_two_broken_clamps_to_zero() -> None:
    """5 + 0 - 6 = -1 → clamped to 0 — broken outbound links still punch through the baseline."""
    score, dbg = score_graph_health(
        slug="foo",
        body="## Related\n\n- [[missing-1]]\n- [[topic/missing-2]]\n",
        wikilink_index={},
        known_slugs={"foo"},
    )
    assert score == 0
    assert dbg["incoming"] == 0
    assert dbg["broken_outgoing"] == 2


def test_graph_health_two_incoming_two_broken_scores_one() -> None:
    """5 + 2 - 6 = 1 — no longer the clamp-to-zero case it was under the old formula."""
    score, dbg = score_graph_health(
        slug="foo",
        body="## Related\n\n- [[missing-1]]\n- [[topic/missing-2]]\n",
        wikilink_index={"foo": 2},
        known_slugs={"foo"},
    )
    assert score == 1
    assert dbg["broken_outgoing"] == 2


# --- structural_smells -----------------------------------------------------


def test_structural_smells_clean_page_scores_ten() -> None:
    """No duplicates, empties, email-slugs, or FM+body ``Related`` overlap → 10/10."""
    body = (
        "Summary paragraph describing the current state.\n\n"
        "## Current state\n\nWords.\n\n"
        "## How it works\n\nMore words here too.\n"
    )
    score, dbg = score_structural_smells(body, frontmatter=None)
    assert score == 10
    assert dbg["duplicate_h2"] == []
    assert dbg["empty_h2_sections"] == []
    assert dbg["email_slug_hits"] == 0
    assert dbg["has_fm_and_body_related"] is False


def test_structural_smells_duplicate_related_scores_seven() -> None:
    """Two ``## Related`` sections → -3 per allowlisted duplicated title."""
    body = (
        "Intro.\n\n## Related\n\n- [[alpha]]\n\n## Middle\n\nStuff.\n\n## Related\n\n- [[beta]]\n"
    )
    score, dbg = score_structural_smells(body, frontmatter=None)
    assert score == 7
    assert dbg["duplicate_h2"] == ["Related"]


def test_structural_smells_compound_penalty_scores_three() -> None:
    """Two ``## Related`` + one empty section + 6 email-slug wikilinks.

    Penalty breakdown: 1 duplicated allowlisted H2 (-3) + 1 empty H2 (-2)
    + 6 email-slug hits (-2 at ``6//3``). 10 - 7 = 3.
    """
    body = (
        "Intro mentions "
        "[[aa-indiamart-com]] and [[neeraj-gmail-com]] and [[other-amazon-com]] "
        "and [[bob-indiamart-com]] and [[ceo-gmail-com]] and [[ops-amazon-com]].\n\n"
        "## Related\n\n- [[alpha]]\n\n"
        "## Empty Section\n"
        "## Non-empty\n\nStuff.\n\n"
        "## Related\n\n- [[beta]]\n"
    )
    score, dbg = score_structural_smells(body, frontmatter=None)
    assert dbg["duplicate_h2"] == ["Related"]
    assert dbg["empty_h2_sections"] == ["Empty Section"]
    assert dbg["email_slug_hits"] == 6
    assert score == 3


def test_structural_smells_fm_body_related_duplication_scores_eight() -> None:
    """Frontmatter ``related:`` list + body ``## Related`` H2 → -2 flat."""
    body = "Intro paragraph.\n\n## Current state\n\nx.\n\n## Related\n\n- [[alpha]]\n- [[beta]]\n"
    frontmatter = {"related": ["[[alpha]]", "[[beta]]"]}
    score, dbg = score_structural_smells(body, frontmatter)
    assert score == 8
    assert dbg["has_fm_and_body_related"] is True
    # No allowlisted duplicate fires — only one ``## Related`` in the body.
    assert dbg["duplicate_h2"] == []


def test_structural_smells_empty_fm_related_does_not_trigger() -> None:
    """Empty / missing ``related:`` means rule 4 shouldn't fire even with ``## Related``."""
    body = "## Current state\n\nx.\n\n## Related\n\n- [[alpha]]\n"
    score, dbg = score_structural_smells(body, frontmatter={"related": []})
    assert score == 10
    assert dbg["has_fm_and_body_related"] is False


def test_structural_smells_email_slug_penalty_caps_at_four() -> None:
    """15 email-slug hits → 15 // 3 = 5 → cap at -4 (score 6, not 5)."""
    slugs = " ".join(f"[[user{i}-indiamart-com]]" for i in range(15))
    body = f"Intro paragraph mentions {slugs}.\n\n## Current state\n\nx.\n"
    score, dbg = score_structural_smells(body, frontmatter=None)
    assert dbg["email_slug_hits"] == 15
    assert score == 6  # 10 - 4 (capped)


def test_structural_smells_email_slug_case_insensitive() -> None:
    """Title-case variants still trigger — pattern matches with ``re.IGNORECASE``."""
    body = "Intro [[AA-Indiamart-Com]] and [[Neeraj-Gmail-Com]] and [[X-Amazon-Com]].\n"
    _, dbg = score_structural_smells(body, frontmatter=None)
    assert dbg["email_slug_hits"] == 3


# --- build_wikilink_incoming_index -----------------------------------------


def test_build_wikilink_incoming_index_two_pages_symmetric(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "a.md").write_text(
        "---\ntitle: A\n---\n\nBody links to [[topic/b]].\n", encoding="utf-8"
    )
    (wiki / "topics" / "b.md").write_text(
        "---\ntitle: B\n---\n\nBody links to [[topic/a]].\n", encoding="utf-8"
    )
    index, known = build_wikilink_incoming_index(wiki)
    assert index == {"a": 1, "b": 1}
    assert known == {"a", "b"}


def test_build_wikilink_incoming_index_skips_self_links(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "solo.md").write_text(
        "---\ntitle: Solo\n---\n\nSee also [[topic/solo]].\n", encoding="utf-8"
    )
    index, known = build_wikilink_incoming_index(wiki)
    assert index == {}
    assert known == {"solo"}


def test_build_wikilink_incoming_index_handles_bare_and_prefixed_targets(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "systems").mkdir(parents=True)
    (wiki / "topics" / "hub.md").write_text(
        "---\ntitle: Hub\n---\n\n[[lens]] and [[system/lens]] and [[topic/child|display]].\n",
        encoding="utf-8",
    )
    (wiki / "systems" / "lens.md").write_text("---\ntitle: Lens\n---\n\nBody.\n", encoding="utf-8")
    (wiki / "topics" / "child.md").write_text("---\ntitle: Child\n---\n\nBody.\n", encoding="utf-8")
    index, known = build_wikilink_incoming_index(wiki)
    assert index == {"lens": 2, "child": 1}
    assert known == {"hub", "lens", "child"}


# --- CSV column shape ------------------------------------------------------


def test_csv_column_order(tmp_path: Path) -> None:
    rows = [
        {
            "slug": "page-a",
            "concept_shape": 8,
            "summary_currency": 7,
            "source_density": 6,
            "graph_health": 5,
            "structural_smells": 9,
            "mean": 7.0,
            "sum": 35,
            "_debug": {},
        }
    ]
    out = tmp_path / "out.csv"
    _write_csv(out, rows)
    with out.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
    assert tuple(header) == CSV_COLUMNS
    assert "structural_smells" in header


# --- GOOD_TOKENS false-positive regression (F1) ----------------------------


def test_summary_currency_does_not_count_is_inside_analysis_etc() -> None:
    """Drop-``is`` regression: prose using ``analysis``/``basis``/``This ``
    must not inflate good_count via substring matches on ``is ``.

    The only GOOD_TOKEN hit here is ``provides ``; ``is `` bare was dropped
    from the token list specifically to stop this class of false positive.
    """
    body = (
        "The analysis provides insight. This assumes a stable basis. "
        "The crisis is real but the analysis is thorough.\n\n"
        "## Current state\n"
    )
    _, dbg = score_summary_currency(body)
    # Only ``provides `` should register; the stripped ``is `` substrings
    # hiding inside ``analysis``/``basis``/``crisis``/``This `` don't count.
    assert dbg["good_count"] == 1


def test_summary_currency_is_responsible_counts_once() -> None:
    """No double-count between dropped ``is `` and retained ``is responsible``."""
    body = "This system is responsible for onboarding.\n\n## Current state\n"
    _, dbg = score_summary_currency(body)
    # ``is responsible`` alone should fire; no sibling ``is `` hit.
    assert dbg["good_count"] == 1


# --- concept_shape case-insensitive (C1) -----------------------------------


def test_concept_shape_case_insensitive_match() -> None:
    body = "## testing results\n\nX.\n\n## BUG REPORT\n\nY.\n"
    score, dbg = score_concept_shape(body)
    assert dbg["count_bad"] == 2
    # 2 bad H2s x -1 = 8 under the softened 2026-04-23 weight.
    assert score == 8


# --- _select_topic_paths hub filter (F2) -----------------------------------


def test_select_topic_paths_excludes_index_md(tmp_path: Path) -> None:
    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "index.md").write_text("listing", encoding="utf-8")
    (topics / "home.md").write_text("home", encoding="utf-8")
    (topics / "alpha.md").write_text("a", encoding="utf-8")
    (topics / "beta.md").write_text("b", encoding="utf-8")

    selected = _select_topic_paths(topics, pages=None, limit=None)
    stems = {p.stem for p in selected}
    assert stems == {"alpha", "beta"}


def test_select_topic_paths_explicit_pages_honours_hub_request(tmp_path: Path) -> None:
    """--pages index still resolves — operators asking by name have reasons."""
    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "index.md").write_text("listing", encoding="utf-8")
    (topics / "alpha.md").write_text("a", encoding="utf-8")

    selected = _select_topic_paths(topics, pages="index", limit=None)
    assert [p.stem for p in selected] == ["index"]


# --- build_wikilink_incoming_index hub-page filter (F3) --------------------


def test_build_wikilink_incoming_index_skips_generated_hub_outbound(
    tmp_path: Path,
) -> None:
    """Hub pages link to most topics — their outbound links must not inflate incoming."""
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "domains").mkdir(parents=True)
    # index.md is a hub listing — its [[topic/foo]] link shouldn't count.
    (wiki / "topics" / "index.md").write_text(
        "---\ntitle: Topics index\n---\n\n- [[topic/foo]]\n", encoding="utf-8"
    )
    # Same for a domains/ hub page.
    (wiki / "domains" / "seller.md").write_text(
        "---\ntitle: Seller domain\n---\n\n- [[topic/foo]]\n", encoding="utf-8"
    )
    # Real topic page linking to foo — this SHOULD count.
    (wiki / "topics" / "bar.md").write_text(
        "---\ntitle: Bar\n---\n\nBody mentions [[topic/foo]].\n", encoding="utf-8"
    )
    (wiki / "topics" / "foo.md").write_text("---\ntitle: Foo\n---\n\nBody.\n", encoding="utf-8")
    index, known = build_wikilink_incoming_index(wiki)
    # Only ``bar`` contributes — the two hub links are ignored.
    assert index == {"foo": 1}
    # But the hub pages are still in known_slugs so broken-outbound checks work.
    assert {"index", "seller", "bar", "foo"}.issubset(known)
