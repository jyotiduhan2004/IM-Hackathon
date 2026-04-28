"""Tests for the MkDocs on_page_markdown hook — external badge rendering.

Hook expects a `page` object with `page.file.src_path` and `page.meta`. We
build minimal stand-ins rather than importing MkDocs internals so the tests
stay fast and decoupled from MkDocs' evolving API.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mkdocs_hooks import on_page_markdown  # noqa: E402


@dataclass
class _FakeFile:
    src_path: str


@dataclass
class _FakePage:
    file: _FakeFile
    meta: dict = field(default_factory=dict)


def _page(src_path: str, meta: dict | None = None) -> _FakePage:
    return _FakePage(file=_FakeFile(src_path=src_path), meta=meta or {})


BADGE_HTML = '<span class="ext-badge" title="External contact (not @indiamart.com)">external</span>'


def test_external_badge_renders_for_entity_with_is_external_true() -> None:
    meta = {
        "title": "Jane External",
        "page_type": "entity",
        "status": "current",
        "is_external": True,
    }
    body = "Email: jane@external.com\n\nSome content.\n"
    out = on_page_markdown(body, page=_page("entities/jane.md", meta), config={}, files=[])
    assert BADGE_HTML in out


def test_no_badge_when_is_external_false() -> None:
    meta = {
        "title": "Bob Internal",
        "page_type": "entity",
        "status": "current",
        "is_external": False,
    }
    body = "Email: bob@indiamart.com\n\nSome content.\n"
    out = on_page_markdown(body, page=_page("entities/bob.md", meta), config={}, files=[])
    assert "ext-badge" not in out


def test_no_badge_when_is_external_missing() -> None:
    meta = {
        "title": "Alice Unknown",
        "page_type": "entity",
        "status": "current",
    }
    body = "Email: alice@indiamart.com\n\nSome content.\n"
    out = on_page_markdown(body, page=_page("entities/alice.md", meta), config={}, files=[])
    assert "ext-badge" not in out


def test_hook_does_not_crash_on_page_without_frontmatter() -> None:
    # Page has no meta at all — hook should still return something sensible.
    body = "# Orphan\n\nNo frontmatter here.\n"
    out = on_page_markdown(body, page=_page("topics/orphan.md", meta=None), config={}, files=[])
    assert "ext-badge" not in out
    assert "Orphan" in out


def test_badge_attached_to_h1_when_present() -> None:
    # Topics have explicit h1 — badge should splice into that line so it
    # visually sits next to the title rather than floating above it.
    meta = {
        "title": "Edge Topic",
        "page_type": "topic",
        "status": "current",
        "is_external": True,
    }
    body = "# Edge Topic\n\nSome body text.\n"
    out = on_page_markdown(body, page=_page("topics/edge.md", meta), config={}, files=[])
    assert BADGE_HTML in out
    # The badge should be on the same line as the h1, not a separate paragraph.
    h1_line = next(line for line in out.splitlines() if line.startswith("# "))
    assert BADGE_HTML in h1_line


def test_badge_skipped_for_non_wiki_pages() -> None:
    # index.md / log.md bypass the whole hook — badge should never render.
    meta = {"title": "Index", "is_external": True}
    body = "# Index\n"
    out = on_page_markdown(body, page=_page("index.md", meta), config={}, files=[])
    assert "ext-badge" not in out


# --- north-star decoration coverage ---------------------------------------
#
# The hook decorates pages under `domains/` and `decisions/` as well as the
# generated top-level pages (`home.md`, `glossary.md`, `changes.md`).
# `log.md` and other top-level files stay bare.


def test_hook_decorates_domains_pages() -> None:
    meta = {
        "title": "Engineering Domain",
        "page_type": "domain",
        "status": "active",
        "sources": ["raw/a.md"],
        "last_compiled": "2026-04-15",
    }
    body = "# Engineering Domain\n\nHub.\n"
    out = on_page_markdown(
        body, page=_page("domains/engineering/index.md", meta), config={}, files=[]
    )
    assert "ns-status-active" in out
    assert "1 source · last compiled 2026-04-15" in out


def test_hook_decorates_decisions_pages() -> None:
    meta = {
        "title": "Decision",
        "page_type": "decision",
        "status": "active",
        "sources": [],
        "last_compiled": "2026-04-15",
    }
    body = "# Decision\n\nBody.\n"
    out = on_page_markdown(
        body, page=_page("decisions/001-some-decision.md", meta), config={}, files=[]
    )
    assert "ns-status-active" in out
    assert "0 sources · last compiled 2026-04-15" in out


def test_hook_decorates_top_level_home_glossary_changes() -> None:
    # home.md / glossary.md / changes.md are generated top-level pages; the
    # north-star viewer wants status + banner on them too.
    for src_path in ("home.md", "glossary.md", "changes.md"):
        meta = {
            "title": "Top",
            "page_type": "index",
            "status": "active",
            "sources": [],
            "last_compiled": "2026-04-15",
        }
        body = "# Top\n\nBody.\n"
        out = on_page_markdown(body, page=_page(src_path, meta), config={}, files=[])
        assert "ns-status-active" in out, f"status pill missing on {src_path}"
        assert "last compiled 2026-04-15" in out, f"banner missing on {src_path}"


def test_hook_still_skips_legacy_log_md() -> None:
    # log.md is the legacy chronological log — still skipped. changes.md
    # (new generated changelog) is the one that should decorate.
    meta = {"title": "Log", "status": "active", "sources": []}
    out = on_page_markdown("# Log\n", page=_page("log.md", meta), config={}, files=[])
    assert "ns-status-" not in out
    assert "last compiled" not in out


def test_hook_still_skips_unrelated_top_level_files() -> None:
    # about.md (hand-written) is not in the allowlist — decoration would be
    # surprising on pages humans write directly.
    meta = {"title": "About", "status": "active", "sources": []}
    out = on_page_markdown("# About\n", page=_page("about.md", meta), config={}, files=[])
    assert "ns-status-" not in out
    assert "last compiled" not in out


# --- NS_CATALOG_SOURCES flag coverage -------------------------------------
#
# Default-on as of C3b: the catalog (message_touched_pages JOIN messages) owns
# the full source history, so the hook calls `get_sources_for_slug(slug)` on
# every page and falls back to frontmatter only when the DB is unreachable or
# returns empty. Set `NS_CATALOG_SOURCES=0` / `false` / `no` to force the
# legacy frontmatter-only path (e.g. a DB-less docs build).


def _set_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NS_CATALOG_SOURCES", "1")


def _set_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NS_CATALOG_SOURCES", "0")


def test_default_is_catalog_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unset env → catalog path runs (default-on as of C3b).
    monkeypatch.delenv("NS_CATALOG_SOURCES", raising=False)

    called: list[str] = []

    def _fake(slug: str, *, limit: int = 50) -> list[dict]:
        called.append(slug)
        return [
            {"raw_path": "raw/msg-db.md", "subject": "s", "date": None, "from_address": ""},
        ]

    monkeypatch.setattr("src.db.touched_pages.get_sources_for_slug", _fake, raising=True)

    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "active",
        "sources": ["raw/msg-fm.md"],  # stale; should be ignored
        "last_compiled": "2026-04-15",
    }
    out = on_page_markdown(
        "# Topic\n\nBody.\n", page=_page("topics/topic.md", meta), config={}, files=[]
    )
    assert called == ["topic"], "catalog lookup should run by default"
    assert "raw/msg-db.md" in out
    assert "raw/msg-fm.md" not in out


def test_explicit_flag_off_reads_sources_from_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NS_CATALOG_SOURCES=0 forces the legacy frontmatter-only path.
    _set_flag_off(monkeypatch)

    def _should_not_be_called(slug: str, *, limit: int = 50) -> list[dict]:
        raise AssertionError(f"catalog path ran despite flag off: slug={slug}")

    monkeypatch.setattr(
        "src.db.touched_pages.get_sources_for_slug", _should_not_be_called, raising=True
    )

    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "active",
        "sources": ["raw/msg-fm.md"],
        "last_compiled": "2026-04-15",
    }
    out = on_page_markdown(
        "# Topic\n\nBody.\n", page=_page("topics/topic.md", meta), config={}, files=[]
    )
    assert "raw/msg-fm.md" in out
    assert "<summary>📚 Sources (1)</summary>" in out


def test_flag_on_uses_catalog_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_flag_on(monkeypatch)

    def _fake(slug: str, *, limit: int = 50) -> list[dict]:
        assert slug == "whatsapp-handoff"
        return [
            {
                "raw_path": "raw/msg-db-new.md",
                "subject": "Newer",
                "date": None,
                "from_address": "x@y.z",
            },
            {
                "raw_path": "raw/msg-db-old.md",
                "subject": "Older",
                "date": None,
                "from_address": "x@y.z",
            },
        ]

    monkeypatch.setattr("src.db.touched_pages.get_sources_for_slug", _fake, raising=True)

    # Frontmatter still says only one stale source — the catalog path MUST win.
    meta = {
        "title": "WhatsApp Handoff",
        "page_type": "topic",
        "status": "active",
        "sources": ["raw/msg-stale-fm.md"],
        "last_compiled": "2026-04-15",
    }
    out = on_page_markdown(
        "# WhatsApp Handoff\n\nBody.\n",
        page=_page("topics/whatsapp-handoff.md", meta),
        config={},
        files=[],
    )
    assert "raw/msg-db-new.md" in out
    assert "raw/msg-db-old.md" in out
    assert "raw/msg-stale-fm.md" not in out
    assert "<summary>📚 Sources (2)</summary>" in out


def test_flag_on_falls_back_to_frontmatter_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_flag_on(monkeypatch)

    def _boom(slug: str, *, limit: int = 50) -> list[dict]:
        raise RuntimeError("db unreachable")

    monkeypatch.setattr("src.db.touched_pages.get_sources_for_slug", _boom, raising=True)

    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "active",
        "sources": ["raw/msg-fm.md"],
        "last_compiled": "2026-04-15",
    }
    out = on_page_markdown(
        "# Topic\n\nBody.\n", page=_page("topics/topic.md", meta), config={}, files=[]
    )
    # Build succeeds and frontmatter list appears — graceful degradation.
    assert "raw/msg-fm.md" in out
    assert "<summary>📚 Sources (1)</summary>" in out


def test_flag_on_falls_back_to_frontmatter_when_catalog_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Backfill hasn't populated this slug yet — don't blank out the block;
    # show what frontmatter has so the viewer still looks populated.
    _set_flag_on(monkeypatch)
    monkeypatch.setattr(
        "src.db.touched_pages.get_sources_for_slug",
        lambda slug, *, limit=50: [],
        raising=True,
    )

    meta = {
        "title": "Topic",
        "page_type": "topic",
        "status": "active",
        "sources": ["raw/msg-fm.md"],
        "last_compiled": "2026-04-15",
    }
    out = on_page_markdown(
        "# Topic\n\nBody.\n", page=_page("topics/topic.md", meta), config={}, files=[]
    )
    assert "raw/msg-fm.md" in out


def test_flag_on_entity_cap_uses_newest_first_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    # Catalog returns newest-first. Entity pages >20 sources show first 10.
    _set_flag_on(monkeypatch)

    rows = [
        {"raw_path": f"raw/msg-{i:02d}.md", "subject": f"s{i}", "date": None, "from_address": ""}
        for i in range(30)  # 0 = newest, 29 = oldest
    ]
    monkeypatch.setattr(
        "src.db.touched_pages.get_sources_for_slug", lambda slug, *, limit=50: rows, raising=True
    )

    meta = {
        "title": "Jane",
        "page_type": "entity",
        "status": "current",
        "sources": [],
        "last_compiled": "2026-04-15",
    }
    out = on_page_markdown(
        "Email: jane@example.com\n\nBody.\n",
        page=_page("entities/jane.md", meta),
        config={},
        files=[],
    )
    # Summary shows total (30); only the 10 newest render; +20 older hint present.
    assert "<summary>📚 Sources (30)</summary>" in out
    assert "raw/msg-00.md" in out  # newest
    assert "raw/msg-09.md" in out  # 10th newest — boundary
    assert "raw/msg-10.md" not in out  # 11th newest — cut off
    assert "raw/msg-29.md" not in out  # oldest — cut off
    assert "+20 older sources" in out


# --- people/ decoration (Tier P) -------------------------------------------


def test_hook_decorates_people_pages() -> None:
    """People/ pages get the same decoration treatment as entities/ during
    the C1 migration. Status pill + metadata banner must render so the viewer
    shows person pages alongside entity pages consistently."""
    meta = {
        "title": "Alice Person",
        "page_type": "person",
        "status": "active",
        "sources": ["raw/a.md"],
        "last_compiled": "2026-04-16",
        "email": "alice@example.com",
    }
    body = "Email: alice@example.com\n\nSome content about Alice.\n"
    out = on_page_markdown(body, page=_page("people/alice.md", meta), config={}, files=[])
    assert "ns-status-active" in out
    assert "1 source · last compiled 2026-04-16" in out


def test_hook_person_page_external_badge_renders() -> None:
    """External flag flows through for person pages too, not just entity."""
    meta = {
        "title": "Jane External Person",
        "page_type": "person",
        "status": "active",
        "is_external": True,
    }
    body = "Email: jane@external.com\n\nSome content.\n"
    out = on_page_markdown(body, page=_page("people/jane.md", meta), config={}, files=[])
    assert BADGE_HTML in out


# --- domain badges (v10-U2) -----------------------------------------------
#
# v10-U2 extends the frontmatter schema to accept `domains: [a, b]`
# alongside the legacy singular `domain: a`. The viewer renders one
# `ns-domain` badge per declared value on the page header so readers
# can see at a glance which domain hub(s) a topic belongs to.


def test_hook_renders_single_domain_badge() -> None:
    meta = {
        "title": "Seller Topic",
        "page_type": "topic",
        "status": "active",
        "domain": "seller-experience",
    }
    out = on_page_markdown(
        "# Seller Topic\n\nBody.\n", page=_page("topics/st.md", meta), config={}, files=[]
    )
    assert '<span class="ns-domain">seller-experience</span>' in out


def test_hook_renders_multi_domain_badges() -> None:
    """`domains: [a, b]` produces one badge per declared value."""
    meta = {
        "title": "Payment Fraud Sweep",
        "page_type": "topic",
        "status": "active",
        "domains": ["trust-safety", "growth-monetization"],
    }
    out = on_page_markdown(
        "# Payment Fraud Sweep\n\nBody.\n",
        page=_page("topics/pfs.md", meta),
        config={},
        files=[],
    )
    assert '<span class="ns-domain">trust-safety</span>' in out
    assert '<span class="ns-domain">growth-monetization</span>' in out


def test_hook_plural_wins_over_singular_domain() -> None:
    """Both fields set — plural drives rendering, singular is ignored."""
    meta = {
        "title": "Dual",
        "page_type": "topic",
        "status": "active",
        "domain": "ai-automation",
        "domains": ["seller-experience", "buyer-experience"],
    }
    out = on_page_markdown(
        "# Dual\n\nBody.\n", page=_page("topics/d.md", meta), config={}, files=[]
    )
    assert '<span class="ns-domain">seller-experience</span>' in out
    assert '<span class="ns-domain">buyer-experience</span>' in out
    assert '<span class="ns-domain">ai-automation</span>' not in out


def test_hook_no_domain_badge_when_fields_missing() -> None:
    meta = {"title": "Legacy", "page_type": "topic", "status": "active"}
    out = on_page_markdown(
        "# Legacy\n\nBody.\n", page=_page("topics/legacy.md", meta), config={}, files=[]
    )
    assert "ns-domain" not in out


# --- auto-rendered ## References (from inline [^msg-*] footnotes) ----------
#
# The agent now writes only inline `[^msg-xxxxxxxx]` markers in prose; the
# hook builds the matching `## References` definition block by resolving each
# hash against the raw/ filesystem index.


@pytest.fixture
def fake_raw_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand up a fake `raw/` directory with two emails and point the hook at it.

    Resets the module-level cache so each test gets a fresh index.
    """
    import mkdocs_hooks

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "2026-01-08_launchim-bl-latency-regression_cda09a3d.md").write_text("body")
    (raw_dir / "2026-01-08_launchim-nitin-missed-call-bug_19b9dc5e.md").write_text("body")
    monkeypatch.setattr(mkdocs_hooks, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mkdocs_hooks, "_raw_index_cache", None)
    return tmp_path


def test_auto_renders_references_block_from_inline_footnotes(fake_raw_files: Path) -> None:
    meta = {"title": "Topic", "page_type": "topic", "status": "active"}
    body = "# Topic\n\nLatency regressed [^msg-cda09a3d]. Nitin flagged the bug [^msg-19b9dc5e].\n"
    out = on_page_markdown(body, page=_page("topics/topic.md", meta), config={}, files=[])
    assert "## References" in out
    assert "[^msg-cda09a3d]: `raw/2026-01-08_launchim-bl-latency-regression_cda09a3d.md`" in out
    assert "[^msg-19b9dc5e]: `raw/2026-01-08_launchim-nitin-missed-call-bug_19b9dc5e.md`" in out


def test_no_references_block_when_no_inline_footnotes(fake_raw_files: Path) -> None:
    meta = {"title": "Plain", "page_type": "topic", "status": "active"}
    body = "# Plain\n\nNo footnotes here.\n"
    out = on_page_markdown(body, page=_page("topics/plain.md", meta), config={}, files=[])
    assert "## References" not in out


def test_references_dedupes_repeated_footnotes(fake_raw_files: Path) -> None:
    meta = {"title": "Repeat", "page_type": "topic", "status": "active"}
    body = (
        "# Repeat\n\n"
        "First mention [^msg-cda09a3d]. Second mention [^msg-cda09a3d]. Third [^msg-cda09a3d].\n"
    )
    out = on_page_markdown(body, page=_page("topics/repeat.md", meta), config={}, files=[])
    # Definition appears exactly once in References.
    assert out.count("[^msg-cda09a3d]: `raw/") == 1


def test_references_skipped_when_legacy_block_present(fake_raw_files: Path) -> None:
    """Legacy pages with hand-authored `## References` keep theirs verbatim."""
    meta = {"title": "Legacy", "page_type": "topic", "status": "active"}
    body = (
        "# Legacy\n\n"
        "Latency regressed [^msg-cda09a3d].\n\n"
        "## References\n\n"
        "[^msg-cda09a3d]: `raw/legacy-hand-authored.md`\n"
    )
    out = on_page_markdown(body, page=_page("topics/legacy-refs.md", meta), config={}, files=[])
    # Only the legacy definition appears — no auto-rendered duplicate.
    assert out.count("[^msg-cda09a3d]:") == 1
    assert "raw/legacy-hand-authored.md" in out


def test_references_handles_unresolvable_footnote(
    fake_raw_files: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown hash — render a clear marker rather than dropping the citation silently."""
    meta = {"title": "Ghost", "page_type": "topic", "status": "active"}
    body = "# Ghost\n\nClaim [^msg-deadbeef].\n"
    with caplog.at_level("WARNING"):
        out = on_page_markdown(body, page=_page("topics/ghost.md", meta), config={}, files=[])
    assert "## References" in out
    assert "[^msg-deadbeef]: *(raw file not found" in out
    assert any("[^msg-deadbeef]" in record.message for record in caplog.records)


def test_strips_body_authored_sources_h2(
    fake_raw_files: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Body-authored ``## Sources`` collides with the hook block — strip + warn."""
    meta = {
        "title": "Bad Heading",
        "page_type": "topic",
        "status": "active",
        "sources": ["raw/something.md"],
    }
    body = (
        "# Bad Heading\n\n"
        "Body content.\n\n"
        "## Sources\n\n"
        "- agent wrote this manually\n"
        "- which collides with the hook\n"
    )
    with caplog.at_level("WARNING"):
        out = on_page_markdown(body, page=_page("topics/bad-heading.md", meta), config={}, files=[])
    # Body-authored ## Sources stripped; warning emitted.
    assert "agent wrote this manually" not in out
    assert any("body-authored '## Sources'" in record.message for record in caplog.records)
    # The hook's own Sources block (collapsible) still renders.
    assert "📚 Sources" in out


def test_strips_sources_h2_preserves_following_h2(fake_raw_files: Path) -> None:
    """``## Related`` after ``## Sources`` must survive the strip."""
    meta = {"title": "Mixed", "page_type": "topic", "status": "active"}
    body = (
        "# Mixed\n\n"
        "Intro.\n\n"
        "## Sources\n\n"
        "- bogus body sources\n\n"
        "## Related\n\n"
        "- [[other-page]]\n"
    )
    out = on_page_markdown(body, page=_page("topics/mixed.md", meta), config={}, files=[])
    assert "bogus body sources" not in out
    assert "## Related" in out
    assert "[[other-page]]" in out
