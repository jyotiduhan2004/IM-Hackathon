"""Draft-page writer — the `write_draft_page` agent tool.

Extracted from the legacy `src/compile/compiler.py`. The tool is registered
directly with `create_deep_agent(..., tools=[...])` in
`src/agent/compiler_agent.py`; there is no re-export shim.
"""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from src.utils import render_with_frontmatter


@tool
def write_draft_page(
    slug: str,
    reason: str,
    content: str,
    wiki_dir: str = "wiki",
) -> dict[str, Any]:
    """Write a draft page to wiki/_drafts/{slug}.md. Hidden from readers.

    WHEN NOT to call: this tool is deprecated; do not call. Pending
      removal in a follow-up PR — write directly to the canonical
      `wiki/topics/<slug>.md` or `wiki/systems/<slug>.md` location
      instead, or call `log_insight(category="insufficient_decision",
      ...)` if you genuinely can't pick a target page.

    Args:
        slug: kebab-case identifier matching the wikilink target.
        reason: 1-2 sentences on why this is a draft.
        content: Markdown body. The tool adds frontmatter.

    Returns:
        {"ok": bool, "path": str, "error": str or None}.
    """
    # Strict kebab-case: leading + trailing alphanumerics, single `-`
    # between segments. Rejects trailing dashes and consecutive dashes so
    # we never produce filenames like `foo--.md` or `foo-.md`.
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        return {
            "ok": False,
            "path": "",
            "error": f"invalid slug: {slug!r} (must be kebab-case, no trailing/double dashes)",
        }

    # Path-traversal guard: this tool is LLM-callable, so a crafted prompt
    # could try to pass wiki_dir="../etc" to escape the wiki tree. Reject
    # any `..` component outright; tests still work because tmp_path is
    # absolute and contains no `..`.
    if ".." in Path(wiki_dir).parts:
        return {
            "ok": False,
            "path": "",
            "error": "wiki_dir must not contain '..' path components",
        }

    drafts_dir = Path(wiki_dir) / "_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{slug}.md"

    fm = {
        "title": slug.replace("-", " ").title(),
        "page_type": "draft",
        "status": "pending_review",
        "reason_logged": reason,
        "created_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(render_with_frontmatter(fm, content), encoding="utf-8")
    return {"ok": True, "path": str(path), "error": None}
