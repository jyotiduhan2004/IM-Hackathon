"""Raw-email access tools — `get_thread_context` and `resolve_page`.

Extracted from `src/compile/compiler.py`. Compiler re-exports these at
the bottom of that module so the `create_deep_agent(..., tools=[...])`
registration keeps working unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast

from langchain_core.tools import tool

from src.utils import extract_body

_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _normalize_query(query: str) -> tuple[str, str | None]:
    """Normalise a resolve_page query. Returns (normalised, original-if-changed).

    Handles three common agent leaks:
    1. URLs (`https://ai.intermesh.net`) → strip scheme + path → host.
    2. Dotted hostnames (`mesh-pg.intermesh.net`) → leftmost label.
    3. Slug variants with underscores (`mesh_pg`) → hyphens.

    The second return value is the pre-normalisation string, or None when
    nothing was rewritten. Used to stamp `auto_corrected_from`/`_to` on
    the tool response so the scorecard can measure adoption.
    """
    original = query.strip()
    if not original:
        return "", None
    q = original

    # Strip URL scheme + path. `https://a.b.com/x/y` → `a.b.com`.
    if _URL_SCHEME_RE.match(q):
        without_scheme = _URL_SCHEME_RE.sub("", q, count=1)
        q = without_scheme.split("/", 1)[0]

    # Dotted host — take leftmost label. We only do this when the query
    # looks like a host (contains a TLD-ish suffix), not when it's a
    # real sentence with a period. Heuristic: last label is ≤4 chars AND
    # alphabetic — matches .com, .net, .io, .ai but not ordinary prose.
    if "." in q and "@" not in q:
        parts = q.split(".")
        last = parts[-1]
        if 0 < len(last) <= 4 and last.isalpha() and len(parts) >= 2:
            q = parts[0]

    # Underscores → hyphens; collapse multiple dashes.
    q = q.replace("_", "-")
    q = re.sub(r"-+", "-", q).strip("-")

    if not q:
        return original, None
    return q, (original if q != original else None)


@tool
def resolve_page(query: str, limit: int = 10) -> dict[str, Any]:
    """Find a wiki page by slug, title, or person email.

    WHEN TO USE: before creating ANY page. Check whether something close
    already exists so you can merge rather than duplicate.

    WHEN NOT TO USE:
    - You already have the exact slug — use `read_file` directly.
    - You want to browse by category — use `list_wiki_pages`.
    - You need free-text body search — use `grep`.

    Args:
        query: Slug, title, or email. The tool auto-detects by shape and
            normalises common leaks:
              - `https://mesh-pg.intermesh.net/x` → `mesh-pg`
              - `mesh_pg` → `mesh-pg`
        limit: Max candidates to return on a miss (default 10, capped at 10).

    Returns (hit):
        {"exists": True, "slug", "title", "page_type", "status",
         "confidence", "why_matched", "auto_corrected_from",
         "auto_corrected_to"}.

    Returns (miss):
        {"exists": False, "candidates": [...up to `limit` close matches...],
         "auto_corrected_from", "auto_corrected_to"}.

    Returns (empty catalog):
        {"exists": False, "catalog_empty_or_stale": True, "error": "...",
         "catalog_counts": {...}}.

    Note: the `path` field is intentionally omitted from the response —
    slugs are the stable identifier. If you need the file path, read the
    slug via `get_page_summary` or open it with `read_file("/wiki/<cat>/<slug>.md")`.
    """
    from src.db.wiki_pages import count_wiki_pages_by_type
    from src.db.wiki_pages import lookup_page
    from src.db.wiki_pages import search_pages

    q_raw = (query or "").strip()
    if not q_raw:
        return {
            "exists": False,
            "error": "query is empty",
            "candidates": [],
        }

    q, original_if_rewritten = _normalize_query(q_raw)
    if not q:
        return {"exists": False, "error": "query normalised to empty", "candidates": []}

    limit = max(1, min(int(limit or 10), 10))

    catalog_counts = count_wiki_pages_by_type()
    if not catalog_counts or sum(catalog_counts.values()) == 0:
        return {
            "exists": False,
            "catalog_empty_or_stale": True,
            "error": (
                "wiki_pages catalog is empty or stale; run "
                "`uv run python scripts/backfill_wiki_pages.py` before relying on resolve_page"
            ),
            "catalog_counts": catalog_counts,
        }

    lookups: list[tuple[str, dict[str, str]]] = []
    if "@" in q:
        lookups.append(("email", {"canonical_user_email": q.lower()}))
    if " " not in q:
        lookups.append(("slug", {"slug": q.lower()}))
    lookups.append(("title", {"title": q}))

    seen_kinds: set[str] = set()
    for kind, kwargs in lookups:
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        row = lookup_page(**kwargs)
        if row is not None:
            response: dict[str, Any] = {
                "exists": True,
                "slug": row["slug"],
                "title": row["title"],
                "page_type": row["page_type"],
                "status": row["status"],
                "confidence": float(row["confidence"]),
                "why_matched": kind,
            }
            if original_if_rewritten is not None:
                response["auto_corrected_from"] = original_if_rewritten
                response["auto_corrected_to"] = q
            return response

    candidates = search_pages(q, limit=limit)
    response = {
        "exists": False,
        "candidates": [
            {
                "slug": c["slug"],
                "title": c["title"],
                "page_type": c["page_type"],
                "status": c["status"],
            }
            for c in candidates
        ],
    }
    if original_if_rewritten is not None:
        response["auto_corrected_from"] = original_if_rewritten
        response["auto_corrected_to"] = q
    return response


@tool
def get_thread_context(
    thread_id: str,
    limit: int = 50,
    response_format: Literal["detailed", "concise"] = "detailed",
) -> dict[str, Any]:
    """Return chronological messages in a thread with short previews.

    WHEN TO USE: when merging a new email into an existing topic page that
      spans multiple emails — gives you the conversation arc cheaply.
      For long threads (>5 messages) prefer `response_format="concise"`
      first to skim the arc in a fraction of the tokens; fall back to
      `"detailed"` only for the specific messages you need to read in full.
    WHEN NOT TO USE: don't call when you only need one message
      (use the email's raw_path directly) or when the thread isn't
      relevant to the current concept.

    Queries Postgres `messages` for every row matching `thread_id`, ordered
    by `date` ASC. In `"detailed"` mode (default, back-compat) each row
    carries a 200-char body preview; in `"concise"` mode each row carries
    a quote-stripped one-line summary capped at 120 chars. Missing raw
    files degrade gracefully (empty preview/one_line). Caps at `limit`
    rows to avoid flooding agent context on long threads.

    **Chronological scope**: when invoked inside `run_compilation`, this
    tool automatically clips results to messages dated at or before the
    batch's latest raw — the agent processes email N of the thread "as a
    writer at that point in time" and should not see future replies. The
    cutoff is reported back in the ``cutoff_date`` field of the response
    so the agent knows the scope was narrowed. Outside of a compile run
    (unit tests, ad-hoc queries) no cutoff is applied.

    Args:
        thread_id: Gmail thread identifier.
        limit: Maximum rows to return. Default 50.
        response_format:
          - "detailed" (default) — full shape with per-message subject,
            raw_path, first_200_chars preview, compile_state. Use when you
            need to decide which message to read next or match against
            page content.
          - "concise" — compact shape `{message_id, date, from_addr,
            one_line}` per message, where `one_line` is the first
            non-empty line of the quote-stripped body capped at 120
            chars. Cheap overview of a long thread; saves ~70% context
            vs. detailed on 10+ message threads.

    Returns:
        Detailed: ``{"thread_id": str, "messages": [{"message_id", "subject",
        "from_addr", "date", "raw_path", "first_200_chars", "compile_state"},
        ...], "truncated": bool, "cutoff_date": str | None}``.
        Concise: ``{"thread_id": str, "message_count": int, "cutoff_date":
        str | None, "summary_lines": [{"message_id", "date", "from_addr",
        "one_line"}, ...], "truncated": bool}``. Empty list when the
        thread is unknown. ``cutoff_date`` echoes the applied cutoff
        (ISO8601), or None when the full thread was returned.
    """
    from src.compile.compiler import _current_batch_cutoff_date
    from src.db import connect
    from src.utils.email_quotes import strip_quoted

    cutoff = _current_batch_cutoff_date.get()
    # Compare date-to-date so a YYYY-MM-DD cutoff (derived from the
    # batch's filename prefix) doesn't timezone-drift against the DB's
    # timestamptz. The middleware enforces the same date-level guard.
    cutoff_clause = "AND (date IS NULL OR date::date <= %s::date)" if cutoff else ""
    params: tuple[Any, ...] = (thread_id, cutoff, limit + 1) if cutoff else (thread_id, limit + 1)

    with connect() as conn:
        raw_rows = conn.execute(
            f"""
            SELECT message_id, raw_path, subject, from_address, date, compile_state
              FROM messages
             WHERE thread_id = %s
                   {cutoff_clause}
             ORDER BY date ASC NULLS LAST, message_id ASC
             LIMIT %s
            """,
            params,
        ).fetchall()
    rows = cast(list[dict[str, Any]], raw_rows)
    truncated = len(rows) > limit
    rows = rows[:limit]

    if response_format == "concise":
        summary_lines: list[dict[str, Any]] = []
        for row in rows:
            raw_path = str(row["raw_path"] or "")
            one_line = ""
            if raw_path:
                try:
                    text = Path(raw_path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                if text:
                    body = extract_body(text)
                    stripped = strip_quoted(body)
                    for line in stripped.splitlines():
                        candidate = line.strip()
                        if candidate:
                            one_line = candidate[:120]
                            break
            date_val = row["date"]
            summary_lines.append(
                {
                    "message_id": str(row["message_id"] or ""),
                    "date": date_val.isoformat() if date_val else "",
                    "from_addr": str(row["from_address"] or ""),
                    "one_line": one_line,
                }
            )
        return {
            "thread_id": thread_id,
            "message_count": len(summary_lines),
            "cutoff_date": cutoff,
            "summary_lines": summary_lines,
            "truncated": truncated,
        }

    messages: list[dict[str, Any]] = []
    for row in rows:
        raw_path = str(row["raw_path"] or "")
        preview = ""
        if raw_path:
            try:
                text = Path(raw_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            if text:
                body = extract_body(text)
                preview = body[:200]
        date_val = row["date"]
        messages.append(
            {
                "message_id": str(row["message_id"] or ""),
                "subject": str(row["subject"] or ""),
                "from_addr": str(row["from_address"] or ""),
                "date": date_val.isoformat() if date_val else "",
                "raw_path": raw_path,
                "first_200_chars": preview,
                "compile_state": str(row["compile_state"] or ""),
            }
        )

    return {
        "thread_id": thread_id,
        "messages": messages,
        "truncated": truncated,
        "cutoff_date": cutoff,
    }
