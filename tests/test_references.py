"""Unit tests for src.wiki.references — shared footnote helpers + the
coordinator-side backfill."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.wiki.references import backfill_references
from src.wiki.references import build_raw_index
from src.wiki.references import clear_raw_index_cache
from src.wiki.references import ordered_unique_footnotes
from src.wiki.references import render_references_block


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_raw_index_cache()


def _make_repo(tmp_path: Path, raw_files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    raw = repo / "raw"
    raw.mkdir(parents=True)
    if raw_files:
        for name, content in raw_files.items():
            (raw / name).write_text(content, encoding="utf-8")
    (repo / "wiki" / "topics").mkdir(parents=True)
    return repo


def _write_page(path: Path, body: str) -> None:
    fm = "---\ntitle: Foo\npage_type: topic\nstatus: active\n---\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm + body, encoding="utf-8")


def test_ordered_unique_footnotes_first_appearance_order() -> None:
    body = "First [^msg-aaaa]. Second [^msg-bbbb]. Repeat [^msg-aaaa]. Third [^msg-cccc]."
    assert ordered_unique_footnotes(body) == ["msg-aaaa", "msg-bbbb", "msg-cccc"]


def test_ordered_unique_footnotes_skips_definitions() -> None:
    """``[^msg-x]:`` in body must not register as a usage (matches mkdocs hook)."""
    body = "Cite [^msg-aaaa].\n\n[^msg-aaaa]: previously-defined inline.\n"
    assert ordered_unique_footnotes(body) == ["msg-aaaa"]


def test_render_block_resolves_known_and_unknown(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x"})
    out = render_references_block(["msg-aaaa", "msg-zzzz"], repo / "raw", "wiki/topics/foo.md")
    assert "[^msg-aaaa]: `raw/2026-04-01_foo_aaaa.md`" in out
    assert "[^msg-zzzz]: *(raw file not found for `msg-zzzz`)*" in out
    assert out.startswith("\n## References\n")


def test_backfill_no_refs_in_body_is_noop(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(page, "Just some prose, no refs.\n")
    assert backfill_references(page, repo / "raw") is False


def test_backfill_appends_h2_and_defs_when_missing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x", "2026-04-02_bar_bbbb.md": "y"})
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(page, "Body cites [^msg-aaaa] and [^msg-bbbb] here.\n")
    assert backfill_references(page, repo / "raw") is True
    txt = page.read_text(encoding="utf-8")
    assert "## References" in txt
    assert "[^msg-aaaa]: `raw/2026-04-01_foo_aaaa.md`" in txt
    assert "[^msg-bbbb]: `raw/2026-04-02_bar_bbbb.md`" in txt


def test_backfill_extends_existing_h2_only_with_missing_defs(tmp_path: Path) -> None:
    """Hand-authored def text must be preserved; only missing defs append."""
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x", "2026-04-02_bar_bbbb.md": "y"})
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(
        page,
        "Body cites [^msg-aaaa] and [^msg-bbbb] here.\n\n"
        "## References\n\n[^msg-aaaa]: hand-authored note about aaaa\n",
    )
    assert backfill_references(page, repo / "raw") is True
    txt = page.read_text(encoding="utf-8")
    assert "[^msg-aaaa]: hand-authored note about aaaa" in txt  # preserved
    assert "[^msg-bbbb]: `raw/2026-04-02_bar_bbbb.md`" in txt  # appended
    # Only one ## References heading.
    assert txt.count("## References") == 1


def test_backfill_complete_defs_under_h2_is_noop(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x"})
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(
        page,
        "Body cites [^msg-aaaa].\n\n"
        "## References\n\n[^msg-aaaa]: `raw/2026-04-01_foo_aaaa.md`\n",
    )
    assert backfill_references(page, repo / "raw") is False


def test_backfill_renders_fallback_for_missing_raw(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)  # no raw files
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(page, "Body cites [^msg-zzzz].\n")
    assert backfill_references(page, repo / "raw") is True
    txt = page.read_text(encoding="utf-8")
    assert "[^msg-zzzz]: *(raw file not found for `msg-zzzz`)*" in txt


def test_backfill_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x"})
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(page, "Body cites [^msg-aaaa].\n")
    assert backfill_references(page, repo / "raw") is True
    assert backfill_references(page, repo / "raw") is False  # second call no-ops


def test_backfill_def_order_follows_first_appearance(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "2026-04-01_a_aaaa.md": "a",
            "2026-04-02_b_bbbb.md": "b",
            "2026-04-03_c_cccc.md": "c",
        },
    )
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(page, "Cites [^msg-cccc] then [^msg-aaaa] then [^msg-bbbb].\n")
    backfill_references(page, repo / "raw")
    txt = page.read_text(encoding="utf-8")
    refs_section = txt.split("## References", 1)[1]
    cccc_pos = refs_section.find("[^msg-cccc]:")
    aaaa_pos = refs_section.find("[^msg-aaaa]:")
    bbbb_pos = refs_section.find("[^msg-bbbb]:")
    assert 0 <= cccc_pos < aaaa_pos < bbbb_pos


def test_backfill_preserves_trailing_h2_after_references(tmp_path: Path) -> None:
    """If something else lives after ``## References``, don't clobber it."""
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x"})
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(
        page,
        "Body cites [^msg-aaaa].\n\n"
        "## References\n\n[^msg-aaaa]: `raw/2026-04-01_foo_aaaa.md`\n\n"
        "## Related\n\n- [[topic/other]]\n",
    )
    assert backfill_references(page, repo / "raw") is False
    assert "## Related" in page.read_text(encoding="utf-8")


def test_backfill_keeps_blank_line_before_following_h2(tmp_path: Path) -> None:
    """When defs are appended into an existing ``## References`` followed by
    another H2, a blank line must separate the last def from the next
    heading (mdlint MD022 / readability)."""
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x", "2026-04-02_bar_bbbb.md": "y"})
    page = repo / "wiki" / "topics" / "foo.md"
    _write_page(
        page,
        "Cites [^msg-aaaa] and [^msg-bbbb].\n\n"
        "## References\n\n[^msg-aaaa]: hand-authored\n\n"
        "## Related\n\n- [[topic/other]]\n",
    )
    assert backfill_references(page, repo / "raw") is True
    txt = page.read_text(encoding="utf-8")
    assert "[^msg-bbbb]: `raw/2026-04-02_bar_bbbb.md`\n\n## Related" in txt


def test_backfill_handles_missing_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    missing = repo / "wiki" / "topics" / "ghost.md"
    assert backfill_references(missing, repo / "raw") is False  # no crash


def test_backfill_preserves_malformed_frontmatter(tmp_path: Path) -> None:
    """A page whose YAML frontmatter doesn't parse must still keep its raw
    frontmatter text round-tripped — the deterministic backfill must not
    silently delete metadata."""
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x"})
    page = repo / "wiki" / "topics" / "broken.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: Broken\nbroken: yaml: with: too: many: colons\n---\n\n"
        "Body cites [^msg-aaaa].\n",
        encoding="utf-8",
    )
    assert backfill_references(page, repo / "raw") is True
    txt = page.read_text(encoding="utf-8")
    # Original frontmatter text preserved verbatim.
    assert "title: Broken" in txt
    assert "broken: yaml: with: too: many: colons" in txt
    # Def appended below body.
    assert "[^msg-aaaa]: `raw/2026-04-01_foo_aaaa.md`" in txt
    # Frontmatter fences intact.
    assert txt.startswith("---\n")


def test_build_raw_index_caches_per_raw_dir(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"2026-04-01_foo_aaaa.md": "x"})
    raw = repo / "raw"
    idx1 = build_raw_index(raw)
    (raw / "2026-04-02_bar_bbbb.md").write_text("y", encoding="utf-8")
    idx2 = build_raw_index(raw)  # cached → still 1 entry
    assert idx1 is idx2
    assert "bbbb" not in idx2


def test_build_raw_index_honours_non_default_raw_dir(tmp_path: Path) -> None:
    """A custom RAW_DIR must be respected — passing a non-``repo/raw``
    path should index that directory, not the legacy convention."""
    custom = tmp_path / "alt-raws"
    custom.mkdir()
    (custom / "2026-04-01_foo_zzzz.md").write_text("x", encoding="utf-8")
    idx = build_raw_index(custom)
    assert idx == {"zzzz": "raw/2026-04-01_foo_zzzz.md"}


def test_clear_raw_index_cache_picks_up_new_raws(tmp_path: Path) -> None:
    """Long-lived processes (live-mode watch loop) ingest new raw files
    between backfill calls. ``clear_raw_index_cache`` must be the
    explicit invalidation knob — without calling it, a permanent cache
    would render new citations as ``raw file not found``."""
    repo = _make_repo(tmp_path, {"2026-04-01_first_aaaa.md": "x"})
    raw = repo / "raw"
    page = repo / "wiki" / "topics" / "foo.md"

    # First backfill caches the index with only `aaaa`.
    _write_page(page, "Cites [^msg-aaaa] and [^msg-bbbb].\n")
    backfill_references(page, raw)
    assert "raw file not found for `msg-bbbb`" in page.read_text(encoding="utf-8")

    # New raw added mid-process.
    (raw / "2026-04-02_second_bbbb.md").write_text("y", encoding="utf-8")

    # Without invalidation, the second ref still resolves to fallback.
    page2 = repo / "wiki" / "topics" / "bar.md"
    _write_page(page2, "Cites [^msg-bbbb] only.\n")
    backfill_references(page2, raw)
    assert "raw file not found for `msg-bbbb`" in page2.read_text(encoding="utf-8")

    # After explicit invalidation, the new file is visible.
    clear_raw_index_cache()
    page3 = repo / "wiki" / "topics" / "baz.md"
    _write_page(page3, "Cites [^msg-bbbb] only.\n")
    backfill_references(page3, raw)
    txt = page3.read_text(encoding="utf-8")
    assert "[^msg-bbbb]: `raw/2026-04-02_second_bbbb.md`" in txt
