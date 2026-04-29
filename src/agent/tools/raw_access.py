"""Raw-email access tools — `get_thread_context` and `resolve_page`.

Extracted from the legacy `src/compile/compiler.py`. The tools are
registered directly with `create_deep_agent(..., tools=[...])` in
`src/agent/compiler_agent.py`; there is no re-export shim.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast

from langchain_core.tools import tool

from src.utils import extract_body

# Cap on per-message summaries in concise mode. Threads longer than
# this still have their `message_count` / `latest_date` / `truncated`
# flag computed correctly — we just stop emitting per-row stubs past
# `_CONCISE_MESSAGE_CAP` so the payload can't grow unbounded on a
# 200-message escalation thread. Detailed mode still honours `limit`.
_CONCISE_MESSAGE_CAP = 20


def _cutoff_to_date(cutoff: str | None) -> str | None:
    """Extract YYYY-MM-DD from a cutoff string for precise note prose.

    The SQL `date::date <= %s::date` compares date-only — if the cutoff
    is `2026-01-14T08:30:12`, messages dated `2026-01-14` at any time
    are still visible. Returning the date-only form avoids implying a
    sub-day cutoff that isn't actually enforced.

    ISO 8601 always starts with `YYYY-MM-DD` so the first 10 chars are
    the date prefix — matches `chronological_scope._cutoff_date`'s
    `raw[:10]` trick. Empty / None / too-short inputs return None.
    """
    if not cutoff or len(cutoff) < 10:
        return None
    return cutoff[:10]


def _cite_key_from_raw_path(raw_path: str) -> str:
    """Return the 8-char footnote target for a raw email path.

    Mirrors the prompt rule (`raw_path.stem.rsplit("_", 1)[-1]`):
    `raw/2026-01-08_subject_cda09a3d.md` → `cda09a3d`. Returns `""`
    for empty / malformed inputs so the LLM can detect a missing
    cite_key and fall back to the raw_path.
    """
    if not raw_path:
        return ""
    stem = Path(raw_path).stem
    if "_" not in stem:
        return ""
    return stem.rsplit("_", 1)[-1]


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
    - You need to find every page that literally contains string X — use `grep`.
      This tool ranks by topical relevance, not substring match.
    - You want a person by email — `create_entities([{email, ...}])` is
      the canonical path and will find them deterministically.

    Candidates come back relevance-ordered. Many candidates include a
    `snippet` showing the line-numbered excerpt that matched — use it
    to decide whether to read the full page, or pick a different
    candidate, without a follow-up `read_file`.

    Args:
        query: Slug, title, or email. Pass the bare slug
            (``whatsapp-buyer-feedback``), not a path-prefixed form
            (``topics/whatsapp-buyer-feedback`` or
            ``system/whatsapp-buyer-feedback``). Prefixed inputs are
            wikilink targets, not resolver inputs. The tool auto-detects
            by shape and normalises common leaks:
              - `https://mesh-pg.intermesh.net/x` → `mesh-pg`
              - `mesh_pg` → `mesh-pg`
        limit: Max candidates to return on a miss (default 10, capped at 10).

    Returns (hit — high-confidence exact match):
        {"exists": True, "slug", "title", "page_type", "status",
         "confidence", "why_matched", "retriever": "exact",
         "snippet": "..." (optional, when semantic retriever also found
         the page), "auto_corrected_from", "auto_corrected_to"}.

    Returns (miss — relevance-ordered candidates for you to pick from):
        {"exists": False,
         "candidates": [{"slug", "title", "page_type", "status",
                         "score" (optional, 0-1), "snippet" (optional)},
                        ...up to `limit`],
         "retriever": "semantic" | "fuzzy",
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

    # Email queries take the SQL fast path because the semantic
    # retriever doesn't index person pages (see `create_entities` for
    # the canonical people path). Everything else runs semantic-first
    # with an exact-match boost that wins when the query literally
    # matches a slug/title the catalog knows.
    if "@" in q:
        row = lookup_page(canonical_user_email=q.lower())
        if row is not None:
            response: dict[str, Any] = {
                "exists": True,
                "slug": row["slug"],
                "title": row["title"],
                "page_type": row["page_type"],
                "status": row["status"],
                "confidence": float(row["confidence"]),
                "why_matched": "email",
                "retriever": "exact",
            }
            if original_if_rewritten is not None:
                response["auto_corrected_from"] = original_if_rewritten
                response["auto_corrected_to"] = q
            return response

    import structlog

    from src.agent.tools.qmd_client import is_enabled as semantic_enabled
    from src.agent.tools.qmd_client import query_qmd

    _logger = structlog.get_logger(__name__)

    semantic_candidates: list[dict[str, Any]] = []
    snippet_by_slug: dict[str, str] = {}
    if semantic_enabled():
        semantic_result = query_qmd(q, limit=limit)
        # Latency + error land on the structured log so the scorecard
        # can trend them without leaking operational telemetry into the
        # LLM-visible tool response. Langfuse already picks up structlog
        # events in the current scope.
        _logger.info(
            "resolve_page.semantic",
            query=q,
            latency_s=semantic_result.get("latency_s"),
            error=semantic_result.get("error"),
            candidate_count=len(semantic_result.get("candidates") or []),
        )
        if not semantic_result.get("error"):
            for cand in semantic_result.get("candidates") or []:
                slug = cand.get("slug")
                if not isinstance(slug, str) or not slug:
                    continue
                db_row = lookup_page(slug=slug.lower())
                if db_row is None:
                    # Semantic index knows a page the catalog doesn't —
                    # skip rather than fabricate metadata. Happens if
                    # the index is ahead of post-batch catalog sync.
                    continue
                semantic_candidates.append(
                    {
                        "slug": db_row["slug"],
                        "title": db_row["title"],
                        "page_type": db_row["page_type"],
                        "status": db_row["status"],
                        "score": cand.get("score"),
                        "snippet": cand.get("snippet") or "",
                    }
                )
                snippet_by_slug[db_row["slug"]] = cand.get("snippet") or ""

    # Exact-match boost: if the normalised query literally matches a
    # slug or title in the catalog, that page wins with high confidence —
    # regardless of where semantic ranked it. Carry the semantic
    # snippet through when the semantic retriever also surfaced the
    # page, so the agent still sees the matching body excerpt.
    exact_row = None
    exact_why: str | None = None
    if " " not in q:
        exact_row = lookup_page(slug=q.lower())
        if exact_row is not None:
            exact_why = "slug"
    if exact_row is None:
        exact_row = lookup_page(title=q)
        if exact_row is not None:
            exact_why = "title"

    if exact_row is not None:
        response = {
            "exists": True,
            "slug": exact_row["slug"],
            "title": exact_row["title"],
            "page_type": exact_row["page_type"],
            "status": exact_row["status"],
            "confidence": float(exact_row["confidence"]),
            "why_matched": exact_why,
            "retriever": "exact",
        }
        snippet = snippet_by_slug.get(exact_row["slug"])
        if snippet:
            response["snippet"] = snippet
        if original_if_rewritten is not None:
            response["auto_corrected_from"] = original_if_rewritten
            response["auto_corrected_to"] = q
        return response

    # No exact match. If semantic retriever produced candidates, return
    # them relevance-ordered with snippets. If semantic was off or
    # unavailable, degrade to the ILIKE fuzzy search (`fuzzy`).
    if semantic_candidates:
        response = {
            "exists": False,
            "candidates": semantic_candidates[:limit],
            "retriever": "semantic",
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
        "retriever": "fuzzy",
    }
    if original_if_rewritten is not None:
        response["auto_corrected_from"] = original_if_rewritten
        response["auto_corrected_to"] = q
    return response


@tool
def get_thread_context(
    thread_id: str,
    limit: int = 50,
    response_format: Literal["concise", "detailed"] = "concise",
) -> dict[str, Any]:
    """Return a thread's messages (or a navigable summary).

    WHEN to use: merging a new email into an existing topic page that
      spans multiple emails — gives you the conversation arc cheaply.
      Start with `response_format="concise"` to see thread size, subject,
      date range, AND a per-message `raw_path` you can pass straight to
      `read_file`. Opt into `"detailed"` only when you also need the
      200-char body preview and compile_state per message.
    WHEN NOT to use: you only need one message you already have the
      raw_path for (call `read_file` directly) or the thread isn't
      relevant to the current concept.

    Queries Postgres `messages` for every row matching `thread_id`,
    ordered by `date` ASC. Caps at `limit` rows to avoid flooding agent
    context on long threads.

    **Chronological scope**: when invoked inside `run_compilation`, this
    tool automatically clips results to messages dated at or before the
    batch's latest raw — the agent processes email N of the thread "as a
    writer at that point in time" and should not see future replies.
    The SQL cutoff is date-only (`date::date <= cutoff::date`) so any
    message dated on the cutoff day at any time is visible. Concise
    mode reports this via `applied_cutoff_date` (full ISO timestamp,
    machine-readable) plus `note_on_cutoff` (human prose using the
    date-only form so the semantics are unambiguous). Detailed mode
    preserves the legacy `cutoff_date` field for backward compatibility.
    Outside of a compile run (unit tests, ad-hoc queries) no cutoff
    is applied.

    Args:
        thread_id: Gmail thread identifier.
        limit: Maximum rows to return. Default 50.
        response_format:
          - "concise" (default, ~120 tokens for a 2-message thread;
            scales linearly with thread size). Navigable shape
            `{thread_id, message_count, first_subject, latest_date,
            applied_cutoff_date, note_on_cutoff, truncated,
            messages_summary: [{message_id, raw_path, cite_key, date, from_addr}, ...]}`.
            `raw_path` is the single most useful field — pass it to
            `read_file` without re-querying. `cite_key` is the 8-char
            hash suffix of the raw filename (`raw/..._cda09a3d.md` →
            `cda09a3d`) — drop it straight into a footnote
            (`[^msg-<cite_key>]`) instead of re-deriving it.
            Per-message bodies and compile_state are dropped to stay
            cheap. `messages_summary` is capped at 20 entries
            regardless of `limit` (which still governs DB fetch size
            for `message_count` / `latest_date` accuracy).
          - "detailed" (~206+ tokens for a 2-message thread; also
            scales linearly) — full shape with `messages` array:
            subject, raw_path, 200-char body preview, and compile_state
            per message. Use when you need the body preview to pick
            which message to read next.

    Returns:
        Concise: ``{"thread_id": str, "message_count": int,
        "first_subject": str, "latest_date": str | None,
        "applied_cutoff_date": str | None, "note_on_cutoff": str | None,
        "truncated": bool, "messages_summary": [{"message_id",
        "raw_path", "cite_key", "date": str | None, "from_addr"}, ...]}``.
        ``cite_key`` is the 8-char hash suffix of ``raw_path`` (or
        ``""`` if the path is missing / malformed). ``note_on_cutoff``
        is None when no cutoff is applied; `date` is None for rows
        with no date in the DB.
        Detailed: ``{"thread_id": str, "messages": [{"message_id",
        "subject", "from_addr", "date": str | None, "raw_path",
        "first_200_chars", "compile_state"}, ...], "truncated": bool,
        "cutoff_date": str | None}``. Empty list / ``message_count: 0``
        when the thread is unknown.
    """
    from src.agent.run_state import _current_batch_cutoff_date
    from src.db import connect

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
            # `latest_date` is the thread's MAX(date) under the same
            # chronological cutoff — for truncated threads we MUST hit
            # the DB again because the tail slipped past the LIMIT (see
            # v10 followup P1-4 #196). When not truncated the window
            # already contains every row, so we can compute MAX locally
            # and skip the extra DB query.
            latest_date: str | None = None
            if truncated:
                max_row = cast(
                    dict[str, Any] | None,
                    conn.execute(
                        f"""
                        SELECT MAX(date) AS max_date
                          FROM messages
                         WHERE thread_id = %s
                               {cutoff_clause}
                        """,
                        (thread_id, cutoff) if cutoff else (thread_id,),
                    ).fetchone(),
                )
                max_date_val = (max_row or {}).get("max_date")
                latest_date = max_date_val.isoformat() if max_date_val else None
            else:
                for row in reversed(rows):
                    if row["date"]:
                        latest_date = row["date"].isoformat()
                        break
            first_subject = str(rows[0]["subject"] or "") if rows else ""
            # `raw_path` is the field the agent needs next — passing it
            # to `read_file` avoids a DB lookup to find the raw file.
            # `cite_key` is precomputed (see `_cite_key_from_raw_path`)
            # so the LLM doesn't redo the `rsplit` per footnote.
            # Body preview + compile_state stay in detailed only. We
            # cap per-row stubs at `_CONCISE_MESSAGE_CAP` so very long
            # threads can't balloon the concise payload.
            capped_rows = rows[:_CONCISE_MESSAGE_CAP]
            messages_summary = [
                {
                    "message_id": str(row["message_id"] or ""),
                    "raw_path": str(row["raw_path"] or ""),
                    "cite_key": _cite_key_from_raw_path(str(row["raw_path"] or "")),
                    # `None` signals "no date in DB" — callers shouldn't
                    # need to distinguish `""` from `None`.
                    "date": row["date"].isoformat() if row["date"] else None,
                    "from_addr": str(row["from_address"] or ""),
                }
                for row in capped_rows
            ]
            # `note_on_cutoff` uses the date-only form of the cutoff
            # because the SQL compares `date::date <= cutoff::date` —
            # messages dated `2026-01-14` at any time are visible even
            # when `cutoff` has a sub-day timestamp.
            cutoff_date = _cutoff_to_date(cutoff)
            note_on_cutoff = (
                f"Messages dated after {cutoff_date} are hidden per "
                f"chronological scope; messages on {cutoff_date} at any time remain visible."
                if cutoff_date
                else None
            )
            return {
                "thread_id": thread_id,
                "message_count": len(rows),
                "first_subject": first_subject,
                "latest_date": latest_date,
                "applied_cutoff_date": cutoff,
                "note_on_cutoff": note_on_cutoff,
                "truncated": truncated,
                "messages_summary": messages_summary,
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
                # Unified with concise mode: `None` signals missing.
                "date": date_val.isoformat() if date_val else None,
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
