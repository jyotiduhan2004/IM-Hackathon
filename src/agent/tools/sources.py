"""Source-finding agent tools — wiki + uncompiled-email lookup.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). `find_new_sources`
and `list_uncompiled_emails` are coordinator-owned helpers (not bound
to the agent's tool surface) but kept here so coordinator scripts have
a single import home.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast

from langchain_core.tools import tool

from src.utils import extract_body as _extract_body
from src.utils import extract_frontmatter as _extract_frontmatter
from src.wiki.pages import _extract_h2_headings
from src.wiki.pages import _extract_tldr
from src.wiki.pages import _find_page_by_slug
from src.wiki.pages import _first_paragraph_capped

_FIND_NEW_SOURCES_MAX_LIMIT = 200
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date(value: str | None, field: str) -> str | None:
    """Return value unchanged if it's a YYYY-MM-DD ISO date, else raise ValueError.

    Guards against SQL injection-lite inputs like `'; DROP TABLE'` and typos
    like `2026/04/13` that would silently mismatch Postgres' date parse.
    """
    if value is None:
        return None
    if not _ISO_DATE_RE.match(value):
        raise ValueError(f"{field}: expected YYYY-MM-DD, got {value!r}")
    # Cheap full parse catches Feb 30 / month=13 / etc.
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field}: invalid calendar date {value!r} ({exc})") from exc
    return value


@tool
def find_new_sources(
    date_from: str | None = None,
    date_to: str | None = None,
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    thread_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, str]] | dict[str, str]:
    """Coordinator-owned helper — not bound to the agent tool surface.

    The coordinator injects `raw_paths` at dispatch; use the
    `_current_raw_paths` ContextVar if you need batch scope inside an
    agent tool. This function is kept in the module for coordinator /
    script-side use (see `scripts/compile_all.py`).

    Filter-aware search for uncompiled email sources with pagination.

    Args:
        date_from: ISO date 'YYYY-MM-DD' lower bound (inclusive).
        date_to: ISO date 'YYYY-MM-DD' upper bound (inclusive).
        sender_contains: case-insensitive substring match on from_address.
        subject_contains: case-insensitive substring match on subject.
        thread_id: exact thread_id match.
        limit: max results (default 50, capped at 200 — paginate larger pulls).
        offset: skip first N matches.

    Returns:
        List of dicts with keys: path, date, subject, from, thread_id. When the
        input is malformed, returns `{"error": "<reason>"}` instead so the
        caller can recover rather than crash the batch.
    """
    try:
        date_from = _validate_iso_date(date_from, "date_from")
        date_to = _validate_iso_date(date_to, "date_to")
    except ValueError as exc:
        return {"error": str(exc)}

    if limit < 1 or offset < 0:
        return {
            "error": f"limit must be ≥1 and offset must be ≥0 (got limit={limit}, offset={offset})"
        }

    # Cap `limit` so a runaway agent call can't drag 10k rows back.
    capped_limit = min(limit, _FIND_NEW_SOURCES_MAX_LIMIT)

    from src.db.messages import list_uncompiled_with_filters

    rows = list_uncompiled_with_filters(
        date_from=date_from,
        date_to=date_to,
        sender_contains=sender_contains,
        subject_contains=subject_contains,
        thread_id=thread_id,
        limit=capped_limit,
        offset=offset,
    )
    return [
        {
            "path": str(row["raw_path"]),
            "date": row["date"].isoformat() if row["date"] else "",
            "subject": str(row["subject"] or ""),
            "from": str(row["from_address"] or ""),
            "thread_id": str(row["thread_id"] or ""),
        }
        for row in rows
    ]


@tool
def list_uncompiled_emails(raw_dir: str = "raw") -> list[dict[str, str]]:
    """Coordinator-owned helper — not bound to the agent tool surface.

    The coordinator injects `raw_paths` at dispatch; use the
    `_current_raw_paths` ContextVar if you need batch scope inside an
    agent tool. This function is kept in the module for coordinator /
    script-side use (see `scripts/compile_all.py`).

    Returns ALL uncompiled emails (up to 1000) with no filters.

    Reads from the Postgres `messages` table (the source of truth as of the
    catalog migration). The `raw_dir` arg is preserved for backward
    compatibility but ignored — paths come from `messages.raw_path`.

    Returns:
        List of dicts with keys: path, date, subject, from, thread_id.
        Empty list if no uncompiled emails.
    """
    from src.db.messages import list_uncompiled

    rows = list_uncompiled()
    return [
        {
            "path": str(row["raw_path"]),
            "date": row["date"].isoformat() if row["date"] else "",
            "subject": str(row["subject"] or ""),
            "from": str(row["from_address"] or ""),
            "thread_id": str(row["thread_id"] or ""),
        }
        for row in rows
    ]


@tool
def list_wiki_pages(
    wiki_dir: str = "wiki",
    response_format: Literal["concise", "detailed"] = "concise",
) -> dict[str, Any]:
    """List all wiki pages.

    WHEN to use: need an overview of available pages, or planning which
      pages to inspect before deciding to merge vs. create. Fallback
      browse of the wiki catalog — prefer `resolve_page` as the first
      discovery call.
    WHEN NOT to use: you already know the specific slug — call
      `get_page_summary` directly.

    Args:
        wiki_dir: Root wiki directory.
        response_format:
          - "concise" (default, ~72 tokens on a small wiki) — a flat
            `{"pages": [{"slug", "title"}, ...]}` list across every
            agent-visible category. Cheapest inventory for "what
            pages exist at all?".
          - "detailed" (~206+ tokens) — `{category: [{slug, title,
            page_type, status, source_count, source_thread_count,
            is_cited, last_compiled}, ...]}` keyed by category with
            per-page metadata. Use when you need the per-category
            breakdown or the citation/status signals to pick a
            merge target.

    Both formats read the same frontmatter from disk — the only
    difference is how much is returned. Categories: topics, systems,
    policies, decisions, people (see
    `src/wiki/categories.py::AGENT_VISIBLE_CATEGORIES`).
    """
    from src.wiki.categories import AGENT_VISIBLE_CATEGORIES

    wiki_path = Path(wiki_dir)
    categories: tuple[str, ...] = AGENT_VISIBLE_CATEGORIES

    if response_format == "concise":
        # page_type MUST stay in concise — two pages can share a slug across
        # categories (e.g. `topic/seller-isq` and `system/seller-isq`), so
        # dropping it collapses them in the agent's view. Cost: +8 tokens
        # per page which is still well under the concise budget.
        pages: list[dict[str, str]] = []
        if wiki_path.exists():
            for category in categories:
                cat_dir = wiki_path / category
                if not cat_dir.exists():
                    continue
                for md_file in sorted(cat_dir.glob("*.md")):
                    if md_file.name == "index.md":
                        continue
                    slug = md_file.stem
                    title = slug
                    page_type = ""
                    try:
                        content = md_file.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        content = ""
                    if content:
                        fm = _extract_frontmatter(content)
                        if fm:
                            title = str(fm.get("title") or slug)
                            page_type = str(fm.get("page_type") or "")
                    pages.append({"slug": slug, "title": title, "page_type": page_type})
        return {"pages": pages}

    detailed: dict[str, list[dict[str, Any]]] = {c: [] for c in categories}
    if not wiki_path.exists():
        return cast(dict[str, Any], detailed)
    for category in categories:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            if md_file.name == "index.md":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = _extract_frontmatter(content)
            if not fm:
                continue
            sources = fm.get("sources") or []
            source_threads = fm.get("source_threads") or []
            source_count = len(sources) if isinstance(sources, list) else 0
            source_thread_count = len(source_threads) if isinstance(source_threads, list) else 0
            detailed[category].append(
                {
                    "slug": md_file.stem,
                    "title": str(fm.get("title") or md_file.stem),
                    "page_type": str(fm.get("page_type") or ""),
                    "status": str(fm.get("status") or "active"),
                    "source_count": source_count,
                    "source_thread_count": source_thread_count,
                    "is_cited": source_count > 0 or source_thread_count > 0,
                    "last_compiled": str(fm.get("last_compiled") or ""),
                }
            )
    return cast(dict[str, Any], detailed)


@tool
def get_page_summary(
    slug: str,
    wiki_dir: str = "wiki",
    response_format: Literal["concise", "detailed"] = "concise",
) -> dict[str, Any]:
    """Return a summary for a wiki page.

    WHEN to use: you need to know what a wiki page is about before
      deciding whether to merge or create a new page.
    WHEN NOT to use: you need the full page body — use `read_file`
      (then `patch_page` for targeted writes) instead.

    Scans `wiki/<category>/<slug>.md` across every wiki category and
    returns the fields an agent actually needs to decide "merge here or
    make a new page". Does NOT return the filesystem path — callers
    should treat the slug as the stable identifier.

    Args:
        slug: kebab-case page identifier (without `.md`).
        wiki_dir: Root wiki directory. Default "wiki".
        response_format:
          - "concise" (default, ~150 tokens) — `{found, slug, title,
            first_paragraph, tldr}`. Cheapest "what is this about?"
            probe. `tldr` prefers any `## TL;DR` (or `## TLDR`) section
            (capped at 400 chars with an ellipsis); when absent, falls
            back to the lead paragraph (the 2026-04-28 prompt-review
            convention: "lead paragraph IS the summary"). None only
            when the page body is empty.
          - "detailed" (~206 tokens) — adds `page_type, status,
            headings, source_count, source_thread_count, is_cited,
            last_compiled`. Use when you also need the citation /
            status signals to decide merge vs. new.

    Returns:
        On miss (both formats): ``{"found": False, "slug": str,
        "reason": "not_found"}``. Concise and detailed return the same
        information at different granularity.
    """
    path = _find_page_by_slug(slug, wiki_dir)
    if path is None:
        return {"found": False, "slug": slug, "reason": "not_found"}

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"found": False, "slug": slug, "reason": f"read_error: {exc}"}

    fm = _extract_frontmatter(content)
    body = _extract_body(content)
    title = str(fm.get("title") or slug)
    first_paragraph = _first_paragraph_capped(body, cap=200)
    tldr = _extract_tldr(body)

    if response_format == "concise":
        return {
            "found": True,
            "slug": slug,
            "title": title,
            "first_paragraph": first_paragraph,
            "tldr": tldr,
        }

    sources = fm.get("sources") or []
    source_threads = fm.get("source_threads") or []
    source_count = len(sources) if isinstance(sources, list) else 0
    source_thread_count = len(source_threads) if isinstance(source_threads, list) else 0

    return {
        "found": True,
        "slug": slug,
        "title": title,
        "page_type": str(fm.get("page_type") or ""),
        "status": str(fm.get("status") or "active"),
        "first_paragraph": first_paragraph,
        "tldr": tldr,
        "headings": _extract_h2_headings(body),
        "source_count": source_count,
        "source_thread_count": source_thread_count,
        "is_cited": source_count > 0 or source_thread_count > 0,
        "last_compiled": str(fm.get("last_compiled") or ""),
    }
