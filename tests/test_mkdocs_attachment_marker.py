"""Tests for `mkdocs_hooks._replace_attachment_refs`.

The viewer container excludes `raw/attachments/` from the build context
(see `.dockerignore`), so any markdown image / link / `<img>` pointing into
that directory would render as a broken icon on the live site. The hook
swaps those refs for a visible inline marker pointing at issue #46.

These tests are pure-string transformations — no MkDocs / DB dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mkdocs_hooks import _replace_attachment_refs  # noqa: E402

# ---------------------------------------------------------------------------
# Hand-crafted markdown covering every reference shape we know of.
# ---------------------------------------------------------------------------
SAMPLE_MARKDOWN = """\
# Sample page

Inline image from the raw email:

![first attachment](raw/attachments/foo.png)

Some HTML embedded by markitdown:

<img src="raw/attachments/bar.jpg" alt="bar">

A non-image attachment as a markdown link:

[PDF brief](raw/attachments/baz.pdf)

An external image that must be left alone:

![remote](https://external.example/img.png)

Another external link untouched:

[external](https://external.example/page)
"""


def test_every_attachment_ref_becomes_a_marker() -> None:
    out = _replace_attachment_refs(SAMPLE_MARKDOWN)
    # No surviving attachment paths anywhere in the output.
    assert "raw/attachments/" not in out
    # Each filename appears in its own marker so context isn't lost.
    for filename in ("foo.png", "bar.jpg", "baz.pdf"):
        assert f"`{filename}`" in out
        assert f"attachment `{filename}` not published" in out
    # Marker links to the tracking issue.
    assert "github.com/indiamart-ai/email-knowledge-base/issues/46" in out


def test_external_image_is_untouched() -> None:
    out = _replace_attachment_refs(SAMPLE_MARKDOWN)
    assert "![remote](https://external.example/img.png)" in out
    assert "[external](https://external.example/page)" in out


def test_marker_count_matches_attachment_count() -> None:
    # Three attachment refs in SAMPLE_MARKDOWN → three markers in output.
    out = _replace_attachment_refs(SAMPLE_MARKDOWN)
    assert out.count("not published on the viewer") == 3


def test_no_op_when_no_attachment_refs() -> None:
    body = "# Just a title\n\nPlain text.\n\n![ext](https://example.com/x.png)\n"
    assert _replace_attachment_refs(body) == body


def test_html_img_with_extra_attrs() -> None:
    body = '<img class="thumb" src="raw/attachments/quux.gif" width="200">'
    out = _replace_attachment_refs(body)
    assert "raw/attachments/" not in out
    assert "`quux.gif`" in out
