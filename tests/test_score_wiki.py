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
from scripts.score_wiki import _write_csv  # noqa: E402
from src.compile.scoring import build_wikilink_incoming_index  # noqa: E402
from src.compile.scoring import score_concept_shape  # noqa: E402
from src.compile.scoring import score_graph_health  # noqa: E402
from src.compile.scoring import score_source_density  # noqa: E402
from src.compile.scoring import score_summary_currency  # noqa: E402

# --- concept_shape ---------------------------------------------------------


def test_concept_shape_three_thread_subject_h2s_scores_4() -> None:
    body = (
        "Summary paragraph.\n\n"
        "## Bug Report\n\nX.\n\n"
        "## Final Decision\n\nY.\n\n"
        "## Launch Announcement\n\nZ.\n\n"
    )
    score, dbg = score_concept_shape(body)
    assert score == 4
    assert dbg["count_bad"] == 3
    assert set(dbg["bad_matches"]) == {"Bug Report", "Final Decision", "Launch Announcement"}


def test_concept_shape_only_good_h2s_scores_10() -> None:
    body = "## Current state\n\nWords.\n\n## How it works\n\nMore.\n\n## Related\n\n- [[other]]\n"
    score, dbg = score_concept_shape(body)
    assert score == 10
    assert dbg["count_bad"] == 0
    assert dbg["h2_titles"] == ["Current state", "How it works", "Related"]


def test_concept_shape_clamps_at_zero() -> None:
    body = "\n\n".join(
        f"## {h}\n\nx."
        for h in [
            "Bug Report",
            "Final Decision",
            "Launch Announcement",
            "Testing Results",
            "QA Results",
            "Release Notes",
        ]
    )
    score, _ = score_concept_shape(body)
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
    # 101 words, target = 101/150 ≈ 0.67 → sources/target ≈ 1.49 → score 10 (capped).
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


# --- graph_health ----------------------------------------------------------


def test_graph_health_five_incoming_no_broken_scores_ten() -> None:
    score, dbg = score_graph_health(
        slug="foo",
        body="## Related\n\n- [[bar]]\n- [[topic/baz]]\n",
        wikilink_index={"foo": 5},
        known_slugs={"foo", "bar", "baz"},
    )
    assert score == 10
    assert dbg["incoming"] == 5
    assert dbg["broken_outgoing"] == 0


def test_graph_health_two_incoming_two_broken_clamps_to_zero() -> None:
    score, dbg = score_graph_health(
        slug="foo",
        body="## Related\n\n- [[missing-1]]\n- [[topic/missing-2]]\n",
        wikilink_index={"foo": 2},
        known_slugs={"foo"},
    )
    # 2*2 - 3*2 = -2 → clamped to 0.
    assert score == 0
    assert dbg["broken_outgoing"] == 2


def test_graph_health_isolated_page_scores_zero() -> None:
    score, dbg = score_graph_health(
        slug="lonely",
        body="No outgoing links.\n",
        wikilink_index={},
        known_slugs={"lonely"},
    )
    assert score == 0
    assert dbg["incoming"] == 0
    assert dbg["outgoing_count"] == 0


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
            "mean": 6.5,
            "sum": 26,
            "_debug": {},
        }
    ]
    out = tmp_path / "out.csv"
    _write_csv(out, rows)
    with out.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
    assert tuple(header) == CSV_COLUMNS
