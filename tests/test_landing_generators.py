"""Tests for North-Star landing-page generators in src/compile/compiler.py.

These are pure-fs generators — no DB required — except `_generate_changes`,
which exercises both the no-conn and the injected-conn path.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from src.utils import extract_frontmatter
from src.wiki.domains import _DOMAINS
from src.wiki.landing import _generate_changes
from src.wiki.landing import _generate_home
from src.wiki.landing import _regenerate_decision_stubs
from src.wiki.landing import _regenerate_domain_hubs


def _write_page(path: Path, frontmatter: dict[str, object], body: str) -> None:
    """Helper — write a wiki page with frontmatter + body."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
    path.write_text(f"---\n{fm_text}\n---\n\n{body}", encoding="utf-8")


class _FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def execute(self, *_args: object, **_kwargs: object) -> _FakeCursor:
        return _FakeCursor(self._rows)


def test_regenerate_domain_hubs_creates_all_eight(tmp_path: Path) -> None:
    # Seed one topic and one system in different domains.
    _write_page(
        tmp_path / "topics" / "buymer-rollout.md",
        {"title": "BuyMer Rollout", "page_type": "topic", "status": "active"},
        "BuyMer is the new buyer experience with BuyLead integration.\n",
    )
    _write_page(
        tmp_path / "systems" / "mcat.md",
        {"title": "MCAT", "page_type": "system", "status": "active"},
        "MCAT is the microcatalog used in marketplace discovery and ranking.\n",
    )

    written = _regenerate_domain_hubs(tmp_path)
    assert len(written) == len(_DOMAINS) == 8

    buyer_hub = (tmp_path / "domains" / "buyer-experience.md").read_text()
    assert "[[topics/buymer-rollout]]" in buyer_hub
    assert "page_type: domain" in buyer_hub

    marketplace_hub = (tmp_path / "domains" / "marketplace-discovery.md").read_text()
    assert "[[systems/mcat]]" in marketplace_hub

    # Unpopulated domain still writes a page so home-page links resolve.
    ai_hub = (tmp_path / "domains" / "ai-automation.md").read_text()
    assert "*No topics yet.*" in ai_hub


def test_domain_hub_respects_explicit_frontmatter(tmp_path: Path) -> None:
    # Explicit domain wins even when keywords would have matched a different bucket.
    _write_page(
        tmp_path / "topics" / "custom.md",
        {
            "title": "BuyMer Migration Plan",  # would keyword-match buyer-experience
            "page_type": "topic",
            "status": "active",
            "domain": "engineering-productivity",
        },
        "Work stream about CI/CD pipelines.\n",
    )
    _regenerate_domain_hubs(tmp_path)
    eng_hub = (tmp_path / "domains" / "engineering-productivity.md").read_text()
    assert "[[topics/custom]]" in eng_hub
    buyer_hub = (tmp_path / "domains" / "buyer-experience.md").read_text()
    assert "[[topics/custom]]" not in buyer_hub


def test_domain_hub_multi_tag(tmp_path: Path) -> None:
    _write_page(
        tmp_path / "topics" / "multi.md",
        {
            "title": "Cross-domain Initiative",
            "page_type": "topic",
            "status": "active",
            "tags": ["buyer-experience", "trust-safety"],
        },
        "Cross-cutting initiative.\n",
    )
    _regenerate_domain_hubs(tmp_path)
    assert "[[topics/multi]]" in (tmp_path / "domains" / "buyer-experience.md").read_text()
    assert "[[topics/multi]]" in (tmp_path / "domains" / "trust-safety.md").read_text()


def test_domain_hub_idempotent(tmp_path: Path) -> None:
    _write_page(
        tmp_path / "topics" / "a.md",
        {"title": "BuyMer A", "page_type": "topic"},
        "BuyMer related content.\n",
    )
    _regenerate_domain_hubs(tmp_path)
    first = (tmp_path / "domains" / "buyer-experience.md").read_text()
    _regenerate_domain_hubs(tmp_path)
    second = (tmp_path / "domains" / "buyer-experience.md").read_text()
    assert first == second


def test_generate_home_lists_all_domains_and_recent(tmp_path: Path) -> None:
    _write_page(
        tmp_path / "topics" / "recent.md",
        {"title": "Recent", "page_type": "topic"},
        "Recent activity.\n",
    )
    _write_page(
        tmp_path / "systems" / "lens.md",
        {"title": "Lens", "page_type": "system"},
        "Lens visual search product.\n",
    )
    path = _generate_home(tmp_path)
    content = path.read_text()
    for slug, title, _keywords in _DOMAINS:
        assert f"[{title}](domains/{slug}.md)" in content
    assert "[[topics/recent]]" in content or "[[systems/lens]]" in content
    assert "page_type: home" in content


def test_generate_changes_no_db_writes_stub(tmp_path: Path) -> None:
    path = _generate_changes(tmp_path, db_conn=_FakeConn(rows=[]))
    content = path.read_text()
    assert "*No recent activity.*" in content
    assert "page_type: changes" in content


def test_generate_changes_groups_by_day(tmp_path: Path) -> None:
    # Post-2026-04-24: _generate_changes reads pages from the filesystem
    # (not compile_attempts from Postgres). Seed two pages touched on
    # different days and expect two day-grouped sections.
    now = datetime.now(UTC)
    _write_page(
        tmp_path / "topics" / "today-page.md",
        {
            "title": "Today Page",
            "page_type": "topic",
            "status": "active",
            "last_compiled": now.isoformat(),
        },
        "Content from today.\n",
    )
    _write_page(
        tmp_path / "topics" / "yesterday-page.md",
        {
            "title": "Yesterday Page",
            "page_type": "topic",
            "status": "active",
            "last_compiled": (now - timedelta(days=1)).isoformat(),
        },
        "Content from yesterday.\n",
    )
    path = _generate_changes(tmp_path)
    content = path.read_text()
    assert content.count("## ") >= 2
    assert "[[topics/today-page]]" in content
    assert "[[topics/yesterday-page]]" in content
    # Must NOT leak LLM compile-runtime detail any more.
    assert "minimax" not in content
    assert "grok" not in content


def test_regenerate_decision_stubs_creates_missing(tmp_path: Path) -> None:
    _write_page(
        tmp_path / "topics" / "ranking.md",
        {"title": "Ranking"},
        "Decision tracked as [[decision/reorder-ranking-formula]].\n",
    )
    written = _regenerate_decision_stubs(tmp_path)
    assert len(written) == 1
    stub = (tmp_path / "decisions" / "reorder-ranking-formula.md").read_text()
    fm = extract_frontmatter(stub)
    assert fm["page_type"] == "decision"
    assert "[[topics/ranking]]" in stub
    assert "Reorder Ranking Formula" in stub


def test_regenerate_decision_stubs_preserves_body_refreshes_refs(tmp_path: Path) -> None:
    _write_page(
        tmp_path / "topics" / "one.md",
        {"title": "One"},
        "See [[decision/choose-model]].\n",
    )
    _regenerate_decision_stubs(tmp_path)

    # Simulate human-edited body on the stub.
    existing = tmp_path / "decisions" / "choose-model.md"
    body = existing.read_text()
    body = body.replace(
        "<TODO: enrich from referencing topic(s)>",
        "Human-authored decision context goes here.",
    )
    existing.write_text(body, encoding="utf-8")

    # Add a second referencing topic and re-run.
    _write_page(
        tmp_path / "topics" / "two.md",
        {"title": "Two"},
        "Also see [[decision/choose-model]].\n",
    )
    _regenerate_decision_stubs(tmp_path)

    refreshed = existing.read_text()
    assert "Human-authored decision context goes here." in refreshed
    assert "[[topics/one]]" in refreshed
    assert "[[topics/two]]" in refreshed


def test_regenerate_decision_stubs_no_backlinks_nothing_written(tmp_path: Path) -> None:
    _write_page(
        tmp_path / "topics" / "noop.md",
        {"title": "No-op"},
        "No decision links here.\n",
    )
    written = _regenerate_decision_stubs(tmp_path)
    assert written == []
    assert not (tmp_path / "decisions").exists()
