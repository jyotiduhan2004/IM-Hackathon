"""Wiki page mutation + draft validation agent tools.

Extracted from the legacy `src/compile/compiler.py` in Phase 1C.
`write_draft_page` lives in `src/wiki/draft.py`; the rest of the
mutation tool surface (patch_page, etc.) lives here.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.utils import extract_body as _extract_body
from src.utils import extract_frontmatter as _extract_frontmatter
from src.utils import render_with_frontmatter as _render_with_frontmatter
from src.wiki.pages import _atomic_write_text
from src.wiki.pages import _find_page_by_slug


@tool
def patch_page(
    slug: str,
    section: str,
    new_content: str,
    wiki_dir: str = "wiki",
) -> dict[str, Any]:
    """Section-aware page mutation.

    WHEN TO USE: when you have a targeted edit to one H2 section of a page
      (e.g. updating "Current state" with new info from a new email).
    WHEN NOT TO USE: don't call for whole-page rewrites or new pages
      (use write_file). Don't call when the change crosses sections —
      do separate patch_page calls per section.

    Loads `wiki/<category>/<slug>.md`, finds the H2 whose text matches
    `section` (case-insensitive, trimmed), and replaces everything under
    it up to the next H2 or EOF. If no matching H2 exists, a new section
    is appended at the bottom of the page. Other sections and frontmatter
    are left untouched. Writes atomically.

    Args:
        slug: kebab-case page identifier (without `.md`).
        section: H2 heading text (e.g. "Current Policy"). Compared
            case-insensitively after trimming.
        new_content: Markdown for the section body. Do NOT include the
            `## <section>` line — the tool writes that itself.
        wiki_dir: Root wiki directory. Default "wiki".

    Returns:
        ``{"ok": bool, "slug": str, "section": str, "action": "replaced"|"created",
        "bytes_written": int}`` on success.
        ``{"ok": False, "slug": str, "error": str}`` on failure (page missing,
        unreadable, or write error).
    """
    from src.wiki.patch import replace_section

    path = _find_page_by_slug(slug, wiki_dir)
    if path is None:
        return {"ok": False, "slug": slug, "error": f"page not found: {slug}"}

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "slug": slug, "error": f"read_error: {exc}"}

    fm = _extract_frontmatter(content)
    body = _extract_body(content)
    new_body, action = replace_section(body, section, new_content)
    rendered = _render_with_frontmatter(fm, new_body)

    try:
        _atomic_write_text(path, rendered)
    except OSError as exc:
        return {"ok": False, "slug": slug, "error": f"write_error: {exc}"}

    return {
        "ok": True,
        "slug": slug,
        "section": section.strip(),
        "action": action,
        "bytes_written": len(rendered.encode("utf-8")),
    }


@tool
def validate_page_draft(
    slug: str,
    body: str,
    title: str | None = None,
    page_type: str | None = None,
    wiki_dir: str = "wiki",
) -> dict[str, Any]:
    """Sanity-check a draft BEFORE writing it.

    WHEN TO USE: before `write_file` on a new page when you're not sure
      it'll pass `check_my_work` — cheaper to fix now than to rebuild later.
    WHEN NOT TO USE: don't call for edits to existing pages (use
      `check_my_work` after the edit) or for trivial drafts where you're
      certain the structure is right.

    Applies four cheap checks that catch the compiler's most frequent
    failure modes:

    - `missing_tldr`: the first H2 is not `## TL;DR` AND the first 500
      characters don't mention TL;DR anywhere.
    - `over_quoting`: more than 30% of non-empty body lines are
      blockquotes (`> ` prefix) — a sign the page is email paste-in,
      not synthesis.
    - `person_page_heuristic`: when `page_type` is ``person`` (or the
      legacy ``entity`` alias, shim retired in #67), the body must
      contain ≥2 substantive sentences of prose (not just headings,
      wikilinks, or CC-list mentions).
    - `likely_duplicate`: another wiki page already has the same
      (case-insensitive) `title`.

    Args:
        slug: kebab-case identifier for the draft (used to exclude
            self-duplication in the likely-duplicate check).
        body: Markdown body being considered.
        title: Draft title. Without it, the duplicate check is skipped.
        page_type: Draft page type (e.g. ``topic``, ``person``; legacy
            ``entity`` tolerated as a shim, retired in #67). Without it,
            the person-page heuristic cannot fire.
        wiki_dir: Root wiki directory for duplicate-title scanning.

    Returns:
        ``{"ok": bool, "warnings": [{"rule": str, "severity":
        "warning"|"blocker", "message": str}, ...]}``. ``ok`` is False
        when any warning has severity ``blocker``; warning-level items
        are advisory only.
    """
    from src.wiki.validation import check_likely_duplicate
    from src.wiki.validation import check_missing_tldr
    from src.wiki.validation import check_over_quoting
    from src.wiki.validation import check_person_page_heuristic

    fm: dict[str, Any] = {}
    if title is not None:
        fm["title"] = title
    if page_type is not None:
        fm["page_type"] = page_type

    warnings = [
        w
        for w in (
            check_missing_tldr(body),
            check_over_quoting(body),
            check_person_page_heuristic(body, fm),
            check_likely_duplicate(slug, fm, wiki_dir),
        )
        if w is not None
    ]
    has_blocker = any(w.get("severity") == "blocker" for w in warnings)
    return {"ok": not has_blocker, "warnings": warnings}
