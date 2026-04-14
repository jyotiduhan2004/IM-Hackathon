"""Tests for scripts/cleanup_auto_stubs.py — the one-shot stub purge.

We never touch the real wiki/ — every test uses tmp_path and a fake
wiki root (entities/ + systems/ subdirs) assembled under it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


def _load_cleanup_module():
    """Import scripts/cleanup_auto_stubs.py as a module."""
    path = Path(__file__).parent.parent / "scripts" / "cleanup_auto_stubs.py"
    spec = importlib.util.spec_from_file_location("_cleanup_auto_stubs_for_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_cleanup_auto_stubs_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cleanup_mod():
    return _load_cleanup_module()


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """A wiki root with empty entities/ and systems/ subdirs."""
    (tmp_path / "entities").mkdir()
    (tmp_path / "systems").mkdir()
    return tmp_path


# === Stub content templates ===

_AUTO_STUB_BODY = """---
title: "Devtron"
page_type: system
status: current
sources: []
related: []
last_compiled: "stub"
---

# Devtron

*Stub page auto-created because [[devtron]] was referenced but no page existed.*

Referenced from: [[some-topic]]
"""

_AUTO_STUB_ENTITY = """---
title: "Lucky Agarwal"
page_type: entity
status: current
sources: []
related: []
last_compiled: "stub"
---

# Lucky Agarwal

*Stub page auto-created because [[lucky-agarwal]] was referenced but no page existed.*

Referenced from: [[another-topic]]
"""

_REAL_COMPILED_PAGE = """---
title: "Real System"
page_type: system
status: current
sources:
  - "raw/2026-04-01_foo.md"
related:
  - "[[something]]"
last_compiled: "2026-04-01T10:00:00+00:00"
---

# Real System

This is a real system that has been compiled from email evidence.
It has sources and actual content.
"""

# Mimics the output of src/compile/entities.py::_stub_markdown — empty
# sources but a legit body line ("Email: foo@bar.com"). Must NOT match.
_LEGIT_ENTITY_FRESH = """---
title: amit@indiamart.com
page_type: entity
status: current
email: amit@indiamart.com
is_external: false
sources: []
related: []
---

Email: amit@indiamart.com
"""


# === Signature matcher tests ===


def test_matches_stub_signature(cleanup_mod, wiki_root: Path) -> None:
    """Classic auto-stub body + empty sources → match."""
    p = wiki_root / "systems" / "devtron.md"
    p.write_text(_AUTO_STUB_BODY, encoding="utf-8")
    assert cleanup_mod.matches_auto_stub(p) is True


def test_matches_stub_signature_entity(cleanup_mod, wiki_root: Path) -> None:
    """Same check but for entities/ variant — the legacy heuristic put
    hyphenated two-word kebabs there."""
    p = wiki_root / "entities" / "lucky-agarwal.md"
    p.write_text(_AUTO_STUB_ENTITY, encoding="utf-8")
    assert cleanup_mod.matches_auto_stub(p) is True


def test_does_not_match_real_page(cleanup_mod, wiki_root: Path) -> None:
    """A page with real sources and content is NOT touched."""
    p = wiki_root / "systems" / "real-system.md"
    p.write_text(_REAL_COMPILED_PAGE, encoding="utf-8")
    assert cleanup_mod.matches_auto_stub(p) is False


def test_does_not_match_legit_empty_entity(cleanup_mod, wiki_root: Path) -> None:
    """Fresh create_entity_page output has empty sources but a legit
    'Email: foo@bar.com' body — must NOT be misclassified as auto-stub."""
    p = wiki_root / "entities" / "amit-indiamart-com.md"
    p.write_text(_LEGIT_ENTITY_FRESH, encoding="utf-8")
    assert cleanup_mod.matches_auto_stub(p) is False


def test_does_not_match_page_with_sources(cleanup_mod, wiki_root: Path) -> None:
    """Even if the body somehow mentions 'Referenced from: [[x]]' deep
    inside, sources being non-empty is a hard stop."""
    body_with_sources = """---
title: "Edge Case"
page_type: system
status: current
sources:
  - "raw/2026-04-01_edge.md"
related: []
last_compiled: "2026-04-01T10:00:00+00:00"
---

# Edge Case

This page has real sources but also quotes the legacy stub marker
inside a paragraph: "Referenced from: [[somewhere]]" as a historical
note. That should NOT make it match.
"""
    p = wiki_root / "systems" / "edge-case.md"
    p.write_text(body_with_sources, encoding="utf-8")
    assert cleanup_mod.matches_auto_stub(p) is False


def test_does_not_match_empty_sources_real_body(cleanup_mod, wiki_root: Path) -> None:
    """Empty sources but real body text (no stub marker) → no match."""
    body = """---
title: "Work In Progress"
page_type: system
status: current
sources: []
related: []
last_compiled: "stub"
---

# Work In Progress

This is a page someone started writing by hand. Empty sources, but
no auto-stub marker. Leave it alone.
"""
    p = wiki_root / "systems" / "work-in-progress.md"
    p.write_text(body, encoding="utf-8")
    assert cleanup_mod.matches_auto_stub(p) is False


# === find_auto_stubs + CLI tests ===


def test_find_auto_stubs_scans_both_dirs(cleanup_mod, wiki_root: Path) -> None:
    """Scanner hits entities/ and systems/ only, skips other folders."""
    (wiki_root / "systems" / "devtron.md").write_text(_AUTO_STUB_BODY, encoding="utf-8")
    (wiki_root / "entities" / "lucky-agarwal.md").write_text(_AUTO_STUB_ENTITY, encoding="utf-8")
    (wiki_root / "systems" / "real-system.md").write_text(_REAL_COMPILED_PAGE, encoding="utf-8")
    # topics/ shouldn't be scanned at all; any stub there is safe.
    topics = wiki_root / "topics"
    topics.mkdir()
    (topics / "decoy.md").write_text(_AUTO_STUB_BODY, encoding="utf-8")

    hits = cleanup_mod.find_auto_stubs(wiki_root)
    names = {p.name for p in hits}
    assert names == {"devtron.md", "lucky-agarwal.md"}


def test_dry_run_does_not_mutate(cleanup_mod, wiki_root: Path) -> None:
    """Default (no --confirm) exits 1 on matches without deleting anything."""
    stub = wiki_root / "systems" / "devtron.md"
    stub.write_text(_AUTO_STUB_BODY, encoding="utf-8")

    result = CliRunner().invoke(
        cleanup_mod.main,
        ["--wiki-dir", str(wiki_root)],
        catch_exceptions=False,
    )
    # Exit 1 signals "matches found" so CI can catch regressions.
    assert result.exit_code == 1
    assert stub.exists()
    assert "devtron.md" in result.output


def test_dry_run_clean_exits_zero(cleanup_mod, wiki_root: Path) -> None:
    """Nothing to clean → exit 0."""
    (wiki_root / "systems" / "real-system.md").write_text(_REAL_COMPILED_PAGE, encoding="utf-8")
    result = CliRunner().invoke(
        cleanup_mod.main,
        ["--wiki-dir", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "No auto-stub pages found" in result.output


def test_confirm_deletes(cleanup_mod, wiki_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--confirm deletes the file. Skip the backfill subprocess call —
    that's covered by backfill_wiki_pages' own tests and here it would
    try to hit the real DB."""
    stub = wiki_root / "systems" / "devtron.md"
    stub.write_text(_AUTO_STUB_BODY, encoding="utf-8")
    real = wiki_root / "systems" / "real-system.md"
    real.write_text(_REAL_COMPILED_PAGE, encoding="utf-8")

    # Stub out backfill so we don't need Postgres in this unit test.
    monkeypatch.setattr(cleanup_mod, "_run_backfill_wiki_pages", lambda: True)

    result = CliRunner().invoke(
        cleanup_mod.main,
        ["--wiki-dir", str(wiki_root), "--confirm"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert not stub.exists()
    assert real.exists()


def test_confirm_handles_untracked_via_unlink(
    cleanup_mod, wiki_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files outside a git repo fall back to os.unlink (git rm errors out)."""
    stub = wiki_root / "systems" / "devtron.md"
    stub.write_text(_AUTO_STUB_BODY, encoding="utf-8")

    monkeypatch.setattr(cleanup_mod, "_run_backfill_wiki_pages", lambda: True)

    # tmp_path is not a git repo, so git rm will fail — unlink takes over.
    result = CliRunner().invoke(
        cleanup_mod.main,
        ["--wiki-dir", str(wiki_root), "--confirm"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert not stub.exists()
