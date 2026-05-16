"""Unit + subprocess tests for scripts/wiki_quality_metrics.py.

The fixture wiki at ``tests/fixtures/metrics_fixture/wiki/`` has:
- ``topics/project-a.md``   (>1000 B body, links ``[[jane]]``)
- ``topics/project-b.md``   (>500 B body, no outgoing links, orphan)
- ``entities/jane.md``      (>500 B body, links ``[[project-a]]``)
- ``systems/lens.md``       (<500 B body, stub, orphan)
- ``index.md``              (links ``[[jane]]`` and ``[[lens]]``)

Expected metrics derivation is documented inline in each test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.wiki_quality_metrics import collect_metrics  # noqa: E402
from scripts.wiki_quality_metrics import main as metrics_main  # noqa: E402

FIXTURE_WIKI = REPO_ROOT / "tests" / "fixtures" / "metrics_fixture" / "wiki"
SCRIPT_PATH = REPO_ROOT / "scripts" / "wiki_quality_metrics.py"


@pytest.fixture
def fixture_wiki() -> Path:
    """Return the fixture wiki path; guard against an accidentally-missing fixture."""
    assert FIXTURE_WIKI.exists(), f"fixture missing: {FIXTURE_WIKI}"
    return FIXTURE_WIKI


def test_page_counts_by_type(fixture_wiki: Path) -> None:
    """topics=2, entities=1, systems=1, rest=0. total=4."""
    metrics = collect_metrics(fixture_wiki)
    assert metrics["pages_by_type"] == {
        "topics": 2,
        "entities": 1,
        "systems": 1,
        "policies": 0,
        "timelines": 0,
        "conflicts": 0,
    }
    assert metrics["total_pages"] == 4


def test_topic_to_entity_ratio(fixture_wiki: Path) -> None:
    """2 topics / 1 entity = 2.0."""
    metrics = collect_metrics(fixture_wiki)
    assert metrics["topic_to_entity_ratio"] == 2.0


def test_stub_rate_pct(fixture_wiki: Path) -> None:
    """Only lens is < 500 B, so 1/4 = 25.0%."""
    metrics = collect_metrics(fixture_wiki)
    assert metrics["total_stubs"] == 1
    assert metrics["stub_rate_pct"] == 25.0
    assert metrics["stubs_by_type"]["systems"] == 1
    assert metrics["stubs_by_type"]["topics"] == 0
    assert metrics["stubs_by_type"]["entities"] == 0


def test_orphan_count(fixture_wiki: Path) -> None:
    """Orphans (no incoming wikilinks from other category pages) are
    project-b and lens — jane has incoming from project-a, and project-a
    has incoming from jane. index.md does NOT count toward incoming."""
    metrics = collect_metrics(fixture_wiki)
    assert metrics["orphan_count"] == 2


def test_pages_only_reachable_from_index(fixture_wiki: Path) -> None:
    """jane has exactly 1 incoming category-link (from project-a) AND
    is listed in index.md — so she counts. project-a has 1 incoming
    (from jane) but is NOT in index.md. lens is in index.md but has 0
    incoming. So only jane qualifies."""
    metrics = collect_metrics(fixture_wiki)
    assert metrics["pages_only_reachable_from_index"] == 1


def test_avg_body_bytes_by_type_nonzero_for_present_types(fixture_wiki: Path) -> None:
    """Sanity: present categories have a non-zero avg, empty ones are 0."""
    metrics = collect_metrics(fixture_wiki)
    avg = metrics["avg_body_bytes_by_type"]
    assert avg["topics"] > 500
    assert avg["entities"] > 500
    assert avg["systems"] > 0
    assert avg["policies"] == 0
    assert avg["timelines"] == 0
    assert avg["conflicts"] == 0


def test_missing_wiki_dir_exits_2(tmp_path: Path) -> None:
    """Pointing at a non-existent wiki should exit 2, not crash."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--wiki-dir",
            str(tmp_path / "nope"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "wiki dir not found" in result.stderr


def test_cli_passes_when_ratio_above_threshold(fixture_wiki: Path) -> None:
    """Ratio 2.0 >= 0.1 → exit 0."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--wiki-dir",
            str(fixture_wiki),
            "--min-topic-ratio",
            "0.1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "pages=4" in result.stdout
    assert "stubs=1" in result.stdout


def test_cli_fails_when_ratio_below_threshold(fixture_wiki: Path) -> None:
    """Ratio 2.0 < 10.0 → exit 1 with a FAIL marker in stderr."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--wiki-dir",
            str(fixture_wiki),
            "--min-topic-ratio",
            "10.0",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "FAIL" in result.stderr
    assert "topic_to_entity_ratio" in result.stderr


def test_cli_json_flag_emits_parseable_json(fixture_wiki: Path) -> None:
    """--json emits only JSON to stdout; no prefix single-line summary."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--wiki-dir",
            str(fixture_wiki),
            "--json",
            "--min-topic-ratio",
            "0.1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["total_pages"] == 4
    assert parsed["orphan_count"] == 2
    assert parsed["topic_to_entity_ratio"] == 2.0


def test_collect_metrics_handles_no_entities(tmp_path: Path) -> None:
    """If entities/ is empty (divide-by-zero guard), ratio is None."""
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics" / "only-topic.md").write_text(
        "---\ntitle: Only Topic\n---\n\n" + ("x" * 600),
        encoding="utf-8",
    )
    metrics = collect_metrics(tmp_path)
    assert metrics["pages_by_type"]["topics"] == 1
    assert metrics["pages_by_type"]["entities"] == 0
    assert metrics["topic_to_entity_ratio"] is None


def test_collect_metrics_empty_wiki(tmp_path: Path) -> None:
    """Empty wiki → all zeros, no crash on divide-by-zero."""
    metrics = collect_metrics(tmp_path)
    assert metrics["total_pages"] == 0
    assert metrics["total_stubs"] == 0
    assert metrics["stub_rate_pct"] == 0.0
    assert metrics["orphan_count"] == 0
    assert metrics["topic_to_entity_ratio"] is None


def test_main_is_a_click_command() -> None:
    """Sanity: ``main`` is a click Command so subprocess invocation works."""
    import click as _click

    assert isinstance(metrics_main, _click.Command)
