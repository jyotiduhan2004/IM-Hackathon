"""Wiki compiler — Deep Agents workflow that compiles raw emails into wiki pages."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import re
import tempfile
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import cast

import structlog
import yaml
from langchain_core.tools import tool
from pydantic import BaseModel
from pydantic import Field

# Explicit `as` aliases are required by mypy-strict's
# `implicit_reexport = false` — without them, external callers doing
# `from src.compile.compiler import resolve_page` (e.g.
# `src/compile/reviewer.py`) trip `attr-defined`. Plain `import X`
# doesn't count as a re-export under strict mode. See PR #176 review.
from src.compile.draft import write_draft_page as write_draft_page
from src.compile.prompts import COMPILER_SYSTEM_PROMPT
from src.compile.tools.raw_access import get_thread_context as get_thread_context
from src.compile.tools.raw_access import resolve_page as resolve_page
from src.config import settings

if TYPE_CHECKING:
    from src.compile.tool_call_log import ToolCallLogHandler

logger = structlog.get_logger(__name__)

# ContextVar carrying the current batch's raw paths. The coordinator sets
# this in `run_compilation` before invoking the agent, and `create_entities`
# reads it without needing the LLM to thread `raw_paths` through. Shrinking
# the LLM-visible signature cuts one frequent error mode (agent forgets or
# malforms the raw_paths list).
_current_raw_paths: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "current_raw_paths", default=None
)

# ContextVar carrying the chronological cutoff for this batch — the latest
# `messages.date` among the batch's raw_paths. `get_thread_context` reads
# it to clip future replies the writer shouldn't see (Bug H fix). The
# prompt tells the agent it's processing email N of a thread "as a writer
# at that point in time"; this enforces it structurally.
#
# Stored as ISO8601 string (not datetime) so the ContextVar stays picklable
# and avoids tz-comparison surprises at query time — Postgres casts the
# literal to timestamptz.
_current_batch_cutoff_date: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_batch_cutoff_date", default=None
)

# ContextVar carrying the batch's thread_id — populated only when every
# raw_path in the batch belongs to the same Gmail thread. Read by the
# `same_thread_topic_guard` middleware to detect a second /wiki/topics/
# write within the same concept stream (Codex 2026-04-17 fragmentation
# bug: Seller BL thread producing two topic pages for one stream).
#
# Stays None when the batch straddles multiple threads — the guard
# isn't meaningful across threads and shouldn't fire.
_current_batch_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_batch_thread_id", default=None
)

# Topic slugs the agent has successfully written during the current
# run. Populated by `SameThreadTopicGuardMiddleware` on each
# successful `write_file` to /wiki/topics/ so that an in-run duplicate
# (second topic write in the same batch, before the coordinator's
# post-run catalog sync has a chance to land the first) is still
# caught. Catalog-only checks miss this case because
# `message_touched_pages` is populated *after* `run_compilation`
# returns. Codex P1 on PR #171.
_current_batch_topic_slugs_written: contextvars.ContextVar[set[str] | None] = (
    contextvars.ContextVar("current_batch_topic_slugs_written", default=None)
)

# Sibling-eligible page slugs (topic + system) the agent has written in
# this batch. Populated by `SiblingDraftCheckMiddleware` to detect
# near-duplicate page creations within a single batch — e.g. the Cycle
# 10 case where one batch produced both `seller-bl-api-optimization`
# and `seller-bl-api-hit-optimisation` from the same thread. The post-
# hoc reviewer (v9-U14) catches these too, but only AFTER both pages
# ship; this set lets the middleware reject the second write before it
# lands. v11-U9.
_current_batch_sibling_slugs_written: contextvars.ContextVar[set[str] | None] = (
    contextvars.ContextVar("current_batch_sibling_slugs_written", default=None)
)


class InvokeWallClockTimeout(Exception):  # noqa: N818 — public contract name
    """Raised when a single `agent.ainvoke` round exceeds `invoke_timeout_s`.

    Distinct from the outer `concurrent.futures.TimeoutError` raised by
    `scripts/compile_all.py::_run_with_timeout`: that one tracks cumulative
    batch wall-clock across model retries, while this one caps a single LLM
    round. Without the inner cap, a wedged proxy (grok-4.1-fast 2026-04-22:
    5h31m hang mid-round) exhausts the outer budget silently instead of
    surfacing as a timeout.
    """


# Cache of prior `check_my_work` payloads keyed by
# (raw_email_path, write_epoch, sha256(acknowledge)). Repeat calls with
# no intervening write hit cache and return the prior payload so the
# agent stops spinning (PR #225 regression: 4-7 blocked calls in a row
# with zero edits between). `_write_epoch` bumps invalidate the entry.
_check_my_work_cache: dict[tuple[str, int, str], dict[str, Any]] = {}

# Bumped by `CheckMyWorkGateMiddleware._record_success` on every
# successful content-page write — exposes one choke point to log or
# instrument if the count diverges from the agent's self-report.
_write_epoch: int = 0


def _bump_write_epoch() -> int:
    """Advance the write epoch and return the new value."""
    global _write_epoch
    _write_epoch += 1
    return _write_epoch


# === Custom Tools for the Compiler Agent ===


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
    `src/compile/categories.py::AGENT_VISIBLE_CATEGORIES`).
    """
    from src.compile.categories import AGENT_VISIBLE_CATEGORIES

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
def stamp_page_compiled_at(file_path: str) -> dict[str, str]:
    """Set last_compiled on a wiki page to the current real-world UTC time.

    Use this INSTEAD OF writing last_compiled yourself in the page frontmatter.
    You do not know the current date; this tool uses the system clock.

    Args:
        file_path: Path to the wiki page markdown file

    Returns:
        Dict with "ok" (bool), "last_compiled" (ISO string), "path" (str).
    """
    path = Path(file_path)
    if not path.exists():
        return {"ok": "false", "error": f"file not found: {file_path}"}

    content = path.read_text(encoding="utf-8")
    frontmatter = _extract_frontmatter(content)
    body = _extract_body(content)

    now_iso = datetime.now(UTC).isoformat()
    frontmatter["last_compiled"] = now_iso
    frontmatter["updated_by"] = settings.llm_model
    # Track running count of recompiles
    frontmatter["update_count"] = int(frontmatter.get("update_count") or 0) + 1

    new_content = _render_with_frontmatter(frontmatter, body)
    path.write_text(new_content, encoding="utf-8")
    return {
        "ok": "true",
        "last_compiled": now_iso,
        "updated_by": settings.llm_model,
        "update_count": frontmatter["update_count"],
        "path": file_path,
    }


@tool
def check_my_work(
    raw_email_path: str,
    acknowledge: list[str] | None = None,
) -> dict[str, Any]:
    """Critique every wiki page that cites a given raw email.

    WHEN: after you've written or edited wiki pages that cite a specific
      raw email — critiques those pages for frontmatter completeness,
      broken wikilinks, duplicate H2s, etc.
    WHEN NOT: for a general wiki page sanity check (use validate_page_draft
      on a specific slug instead); or if no page cites the raw yet (this
      tool has nothing to critique).

    What it checks: malformed frontmatter, duplicate H2 headings
    (most often caused by appending instead of merging), broken wikilinks
    (pointing to pages that don't exist), stray markdown brackets,
    H1-in-body (title belongs in frontmatter). Blockers fail the check;
    warnings are advisory.

    Feedback loop (single-thread, same session):
      1. Finish writing. Call `check_my_work(raw_email_path)`.
      2. If `blockers` is empty → you're done with this email. Move on.
      3. If `blockers` is non-empty → edit the flagged pages to resolve
         each one (usually a merge or a broken link to fix). Call the
         tool again. Repeat until clean.
      4. If you genuinely believe a blocker is a false positive for this
         context, call again with ``acknowledge=['id1','id2']`` — the
         check treats those IDs as intentional and passes.

    Every call writes an audit file to
    ``docs/audits/critique-<ISO>-<msgid>.md`` so the operator can sample
    how often blockers surfaced, what you fixed, and what you acked.

    NOTE: This tool does NOT flip DB state. The coordinator
    (``scripts/compile_all.py``) programmatically marks messages compiled
    after your session returns, based on citation + this audit trail. Your
    job is just to make the check come back clean.

    Args:
        raw_email_path: path to the raw email whose citing wiki pages
            should be critiqued. Format: "raw/2026-01-14_foo_bar.md".
        acknowledge: Optional list of issue IDs from a prior blocked call
            that you've decided are false positives.

    Returns:
        ``{"ok": "true", "status": "clean", "warnings": N,
        "raw_email_path": str, "pages_critiqued": list[str], "audit": path}``
        when blockers are resolved or acknowledged, OR
        ``{"ok": "false", "status": "blocked", "issues": [{id, check,
        page, message}, ...], "raw_email_path": str, "pages_critiqued":
        list[str], "audit": path, "hint": ...}`` when action is required.
        Repeat calls with no intervening write include
        ``"unchanged_since": true`` and a nudge in the message to stop
        spinning.
    """
    from src.compile.critique import critique_pages
    from src.compile.critique import find_touched_pages
    from src.compile.critique import write_audit

    repo_root = Path.cwd()
    wiki_dir = repo_root / "wiki"
    audit_dir = repo_root / "docs" / "audits"

    # Sorted ack list so (['b', 'a'],) hashes the same as (['a', 'b'],).
    ack_hash = hashlib.sha256((",".join(sorted(acknowledge or []))).encode("utf-8")).hexdigest()
    cache_key = (raw_email_path, _write_epoch, ack_hash)
    cached = _check_my_work_cache.get(cache_key)
    if cached is not None:
        payload = dict(cached)
        payload["unchanged_since"] = True
        payload["message"] = (
            "Same blockers as last check_my_work call, no intervening "
            "edits. Either write a page or call log_insight and return."
        )
        logger.info(
            "check_my_work_cache_hit",
            raw_email_path=raw_email_path,
            write_epoch=_write_epoch,
        )
        return payload

    touched = find_touched_pages(raw_email_path, wiki_dir)
    result = critique_pages(touched, wiki_dir, repo_root)

    ack_ids = set(acknowledge or [])
    unresolved = [i for i in result.blockers if i.id not in ack_ids]

    if unresolved:
        audit_path = write_audit(
            result, raw_email_path, "blocked", audit_dir, acknowledged_ids=ack_ids
        )
        logger.info(
            "check_my_work blocked",
            raw_email_path=raw_email_path,
            blockers=len(unresolved),
            audit=str(audit_path),
        )
        payload = {
            "ok": "false",
            "status": "blocked",
            "issues": [
                {
                    "id": i.id,
                    "check": i.check,
                    "page": i.page,
                    "message": i.message,
                }
                for i in unresolved
            ],
            "raw_email_path": raw_email_path,
            "pages_critiqued": result.pages_critiqued,
            "audit": str(audit_path.relative_to(repo_root)),
            "hint": (
                "Edit the flagged pages to fix each blocker (usually: "
                "merge duplicate H2 sections, resolve broken wikilinks, "
                "remove stray brackets) and call check_my_work again. "
                "If a blocker is genuinely a false positive, call with "
                "acknowledge=['issue_id', ...] to proceed."
            ),
        }
        _check_my_work_cache[cache_key] = payload
        return payload

    audit_path = write_audit(result, raw_email_path, "clean", audit_dir, acknowledged_ids=ack_ids)
    payload = {
        "ok": "true",
        "status": "clean",
        "warnings": len(result.warnings),
        "raw_email_path": raw_email_path,
        "pages_critiqued": result.pages_critiqued,
        "audit": str(audit_path.relative_to(repo_root)),
    }
    _check_my_work_cache[cache_key] = payload
    return payload


@tool
def mark_as_compiled(file_path: str) -> dict[str, str | int]:
    """Mark a raw email as compiled in the Postgres catalog. NOT exposed to
    the agent — kept importable for manual ops. The coordinator
    (``scripts/compile_all.py``) marks batches deterministically after the
    agent returns.
    """
    from src.db.messages import find_by_raw_path
    from src.db.messages import finish_message_compile
    from src.db.messages import remaining_uncompiled_count

    row = find_by_raw_path(file_path)
    if row is None:
        return {"ok": "false", "error": f"no messages row for raw_path={file_path}"}

    finish_message_compile(row["message_id"])

    return {
        "ok": "true",
        "remaining_uncompiled": remaining_uncompiled_count(),
        "path": file_path,
    }


def _compile_progress_block() -> list[str]:
    """Render a compile-progress section for wiki/index.md.

    Pulls live state from Postgres: overall state counts + per-week email
    volume (ingested vs compiled, bucketed by the email's send date). Fails
    open with an empty list if the DB is unreachable so index generation
    always succeeds.
    """
    try:
        from src.db import connect
    except ImportError:
        return []

    try:
        with connect() as conn:
            state_rows = conn.execute(
                "SELECT compile_state, count(*)::int AS n FROM messages GROUP BY 1"
            ).fetchall()
            weekly_rows = conn.execute(
                """
                SELECT date_trunc('week', date)::date AS week,
                       count(*)::int AS total,
                       count(*) FILTER (WHERE compile_state = 'compiled')::int AS compiled
                  FROM messages
                 WHERE date IS NOT NULL
                 GROUP BY 1
                 ORDER BY 1
                """
            ).fetchall()
    except Exception:  # noqa: BLE001 — DB is best-effort for index rendering
        return []

    states = {r["compile_state"]: r["n"] for r in state_rows}
    total = sum(states.values())
    if total == 0:
        return []
    compiled = states.get("compiled", 0)
    pending = states.get("pending", 0)
    failed = states.get("failed", 0)
    claimed = states.get("claimed", 0)
    skipped = states.get("skipped", 0)
    pct = (100 * compiled / total) if total else 0.0

    summary = f"{pending:,} pending, {failed:,} failed, {claimed:,} in-flight"
    if skipped:
        summary += f", {skipped:,} skipped"

    lines: list[str] = [
        "## Compile progress",
        "",
        f"**{compiled:,} of {total:,} emails compiled** ({pct:.1f}%). {summary}.",
        "",
        "```mermaid",
        "pie showData title Compile state",
        f'    "compiled" : {compiled}',
        f'    "pending" : {pending}',
    ]
    if failed:
        lines.append(f'    "failed" : {failed}')
    if claimed:
        lines.append(f'    "in-flight" : {claimed}')
    if skipped:
        lines.append(f'    "skipped" : {skipped}')
    lines.extend(["```", ""])

    if weekly_rows:
        # Ascii bar chart: one bar per week, width scaled to the busiest week.
        max_total = max(r["total"] for r in weekly_rows) or 1
        lines.extend(
            [
                "### Emails per week (by send date)",
                "",
                "| Week starting | Ingested | Compiled | Coverage |",
                "|---|---:|---:|---|",
            ]
        )
        for r in weekly_rows:
            bar_width = round(20 * r["total"] / max_total) or 1
            compiled_width = round(20 * r["compiled"] / max_total)
            bar = "█" * compiled_width + "░" * (bar_width - compiled_width)
            lines.append(f"| {r['week']} | {r['total']} | {r['compiled']} | `{bar}` |")
        lines.append("")

    return lines


@tool
def update_wiki_index(wiki_dir: str = "wiki") -> str:
    """Regenerate `wiki/compile-status.md` — internal ops view of the compile
    pipeline (counts, weekly coverage, per-category page listings).

    `/` is served by `wiki/index.md` (the North-Star 8-domain-card home,
    written by `_generate_home`). This function's output lives at
    `/compile-status/` so the owner can still inspect pipeline health
    without that view being the reader front door. Prior to 2026-04-24 this
    function wrote `wiki/index.md` directly; that stomped the reader home.

    Also auto-stamps `last_compiled` on any page missing the field, using the
    current real UTC time. This guarantees every page has a timestamp without
    relying on the agent to call `stamp_page_compiled_at` for each one.

    Args:
        wiki_dir: Root wiki directory

    Returns:
        Summary: pages indexed + pages auto-stamped
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return f"ERROR: wiki directory not found: {wiki_dir}"

    categories: dict[str, list[str]] = {
        "policies": [],
        "topics": [],
        "entities": [],
        "systems": [],
        "timelines": [],
        "conflicts": [],
    }
    stamped = 0
    now_iso = datetime.now(UTC).isoformat()

    for category in categories:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = _extract_frontmatter(content)
                # Auto-stamp only if the frontmatter looks complete enough.
                # A broken frontmatter (e.g., only `last_compiled` present) means
                # the agent's edit_file mangled the page — don't overwrite it,
                # or we'll destroy what's left.
                has_real_fields = "title" in fm or "page_type" in fm
                if "last_compiled" not in fm and has_real_fields:
                    fm["last_compiled"] = now_iso
                    fm.setdefault("updated_by", settings.llm_model)
                    fm["update_count"] = int(fm.get("update_count") or 0) + 1
                    body = _extract_body(content)
                    md_file.write_text(_render_with_frontmatter(fm, body), encoding="utf-8")
                    stamped += 1
                title = fm.get("title", md_file.stem)
                status = fm.get("status", "current")
                name = md_file.stem
                entry = f"- [[{name}]] — {title}"
                if status != "current":
                    entry += f" *({status})*"
                categories[category].append(entry)
            except (yaml.YAMLError, UnicodeDecodeError):
                continue

    lines = [
        "# Compile status",
        "",
        "Internal pipeline view — counts, weekly coverage, and the full page",
        "catalog. For the reader-facing home, see [/]( ./).",
        "",
        f"Last updated: {now_iso}",
        "",
    ]
    lines.extend(_compile_progress_block())

    total = 0
    cat_blocks: list[str] = []
    for cat_name, entries in categories.items():
        if entries:
            cat_blocks.append(f"## {cat_name.title()} ({len(entries)})")
            cat_blocks.extend(entries)
            cat_blocks.append("")
            total += len(entries)

    lines.insert(5, f"Total pages: {total}")
    lines.insert(6, "")
    lines.extend(cat_blocks)

    index_path = wiki_path / "compile-status.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Retire the legacy `wiki/index.md` written by this function before
    # 2026-04-24 — `_generate_home` now owns that path. Safe to unlink
    # because `_generate_home` (called later in the landing-surfaces
    # phase) will recreate index.md with the reader-facing home content.
    legacy_index = wiki_path / "index.md"
    if legacy_index.exists():
        try:
            legacy_index.unlink()
        except OSError:
            pass

    # Rebuild reader-facing landing pages (home.md + section indexes) so the
    # deployed site shows real page listings instead of the hand-written
    # "(Placeholder — comes in a later PR)" stubs. Deterministic — no LLM in
    # this path — so it's safe to run every compile cycle.
    landing_summary = rebuild_landing_pages(wiki_dir)

    return (
        f"updated index: {total} pages across "
        f"{sum(1 for v in categories.values() if v)} categories; "
        f"auto-stamped {stamped} pages with last_compiled; "
        f"{landing_summary}"
    )


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (temp file + Path.replace).

    Prevents truncated/partial landing pages if the process dies mid-write
    or two coordinator entrypoints race (e.g. compile_all + watch_and_compile
    running at the same time). `Path.replace` is atomic on POSIX and Windows
    when src and dst are on the same filesystem — tempfile creates in the
    same directory, so the guarantee holds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _page_summary(md_file: Path) -> dict[str, Any] | None:
    """Extract reader-facing summary fields from a wiki page.

    Returns None for pages with broken frontmatter so the caller can skip
    them. The `is_stub` flag is True when the page has no provenance
    evidence — neither per-message `sources:` nor post-Phase-A
    `source_threads:` — used to hide ghost person/system pages from the
    landing listings.
    """
    try:
        content = md_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = _extract_frontmatter(content)
    if not fm or "title" not in fm:
        return None
    body = _extract_body(content)
    sources = fm.get("sources") or []
    # Post-Phase-A pages carry provenance as `source_threads:` — the agent
    # no longer writes `sources:`. Either field counts as "not a stub".
    source_threads = fm.get("source_threads") or []
    sources_list = sources if isinstance(sources, list) else []
    threads_list = source_threads if isinstance(source_threads, list) else []
    has_provenance = bool(sources_list) or bool(threads_list)
    last_compiled = str(fm.get("last_compiled", "") or "")
    # `last_compiled: stub` and `stub-backfilled` are the canonical stub
    # markers used by `scripts/backfill_stubs.py` — keep this rule in sync
    # with `scripts/backfill_stubs.py::_is_stub`.
    is_stub_marker = last_compiled in ("stub", "stub-backfilled")
    return {
        "slug": md_file.stem,
        "title": str(fm.get("title", md_file.stem)),
        "status": str(fm.get("status", "active")),
        "last_compiled": last_compiled,
        "summary": _first_paragraph(body),
        "sources_count": len(sources_list) + len(threads_list),
        "is_stub": not has_provenance or is_stub_marker,
    }


def _first_paragraph(body: str) -> str:
    """Return the first non-empty, non-heading paragraph from a page body.

    Used as the one-line summary next to each listing entry. Truncated to
    a single line (280 chars) so long intros don't blow up the listing.
    """
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current:
                break
            continue
        if not stripped:
            if current:
                break
            continue
        current.append(stripped)
    if not current:
        return ""
    para = " ".join(current)
    return para[:277] + "..." if len(para) > 280 else para


_SECTION_BLURBS = {
    "topics": (
        "Projects, initiatives, rollouts, incidents, decisions, and "
        "migrations being discussed on the mailing list. If a page is "
        "mostly about **status and change**, it lives here."
    ),
    "systems": (
        "Durable products, platforms, tools, services, and mailing lists. "
        "If a page is mostly about **the thing itself**, it lives here."
    ),
    "people": (
        "Contributors and owners named on the mailing list. Stub pages "
        "with no cited sources are hidden from this view."
    ),
    "policies": (
        "Current rules, approval flows, guidelines, and procedures — "
        "including the supersession history when a policy has been replaced."
    ),
    "domains": (
        "Compiler-generated domain hubs — one per North-Star domain. "
        "Each lists the topics and systems tagged with that domain."
    ),
    "decisions": (
        "Explicit decisions made on the mailing list — rollouts, "
        "policy changes, supersession calls, architectural picks. "
        "Lazy-created and linked from topics."
    ),
}

_SECTION_TITLES = {
    "topics": "Topics",
    "systems": "Products & Platforms",
    "people": "People",
    "policies": "Policies",
    "domains": "Domains",
    "decisions": "Decisions",
}


def rebuild_landing_pages(wiki_dir: str = "wiki") -> str:
    """Regenerate home.md + 4 section index pages as real listings.

    Replaces the hardcoded "(Placeholder — comes in a later PR)" stubs
    with full page listings sorted by `last_compiled` desc. Entity and
    system pages with `sources: []` are hidden (they're ghost pages left
    over from the create_entity evidence-gate workflow).

    Also rebuilds the "## Appears in" backlinks section on every person
    page — readers landing on a person page (often via wikilink from a
    topic) otherwise see a bare stub with no way back to the content
    they came from.

    This is a pure coordinator function — no LLM — so it runs on every
    compile cycle via `update_wiki_index` (the shared entrypoint used
    by compile_all, compile_parallel, and watch_and_compile).

    Returns a one-line summary for the CLI log.
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return f"landing rebuild skipped: wiki dir not found ({wiki_dir})"

    now_iso = datetime.now(UTC).isoformat()
    sections: dict[str, list[dict[str, Any]]] = {}
    for category in _SECTION_TITLES:
        cat_dir = wiki_path / category
        pages: list[dict[str, Any]] = []
        if cat_dir.exists():
            for md_file in cat_dir.glob("*.md"):
                if md_file.name == "index.md":
                    continue
                summary = _page_summary(md_file)
                if summary is None:
                    continue
                # Hide ghost system pages (auto-stub gate is closed but
                # legacy ones remain). People pages are intentionally
                # provenance-light — they're cited via incoming wikilinks
                # from topics/systems, not via per-message `sources:` —
                # so filtering them by the same stub rule would zero out
                # the people index. Status filter handles superseded.
                if category == "systems" and summary["is_stub"]:
                    continue
                pages.append(summary)
        pages.sort(key=lambda p: (p["last_compiled"], p["title"]), reverse=True)
        sections[category] = pages

    for category, pages in sections.items():
        _write_section_index(wiki_path, category, pages, now_iso)

    # home.md retired 2026-04-24 — `_generate_home` (called later in
    # `_regenerate_landing_surfaces`) owns the reader-facing front door
    # at `wiki/index.md` with the 8-domain card layout. Keeping a
    # `_write_home` call here would just write a file that the next
    # step deletes.
    backlinks_count = _rebuild_person_backlinks(wiki_path)

    totals = ", ".join(f"{k}={len(v)}" for k, v in sections.items())
    return f"rebuilt landing pages ({totals}, person_backlinks={backlinks_count})"


# Source categories scanned for incoming wikilinks. Person/entity pages
# aren't scanned — they're the targets, not linkers. Legacy wiki/entities/
# is intentionally out: C1 migrated those to wiki/people/ and anything
# left is a stub.
_BACKLINK_SOURCE_CATEGORIES: tuple[str, ...] = (
    "topics",
    "systems",
    "policies",
    "decisions",
    "timelines",
    "conflicts",
    "domains",
)

# Captures `[[target]]` or `[[target|display]]`. Non-greedy on pipe so
# `[[a|b]][[c]]` is two links, not one.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\[\]]*?)?(?:#[^\[\]]*?)?\]\]")

# Marker for the auto-generated backlinks section. Seeing it in a page's
# body tells the next rebuild where to splice — anything after the marker
# gets replaced, anything before is preserved so hand-written body
# content stays.
_BACKLINKS_HEADING = "## Appears in"
_BACKLINKS_FOOTER = "<!-- generated by compiler; do not edit by hand -->"


def _rebuild_person_backlinks(wiki_path: Path) -> int:
    """Append/refresh an "Appears in" section on every wiki/people page.

    Scans content pages (topics, systems, policies, decisions, glossary,
    timelines, conflicts, domains) for `[[<slug>]]` wikilinks whose
    target matches a slug in `wiki/people/`. For each person with ≥ 1
    incoming link, rewrites the body's auto-generated section to list
    the linking pages grouped by category.

    Idempotent: re-running produces identical output. The section is
    marked with a sentinel comment so manual-edited body content above
    it is preserved.

    Returns the number of person pages whose backlinks section was
    written (either created or updated).
    """
    people_dir = wiki_path / "people"
    if not people_dir.exists():
        return 0

    # Build person-slug → person-md-path index. Target set for link matching.
    person_pages: dict[str, Path] = {}
    for md in people_dir.glob("*.md"):
        if md.name == "index.md":
            continue
        person_pages[md.stem] = md
    if not person_pages:
        return 0

    # Scan source pages. Collect per-target list of (linker_slug, title, category).
    backlinks: dict[str, list[dict[str, str]]] = {slug: [] for slug in person_pages}
    for category in _BACKLINK_SOURCE_CATEGORIES:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md in cat_dir.glob("*.md"):
            if md.name == "index.md":
                continue
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = _extract_frontmatter(content)
            body = _extract_body(content)
            title = str(fm.get("title") or md.stem)
            seen_here: set[str] = set()
            for match in _WIKILINK_RE.finditer(body):
                target = match.group(1).strip()
                # Normalize `people/<slug>` → `<slug>` so either form matches.
                if "/" in target:
                    target = target.rsplit("/", 1)[-1]
                if target in person_pages and target not in seen_here:
                    seen_here.add(target)
                    backlinks[target].append(
                        {"slug": md.stem, "title": title, "category": category}
                    )

    # Deterministic order: category asc, then slug asc. Stable snapshot.
    for entries in backlinks.values():
        entries.sort(key=lambda e: (e["category"], e["slug"]))

    written = 0
    for slug, path in person_pages.items():
        entries = backlinks[slug]
        if _write_person_backlinks(path, entries):
            written += 1
    return written


def _write_person_backlinks(path: Path, entries: list[dict[str, str]]) -> bool:
    """Rewrite the backlinks section of ONE person page. Returns True if
    the file changed on disk.

    Preserves frontmatter and any body above the `## Appears in` heading.
    If the heading is absent, appends at the bottom. If `entries` is
    empty, removes any existing generated section so bare stubs don't
    carry a dangling header.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm = _extract_frontmatter(content)
    body = _extract_body(content)

    # Guard on the sentinel footer so a hand-written `## Appears in`
    # section (without the sentinel) is preserved — otherwise the
    # "NEVER remove history" invariant leaks. Three states:
    #   - has_generated (heading + sentinel): strip + rewrite
    #   - has_handwritten (heading but no sentinel): leave the page alone
    #   - neither: append the generated block fresh
    heading_idx = body.find(_BACKLINKS_HEADING)
    sentinel_idx = body.find(_BACKLINKS_FOOTER)
    has_generated = heading_idx != -1 and sentinel_idx != -1 and sentinel_idx > heading_idx
    has_handwritten = heading_idx != -1 and not has_generated

    if has_handwritten:
        # Hand-written section wins. Don't strip, don't append — a
        # second `## Appears in` underneath would duplicate the heading.
        return False

    if has_generated:
        body = body[:heading_idx].rstrip() + "\n"

    if entries:
        lines = ["", _BACKLINKS_HEADING, ""]
        current_category: str | None = None
        for entry in entries:
            if entry["category"] != current_category:
                current_category = entry["category"]
                lines.append(f"### {_SECTION_TITLES.get(current_category, current_category)}")
                lines.append("")
            lines.append(f"- [[{entry['slug']}]] — {entry['title']}")
        lines.append("")
        lines.append(_BACKLINKS_FOOTER)
        lines.append("")
        new_body = body.rstrip() + "\n" + "\n".join(lines)
    else:
        new_body = body

    new_content = _render_with_frontmatter(fm, new_body)
    if new_content == content:
        return False
    _atomic_write_text(path, new_content)
    return True


def _write_section_index(
    wiki_path: Path, category: str, pages: list[dict[str, Any]], now_iso: str
) -> None:
    """Write `<category>/index.md` as a real listing."""
    title = _SECTION_TITLES[category]
    blurb = _SECTION_BLURBS[category]
    lines = [f"# {title}", "", blurb, ""]
    if pages:
        lines.extend([f"**{len(pages)} pages**, most recently compiled first.", ""])
        for page in pages:
            # Canonical status values — `active` (new) and `current` (legacy)
            # both mean "live page, no suffix". Everything else (`superseded`,
            # `archived`, `contested`) gets a suffix so readers see it.
            status_suffix = (
                "" if page["status"] in ("active", "current") else f" *({page['status']})*"
            )
            entry = f"- [[{page['slug']}]] — {page['title']}{status_suffix}"
            lines.append(entry)
            if page["summary"]:
                lines.append(f"  <br>{page['summary']}")
    else:
        lines.append("*No pages compiled yet.*")
    lines.append("")

    fm = {
        "title": title,
        "page_type": "index",
        "status": "active",
        "last_compiled": now_iso,
    }
    _atomic_write_text(
        wiki_path / category / "index.md",
        _render_with_frontmatter(fm, "\n".join(lines)),
    )


# === North-Star landing generators ===
#
# These surface the 8 fixed domain hubs, glossary, home, changes, and lazy
# decision stubs called out in docs/NORTH-STAR.md. All are pure coordinator
# functions (no LLM) called after `rebuild_landing_pages` so the deployed
# site reflects the concept-wiki ontology even before per-page frontmatter
# catches up.


_DOMAINS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "buyer-experience",
        "Buyer Experience",
        ("buymer", "buylead", "buyer app", "search ux", "lens", "whatsapp buyer"),
    ),
    (
        "seller-experience",
        "Seller Experience",
        ("auditmate", "seller im", "seller dashboard", "specs", "compliance"),
    ),
    (
        "marketplace-discovery",
        "Marketplace & Discovery",
        (
            "mcat",
            "isq",
            "photosearch",
            "ranking",
            "categorization",
            "recommendations",
        ),
    ),
    (
        "platform-reliability",
        "Platform Reliability & Infrastructure",
        ("gke", "mesh pg", "db ops", "api framework", "performance"),
    ),
    (
        "trust-safety",
        "Trust, Safety & Compliance",
        ("kyc", "gst", "fraud", "moderation", "payment protection", "trustseal"),
    ),
    (
        "ai-automation",
        "AI Agents & Automation",
        ("crashagent", "whatsapp 9696", "autonomous assistant"),
    ),
    (
        "growth-monetization",
        "Growth, Monetization & Partnerships",
        ("export", "ads", "affiliates", "google merchant", "tenders"),
    ),
    (
        "engineering-productivity",
        "Engineering Productivity & Quality",
        ("ci/cd", "code quality", "testing", "dev tools"),
    ),
)

# Keyed by domain slug → (display_name, keywords). Used for O(1) lookup
# when frontmatter names a domain explicitly.
_DOMAIN_BY_SLUG: dict[str, tuple[str, tuple[str, ...]]] = {
    slug: (title, keywords) for slug, title, keywords in _DOMAINS
}

# Slug-prefix → expected canonical domain. Tiebreaker hint when keyword
# inference is ambiguous and a sanity signal the validator surfaces when
# the agent set an obviously wrong domain (v11-U8 — Cycle 10 audit found
# `seller-bl-api-optimization` shipped with `domain: buyer-experience`).
# Every value MUST be a key of `_DOMAIN_BY_SLUG`; drift would silently
# misroute pages.
_SLUG_PREFIX_DOMAIN: dict[str, str] = {
    # Seller-facing pages
    "seller-": "seller-experience",
    "bl-": "seller-experience",  # BL = buy-lead (a seller acquisition product)
    "buylead-": "seller-experience",
    "lms-": "seller-experience",
    # Buyer-facing pages
    "buyer-": "buyer-experience",
    "buyermy-": "buyer-experience",
    # Discovery / marketplace
    "mcat-": "marketplace-discovery",
    "isq-": "marketplace-discovery",
    "categoriz-": "marketplace-discovery",
    # Engineering productivity
    "gladmin-": "engineering-productivity",
    "ci-": "engineering-productivity",
    "cd-": "engineering-productivity",
}
# Defensive guard: reject any prefix that doesn't map to a canonical
# domain. Cheaper to fail at import than to silently route a page to a
# nonexistent hub.
_bad_prefix_domains = {
    domain for domain in _SLUG_PREFIX_DOMAIN.values() if domain not in _DOMAIN_BY_SLUG
}
if _bad_prefix_domains:
    raise RuntimeError(
        f"_SLUG_PREFIX_DOMAIN values must be canonical domain slugs; "
        f"unknown: {sorted(_bad_prefix_domains)}"
    )


def _domain_from_slug_prefix(slug: str) -> str | None:
    """Return the expected domain for a page slug, or None if no prefix matches.

    Longest-prefix-match wins so `buyermy-` beats `buyer-`. Used as a
    tiebreaker by `_assign_domains` and as a sanity check by the
    validator (`check_domain_prefix_mismatch`).
    """
    for prefix in sorted(_SLUG_PREFIX_DOMAIN, key=len, reverse=True):
        if slug.startswith(prefix):
            return _SLUG_PREFIX_DOMAIN[prefix]
    return None


_GENERATED_MARKER = "<!-- generated by compiler; do not edit by hand -->"


def _iter_content_pages(wiki_path: Path) -> list[Path]:
    """Return every topic + system page. Used by multiple generators.

    Skips the section `index.md` files — those are generated listings, not
    source pages for domain/glossary/decision rollups.
    """
    pages: list[Path] = []
    for category in ("topics", "systems"):
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            if md_file.name == "index.md":
                continue
            pages.append(md_file)
    return pages


def _read_page(md_file: Path) -> tuple[dict[str, Any], str] | None:
    """Return (frontmatter, body) for a page, or None on corrupt/unreadable.

    Single read per file — callers avoid duplicate disk hits for pages that
    drive several generators in the same pass.
    """
    try:
        content = md_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = _extract_frontmatter(content)
    if not fm:
        return None
    body = _extract_body(content)
    return fm, body


def _infer_domain_from_keywords(title: str, body: str) -> str | None:
    """Return the first domain slug whose keyword list hits title+body.

    Body is scanned to its first paragraph only (via `_first_paragraph`) —
    scanning the full body let noise from "related" sections win over the
    page's actual subject. First-match wins for determinism; `_DOMAINS`
    order is the tie-breaker.
    """
    haystack = f"{title}\n{_first_paragraph(body)}".lower()
    for slug, _title, keywords in _DOMAINS:
        for kw in keywords:
            if kw in haystack:
                return slug
    return None


def _assign_domains(fm: dict[str, Any], body: str, slug: str = "") -> list[str]:
    """Decide which domain hub(s) a page belongs to.

    `slug` is the page's filename stem (or frontmatter `slug:`); it
    powers the v11-U8 prefix tiebreaker. Empty string disables it.

    Preference order (per North Star):
    1. Explicit `domains:` list frontmatter — v10-U2 multi-value form;
       every canonical slug in the list attaches the page (a topic that
       spans e.g. trust-safety + growth-monetization shows up on both
       hubs). When any non-empty `domains:` list is present, it is
       authoritative: even a list of entirely non-canonical entries
       still blocks the fallthrough so the validator's `unknown-domain-
       value` warnings match the viewer's & rollup's rendering. Matches
       `_render_domain_badges` / `_extract_domain_values` precedence.
    2. Explicit `domain:` frontmatter — trusted verbatim (1 hub). The
       slug-prefix sanity check lives in the validator, not here, so the
       compiler never silently overrides a human/agent decision.
    3. `tags:` list — every tag matching a domain slug attaches the page.
    4. Keyword match against the page title + first paragraph — transitional
       fallback. Callers aggregate the inferred count and emit a single
       summary log per pass (see `_regenerate_domain_hubs`,
       `_bucket_pages_by_domain`) instead of per-page noise.
    5. Slug-prefix tiebreaker (v11-U8) — when keyword inference fell
       through without a hit, the slug prefix may still tag the page.
       Only fires AFTER explicit input is exhausted; prefix is a hint,
       never an override. `seller-bl-api-optimization` would land on
       `seller-experience` instead of `[]`.
    """
    plural = fm.get("domains")
    if isinstance(plural, list) and plural:
        # Any non-empty list is authoritative: if every entry is bogus we
        # still return `[]` rather than fall through to `domain:` /
        # keyword inference, matching the validator + renderer.
        return [v for v in plural if isinstance(v, str) and v in _DOMAIN_BY_SLUG]

    explicit = fm.get("domain")
    if isinstance(explicit, str) and explicit in _DOMAIN_BY_SLUG:
        return [explicit]

    tags = fm.get("tags") or []
    if isinstance(tags, list):
        tag_hits = [t for t in tags if isinstance(t, str) and t in _DOMAIN_BY_SLUG]
        if tag_hits:
            return tag_hits

    title = str(fm.get("title", ""))
    inferred = _infer_domain_from_keywords(title, body)
    if inferred:
        return [inferred]

    # Tiebreaker: only consult slug-prefix when keyword inference came
    # up empty. Conservative — never overrides an inferred hit. The
    # validator owns the louder "your explicit `domain:` disagrees with
    # your slug" warning so we don't mangle an agent's deliberate choice.
    prefix_hint = _domain_from_slug_prefix(slug) if slug else None
    if prefix_hint:
        return [prefix_hint]
    return []


def _was_domain_inferred(fm: dict[str, Any]) -> bool:
    """Return True iff `_assign_domains` would fall through to keyword inference.

    A page "needs the inference fallback" when none of `domains:` (non-empty
    list), `domain:`, or `tags:` surfaces a canonical domain slug. Pure
    helper used by callers that want to emit a single aggregate log per
    pass rather than per page (the per-page log generated duplicate lines
    when the same page drove multiple rollups).
    """
    plural = fm.get("domains")
    if isinstance(plural, list) and plural:
        return False
    explicit = fm.get("domain")
    if isinstance(explicit, str) and explicit in _DOMAIN_BY_SLUG:
        return False
    tags = fm.get("tags") or []
    return not (
        isinstance(tags, list) and any(isinstance(t, str) and t in _DOMAIN_BY_SLUG for t in tags)
    )


def _regenerate_domain_hubs(wiki_dir: Path) -> list[Path]:
    """Emit `wiki/domains/<slug>.md` for each of the 8 fixed domains.

    Always writes all 8 — an empty domain still gets a page so the home
    page's domain-card grid resolves every link. Idempotent: same corpus
    produces byte-identical output.
    """
    buckets: dict[str, dict[str, list[tuple[str, str]]]] = {
        slug: {"topics": [], "systems": []} for slug, _t, _k in _DOMAINS
    }
    inferred_count = 0

    for md_file in _iter_content_pages(wiki_dir):
        read = _read_page(md_file)
        if read is None:
            continue
        fm, body = read
        category = md_file.parent.name
        slugs = _assign_domains(fm, body, md_file.stem)
        if not slugs:
            continue
        if _was_domain_inferred(fm):
            inferred_count += 1
        entry = (md_file.stem, _first_paragraph(body))
        for slug in slugs:
            buckets[slug][category].append(entry)

    if inferred_count:
        # Single summary log per regen pass — replaces the per-page warning
        # that fired once per bucket lookup (v10-U3 followup P2, #191).
        logger.info(
            "domain-hub-inferred-from-keyword-summary",
            pages=inferred_count,
        )

    domains_dir = wiki_dir / "domains"
    written: list[Path] = []
    for slug, title, _keywords in _DOMAINS:
        bucket = buckets[slug]
        for category_pages in bucket.values():
            category_pages.sort(key=lambda e: e[0])

        lines = [f"# {title}", "", "## Topics", ""]
        topics = bucket["topics"]
        if topics:
            for stem, summary in topics:
                suffix = f" — {summary}" if summary else ""
                lines.append(f"- [[topics/{stem}]]{suffix}")
        else:
            lines.append("*No topics yet.*")
        lines.extend(["", "## Systems", ""])
        systems = bucket["systems"]
        if systems:
            for stem, summary in systems:
                suffix = f" — {summary}" if summary else ""
                lines.append(f"- [[systems/{stem}]]{suffix}")
        else:
            lines.append("*No systems yet.*")
        lines.extend(["", _GENERATED_MARKER, ""])

        fm = {
            "title": title,
            "page_type": "domain",
            "status": "active",
            "slug": f"domains/{slug}",
        }
        path = domains_dir / f"{slug}.md"
        _atomic_write_text(path, _render_with_frontmatter(fm, "\n".join(lines)))
        logger.info(
            "generated",
            kind="domain-hub",
            slug=slug,
            topics=len(topics),
            systems=len(systems),
        )
        written.append(path)
    return written


_DECISION_LINK_RE = re.compile(r"\[\[decisions?/([A-Za-z0-9_\-]+)\]\]")


def _regenerate_decision_stubs(wiki_dir: Path) -> list[Path]:
    """Lazy-create `wiki/decisions/<slug>.md` stubs for every wikilink target.

    Idempotent:
    - Never overwrites an existing decision page's body — they may be
      human-authored or future-compiled with real content.
    - Always rewrites the "Referenced by" block so links stay fresh as
      topics are added/removed.
    """
    backrefs: dict[str, list[str]] = {}
    for md_file in _iter_content_pages(wiki_dir):
        try:
            body = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _DECISION_LINK_RE.finditer(body):
            slug = match.group(1)
            link = f"{md_file.parent.name}/{md_file.stem}"
            slot = backrefs.setdefault(slug, [])
            if link not in slot:
                slot.append(link)

    decisions_dir = wiki_dir / "decisions"
    written: list[Path] = []
    for slug, refs in backrefs.items():
        path = decisions_dir / f"{slug}.md"
        title = slug.replace("-", " ").replace("_", " ").strip().title()
        refs_sorted = sorted(refs)

        existing = _read_page(path) if path.exists() else None
        if existing is None:
            lines = [
                f"# {title}",
                "",
                "<TODO: enrich from referencing topic(s)>",
                "",
                "## Referenced by",
                "",
            ]
            lines.extend(f"- [[{r}]]" for r in refs_sorted)
            lines.extend(["", _GENERATED_MARKER, ""])
            fm = {
                "title": title,
                "page_type": "decision",
                "status": "active",
                "slug": f"decisions/{slug}",
            }
            _atomic_write_text(path, _render_with_frontmatter(fm, "\n".join(lines)))
            logger.info("generated", kind="decision-stub", slug=slug, refs=len(refs_sorted))
        else:
            fm, body = existing
            new_body = _replace_referenced_by(body, refs_sorted)
            if new_body != body:
                _atomic_write_text(path, _render_with_frontmatter(fm, new_body))
                logger.info(
                    "updated",
                    kind="decision-stub-refs",
                    slug=slug,
                    refs=len(refs_sorted),
                )
        written.append(path)
    return written


_REFERENCED_BY_RE = re.compile(r"(## Referenced by\n)(.*?)(?=\n## |\n<!--|\Z)", re.DOTALL)


def _replace_referenced_by(body: str, refs: list[str]) -> str:
    """Rewrite the "## Referenced by" block in `body` with `refs`.

    Appends the block if missing. Keeps the surrounding body untouched so
    a human-authored decision page retains its free-form content above.
    """
    block_lines = ["\n"]
    block_lines.extend(f"- [[{r}]]\n" for r in refs)
    block_body = "".join(block_lines)

    if _REFERENCED_BY_RE.search(body):
        return _REFERENCED_BY_RE.sub(lambda m: m.group(1) + block_body, body, count=1)

    separator = "\n\n" if body and not body.endswith("\n\n") else ""
    return f"{body}{separator}## Referenced by\n{block_body}"


@tool
def append_to_log(entry: str, wiki_dir: str = "wiki") -> str:
    """Append a timestamped entry to wiki/log.md.

    Args:
        entry: Human-readable description of what was compiled
        wiki_dir: Root wiki directory

    Returns:
        Confirmation
    """
    wiki_path = Path(wiki_dir)
    wiki_path.mkdir(parents=True, exist_ok=True)
    log_path = wiki_path / "log.md"

    timestamp = datetime.now(UTC).isoformat()

    if not log_path.exists():
        header = "# Compilation Log\n\n| Timestamp | Event |\n|---|---|\n"
        log_path.write_text(header, encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"| {timestamp} | {entry} |\n")

    return f"logged: {entry}"


_VALID_INSIGHT_CATEGORIES = frozenset(
    {
        "topic_merge_candidate",
        "question_for_human",
        "prompt_ambiguity",
        "tool_gap",
        "supersession_doubt",
        "structure_suggestion",
        "trivial_skip",
        "already_captured",
        # V12 audit fix-C (2026-04-23): escape hatch for the
        # "substantive, not captured, no obvious target page" case the
        # terminal-decision guard surfaced. Before this category the
        # agent either fabricated a topic page or exited silently; now
        # it can declare "this needs human triage" and the coordinator
        # records the skip with a distinct reason humans can grep.
        "insufficient_decision",
    }
)

# Categories that the coordinator uses to mark a message ``skipped``.
# For these the insight MUST name the specific raw path it applies to —
# otherwise the coordinator can't correlate the insight back to a message
# and the decision is silently lost. See Cycle 4 Case #2 audit.
# ``insufficient_decision`` joins the skip set so the batch doesn't sit
# pending waiting for a re-queue that can only produce the same
# "no obvious target" answer — see terminal_decision_guard middleware.
_SKIP_INSIGHT_CATEGORIES = frozenset({"trivial_skip", "already_captured", "insufficient_decision"})


@tool
def log_insight(
    category: str,
    message: str,
    email_path: str | None = None,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    """Record a structured meta-observation during compile.

    Use this when you need to flag something for human review — uncertain
    between page updates, weird thread structure, possible policy
    supersession, missing tool. The coordinator surfaces the top few at
    batch-end in the audit log.

    Args:
        category: One of 'topic_merge_candidate', 'question_for_human',
            'prompt_ambiguity', 'tool_gap', 'supersession_doubt',
            'structure_suggestion', 'trivial_skip', 'already_captured',
            or 'insufficient_decision'.

            Note the semantic split between the three "no page delta"
            categories (all three mark the email ``skipped`` in the
            coordinator):

            - ``trivial_skip``: the email is **not substantive** — e.g.
              a one-line confirmation ("Yes, please"), out-of-office
              auto-reply, calendar ack. There's no content worth
              capturing anywhere.
            - ``already_captured``: the email **is substantive** (real
              stats, decisions, dates), but every fact it carries is
              already on the existing topic page — typically because
              a prior message in the same thread was already compiled.
              No new page delta needed, but the signal is different
              from ``trivial_skip`` and we want to preserve it.
            - ``insufficient_decision``: the email is substantive AND
              not captured elsewhere, but there's no obvious target
              page to land it on. Use sparingly — this means a human
              needs to triage. The terminal-decision guard accepts
              this as a commitment so the batch can exit cleanly
              instead of looping.
        message: 1-2 sentence observation.
        email_path: Raw email path this insight is about (e.g.
            ``raw/2026-04-11_subject_abc.md``). **Required** for
            ``trivial_skip`` and ``already_captured`` — the coordinator
            uses it to materialize the skip. Optional for investigatory
            categories. In single-email batches the path is inferred
            from the coordinator's batch scope when omitted; multi-email
            batches still require explicit selection.
        suggested_action: Optional concrete fix the human could take.

    Returns:
        ``{"ok": True, "id": <int>}`` on success, or
        ``{"ok": False, "error": "..."}`` on invalid category or a
        skip-category call that omitted ``email_path`` in a non-single-
        email batch.
    """
    import os

    from src.db.insights import record

    if category not in _VALID_INSIGHT_CATEGORIES:
        return {
            "ok": False,
            "error": (
                f"invalid category {category!r}; must be one of {sorted(_VALID_INSIGHT_CATEGORIES)}"
            ),
        }

    inferred_from_batch: str | None = None
    if category in _SKIP_INSIGHT_CATEGORIES and not email_path:
        # Self-heal: in a single-email batch the coordinator already knows
        # which email is in scope, so we can infer it instead of looping
        # on the structured error. Multi-email batches still need explicit
        # selection — we can't guess which of N messages the insight is
        # about.
        batch_paths = _current_raw_paths.get() or []
        if len(batch_paths) == 1:
            email_path = batch_paths[0]
            inferred_from_batch = email_path
        else:
            return {
                "ok": False,
                "error": (
                    f"email_path is required for category={category!r} — "
                    f"call log_insight once per email you're skipping, with "
                    f"email_path='raw/YYYY-MM-DD_..._hash.md'. Without it the "
                    f"coordinator can't mark the message skipped and the "
                    f"decision is lost."
                ),
            }

    original_path = email_path
    if email_path and inferred_from_batch is None:
        email_path = _autoheal_email_path(email_path)

    run_id = os.environ.get("COMPILE_RUN_ID")
    new_id = record(
        run_id=run_id,
        category=category,
        message=message,
        email_path=email_path,
        suggested_action=suggested_action,
    )
    result: dict[str, Any] = {"ok": True, "id": new_id}
    if inferred_from_batch is not None:
        result["auto_corrected"] = {
            "inferred_from_batch": inferred_from_batch,
            "note": (
                "email_path inferred from single-email batch scope — pass it explicitly next time."
            ),
        }
    elif original_path and original_path != email_path:
        result["auto_corrected"] = {
            "from": original_path,
            "to": email_path,
            "note": (
                "email_path normalized (leading slash stripped). The next "
                "call should use the unrooted form directly."
            ),
        }
    return result


def _autoheal_email_path(email_path: str) -> str:
    """Normalize + verify `email_path` against DB + filesystem.

    Agent sees the raw dir as ``/raw/`` via its chrooted virtual-mode
    filesystem, so it often passes the virtual path (leading slash).
    The coordinator's skip-path matcher compares to ``messages.raw_path``
    which stores the unrooted form — so ``/raw/...`` silently misses
    (Bug L). Autoheal steps:

    1. Normalize: strip any leading slashes so ``/raw/...`` becomes
       ``raw/...``.
    2. DB check: if the normalized path exists in ``messages.raw_path``,
       accept it — this is the happy path.
    3. Filesystem fallback: if DB doesn't know the path (test fixtures,
       pre-ingest fossils) but the file exists on disk, accept it with
       a warning.
    4. Warn: otherwise, log ``log_insight_path_unknown`` and return the
       normalized path anyway. The coordinator's batch-end skip-path
       materialization is the authoritative gate — rejecting here would
       only couple ``log_insight`` to infrastructure state.

    Always returns the normalized path. Point of autoheal is the common
    leading-slash case (caught by strip) + observability when something
    less obvious drifts.
    """
    normalized = email_path.lstrip("/")

    try:
        from src.db.messages import find_by_raw_path

        row = find_by_raw_path(normalized)
    except Exception as exc:  # noqa: BLE001 — DB outage is non-fatal
        logger.warning("autoheal_db_lookup_failed", path=normalized, error=str(exc))
        return normalized

    if row is None and not Path(normalized).is_file():
        logger.warning(
            "log_insight_path_unknown",
            path=normalized,
            reason="not in messages and not on disk — skip-materialization will fail",
        )

    return normalized


class EntityRequest(BaseModel):
    """One person to resolve/create as a person page.

    `email` is REQUIRED and is the identity — slugs are derived from it
    deterministically. An empty or missing `email` is a schema violation
    and will be rejected before the tool body runs.

    Class name kept as ``EntityRequest`` for backwards compatibility with
    the public ``create_entities`` tool; filename + symbol retired with
    the shim in #67.
    """

    email: str = Field(
        ...,
        description=(
            "The person's email address, e.g. 'amit@indiamart.com'. "
            "Case-insensitive. Must appear literally in one of the batch's "
            "raw email files or the tool will refuse."
        ),
        min_length=5,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+",
    )
    display_name: str = Field(
        default="",
        description=(
            "Stub page title for NEW pages. Ignored when the page already "
            "exists. Leave blank if unknown; the tool falls back to the "
            "email as title."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "Bypass the weak-evidence gate. Only set true when THIS TURN "
            "is also writing multi-sentence content about the person. "
            "Merely linking a CC'd name is not enough."
        ),
    )


@tool
def create_entities(entities: list[EntityRequest]) -> dict[str, Any]:
    """Resolve or create people pages for the humans mentioned in this batch.

    Always use this tool for people pages — do NOT invent slugs or
    `write_file` a new entity markdown directly. The tool derives the
    canonical slug from each email deterministically, checks for existing
    pages (by canonical slug OR by legacy display-name slug with
    `email:` frontmatter), and gates new-page creation on evidence
    strength.

    The coordinator injects `raw_paths` for the current batch automatically
    — you only pass the people you want to resolve or create. Each email
    must appear literally in at least one of the batch's raw files; any
    that don't match are refused with `reason: "email_not_in_raw"`.

    Per-entity outcomes:

    - **Existing page** (by canonical or legacy slug): returns
      `{"ok": True, "slug": ..., "created": False, ...}`. Use the
      returned slug in wikilinks. Enrich via `read_file` + `edit_file`.
    - **New page, strong/medium evidence**: writes a stub and returns
      `{"ok": True, "slug": ..., "created": True, "evidence_level": ...}`.
      Strong = email appears in From/To somewhere; medium = CC'd across
      ≥2 distinct threads.
    - **New page, weak evidence** (`force=false`): refuses with
      `reason: "weak_evidence"`. CC-only on one thread doesn't warrant a
      page. Only set `force=true` if you're writing substantive content
      about this person in the same turn.
    - **Invalid email**: refuses with `reason: "invalid_email"` /
      `"email_not_in_raw"`. Do NOT retry with a guessed variant —
      re-read the raw file if you're unsure of the address.

    Args:
        entities: List of `EntityRequest` objects. **Each item MUST have
            a non-empty `email`.** Do not emit empty objects — the
            schema requires `email`. One entry per person; batching 5-30
            people in a single call is normal.

    Returns:
        {"ok": bool, "validated_raw_paths": [...], "results": [...]}.
        `results[i]` has `ok`/`slug`/`created`/`evidence_level` on
        success, or `ok: false` + `reason` + `guidance` on refusal.
    """
    from src.compile.entities import create_entity_pages

    raw_paths = _current_raw_paths.get()
    if not raw_paths:
        return {
            "ok": False,
            "error": (
                "no raw_paths in batch context — the coordinator is supposed to "
                "inject them before invoking the agent. If you're testing this "
                "tool directly, call create_entity_pages(raw_paths, entities) "
                "instead."
            ),
            "results": [],
        }

    person_dicts = [e.model_dump() for e in entities]
    return create_entity_pages(raw_paths, person_dicts)


# === Browse / patch / validate tools (north-star recovery) ===

from src.compile.categories import WIKI_CATEGORIES as _WIKI_CATEGORIES  # noqa: E402


def _find_page_by_slug(slug: str, wiki_dir: str = "wiki") -> Path | None:
    """Locate `wiki/<category>/<slug>.md` across known category dirs.

    Returns the first match or None. Keep this private — the public tools
    only expose the summary/patch result, never the filesystem path, so
    callers can't treat the path as an agent-visible identifier.
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return None
    for category in _WIKI_CATEGORIES:
        candidate = wiki_path / category / f"{slug}.md"
        if candidate.is_file():
            return candidate
    return None


def _first_paragraph_capped(body: str, cap: int = 200) -> str:
    """Return the first non-heading paragraph from `body`, hard-capped at `cap` chars.

    Mirrors the convention in `_first_paragraph` above but with a tighter
    cap so `get_page_summary` never floods the agent context. Headings
    (`#`), blank lines, and blockquotes are skipped when they lead.
    """
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current:
                break
            continue
        if not stripped:
            if current:
                break
            continue
        current.append(stripped)
    if not current:
        return ""
    para = " ".join(current)
    if len(para) > cap:
        return para[: cap - 3].rstrip() + "..."
    return para


def _extract_h2_headings(body: str) -> list[str]:
    """Return the text of every `## H2` heading in document order."""
    headings: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


_TLDR_MAX_CHARS = 400


def _extract_tldr(body: str) -> str | None:
    """Return the content of the page's `## TL;DR` (or `## TLDR`) H2.

    Case-insensitive match on `TL;DR` / `TLDR`. Content is everything
    between that heading and the next `##` heading (or end of file),
    stripped. Returns None if no such section exists. Used by
    `get_page_summary` so agents can surface durable lead prose without
    re-reading the page.

    Capped at `_TLDR_MAX_CHARS` chars with an ellipsis suffix when
    longer — a runaway TL;DR section (e.g. agent wrote a whole essay
    under the heading) must not blow the concise token budget. Full
    content stays accessible via `read_file`.
    """
    in_tldr = False
    collected: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if not in_tldr and heading in ("tl;dr", "tldr"):
                in_tldr = True
                continue
            # Non-TL;DR H2: if we're inside the TL;DR section, we're done;
            # otherwise we haven't entered yet and keep scanning.
            if in_tldr:
                break
            continue
        if in_tldr:
            collected.append(line)
    # When we never entered the TL;DR block `collected` stays empty, so
    # `text == ""` and the `if not text` guard returns None — no
    # separate `if not in_tldr: return None` is needed.
    text = "\n".join(collected).strip()
    if not text:
        return None
    if len(text) > _TLDR_MAX_CHARS:
        # Leave room for the ellipsis marker. Preserve trailing
        # whitespace before the truncation point — `rstrip()` + `…`
        # reads more naturally than a mid-word cut.
        return text[: _TLDR_MAX_CHARS - 1].rstrip() + "…"
    return text


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
          - "concise" (default, ~150 tokens when TL;DR present; ~70
            when absent) — `{found, slug, title, first_paragraph, tldr}`.
            Cheapest "what is this about?" probe. `tldr` is the content
            of any `## TL;DR` (or `## TLDR`) section (capped at 400
            chars with an ellipsis), or None if absent — pages that
            author one let future calls skip the re-read.
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
    from src.compile.patch import replace_section

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
    from src.compile.validation import check_likely_duplicate
    from src.compile.validation import check_missing_tldr
    from src.compile.validation import check_over_quoting
    from src.compile.validation import check_person_page_heuristic

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


# === Frontmatter helpers ===


# Re-exported from `src.compile.landing` — kept at module level so existing
# callers (`from src.compile.compiler import _generate_home` etc.) and tests
# keep working after the landing-generators extraction. Explicit `as` aliases
# are required by mypy-strict's `implicit_reexport = false`.
#
# Glossary re-exports (`_APPROVED_ALIASES`, `_generate_glossary`) removed
# 2026-04-24 alongside the underlying regex extractor.
from src.compile.landing import _generate_changes as _generate_changes  # noqa: E402
from src.compile.landing import _generate_home as _generate_home  # noqa: E402
from src.utils import extract_body as _extract_body  # noqa: E402
from src.utils import extract_frontmatter as _extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter as _render_with_frontmatter  # noqa: E402

# === Compiler Factory ===


def _make_chat_model(model_name: str) -> Any:
    """Build a chat model, routing through LiteLLM proxy if configured.

    LiteLLM proxies expose an OpenAI-compatible API, so we use langchain-openai's
    ChatOpenAI and point it at the proxy's base URL. This works for any model
    string the proxy knows (e.g. "z-ai/glm-5", "anthropic/claude-opus-4-6"),
    regardless of whether langchain has a native provider for it.

    timeout=120s prevents the "half-open TCP socket" stall we've hit
    twice on 2026-04-14 (laptop sleep / network blip → kernel keepalive
    defaults to hours → Python blocks in recv() forever). With the
    timeout the SDK raises, the batch fails loudly, and the next batch
    continues. p95 completion today is ~30s so 2 min is a comfortable
    ceiling that still catches the dead-socket case.
    """
    if settings.litellm_base_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            base_url=settings.litellm_base_url,
            api_key=settings.openai_api_key or "dummy",
            timeout=120,
        )

    # Fallback: use langchain's provider inference. No timeout knob at this
    # layer — direct providers don't exhibit the proxy-socket-stall issue.
    from langchain.chat_models import init_chat_model

    return init_chat_model(model_name)


def _build_compile_view(raw_dir: Path, wiki_dir: Path) -> Path:
    """Resolve the filesystem view-root for the compile agent.

    Returns the common parent of the resolved raw_dir and wiki_dir, which
    is what FilesystemBackend's virtual_mode uses as `root_dir`. Inside the
    view, the agent sees `/raw` and `/wiki` as virtual paths.

    Why not a tempdir + symlinks: FilesystemBackend resolves symlinks then
    checks `relative_to(root_dir)`. A symlink target outside the tempdir
    fails that check (`Path:... outside root directory: /tmp/...`). We
    instead anchor `root_dir` at the real common parent so resolution
    stays inside.

    The host path is still hidden from the LLM via the prompt + the
    path_autoheal middleware, which rewrites accidental host-prefix leaks
    back to virtual `/raw/...` / `/wiki/...` form.
    """
    raw_real = raw_dir.resolve()
    wiki_real = wiki_dir.resolve()
    if raw_real.parent != wiki_real.parent:
        # Non-default layout (raw and wiki in different parent dirs). Fall
        # back to cwd so the backend at least doesn't reject every call.
        # Operator should fix the layout — log so this is loud.
        logger.warning(
            "compile_view_mismatched_parents",
            raw_parent=str(raw_real.parent),
            wiki_parent=str(wiki_real.parent),
            falling_back_to=str(Path.cwd().resolve()),
        )
        return Path.cwd().resolve()
    return raw_real.parent


def _cleanup_compile_view(view_root: Path) -> None:
    """No-op when the view-root is a real repo dir (the typical case).

    Kept for forward compatibility — earlier iterations of the design used
    a tempdir + symlinks per run. If we ever revive that approach, the
    cleanup goes here. Today the view-root is the repo's working dir (or
    a parent of raw/+wiki/) and must NOT be deleted.
    """
    _ = view_root  # placeholder — see docstring


def create_compiler(
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    view_root: Path | None = None,
) -> Any:
    """Create a Deep Agents wiki compiler.

    Model routing:
    - If LITELLM_BASE_URL is set, routes all models through the LiteLLM proxy
      using an OpenAI-compatible client. This lets us use any model name the
      proxy knows (e.g. "z-ai/glm-5", "anthropic/claude-opus-4-6").
    - Otherwise uses init_chat_model's provider inference (requires provider
      prefix like "openai:gpt-4o" or a recognized model name).

    Args:
        model_name: Model string. Defaults to settings.llm_model.
        raw_dir: Path to raw/ directory. Used for symlink target inside
            the view-root and for ergonomic error messages.
        wiki_dir: Path to wiki/ directory. Same treatment as raw_dir.
        view_root: Pre-built view-root to use (for tests/reuse). When None,
            a fresh per-run view is built with symlinks to raw_dir/wiki_dir.
            The caller MUST clean up the view when done (run_compilation
            does this automatically).

    Returns:
        A compiled LangGraph agent ready to invoke.
    """
    from deepagents import FilesystemPermission
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    from src.compile.middleware.chronological_scope import ChronologicalScopeMiddleware
    from src.compile.middleware.edit_payload_sanity import EditPayloadSanityMiddleware
    from src.compile.middleware.entity_write_autoheal import EntityWriteAutohealMiddleware
    from src.compile.middleware.glob_narrowing import GlobNarrowingMiddleware
    from src.compile.middleware.legacy_page_hint import LegacyPageHintMiddleware
    from src.compile.middleware.path_autoheal import PathAutohealMiddleware
    from src.compile.middleware.read_file_truncation_hint import ReadFileTruncationHintMiddleware
    from src.compile.middleware.same_thread_topic_guard import SameThreadTopicGuardMiddleware
    from src.compile.middleware.sibling_draft_check import SiblingDraftCheckMiddleware
    from src.compile.reviewer import build_reviewer_subagent

    model_name = model_name or settings.llm_model
    logger.info(
        "creating wiki compiler",
        model=model_name,
        via_proxy=bool(settings.litellm_base_url),
    )

    model = _make_chat_model(model_name)

    # Per-run view: chroot the agent's filesystem to {view}/raw and
    # {view}/wiki only. Host paths like /Users/... are not visible.
    # The view is built here when none is passed in (typical path). Caller
    # (run_compilation) cleans up on exit.
    if view_root is None:
        view_root = _build_compile_view(Path(raw_dir), Path(wiki_dir))
        logger.info("compile view built", view_root=str(view_root))

    backend = FilesystemBackend(root_dir=str(view_root), virtual_mode=True)

    # Permission rules, evaluated in declaration order:
    # 1. deny write anywhere under /raw — emails are immutable source of truth.
    # 2. deny read of /raw/attachments — binary data; agent can't do anything
    #    useful with it and reading eats context.
    # 3. reads/writes elsewhere fall through to the permissive default.
    permissions = [
        FilesystemPermission(operations=["write"], paths=["/raw/**"], mode="deny"),
        FilesystemPermission(operations=["read"], paths=["/raw/attachments/**"], mode="deny"),
    ]

    system_prompt = (
        COMPILER_SYSTEM_PROMPT
        + "\n\n## Runtime context\n\n"
        + "- Your filesystem is chrooted. Only `/raw/` and `/wiki/` exist.\n"
        + f"- Model: `{model_name}`.\n"
    )

    reviewer_spec = build_reviewer_subagent()

    # Compile agent surface:
    # - Custom tools: list_wiki_pages, resolve_page, create_entities,
    #   write_draft_page, log_insight, check_my_work, get_page_summary,
    #   get_thread_context, patch_page, validate_page_draft.
    # - Inherited filesystem tools (ls, read_file, write_file, edit_file,
    #   glob, grep) from FilesystemMiddleware auto-added by create_deep_agent.
    # - Middleware: path_autoheal (rewrites host-path leaks) +
    #   entity_write_autoheal (nudges raw entity writes toward create_entities)
    #   + legacy_page_hint (touch-it-fix-it annotation on reads of
    #   legacy-ontology wiki pages — once-per-page-per-run)
    #   + check_my_work_gate (short-circuits check_my_work calls made before
    #   any content-page write succeeds — live traces show 59-78% of
    #   batches hit the validator before writing anything)
    #   + glob_narrowing (rejects `**/<slug>.md` slug-lookup globs;
    #   24.5% of glob calls were timing out at the 20s deepagents cap —
    #   per-obs scores issue #185).
    # - Subagent: reviewer (read-only, structured verdict; retains glob
    #   for its grep-heavy review workflow).
    # Bookkeeping tools (mark_as_compiled, stamp_page_compiled_at,
    # append_to_log, update_wiki_index) remain importable but NOT bound —
    # the coordinator handles them deterministically post-run.
    # NOTE: `list_uncompiled_emails` + `find_new_sources` are deliberately
    # NOT exposed to the agent. The coordinator owns the compile queue and
    # already passes the batch file list in the user instruction. Agent
    # queue-discovery is pure context tax. Historical trace data showed
    # `find_new_sources(thread_id=...)` being used as a stand-in for
    # `get_thread_context(thread_id)` — same information, clearer intent;
    # the latter is the right tool. Both functions remain importable for
    # coordinator + script use.
    from src.compile.middleware import CheckMyWorkGateMiddleware
    from src.compile.middleware import TerminalDecisionGuardMiddleware

    return create_deep_agent(
        model=model,
        tools=[
            list_wiki_pages,
            resolve_page,
            create_entities,
            write_draft_page,
            log_insight,
            check_my_work,
            get_page_summary,
            get_thread_context,
            patch_page,
            validate_page_draft,
        ],
        system_prompt=system_prompt,
        backend=backend,
        permissions=permissions,
        middleware=[
            PathAutohealMiddleware(),
            ChronologicalScopeMiddleware(),
            EditPayloadSanityMiddleware(),
            EntityWriteAutohealMiddleware(),
            LegacyPageHintMiddleware(),
            SameThreadTopicGuardMiddleware(),
            # Sibling-aware draft check (v11-U9): catches batch-local
            # near-duplicates BEFORE they hit disk, complementing
            # SameThreadTopicGuardMiddleware (which is a hard topic-only
            # block) and the v9-U14 reviewer merge-candidate queue
            # (which is a post-hoc catch). Conservative thresholds to
            # avoid false positives that erode agent trust.
            SiblingDraftCheckMiddleware(),
            CheckMyWorkGateMiddleware(),
            # V12 audit fix-C (2026-04-23): block batch exit without a
            # terminal commitment. Batch 45 (kimi-k2.6) completed with
            # `turns=6 tools=8 writes=0` and no log_insight — email
            # stayed pending, got re-queued, agent paid same cost twice.
            # This guard injects a nudge before END and loops the agent
            # back to the model; after `_MAX_NUDGES` the coordinator's
            # `mark_skipped("agent_exited_without_terminal_decision")`
            # fallback kicks in.
            TerminalDecisionGuardMiddleware(),
            # Glob narrowed 2026-04-18 (v10-U5): 24.5% of glob calls were
            # `**/<slug>.md` slug lookups timing out at 20s. Reject those
            # with a pointer to resolve_page; legitimate enumeration
            # patterns (wiki/topics/*.md) pass through. Reviewer subagent
            # keeps glob — see src/compile/reviewer.py.
            GlobNarrowingMiddleware(),
            # v11-U3: every `read_file` ToolMessage gets a footer with
            # `total_lines` (and a `next offset=` hint when truncated).
            # Inherited deepagents tool defaults to limit=100 and gives
            # zero signal that more content exists below — agent flies
            # blind on 83% of compile traces. View-root binds at
            # construction so we can map virtual paths to disk.
            ReadFileTruncationHintMiddleware(view_root=view_root),
        ],
        subagents=[cast(Any, reviewer_spec)],
    )


def get_langfuse_handler(
    *,
    update_trace: bool = True,
) -> Any | None:
    """Return a Langfuse callback handler if configured, else None.

    Langfuse v3+ removed the legacy `langfuse.callback` module. The handler
    now lives in `langfuse.langchain` and instantiates its own internal
    Langfuse client — it does NOT take a `langfuse_client` arg (verified
    against 3.14.6). That means a `Langfuse(...)` constructor call we make
    here is discarded by the time `CallbackHandler()` runs. So we configure
    everything via env vars instead, which both the OTel pipeline and the
    Langfuse client read at instantiation time.

    **Hang-safety**: the self-hosted server's OTLP ingestion endpoint
    (`/api/public/otel/v1/traces`) has been observed to Read-timeout for
    minutes at a time. Without bounded export timeouts, LangChain's
    synchronous callback path can block compile runs for ~8+ minutes per
    batch when the queue fills. Capping per-export attempts via env vars
    drains failures fast so tracing degrades to best-effort no-op instead
    of stalling the agent. See issue #17 for the server-side root cause.
    """
    if not settings.langfuse_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None

    # Set timeouts via env vars so they apply to BOTH the OTel exporter
    # (used by CallbackHandler internally) AND the Langfuse client
    # singletons. `setdefault` lets operators override when the server
    # is known-healthy.
    import os as _os

    # OTel BatchSpanProcessor + OTLP exporter (the actual span shipping)
    _os.environ.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "2000")  # ms per export
    _os.environ.setdefault("OTEL_BSP_SCHEDULE_DELAY", "5000")  # ms between flushes
    _os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "512")  # drop oldest on full
    # NB: opentelemetry-sdk Python parses OTEL_EXPORTER_OTLP_TIMEOUT as
    # SECONDS despite the OTel spec defining it in ms. Don't normalize to
    # 2000 to "fix the units" — that would give a 2s timeout, not 2 ms.
    _os.environ.setdefault("OTEL_EXPORTER_OTLP_TIMEOUT", "2")

    # Langfuse client config — picked up by CallbackHandler's internal client.
    _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    _os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    _os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    _os.environ.setdefault("LANGFUSE_TIMEOUT", "2")  # seconds for the SDK's HTTP client
    _os.environ.setdefault("LANGFUSE_FLUSH_AT", "50")  # batch size before forced flush
    _os.environ.setdefault("LANGFUSE_FLUSH_INTERVAL", "5")  # seconds between flushes

    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("langfuse not installed, tracing disabled")
        return None

    return CallbackHandler(update_trace=update_trace)


def _extract_raw_paths_from_instruction(instruction: str) -> list[str]:
    """Pull raw/*.md paths out of the coordinator's instruction string.

    The coordinator inlines the batch's raw paths in the user message (see
    `scripts/compile_all.py::_build_batch_instruction`). We grep them out
    so `create_entities` can inject `raw_paths` without the LLM having to
    thread them through.

    Returns the unique list in source order. An empty list is a benign
    signal — create_entities will error out with a clear message.
    """
    matches = re.findall(r"raw/[^\s`'\"]+?\.md", instruction)
    seen: dict[str, None] = {}
    for m in matches:
        seen[m] = None
    return list(seen)


def _preflight_raw_paths_exist(raw_paths: list[str]) -> None:
    """Assert every batch raw_path exists on disk before agent invocation.

    Catches the "agent didn't write anything" failure mode from 2026-04-16
    where ``find_new_sources`` returned valid raw_paths from the DB but the
    filesystem mount was empty (worktree with only ``.gitkeep`` under raw/).
    In that case every `read_file` silently failed and traces looked like
    synthesis failures — this guard turns it into an unambiguous
    environment/config error BEFORE any LLM cost is incurred.

    Empty ``raw_paths`` is not an error here — run_compilation is sometimes
    invoked without a batch list (e.g. free-form queries). The guard fires
    only when the caller explicitly passes paths that should exist.
    """
    missing = [p for p in raw_paths if not Path(p).exists()]
    if missing:
        raise FileNotFoundError(
            "environment/config error — raw files missing from disk: "
            + ", ".join(missing[:5])
            + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
        )


def _count_view_raw_md_files(view_root: Path) -> int:
    """Count ``.md`` files at top-level of ``view_root/raw``.

    Reported as ``mounted_raw_file_count`` in trace metadata so Langfuse
    can correlate "agent wrote nothing" traces with a zero-file mount.
    Top-level-only — attachments/ holds binaries the agent can't read.
    """
    raw_root = view_root / "raw"
    if not raw_root.exists():
        return 0
    return sum(1 for _ in raw_root.glob("*.md"))


def _preflight_view_resolves_paths(view_root: Path, raw_paths: list[str]) -> None:
    """Assert every batch raw_path resolves inside ``view_root``.

    The agent's filesystem is chrooted to ``view_root`` (FilesystemBackend's
    ``root_dir`` with ``virtual_mode=True``). If a batch path lives outside
    the view, every `read_file("/raw/...md")` the agent tries will
    silently fail because the chroot rejects it. Count mismatches and
    abort with a clear error so we don't waste an LLM call discovering it.
    """
    view_root_abs = view_root.resolve()
    missing = []
    for p in raw_paths:
        resolved = Path(p).resolve()
        try:
            resolved.relative_to(view_root_abs)
        except ValueError:
            missing.append(p)
    if missing:
        raise RuntimeError(
            f"view-root /raw is missing {len(missing)} of {len(raw_paths)} "
            f"expected raw paths (view_root={view_root_abs}); "
            f"first few: {missing[:3]}"
        )


def run_compilation(
    instruction: str = "Compile all uncompiled raw emails into wiki pages.",
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    recursion_limit: int = 250,
    cache_stats: Any | None = None,
    tool_log: ToolCallLogHandler | None = None,
    run_name: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
    trace_tags: list[str] | None = None,
    raw_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Run a compilation pass. Returns the agent's final state.

    recursion_limit of 250 accommodates ~5 emails per batch with 2-page
    multi-system writes + reviewer subagents. LangGraph counts every node
    visit (model + ToolNode + each `after_model` middleware) as a super-
    step, not a parent turn. Today there are 3 active middlewares
    (TodoListMiddleware, CheckMyWorkGateMiddleware, terminal_decision_guard)
    so each parent turn costs ~5 super-steps; reviewer subagents share the
    parent's budget. Smoke run `99a267f4` (2026-04-28) trace audit found
    legitimate 5-email batches with two reviewer rounds + one stale-date
    re-loop costing ~180-220 super-steps even when the work itself was
    clean. Lifting to 250 (was 150) gives headroom without rewarding
    pathological loops — the existing `_check_my_work_cache` per-write-
    epoch dedupe still catches genuine spirals.

    Pass a `CacheStatsCallback` as `cache_stats` to capture per-batch prompt-
    caching metrics (hit rate, cached tokens, total tokens). See
    `src/compile/cache_stats.py`.

    Pass a `ToolCallLogHandler` as `tool_log` to capture per-tool-call
    telemetry (name, inputs, latency, status). See
    `src/compile/tool_call_log.py`.

    `raw_paths` is optional. When provided, the coordinator's list of raw
    paths for this batch is injected into the `_current_raw_paths` ContextVar
    so `create_entities` can use them without the LLM threading them
    through. When None, we grep them out of the instruction string.
    """
    raw_dir_abs = Path(raw_dir).resolve()
    wiki_dir_abs = Path(wiki_dir).resolve()
    # Build the view-root here so cleanup is paired with this run's
    # lifecycle, not the cached agent graph.
    view_root = _build_compile_view(Path(raw_dir), Path(wiki_dir))
    try:
        effective_raw_paths = raw_paths or _extract_raw_paths_from_instruction(instruction)

        # Per-batch preflight: fail fast if the filesystem mount doesn't
        # contain the files the DB says we should read. F3 fix — live
        # Tier-A traces on 2026-04-16 silently failed because
        # ``find_new_sources`` returned DB paths but read_file saw an
        # empty /raw mount.
        if effective_raw_paths:
            _preflight_raw_paths_exist(effective_raw_paths)
            _preflight_view_resolves_paths(view_root, effective_raw_paths)

        mounted_raw_count = _count_view_raw_md_files(view_root)
        logger.info(
            "run_compilation_preflight",
            raw_dir=str(raw_dir_abs),
            wiki_dir=str(wiki_dir_abs),
            view_root=str(view_root),
            mounted_raw_file_count=mounted_raw_count,
            batch_raw_paths=len(effective_raw_paths),
        )

        agent = create_compiler(
            model_name=model_name,
            raw_dir=raw_dir,
            wiki_dir=wiki_dir,
            view_root=view_root,
        )

        callbacks = []
        lf = get_langfuse_handler(update_trace=True)
        if lf:
            callbacks.append(lf)
        if cache_stats is not None:
            callbacks.append(cache_stats)
        if tool_log is not None:
            callbacks.append(tool_log)

        # Enrich trace metadata with mount-sanity info so Langfuse can
        # surface the infra-vs-synthesis distinction. Deterministic —
        # safe to add to every trace.
        enriched_metadata: dict[str, Any] = dict(trace_metadata) if trace_metadata else {}
        enriched_metadata.setdefault("cwd", str(Path.cwd()))
        enriched_metadata.setdefault("raw_dir", str(raw_dir_abs))
        enriched_metadata.setdefault("wiki_dir", str(wiki_dir_abs))
        enriched_metadata.setdefault("view_root", str(view_root))
        enriched_metadata.setdefault("mounted_raw_file_count", mounted_raw_count)
        enriched_metadata.setdefault("missing_raw_paths_count", 0)

        config: dict[str, Any] = {}
        if callbacks:
            config["callbacks"] = callbacks
        config["recursion_limit"] = recursion_limit
        if run_name:
            config["run_name"] = run_name
        config["metadata"] = enriched_metadata
        if trace_tags:
            config["tags"] = trace_tags

        logger.info(
            "running compilation",
            instruction=instruction[:100],
            recursion_limit=recursion_limit,
            raw_paths_count=len(effective_raw_paths),
        )

        cutoff_date = _compute_batch_cutoff_date(effective_raw_paths)
        if cutoff_date:
            logger.info(
                "batch_cutoff_date",
                cutoff_date=cutoff_date,
                raw_paths_count=len(effective_raw_paths),
            )

        from src.db.messages import shared_thread_id_for_paths

        batch_thread_id = shared_thread_id_for_paths(effective_raw_paths)
        if batch_thread_id:
            logger.info(
                "batch_thread_id",
                thread_id=batch_thread_id,
                raw_paths_count=len(effective_raw_paths),
            )

        raw_paths_token = _current_raw_paths.set(effective_raw_paths)
        cutoff_token = _current_batch_cutoff_date.set(cutoff_date)
        thread_id_token = _current_batch_thread_id.set(batch_thread_id)
        topic_slugs_token = _current_batch_topic_slugs_written.set(set())
        sibling_slugs_token = _current_batch_sibling_slugs_written.set(set())
        try:
            # Wrap the single agent round in `asyncio.wait_for` — the outer
            # `--batch-timeout` tracks cumulative wall-clock across model
            # retries and can't bound a single hung round (2026-04-22
            # grok-4.1-fast: 5h31m mid-round hang).
            result = asyncio.run(
                _ainvoke_with_timeout(agent, instruction, config, settings.invoke_timeout_s)
            )
            _check_silent_fail(result, model=model_name)
            return result
        finally:
            _current_batch_sibling_slugs_written.reset(sibling_slugs_token)
            _current_batch_topic_slugs_written.reset(topic_slugs_token)
            _current_batch_thread_id.reset(thread_id_token)
            _current_batch_cutoff_date.reset(cutoff_token)
            _current_raw_paths.reset(raw_paths_token)
    finally:
        _cleanup_compile_view(view_root)


async def _ainvoke_with_timeout(
    agent: Any,
    instruction: str,
    config: dict[str, Any],
    timeout_s: int,
) -> dict[str, Any]:
    """Call `agent.ainvoke(...)` capped by `timeout_s` seconds.

    Raises `InvokeWallClockTimeout` on expiry — callers pattern-match on
    that to distinguish "wedged LLM round" from other `TimeoutError`s.
    """
    try:
        return cast(
            dict[str, Any],
            await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": instruction}]},
                    config=config,
                ),
                timeout=timeout_s,
            ),
        )
    except TimeoutError as exc:
        logger.error("invoke_wall_clock_timeout", timeout_s=timeout_s)
        raise InvokeWallClockTimeout(
            f"agent.ainvoke exceeded {timeout_s}s wall-clock limit"
        ) from exc


class SilentModelFailError(RuntimeError):
    """Raised when the agent's only model response is an empty ChatCompletion.

    The LiteLLM proxy occasionally returns HTTP 200 with
    ``completion_tokens=0 prompt_tokens=0 content=""`` on certain
    model requests (observed on minimax/minimax-m2.7-20260318, Cycle 5).
    The agent sees an empty AI message, terminates with no tool calls,
    and the coordinator records a spurious ``outcome='failed'`` with
    ``error='not cited in wiki'`` — indistinguishable from a genuine
    agent failure.

    `compile_all.py` treats this as an infrastructure error: retry the
    batch with a different model from the pool, same as the LiteLLM
    401/400 path. See docs/audits/cycle-5-case-bug-j-minimax-silent-fail.md.
    """


def _check_silent_fail(result: dict[str, Any], *, model: str | None = None) -> None:
    """Raise SilentModelFailError if the agent's final state is the
    zero-token empty-content shape produced by the LiteLLM proxy on
    malfunctioning model requests.
    """
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return

    ai_messages = [m for m in messages if _message_is_ai(m)]
    if len(ai_messages) != 1:
        return

    ai = ai_messages[0]
    content = _message_content(ai)
    tool_calls = _message_tool_calls(ai)
    if content or tool_calls:
        return

    token_total = _message_total_tokens(ai)
    if token_total != 0:
        return

    raise SilentModelFailError(
        f"LiteLLM returned 200-empty on model={model!r} "
        "(completion_tokens=0 prompt_tokens=0 content=''). "
        "Retry with a different model."
    )


_VERDICT_KEY_RE = re.compile(r'"verdict"\s*:\s*"(pass|revise|block)"', re.IGNORECASE)


def _extract_merge_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return reviewer-flagged merge candidate pairs from an agent result.

    When the main agent calls ``task(subagent_type="reviewer", ...)`` the
    reviewer's final ``ReviewReport`` JSON is returned as the content of a
    ``ToolMessage`` in the agent state (see
    ``deepagents.middleware.subagents._return_command_with_state_update``).

    Scans every message for JSON objects that carry a reviewer ``verdict``
    and a non-empty ``merge_candidates`` list, and returns one pair per
    candidate::

        [{"slug_a": "bl-notif", "slug_b": "bl-sms", "note": <summary>}]

    ``slug_a`` is the page the reviewer was reading (first finding's
    ``slug``, empty when none); ``slug_b`` is the flagged candidate.

    Returns ``[]`` when there are no messages, no reviewer ran, or every
    report had ``merge_candidates=[]``. Malformed JSON is skipped — parser
    errors must never crash a compile run.
    """
    import json

    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return []

    decoder = json.JSONDecoder()
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for msg in messages:
        text = _message_any_text(msg)
        if not text:
            continue
        for match in _VERDICT_KEY_RE.finditer(text):
            # Walk left for the ENCLOSING `{` (not the nearest — a nested
            # object would break json.raw_decode since `"verdict"` may sit
            # at the top level while an inner `{` is closer). Count braces
            # so a ``{"x": {"y":1}, "verdict": ...}`` matches the outer.
            start = _find_enclosing_brace(text, match.start())
            if start < 0:
                continue
            try:
                payload, _end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("verdict") not in {"pass", "revise", "block"}:
                continue
            candidates = payload.get("merge_candidates")
            if not isinstance(candidates, list) or not candidates:
                continue
            slug_a = _reviewed_slug(payload)
            note = _reviewer_note(payload)
            for c in candidates:
                slug_b = c.strip() if isinstance(c, str) else ""
                if not slug_b:
                    continue
                key = tuple(sorted([slug_a, slug_b]))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append({"slug_a": slug_a, "slug_b": slug_b, "note": note})

    return pairs


def _find_enclosing_brace(text: str, pos: int) -> int:
    """Return the index of the ``{`` that encloses ``pos``, or -1.

    Walks left counting ``}``/``{`` depth so a match inside a nested JSON
    object finds the outer container, not a sibling's. Literal braces
    inside string values would break this count, but reviewer JSON never
    emits those — worst case is ``raw_decode`` raising and the pair being
    dropped (best-effort is fine).
    """
    depth = 0
    for i in range(pos, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return i
            depth -= 1
    return -1


def _message_any_text(msg: Any) -> str:
    """Return the text content of any message (AI, Tool, Human, ...).

    Handles both dict-shaped and LangChain BaseMessage instances. List-
    of-block content (Anthropic format) is joined by ``text`` fields.
    """
    content = (
        msg.get("content", "") if isinstance(msg, dict) else (getattr(msg, "content", "") or "")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


def _reviewed_slug(payload: dict[str, Any]) -> str:
    """Return the first non-empty finding ``slug`` in blockers → warnings.

    ReviewReport has no ``target`` field — the reviewer places the
    offending slug on each finding. Empty when no findings attached.
    """
    for key in ("blockers", "warnings"):
        findings = payload.get(key)
        if not isinstance(findings, list):
            continue
        for f in findings:
            if isinstance(f, dict):
                slug = f.get("slug")
                if isinstance(slug, str) and slug.strip():
                    return slug.strip()
    return ""


def _reviewer_note(payload: dict[str, Any]) -> str:
    """Compose a <=200-char reviewer note for the merge queue.

    Prefers ``summary``; falls back to the first blocker/warning message.
    """
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()[:200]
    for key in ("blockers", "warnings"):
        findings = payload.get(key)
        if isinstance(findings, list):
            for f in findings:
                if isinstance(f, dict):
                    msg = f.get("message")
                    if isinstance(msg, str) and msg.strip():
                        return msg.strip()[:200]
    return ""


def _message_is_ai(msg: Any) -> bool:
    if isinstance(msg, dict):
        return msg.get("type") == "ai" or msg.get("role") == "assistant"
    return msg.__class__.__name__ == "AIMessage"


def _message_content(msg: Any) -> str:
    c = msg.get("content", "") if isinstance(msg, dict) else (getattr(msg, "content", "") or "")
    return c.strip() if isinstance(c, str) else ""


def _message_tool_calls(msg: Any) -> list[Any]:
    if isinstance(msg, dict):
        calls = msg.get("tool_calls") or (msg.get("additional_kwargs") or {}).get("tool_calls")
    else:
        calls = getattr(msg, "tool_calls", None) or getattr(msg, "additional_kwargs", {}).get(
            "tool_calls"
        )
    return calls if isinstance(calls, list) else []


def _message_total_tokens(msg: Any) -> int | None:
    if isinstance(msg, dict):
        meta = msg.get("response_metadata") or {}
    else:
        meta = getattr(msg, "response_metadata", {}) or {}
    usage = meta.get("token_usage") if isinstance(meta, dict) else None
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    return int(total) if isinstance(total, (int, float)) else None


def _compute_batch_cutoff_date(raw_paths: list[str]) -> str | None:
    """Return the latest raw-filename date for the batch as YYYY-MM-DD.

    The cutoff is derived from filename prefixes (``YYYY-MM-DD_...``),
    NOT from the Postgres ``messages.date`` timestamp. Rationale: the
    ingest pipeline writes filenames in its local timezone (IST for
    IndiaMART), but ``messages.date`` lands as UTC timestamptz. A
    near-midnight email can therefore have a filename dated Jan 10 and
    a DB timestamp on Jan 9 UTC — the middleware, which compares against
    the filename prefix, would false-reject the batch's own raw if we
    went through the DB. Using the filename on BOTH sides keeps the
    enforcement layer consistent.

    Returns None when no raw path has a parseable date prefix (test
    fixtures, pre-ingest fossils).
    """
    if not raw_paths:
        return None

    from src.compile.middleware.chronological_scope import _raw_file_date

    dates = [d for p in raw_paths if (d := _raw_file_date(p)) is not None]
    if not dates:
        return None
    return max(dates).isoformat()
