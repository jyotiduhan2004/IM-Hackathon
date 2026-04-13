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


BADGE_HTML = (
    '<span class="ext-badge" '
    'title="External contact (not @indiamart.com)">external</span>'
)


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
