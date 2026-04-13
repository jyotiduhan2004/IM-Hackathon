"""Tests for the hidden drafts folder workflow.

Covers:
- `write_draft_page` tool happy path (correct frontmatter, path under
  `wiki/_drafts/`).
- Slug validation rejects non-kebab-case input.
- Idempotent overwrite on second call with the same slug.
- `exclude_docs: _drafts/**` in mkdocs.yml actually keeps `_drafts/` out
  of the built site (synthetic fixture under `tests/fixtures/drafts_fixture/`).
- Compiler system prompt teaches the agent when to reach for this tool.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.compiler import write_draft_page  # noqa: E402
from src.compile.prompts import COMPILER_SYSTEM_PROMPT  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402


def _invoke_write_draft(
    *,
    slug: str,
    reason: str,
    content: str,
    wiki_dir: str | Path,
) -> dict[str, object]:
    """Invoke the LangChain `@tool` wrapper with a plain dict input."""
    result: dict[str, object] = write_draft_page.invoke(
        {
            "slug": slug,
            "reason": reason,
            "content": content,
            "wiki_dir": str(wiki_dir),
        }
    )
    return result


def test_write_draft_page_creates_file_with_frontmatter(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"

    result = _invoke_write_draft(
        slug="cool-concept",
        reason="unclear if this is a topic or a system page",
        content="## Summary\nTODO",
        wiki_dir=wiki_dir,
    )

    assert result["ok"] is True
    assert result["error"] is None

    path = Path(str(result["path"]))
    assert path == wiki_dir / "_drafts" / "cool-concept.md"
    assert path.exists()

    raw = path.read_text(encoding="utf-8")
    fm = extract_frontmatter(raw)
    body = extract_body(raw)

    assert fm["page_type"] == "draft"
    assert fm["status"] == "pending_review"
    assert fm["reason_logged"] == "unclear if this is a topic or a system page"
    assert fm["title"] == "Cool Concept"
    assert "## Summary" in body
    assert "TODO" in body


def test_write_draft_page_rejects_non_kebab_slug(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"

    result = _invoke_write_draft(
        slug="BADSLUG!",
        reason="anything",
        content="body",
        wiki_dir=wiki_dir,
    )

    assert result["ok"] is False
    assert "invalid slug" in str(result["error"])
    assert not (wiki_dir / "_drafts" / "BADSLUG!.md").exists()
    # Drafts folder is NOT created when validation fails before mkdir.
    assert not (wiki_dir / "_drafts").exists()


def test_write_draft_page_is_idempotent_overwrite(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"

    first = _invoke_write_draft(
        slug="cool-concept",
        reason="first write",
        content="## Summary\nfirst body",
        wiki_dir=wiki_dir,
    )
    second = _invoke_write_draft(
        slug="cool-concept",
        reason="second write",
        content="## Summary\nsecond body",
        wiki_dir=wiki_dir,
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["path"] == second["path"]

    path = Path(str(second["path"]))
    raw = path.read_text(encoding="utf-8")
    fm = extract_frontmatter(raw)
    body = extract_body(raw)

    # Second call overwrites: latest reason + latest body wins.
    assert fm["reason_logged"] == "second write"
    assert "second body" in body
    assert "first body" not in body


def test_mkdocs_excludes_drafts_from_build(tmp_path: Path) -> None:
    """Synthetic fixture: run `mkdocs build` and assert _drafts is excluded
    while a normal topic page is still published."""
    fixture_src = Path(__file__).parent / "fixtures" / "drafts_fixture"
    assert fixture_src.is_dir(), f"missing fixture: {fixture_src}"

    fixture_dst = tmp_path / "drafts_fixture"
    shutil.copytree(fixture_src, fixture_dst)

    # Use the repo's uv-managed mkdocs install so the test uses the same
    # version as the real site.
    build = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "mkdocs",
            "build",
            "--clean",
        ],
        cwd=fixture_dst,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, (
        f"mkdocs build failed: stdout={build.stdout}\nstderr={build.stderr}"
    )

    site = fixture_dst / "site"
    assert (site / "topics" / "visible" / "index.html").exists(), (
        "visible topic page missing from build output"
    )
    assert not (site / "_drafts" / "secret" / "index.html").exists(), (
        "secret draft leaked into build output"
    )
    # Belt-and-braces: the whole _drafts tree must be gone.
    assert not (site / "_drafts").exists(), "_drafts directory present in build"


def test_prompt_explains_when_to_write_a_draft() -> None:
    assert "## When to write a draft" in COMPILER_SYSTEM_PROMPT
    assert "write_draft_page" in COMPILER_SYSTEM_PROMPT
    assert "Good draft cases:" in COMPILER_SYSTEM_PROMPT
    assert "Bad draft cases" in COMPILER_SYSTEM_PROMPT
    # The section must come before the wikilink rules, so the agent has
    # the draft off-ramp context when it hits "every wikilink must resolve".
    draft_idx = COMPILER_SYSTEM_PROMPT.index("## When to write a draft")
    wikilink_idx = COMPILER_SYSTEM_PROMPT.index("## Wikilink rules")
    assert draft_idx < wikilink_idx
