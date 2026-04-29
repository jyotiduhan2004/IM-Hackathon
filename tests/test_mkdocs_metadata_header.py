"""Tests for the per-page metadata header banner in mkdocs_hooks.

Invokes `on_page_markdown` (and the helper directly) on fake page objects
and on the synthetic `tests/fixtures/wiki_mini/` pages. We skip a full
mkdocs build — the hook is pure per-page markdown-in / markdown-out, so
asserting on its output covers the rendered HTML downstream.

The expected format is `N sources · last compiled YYYY-MM-DD · status: X`
and must render on every topic/entity/system/policy/timeline/conflict page,
including pages with missing or empty frontmatter fields.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mkdocs_hooks import _page_metadata_banner  # noqa: E402
from mkdocs_hooks import _render_status_badge  # noqa: E402
from mkdocs_hooks import on_page_markdown  # noqa: E402

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "wiki_mini"


@dataclass
class _FakeFile:
    src_path: str


@dataclass
class _FakePage:
    file: _FakeFile
    meta: dict = field(default_factory=dict)


def _page(src_path: str, meta: dict | None = None) -> _FakePage:
    return _FakePage(file=_FakeFile(src_path=src_path), meta=meta or {})


def _split_fixture(fixture_path: Path) -> tuple[dict, str]:
    """Parse the YAML frontmatter + body from a fixture file. mkdocs itself
    does this at load time and passes page.meta + body-only markdown to the
    hook, so we mirror that split to keep fixture tests realistic.
    """
    raw = fixture_path.read_text(encoding="utf-8")
    assert raw.startswith("---\n"), f"fixture missing frontmatter: {fixture_path}"
    _, fm_text, body = raw.split("---", 2)
    fm = yaml.safe_load(fm_text) or {}
    return fm, body.lstrip("\n")


# --- helper-level unit tests (no MkDocs plumbing) -------------------------


def test_banner_renders_all_three_fields_with_complete_frontmatter() -> None:
    fm = {
        "sources": ["raw/a.md", "raw/b.md", "raw/c.md"],
        "last_compiled": "2026-04-13T10:30:00+00:00",
        "status": "current",
    }
    banner = _page_metadata_banner(fm)
    assert banner.startswith("3 sources · last compiled 2026-04-13 · status: current")


def test_banner_shows_zero_when_sources_missing() -> None:
    # Empty-list and absent keys both surface "0 sources" — readers should
    # never see the banner crash or silently hide itself just because one
    # field is unset.
    for fm in ({}, {"sources": []}, {"sources": None}):
        banner = _page_metadata_banner(fm)
        assert banner.startswith("0 sources · ")


def test_banner_uses_unknown_when_last_compiled_missing() -> None:
    banner = _page_metadata_banner({"sources": ["raw/a.md"]})
    assert "last compiled unknown" in banner


def test_banner_preserves_stub_marker() -> None:
    # Backfilled stubs carry `last_compiled: stub` explicitly so readers can
    # tell they are placeholders rather than freshly compiled pages.
    banner = _page_metadata_banner({"last_compiled": "stub"})
    assert "last compiled stub" in banner


def test_banner_defaults_status_to_active() -> None:
    # Phase 0 runtime hardening flipped the fallback from `current` →
    # `active` so pages missing frontmatter `status:` no longer surface
    # the legacy value the C1/C2 migrations just emptied.
    banner = _page_metadata_banner({})
    assert "status: active" in banner


def test_banner_surfaces_non_current_status() -> None:
    banner = _page_metadata_banner({"status": "superseded"})
    assert "status: superseded" in banner


def test_banner_strips_time_component_from_iso_timestamp() -> None:
    banner = _page_metadata_banner({"last_compiled": "2026-04-13T10:30:00+00:00"})
    assert "2026-04-13" in banner
    assert "10:30" not in banner


def test_banner_survives_non_list_sources() -> None:
    # Robustness: if frontmatter got corrupted and `sources` is a string
    # or dict, don't crash — fall back to 0.
    banner = _page_metadata_banner({"sources": "raw/a.md"})
    assert banner.startswith("0 sources · ")


# --- on_page_markdown integration tests ----------------------------------


def test_hook_splices_banner_after_h1_on_topic_pages() -> None:
    meta = {
        "title": "Example Topic",
        "page_type": "topic",
        "status": "current",
        "last_compiled": "2026-04-13T10:30:00+00:00",
        "sources": ["raw/a.md", "raw/b.md", "raw/c.md"],
    }
    body = "# Example Topic\n\nSome body.\n"
    out = on_page_markdown(body, page=_page("topics/example.md", meta), config={}, files=[])
    lines = out.splitlines()
    h1_idx = next(i for i, line in enumerate(lines) if line.startswith("# "))
    banner_text = "3 sources · last compiled 2026-04-13 · status: current"
    # Layout is H1 → status pill → banner → body, so the banner sits a few
    # lines further down than under the older header-only flow.
    assert banner_text in "\n".join(lines[h1_idx + 1 : h1_idx + 6])


def test_hook_prepends_banner_on_entity_page_without_h1() -> None:
    # Entity pages rely on Material to auto-generate the h1 from `title:`,
    # so the body has no h1 line. The banner should still appear in the
    # rendered markdown (it ends up right under the auto-h1).
    meta = {
        "title": "Jane Doe",
        "page_type": "entity",
        "status": "current",
        "last_compiled": "2026-04-12",
        "sources": [],
    }
    body = "Email: jane@indiamart.com\n\nBody text.\n"
    out = on_page_markdown(body, page=_page("entities/jane.md", meta), config={}, files=[])
    assert "0 sources · last compiled 2026-04-12 · status: current" in out


def test_hook_renders_banner_on_system_page_with_missing_last_compiled() -> None:
    meta = {
        "title": "Legacy System",
        "page_type": "system",
        "status": "superseded",
        "sources": ["raw/legacy.md"],
    }
    body = "# Legacy System\n\nOlder system.\n"
    out = on_page_markdown(body, page=_page("systems/legacy.md", meta), config={}, files=[])
    assert "1 source · last compiled unknown · status: superseded" in out


def test_singular_source_uses_singular_noun() -> None:
    """1 source (singular) — not '1 sources'."""
    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "current",
        "last_compiled": "2026-04-13",
        "sources": ["raw/a.md"],
    }
    out = on_page_markdown("# Topic\n", page=_page("topics/t.md", meta), config={}, files=[])
    assert "1 source · last compiled" in out
    assert "1 sources · " not in out


def test_zero_and_many_sources_use_plural_noun() -> None:
    for count in (0, 2, 5):
        meta = {
            "title": "Topic",
            "page_type": "topic",
            "status": "current",
            "last_compiled": "2026-04-13",
            "sources": [f"raw/{i}.md" for i in range(count)],
        }
        out = on_page_markdown("# Topic\n", page=_page("topics/t.md", meta), config={}, files=[])
        assert f"{count} sources · last compiled" in out


def test_hook_skips_banner_on_index_page() -> None:
    # index.md and log.md bypass the whole hook — no banner either.
    meta = {"title": "Index", "sources": ["raw/x.md"]}
    out = on_page_markdown("# Index\n", page=_page("index.md", meta), config={}, files=[])
    assert "sources · last compiled" not in out


def test_hook_is_idempotent_on_rerun() -> None:
    """Calling the hook twice on the same body must not stack banners."""
    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "current",
        "last_compiled": "2026-04-13",
        "sources": ["raw/a.md"],
    }
    body = "# Topic\n\nBody.\n"
    once = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    twice = on_page_markdown(once, page=_page("topics/t.md", meta), config={}, files=[])
    # Idempotency: second pass must NOT add a second banner.
    assert twice.count("· last compiled 2026-04-13 · status: current") == 1


# --- fixture-driven tests (one of each page type) ------------------------


def test_fixture_topic_page_renders_full_banner() -> None:
    fm, body = _split_fixture(FIXTURE_ROOT / "topics" / "example-topic.md")
    out = on_page_markdown(body, page=_page("topics/example-topic.md", fm), config={}, files=[])
    assert "3 sources · last compiled 2026-04-13 · status: current" in out


def test_fixture_entity_page_renders_zero_sources_banner() -> None:
    fm, body = _split_fixture(FIXTURE_ROOT / "entities" / "jane-doe.md")
    out = on_page_markdown(body, page=_page("entities/jane-doe.md", fm), config={}, files=[])
    assert "0 sources · last compiled 2026-04-12 · status: current" in out


def test_fixture_system_page_renders_superseded_status() -> None:
    fm, body = _split_fixture(FIXTURE_ROOT / "systems" / "legacy-system.md")
    out = on_page_markdown(body, page=_page("systems/legacy-system.md", fm), config={}, files=[])
    assert "1 source · last compiled unknown · status: superseded" in out


# --- status pill tests ---------------------------------------------------


def test_status_badge_renders_for_each_north_star_value() -> None:
    assert 'class="ns-status ns-status-active">Active<' in _render_status_badge(
        {"status": "active"}
    )
    assert 'class="ns-status ns-status-superseded">Superseded<' in _render_status_badge(
        {"status": "superseded"}
    )
    assert 'class="ns-status ns-status-archived">Archived<' in _render_status_badge(
        {"status": "archived"}
    )


def test_status_badge_maps_legacy_current_to_active_pill() -> None:
    # On-disk pages still carry `status: current` during migration; the
    # viewer should show the same green "Active" pill the north-star values do.
    html = _render_status_badge({"status": "current"})
    assert 'class="ns-status ns-status-active">Active<' in html


def test_status_badge_keeps_contested_distinct() -> None:
    # Legacy `contested` has a red palette of its own until Week-2 backfill
    # collapses it into the new model — confirm it doesn't fold into active.
    html = _render_status_badge({"status": "contested"})
    assert 'class="ns-status ns-status-contested">Contested<' in html


def test_status_badge_empty_for_missing_or_unknown_status() -> None:
    assert _render_status_badge({}) == ""
    assert _render_status_badge({"status": None}) == ""
    assert _render_status_badge({"status": "bogus"}) == ""


def test_hook_splices_status_pill_under_h1() -> None:
    meta = {"title": "T", "page_type": "topic", "status": "active"}
    out = on_page_markdown("# T\n\nBody.\n", page=_page("topics/t.md", meta), config={}, files=[])
    lines = out.splitlines()
    h1_idx = next(i for i, line in enumerate(lines) if line.startswith("# "))
    pill_window = "\n".join(lines[h1_idx + 1 : h1_idx + 4])
    assert "ns-status-active" in pill_window


def test_hook_is_idempotent_for_status_pill() -> None:
    meta = {"title": "T", "page_type": "topic", "status": "active"}
    body = "# T\n\nBody.\n"
    once = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    twice = on_page_markdown(once, page=_page("topics/t.md", meta), config={}, files=[])
    assert twice.count("ns-status-active") == 1


def test_hook_omits_pill_when_status_missing() -> None:
    meta = {"title": "T", "page_type": "topic"}
    out = on_page_markdown("# T\n\nBody.\n", page=_page("topics/t.md", meta), config={}, files=[])
    assert "ns-status" not in out


# --- source_threads rendering (Phase A U5) -------------------------------


def test_hook_renders_threads_block_when_source_threads_present(monkeypatch) -> None:
    """Page with `source_threads:` only → render as 'Threads' block, not 'Sources'."""
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")  # force frontmatter path
    meta = {
        "title": "Thread-Only Topic",
        "page_type": "topic",
        "status": "active",
        "last_compiled": "2026-04-13",
        "source_threads": ["19b59cdc863ac109", "19aee0c7cc330376"],
    }
    body = "# Thread-Only Topic\n\nBody text.\n"
    out = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    # The threads block uses a thread emoji + "Threads" summary.
    assert "🧵 Threads (2)" in out
    # Each thread renders as a short-id tag (first 12 chars).
    assert "- Thread: `19b59cdc863a`" in out
    assert "- Thread: `19aee0c7cc33`" in out
    # Banner switches to "thread(s)" noun in the top-of-page stamp.
    assert "2 threads · last compiled 2026-04-13 · status: active" in out


def test_hook_prefers_source_threads_over_legacy_sources(monkeypatch) -> None:
    """Dual-write during migration: render threads, not the raw email block."""
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")
    meta = {
        "title": "Dual-Write Topic",
        "page_type": "topic",
        "status": "active",
        "last_compiled": "2026-04-13",
        "sources": ["raw/2026-04-01_hello_abc.md"],
        "source_threads": ["19b59cdc863ac109"],
    }
    body = "# Dual-Write Topic\n\nBody.\n"
    out = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    # Threads block wins.
    assert "🧵 Threads (1)" in out
    # Banner noun matches.
    assert "1 thread · last compiled" in out
    # Sources block must not duplicate the output.
    assert "📚 Sources" not in out


def test_hook_falls_back_to_sources_when_source_threads_absent(monkeypatch) -> None:
    """Legacy-only page (sources: only, no source_threads:) still renders sources."""
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")
    meta = {
        "title": "Legacy Topic",
        "page_type": "topic",
        "status": "active",
        "last_compiled": "2026-04-13",
        "sources": ["raw/2026-04-01_hello_abc.md"],
    }
    body = "# Legacy Topic\n\nBody.\n"
    out = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    assert "📚 Sources (1)" in out
    # Old-style banner noun.
    assert "1 source · last compiled" in out
    assert "🧵 Threads" not in out


def test_hook_skips_threads_block_when_list_empty(monkeypatch) -> None:
    """Empty `source_threads:` AND empty `sources:` → no block rendered."""
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")
    meta = {
        "title": "Empty Topic",
        "page_type": "topic",
        "status": "active",
        "last_compiled": "2026-04-13",
        "source_threads": [],
    }
    body = "# Empty Topic\n\nBody.\n"
    out = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    assert "🧵 Threads" not in out
    assert "📚 Sources" not in out


def test_hook_threads_banner_noun_pluralises_correctly(monkeypatch) -> None:
    """Singular 'thread' for 1, plural 'threads' for 0 / 2+."""
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")

    def _banner_with(count: int) -> str:
        meta = {
            "title": "T",
            "page_type": "topic",
            "status": "active",
            "source_threads": [f"abcdef{i:010x}" for i in range(count)],
        }
        return on_page_markdown("# T\n", page=_page("topics/t.md", meta), config={}, files=[])

    assert "1 thread · " in _banner_with(1)
    assert "2 threads · " in _banner_with(2)


def test_hook_banner_idempotent_with_source_threads(monkeypatch) -> None:
    """Re-running the hook must not stack the top-of-page banner.

    Mirrors the existing sources-path idempotency guarantee (see
    test_hook_is_idempotent_on_rerun) — the banner block re-check is the
    only current idempotency contract. The Sources/Threads collapsible
    block stacking on re-entry is an existing behavior (MkDocs doesn't
    invoke the hook twice in practice) and outside U5 scope.
    """
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")
    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "active",
        "last_compiled": "2026-04-13",
        "source_threads": ["19b59cdc863ac109"],
    }
    body = "# Topic\n\nBody.\n"
    once = on_page_markdown(body, page=_page("topics/t.md", meta), config={}, files=[])
    twice = on_page_markdown(once, page=_page("topics/t.md", meta), config={}, files=[])
    # The banner must not double up (mirrors the sources-path contract).
    assert twice.count("last compiled 2026-04-13") == 1
