"""Unit tests for scripts/post_run_metrics.py — the prompt-revamp dipstick.

We test the deterministic helpers (lead-paragraph detector, archetype
classifier, owner-frontmatter check, FS sweep, markdown rendering) but
skip the langfuse + DB paths — those are exercised end-to-end via
``uv run python scripts/post_run_metrics.py`` rather than mocked here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.post_run_metrics import MetricResult  # noqa: E402
from scripts.post_run_metrics import Report  # noqa: E402
from scripts.post_run_metrics import _detect_archetype  # noqa: E402
from scripts.post_run_metrics import _has_owner_frontmatter  # noqa: E402
from scripts.post_run_metrics import _has_strikethrough  # noqa: E402
from scripts.post_run_metrics import _has_tldr_h2  # noqa: E402
from scripts.post_run_metrics import _lead_has_number_and_two_sentences  # noqa: E402
from scripts.post_run_metrics import _lead_paragraph  # noqa: E402
from scripts.post_run_metrics import _new_pages_since  # noqa: E402
from scripts.post_run_metrics import _people_wikilink_count  # noqa: E402
from scripts.post_run_metrics import compute_filesystem_metrics  # noqa: E402
from scripts.post_run_metrics import render_markdown  # noqa: E402

# ---------------------------------------------------------------------------
# M1: owner frontmatter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fm", "expected"),
    [
        ({"owner": "asha"}, True),
        ({"owner": "asha@indiamart.com"}, True),
        ({"owner": "  "}, False),
        ({"owner": ""}, False),
        ({"owner": ["asha", "bob"]}, True),
        ({"owner": []}, False),
        ({"owner": [""]}, False),
        ({"owner": None}, False),
        ({}, False),
    ],
)
def test_has_owner_frontmatter(fm: dict[str, object], expected: bool) -> None:
    assert _has_owner_frontmatter(fm) is expected


# ---------------------------------------------------------------------------
# M2: lead paragraph + number + two-sentence rule
# ---------------------------------------------------------------------------


def test_lead_paragraph_skips_blank_lines_and_headings() -> None:
    body = "\n\n\nLens shipped 5 pilot stores in March. It serves 12 SKUs.\n\n## Detail\n"
    assert _lead_paragraph(body) == "Lens shipped 5 pilot stores in March. It serves 12 SKUs."


def test_lead_paragraph_skips_initial_h1() -> None:
    body = "# Title\n\nFirst sentence here. Second sentence with 5 items.\n"
    assert _lead_paragraph(body) == "First sentence here. Second sentence with 5 items."


def test_lead_paragraph_breaks_on_blank_line() -> None:
    body = "Line one. Line two with 7.\n\nThis should not be in the lead.\n"
    assert _lead_paragraph(body) == "Line one. Line two with 7."


def test_lead_has_number_passes_for_well_formed_lead() -> None:
    body = "Lens is the visual search system. It indexed 4M SKUs in Q1.\n"
    assert _lead_has_number_and_two_sentences(body) is True


def test_lead_has_number_fails_when_no_digit() -> None:
    body = "This is the description. It has two sentences but no number.\n"
    assert _lead_has_number_and_two_sentences(body) is False


def test_lead_has_number_fails_with_one_sentence() -> None:
    body = "One long descriptive sentence with the number 42 in it\n"
    assert _lead_has_number_and_two_sentences(body) is False


def test_lead_has_number_fails_for_empty_body() -> None:
    assert _lead_has_number_and_two_sentences("") is False


# ---------------------------------------------------------------------------
# M5/M6: TL;DR + strikethrough detectors
# ---------------------------------------------------------------------------


def test_tldr_h2_detected_case_insensitive() -> None:
    assert _has_tldr_h2("intro\n\n## TL;DR\n\nShort.") is True
    assert _has_tldr_h2("intro\n\n## tl;dr\n\nShort.") is True
    assert _has_tldr_h2("intro\n\n### TL;DR\n\nShort.") is False
    assert _has_tldr_h2("intro paragraph") is False


def test_strikethrough_detected() -> None:
    assert _has_strikethrough("removed ~~old plan~~ in favor of new") is True
    assert _has_strikethrough("just a single ~ in here") is False


# ---------------------------------------------------------------------------
# M7: people wikilinks
# ---------------------------------------------------------------------------


def test_people_wikilink_count_only_counts_people_namespace() -> None:
    body = (
        "See [[people/asha-foo]], [[people/bob-bar]], and [[topic/lens]] "
        "plus [[system/isq]]. Also [[people/cee-baz|Cee]]."
    )
    assert _people_wikilink_count(body) == 3


# ---------------------------------------------------------------------------
# M10: archetype detection
# ---------------------------------------------------------------------------


def test_archetype_directory_wins_for_decisions() -> None:
    assert _detect_archetype({"tags": ["launch"]}, "", "decisions") == "decision"


def test_archetype_falls_back_to_tags() -> None:
    assert _detect_archetype({"tags": ["bug", "p0"]}, "", "topics") == "bug"


def test_archetype_body_keyword_fallback() -> None:
    body = "Some intro\n\n## Launch\n\nLaunched in Q3."
    assert _detect_archetype({}, body, "topics") == "launch"


def test_archetype_other_when_unknown() -> None:
    assert _detect_archetype({"tags": ["misc"]}, "", "topics") == "other"


# ---------------------------------------------------------------------------
# Full FS sweep on a synthetic wiki tree
# ---------------------------------------------------------------------------


def _write_page(
    wiki_dir: Path, category: str, slug: str, frontmatter: dict[str, object], body: str
) -> Path:
    cat = wiki_dir / category
    cat.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        f"{k}: {v}" if not isinstance(v, list) else f"{k}: {v!r}" for k, v in frontmatter.items()
    ]
    p = cat / f"{slug}.md"
    p.write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body, encoding="utf-8")
    return p


def test_compute_filesystem_metrics_handles_empty_set() -> None:
    out = compute_filesystem_metrics([])
    assert out["owner_rate"] is None
    assert out["archetype_dist"] == {}


def test_compute_filesystem_metrics_smoke(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    p1 = _write_page(
        wiki_dir,
        "topics",
        "alpha",
        {"owner": "asha"},
        "Alpha is the new system. It shipped 3 features in Q1.\n",
    )
    p2 = _write_page(
        wiki_dir,
        "topics",
        "beta",
        {},
        "## TL;DR\n\nBeta details here without a number.\n",
    )
    p3 = _write_page(
        wiki_dir,
        "decisions",
        "scale-trust-50",
        {"owner": "lead"},
        "Decision to scale trust to 50%. Took effect 2026-04-15.\n",
    )
    pages = [p1, p2, p3]
    out = compute_filesystem_metrics(pages)
    # `sample_size_pages` is set by the caller (build_report), not by
    # compute_filesystem_metrics directly.
    assert pytest.approx(out["owner_rate"]) == 2 / 3
    # p1 + p3 have lead-with-number; p2 starts with TL;DR (skipped) but no number.
    assert pytest.approx(out["lead_with_number_rate"]) == 2 / 3
    assert pytest.approx(out["tldr_rate"]) == 1 / 3
    assert out["strikethrough_rate"] == 0.0
    assert out["archetype_dist"]["decision"] == 1


# ---------------------------------------------------------------------------
# _new_pages_since glob
# ---------------------------------------------------------------------------


def test_new_pages_since_baseline_returns_all(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    _write_page(wiki_dir, "topics", "a", {}, "body\n")
    _write_page(wiki_dir, "systems", "b", {}, "body\n")
    pages = _new_pages_since(wiki_dir, since=None)
    assert {p.name for p in pages} == {"a.md", "b.md"}


def test_new_pages_since_filters_future_cutoff_returns_empty(tmp_path: Path) -> None:
    """A cutoff in the future filters out every existing page.

    We can't reliably backdate ctime on macOS (os.utime only touches
    atime/mtime), so we exercise the "all-after-cutoff" direction here:
    no page in `wiki_dir` was created or modified after `now()`.
    """
    import datetime as _dt

    wiki_dir = tmp_path / "wiki"
    _write_page(wiki_dir, "topics", "a", {}, "a\n")
    _write_page(wiki_dir, "systems", "b", {}, "b\n")
    future_cutoff = _dt.datetime.now(_dt.UTC) + _dt.timedelta(hours=1)
    pages = _new_pages_since(wiki_dir, since=future_cutoff)
    assert pages == []


def test_new_pages_since_past_cutoff_returns_all(tmp_path: Path) -> None:
    import datetime as _dt

    wiki_dir = tmp_path / "wiki"
    _write_page(wiki_dir, "topics", "a", {}, "a\n")
    _write_page(wiki_dir, "systems", "b", {}, "b\n")
    past_cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)
    pages = _new_pages_since(wiki_dir, since=past_cutoff)
    assert {p.name for p in pages} == {"a.md", "b.md"}


# ---------------------------------------------------------------------------
# Markdown rendering (smoke + comparison column)
# ---------------------------------------------------------------------------


def _make_metric(
    name: str, value: float | None, target: str = "≥80%", unit: str = "pct"
) -> MetricResult:
    return MetricResult(
        name=name,
        label=f"label-{name}",
        value=value,
        target=target,
        sample_size=10,
        unit=unit,
    )


def test_metric_fmt_value_respects_unit() -> None:
    pct = _make_metric("M1", 0.85, unit="pct")
    raw = _make_metric("M7", 2.5, target="≤3", unit="raw")
    none_val = _make_metric("M9", None, unit="raw")
    assert pct.fmt_value() == "85.0%"
    assert raw.fmt_value() == "2.50"
    assert none_val.fmt_value() == "-"


def test_render_markdown_includes_metrics_table_without_prior() -> None:
    report = Report(
        run_id="r1",
        generated_at="2026-04-29T10:00:00+00:00",
        since="2026-04-29T09:00:00+00:00",
        new_pages_total=4,
        metrics=[_make_metric("M1", 0.85), _make_metric("M5", 0.0, "→0%")],
        archetype_dist={"launch": 2, "bug": 1, "other": 1},
    )
    md = render_markdown(report, prior=None)
    assert "Post-run metrics" in md
    assert "| M1 |" in md
    assert "85.0%" in md
    assert "M10 — archetype distribution" in md
    assert "| launch | 2 |" in md


def test_render_markdown_emits_delta_column_when_prior_present() -> None:
    report = Report(
        run_id="r1",
        generated_at="now",
        since=None,
        new_pages_total=2,
        metrics=[_make_metric("M1", 0.90)],
    )
    md = render_markdown(report, prior={"M1": 0.80})
    assert "Δ vs prior" in md
    assert "+10.0pp" in md


def test_render_markdown_warnings_block(tmp_path: Path) -> None:
    report = Report(
        run_id=None,
        generated_at="now",
        since=None,
        new_pages_total=0,
        metrics=[],
        warnings=["langfuse: unreachable"],
    )
    md = render_markdown(report, prior=None)
    assert "Partial report" in md
    assert "langfuse: unreachable" in md
