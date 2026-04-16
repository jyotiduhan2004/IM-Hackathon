"""Wiki compiler — Deep Agents workflow that compiles raw emails into wiki pages."""

from __future__ import annotations

import re
import tempfile
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

import structlog
import yaml
from langchain_core.tools import tool
from pydantic import BaseModel
from pydantic import Field

from src.compile.prompts import COMPILER_SYSTEM_PROMPT
from src.config import settings

if TYPE_CHECKING:
    from src.compile.tool_call_log import ToolCallLogHandler

logger = structlog.get_logger(__name__)


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
    """Filter-aware search for uncompiled email sources. Returns paginated results.

    Use this INSTEAD of list_uncompiled_emails when you want to narrow down
    which emails to process — by date range, sender, or thread.

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
        agent can recover rather than crash the batch.
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
    """List raw email files that haven't been compiled yet.

    DEPRECATED: prefer `find_new_sources` for filter + pagination support. This
    tool returns ALL uncompiled emails (up to 1000) with no filters.

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
def list_wiki_pages(wiki_dir: str = "wiki") -> dict[str, list[str]]:
    """List all existing wiki pages grouped by category.

    Call this BEFORE creating new pages so you know what already exists and
    can update the existing page instead of duplicating.

    Returns:
        Dict with keys: topics, entities, policies, timelines, conflicts.
        Each value is a list of page names (without .md extension).
        These names are what you should use in [[wikilinks]].
    """
    wiki_path = Path(wiki_dir)
    result: dict[str, list[str]] = {
        "topics": [],
        "entities": [],
        "systems": [],
        "policies": [],
        "timelines": [],
        "conflicts": [],
    }
    if not wiki_path.exists():
        return result

    for category in result:
        cat_dir = wiki_path / category
        if cat_dir.exists():
            result[category] = sorted(f.stem for f in cat_dir.glob("*.md"))
    return result


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
    file_path: str,
    acknowledge: list[str] | None = None,
) -> dict[str, Any]:
    """WHEN TO USE: Call this as the last step of compiling each raw email,
    before moving on to the next one. It runs a quality review over every
    wiki page that cites this email as a source and either gives you a
    punch list to fix, or confirms you're done.

    WHEN NOT TO USE: Don't call before writing the wiki pages. Don't call
    when you haven't touched any page for this email. This is not a search
    or lookup tool.

    What it checks: malformed frontmatter, duplicate H2 headings
    (most often caused by appending instead of merging), broken wikilinks
    (pointing to pages that don't exist), stray markdown brackets,
    H1-in-body (title belongs in frontmatter). Blockers fail the check;
    warnings are advisory.

    Feedback loop (single-thread, same session):
      1. Finish writing. Call `check_my_work(raw_path)`.
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
        file_path: Path to the raw email markdown file
            (e.g., ``"raw/2026-04-11_foo_abc12345.md"``).
        acknowledge: Optional list of issue IDs from a prior blocked call
            that you've decided are false positives.

    Returns:
        ``{"ok": "true", "status": "clean", "warnings": N, "audit": path}``
          when blockers are resolved or acknowledged, OR
        ``{"ok": "false", "status": "blocked", "issues": [{id, check,
        page, message}, ...], "audit": path, "hint": ...}`` when action
        is required.
    """
    from src.compile.critique import critique_pages
    from src.compile.critique import find_touched_pages
    from src.compile.critique import write_audit

    repo_root = Path.cwd()
    wiki_dir = repo_root / "wiki"
    audit_dir = repo_root / "docs" / "audits"

    touched = find_touched_pages(file_path, wiki_dir)
    result = critique_pages(touched, wiki_dir, repo_root)

    ack_ids = set(acknowledge or [])
    unresolved = [i for i in result.blockers if i.id not in ack_ids]

    if unresolved:
        audit_path = write_audit(result, file_path, "blocked", audit_dir, acknowledged_ids=ack_ids)
        logger.info(
            "check_my_work blocked",
            file_path=file_path,
            blockers=len(unresolved),
            audit=str(audit_path),
        )
        return {
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
            "audit": str(audit_path.relative_to(repo_root)),
            "hint": (
                "Edit the flagged pages to fix each blocker (usually: "
                "merge duplicate H2 sections, resolve broken wikilinks, "
                "remove stray brackets) and call check_my_work again. "
                "If a blocker is genuinely a false positive, call with "
                "acknowledge=['issue_id', ...] to proceed."
            ),
        }

    audit_path = write_audit(result, file_path, "clean", audit_dir, acknowledged_ids=ack_ids)
    return {
        "ok": "true",
        "status": "clean",
        "warnings": len(result.warnings),
        "audit": str(audit_path.relative_to(repo_root)),
    }


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
    """Regenerate wiki/index.md by scanning all wiki pages and their frontmatter.

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
        "# Knowledge Base Index",
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

    lines.insert(3, f"Total pages: {total}")
    lines.insert(4, "")
    lines.extend(cat_blocks)

    index_path = wiki_path / "index.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

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
    them. The `is_stub` flag is True when the page has no sources cited —
    used to hide ghost entity/system pages from the landing listings.
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
    last_compiled = str(fm.get("last_compiled", "") or "")
    # `last_compiled: stub` and `stub-backfilled` are the canonical stub
    # markers used by `scripts/backfill_stubs.py` — keep this rule in sync
    # with `scripts/backfill_stubs.py::_is_stub`.
    is_stub_marker = last_compiled in ("stub", "stub-backfilled")
    return {
        "slug": md_file.stem,
        "title": str(fm.get("title", md_file.stem)),
        "status": str(fm.get("status", "current")),
        "last_compiled": last_compiled,
        "summary": _first_paragraph(body),
        "sources_count": len(sources) if isinstance(sources, list) else 0,
        "is_stub": not sources or is_stub_marker,
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
    "entities": (
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
}

_SECTION_TITLES = {
    "topics": "Topics",
    "systems": "Products & Platforms",
    "entities": "People",
    "policies": "Policies",
    "domains": "Domains",
}


def rebuild_landing_pages(wiki_dir: str = "wiki") -> str:
    """Regenerate home.md + 4 section index pages as real listings.

    Replaces the hardcoded "(Placeholder — comes in a later PR)" stubs
    with full page listings sorted by `last_compiled` desc. Entity and
    system pages with `sources: []` are hidden (they're ghost pages left
    over from the create_entity evidence-gate workflow).

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
                if category in ("entities", "systems") and summary["is_stub"]:
                    continue
                pages.append(summary)
        pages.sort(key=lambda p: (p["last_compiled"], p["title"]), reverse=True)
        sections[category] = pages

    for category, pages in sections.items():
        _write_section_index(wiki_path, category, pages, now_iso)

    _write_home(wiki_path, sections, now_iso)

    totals = ", ".join(f"{k}={len(v)}" for k, v in sections.items())
    return f"rebuilt landing pages ({totals})"


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
            status_suffix = "" if page["status"] == "current" else f" *({page['status']})*"
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
        "status": "current",
        "last_compiled": now_iso,
    }
    _atomic_write_text(
        wiki_path / category / "index.md",
        _render_with_frontmatter(fm, "\n".join(lines)),
    )


def _write_home(wiki_path: Path, sections: dict[str, list[dict[str, Any]]], now_iso: str) -> None:
    """Write `home.md` as a summary + recent-activity rollup."""
    counts = {k: len(v) for k, v in sections.items()}
    all_pages = [{**p, "category": cat} for cat, pages in sections.items() for p in pages]
    all_pages.sort(key=lambda p: (p["last_compiled"], p["title"]), reverse=True)
    recent = all_pages[:15]

    lines = [
        "# Home",
        "",
        "Internal knowledge base compiled from our mailing lists.",
        "",
        "## What's here",
        "",
        f"- [Topics](topics/) — **{counts['topics']}** pages on rollouts, "
        "decisions, incidents, and experiments.",
        f"- [Products & Platforms](systems/) — **{counts['systems']}** pages on "
        "durable tools, services, and mailing lists.",
        f"- [Policies](policies/) — **{counts['policies']}** pages on current "
        "rules, approval flows, and guidelines.",
        f"- [People](entities/) — **{counts['entities']}** pages on "
        "contributors and owners (stubs hidden).",
        "- [Changes](log/) — chronological compile log.",
        "- [About](about/) — how this wiki is built.",
        "",
        "## Most recently updated",
        "",
    ]
    if recent:
        for page in recent:
            cat = page["category"]
            status_suffix = "" if page["status"] == "current" else f" *({page['status']})*"
            lines.append(f"- [[{page['slug']}]] — {page['title']}{status_suffix} · *{cat}*")
    else:
        lines.append("*No pages compiled yet.*")
    lines.append("")

    fm = {
        "title": "Home",
        "page_type": "index",
        "status": "current",
        "last_compiled": now_iso,
    }
    _atomic_write_text(
        wiki_path / "home.md",
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

# Expansion table for acronyms whose in-text definition we can't always find.
# Keep short and high-signal — the glossary tool pulls the rest from running
# text. Extend as the corpus reveals new canonical acronyms.
_APPROVED_ALIASES: dict[str, str] = {
    "MCAT": "Microcatalog",
    "ISQ": "Item Searchable Quantity",
    "KYC": "Know Your Customer",
    "GST": "Goods & Services Tax",
}

_GENERATED_MARKER = "<!-- generated by compiler; do not edit by hand -->"

# Anchored to `\b` on both sides — avoids matching the `TL` in `TL;DR` or
# the CONST in `MAX_LIMIT` inside code samples. 2+ letters keeps `OK` / `US`
# out; the glossary would be noise otherwise.
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")

# "(<expansion>)" immediately following the acronym — whitespace-tolerant.
# Captures anything other than ')' up to 80 chars so run-on sentences
# don't balloon a single definition.
_ACRONYM_DEFINITION_RE = re.compile(r"\(([^)]{1,80})\)")


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


def _assign_domains(fm: dict[str, Any], body: str) -> list[str]:
    """Decide which domain hub(s) a page belongs to.

    Preference order (per North Star):
    1. Explicit `domain:` frontmatter — trusted verbatim (1 hub).
    2. `tags:` list — every tag matching a domain slug attaches the page.
    3. Keyword match against the page title + first paragraph — transitional
       fallback, logged so operators see which pages still need explicit tags.
    """
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
        slug = fm.get("slug") or fm.get("title") or ""
        logger.warning(
            "domain-hub-inferred-from-keyword",
            slug=str(slug),
            domain=inferred,
        )
        return [inferred]
    return []


def _regenerate_domain_hubs(wiki_dir: Path) -> list[Path]:
    """Emit `wiki/domains/<slug>.md` for each of the 8 fixed domains.

    Always writes all 8 — an empty domain still gets a page so the home
    page's domain-card grid resolves every link. Idempotent: same corpus
    produces byte-identical output.
    """
    buckets: dict[str, dict[str, list[tuple[str, str]]]] = {
        slug: {"topics": [], "systems": []} for slug, _t, _k in _DOMAINS
    }

    for md_file in _iter_content_pages(wiki_dir):
        read = _read_page(md_file)
        if read is None:
            continue
        fm, body = read
        category = md_file.parent.name
        slugs = _assign_domains(fm, body)
        if not slugs:
            continue
        entry = (md_file.stem, _first_paragraph(body))
        for slug in slugs:
            buckets[slug][category].append(entry)

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


def _extract_acronyms(body: str) -> dict[str, str]:
    """Return `{ACRONYM: expansion}` for acronyms defined in a page body.

    Expansion is taken from "(<expansion>)" immediately following the first
    acronym match, or from `_APPROVED_ALIASES` if we've canonicalized it.
    An acronym with neither in-text definition nor approved alias is
    skipped (the manual-glossary-add path from the spec).
    """
    found: dict[str, str] = {}
    for match in _ACRONYM_RE.finditer(body):
        term = match.group(0)
        if term in found:
            continue
        tail = body[match.end() : match.end() + 82]
        def_match = _ACRONYM_DEFINITION_RE.match(tail.lstrip())
        if def_match:
            found[term] = def_match.group(1).strip()
        elif term in _APPROVED_ALIASES:
            found[term] = _APPROVED_ALIASES[term]
    return found


def _generate_glossary(wiki_dir: Path) -> Path:
    """Emit `wiki/glossary.md` — an alphabetized term table.

    First-seen-on link is the first page (by stem, deterministic) that
    defined the term. Approved aliases always appear even if no page uses
    them — they're the canonical IndiaMART vocabulary.
    """
    entries: dict[str, tuple[str, str]] = {}
    for md_file in _iter_content_pages(wiki_dir):
        read = _read_page(md_file)
        if read is None:
            continue
        _fm, body = read
        link_target = f"{md_file.parent.name}/{md_file.stem}"
        for term, expansion in _extract_acronyms(body).items():
            entries.setdefault(term, (expansion, link_target))

    # Always seed canonical aliases; in-text definitions above win when present.
    for term, expansion in _APPROVED_ALIASES.items():
        entries.setdefault(term, (expansion, ""))

    lines = [
        "# Glossary",
        "",
        "| Term | Expansion | First seen on |",
        "|---|---|---|",
    ]
    for term in sorted(entries):
        expansion, link_target = entries[term]
        seen_cell = f"[[{link_target}]]" if link_target else "—"
        lines.append(f"| {term} | {expansion} | {seen_cell} |")
    lines.extend(["", _GENERATED_MARKER, ""])

    fm = {"title": "Glossary", "page_type": "glossary", "status": "active"}
    path = wiki_dir / "glossary.md"
    _atomic_write_text(path, _render_with_frontmatter(fm, "\n".join(lines)))
    logger.info("generated", kind="glossary", terms=len(entries))
    return path


def _recent_page_entries(wiki_dir: Path, limit: int = 10) -> list[tuple[Path, float]]:
    """Return the `limit` most recently modified topic+system pages.

    Uses filesystem mtime (not frontmatter) so a manually-touched page
    still surfaces. Sorted newest-first so callers render them in order.
    """
    pages_with_mtime = [
        (md_file, md_file.stat().st_mtime) for md_file in _iter_content_pages(wiki_dir)
    ]
    pages_with_mtime.sort(key=lambda p: p[1], reverse=True)
    return pages_with_mtime[:limit]


def _generate_home(wiki_dir: Path) -> Path:
    """Overwrite `wiki/home.md` with the North-Star 8-domain landing layout.

    Runs after `rebuild_landing_pages` — the latter's `_write_home` leaves
    the file in a valid but pre-North-Star shape; this overwrite is
    intentional. The resulting page is the site's home per `mkdocs.yml`.
    """
    lines = [
        "# Email Knowledge Base — IndiaMART",
        "",
        "A compiled wikipedia derived from our mailing lists. Pages are",
        "about *things* (products, systems, initiatives, decisions), not",
        "*events* (threads, emails).",
        "",
        "Browse the [Glossary](glossary.md) for IndiaMART-specific acronyms,",
        "or jump to [Changes](changes.md) for recent compile activity.",
        "",
        "## Explore by domain",
        "",
    ]
    for slug, title, _keywords in _DOMAINS:
        lines.append(f"- [{title}](domains/{slug}.md)")

    lines.extend(["", "## Recent changes", ""])
    recent = _recent_page_entries(wiki_dir, limit=10)
    if recent:
        for md_file, mtime in recent:
            category = md_file.parent.name
            stamp = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d")
            lines.append(f"- {stamp} — [[{category}/{md_file.stem}]]")
    else:
        lines.append("*No pages compiled yet.*")
    lines.extend(
        [
            "",
            "## Tip",
            "",
            "Use the search box above to find pages by keyword, or",
            "browse by domain using the cards above.",
            "",
            _GENERATED_MARKER,
            "",
        ]
    )

    fm = {"title": "Home", "page_type": "home", "status": "active"}
    path = wiki_dir / "home.md"
    _atomic_write_text(path, _render_with_frontmatter(fm, "\n".join(lines)))
    logger.info("generated", kind="home", recent_count=len(recent))
    return path


def _generate_changes(wiki_dir: Path, db_conn: Any | None = None) -> Path:
    """Emit `wiki/changes.md` — last 30 days of compile activity from Postgres.

    `db_conn` is optional so tests and no-DB environments still produce a
    stub page (with "No recent activity"). On DB errors we log and fall
    through to the stub — landing pages should never fail the compile run.
    """
    rows: list[dict[str, Any]] = []
    if db_conn is not None:
        try:
            rows = _fetch_recent_compile_activity(db_conn)
        except Exception as exc:  # noqa: BLE001 — landing gen must never crash run
            logger.warning("changes-db-fetch-failed", error=str(exc))
            rows = []
    else:
        try:
            from src.db import connect

            with connect() as conn:
                rows = _fetch_recent_compile_activity(conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("changes-db-fetch-failed", error=str(exc))
            rows = []

    lines = ["# Changes", ""]
    if not rows:
        lines.append("*No recent activity.*")
    else:
        by_day: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            attempted = row["attempted_at"]
            day = attempted.astimezone(UTC).strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append(row)

        for day in sorted(by_day, reverse=True):
            lines.append(f"## {day}")
            lines.append("")
            for row in by_day[day]:
                when = row["attempted_at"].astimezone(UTC).strftime("%H:%M UTC")
                outcome = row.get("outcome") or "in-flight"
                model = row.get("compile_model") or "unknown"
                lines.append(f"- {when} — {outcome} ({model})")
            lines.append("")

    lines.extend([_GENERATED_MARKER, ""])

    fm = {"title": "Changes", "page_type": "changes", "status": "active"}
    path = wiki_dir / "changes.md"
    _atomic_write_text(path, _render_with_frontmatter(fm, "\n".join(lines)))
    logger.info("generated", kind="changes", rows=len(rows))
    return path


def _fetch_recent_compile_activity(conn: Any) -> list[dict[str, Any]]:
    """Return compile_attempts rows from the last 30 days, newest first.

    Filters to finished attempts only — in-flight rows without `finished_at`
    would otherwise flood the page if the script runs mid-batch.
    """
    cur = conn.execute(
        """
        SELECT attempted_at, outcome, compile_model
          FROM compile_attempts
         WHERE attempted_at >= now() - interval '30 days'
           AND finished_at IS NOT NULL
         ORDER BY attempted_at DESC
        """
    )
    return list(cur.fetchall())


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
    }
)


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
            'prompt_ambiguity', 'tool_gap', 'supersession_doubt', or
            'structure_suggestion'.
        message: 1-2 sentence observation.
        email_path: Optional raw email this is about (e.g.
            ``raw/2026-04-11_subject_abc.md``).
        suggested_action: Optional concrete fix the human could take.

    Returns:
        ``{"ok": True, "id": <int>}`` on success, or
        ``{"ok": False, "error": "..."}`` on invalid category.
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

    run_id = os.environ.get("COMPILE_RUN_ID")
    new_id = record(
        run_id=run_id,
        category=category,
        message=message,
        email_path=email_path,
        suggested_action=suggested_action,
    )
    return {"ok": True, "id": new_id}


@tool
def resolve_page(query: str) -> dict[str, Any]:
    """Find a wiki page by slug, title, or entity email.

    WHEN TO USE: before creating a new page — check the `wiki_pages`
    catalog for an existing page covering the same concept or person.
    On a miss, the tool returns up to 5 substring candidates so you
    don't have to retry with slug variants.

    WHEN NOT TO USE:
    - You already have the exact slug and want the file contents
      → use `read_file` directly.
    - You want to browse the full catalog by category
      → use `list_wiki_pages`.
    - You need free-text search across page BODIES
      → use `grep` on the wiki directory.

    Args:
        query: A single string. Auto-detected by shape:
          - contains "@"    → email lookup against entity pages
          - kebab-case slug → exact slug match
          - anything else   → exact title match (case-insensitive)
          The tool falls back across all three shapes before giving up,
          so a mis-classified shape still has a chance to hit.

    Returns:
        On hit: {"exists": True, "slug", "title", "page_type", "path",
                 "status", "confidence"}.
                `status` is "current" | "superseded" | "contested" — use
                it to decide whether to create a replacement page.
        On miss with candidates:
                {"exists": False, "candidates": [<up to 5 substring
                matches with slug/title/page_type/path/status>]}.
        On miss with nothing close:
                {"exists": False, "candidates": []}.
        On empty/stale catalog:
                {"exists": False, "catalog_empty_or_stale": True,
                 "error": "...", "catalog_counts": {...}}.
    """
    # deferred to avoid circular import at module load time
    from src.db.wiki_pages import count_wiki_pages_by_type
    from src.db.wiki_pages import lookup_page
    from src.db.wiki_pages import search_pages

    q = (query or "").strip()
    if not q:
        return {
            "exists": False,
            "error": "query is empty",
            "candidates": [],
        }

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

    # Fallback chain: try whichever shape is most likely first, but if
    # that misses, try the others before declaring the query a miss. This
    # stops an agent that passes "Amit Agarwal" (a title) as `query` from
    # missing just because title-lookup happened to run last.
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
        row = lookup_page(**kwargs)  # type: ignore[arg-type]
        if row is not None:
            return {
                "exists": True,
                "slug": row["slug"],
                "title": row["title"],
                "page_type": row["page_type"],
                "path": row["path"],
                "status": row["status"],
                "confidence": float(row["confidence"]),
            }

    # Real miss — help the agent find something close without retrying.
    candidates = search_pages(q, limit=5)
    return {
        "exists": False,
        "candidates": [
            {
                "slug": c["slug"],
                "title": c["title"],
                "page_type": c["page_type"],
                "path": c["path"],
                "status": c["status"],
            }
            for c in candidates
        ],
    }


@tool
def write_draft_page(
    slug: str,
    reason: str,
    content: str,
    wiki_dir: str = "wiki",
) -> dict[str, Any]:
    """Write a draft page to wiki/_drafts/{slug}.md. Hidden from readers.

    Use WHEN:
    - You reference a [[wikilink]] but aren't confident the target deserves
      its own topic or system page yet.
    - You found a pattern (e.g. "all WhatsApp work") that could become a hub
      but isn't ready to promote.

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
    path.write_text(_render_with_frontmatter(fm, content), encoding="utf-8")
    return {"ok": True, "path": str(path), "error": None}


class EntityRequest(BaseModel):
    """One person to resolve/create as an entity page.

    `email` is REQUIRED and is the identity — slugs are derived from it
    deterministically. An empty or missing `email` is a schema violation
    and will be rejected before the tool body runs.
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
def create_entities(raw_paths: list[str], entities: list[EntityRequest]) -> dict[str, Any]:
    """Resolve or create entity pages for the people mentioned in this batch.

    Always use this tool for entity pages — do NOT invent slugs or
    `write_file` a new entity markdown directly. The tool derives the
    canonical slug from each email deterministically, checks for existing
    pages (by canonical slug OR by legacy display-name slug with
    `email:` frontmatter), and gates new-page creation on evidence
    strength.

    Each requested email MUST appear literally in at least one raw file
    from ``raw_paths``. Addresses that don't match are refused with
    `reason: "email_not_in_raw"`.

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
        raw_paths: Relative raw email paths for the current batch, e.g.
            ["raw/2026-04-11_subject_a.md"]. The tool reads these to
            validate that each requested email actually appears.
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

    # LangChain validates & coerces each list item into an EntityRequest
    # (Pydantic model). Unwrap to plain dicts so the repo-layer helper
    # keeps its simple signature and stays usable from scripts and tests
    # that don't import the tool layer.
    entity_dicts = [e.model_dump() for e in entities]
    return create_entity_pages(raw_paths, entity_dicts)


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


@tool
def get_page_summary(slug: str, wiki_dir: str = "wiki") -> dict[str, Any]:
    """Return a summary for a wiki page.

    WHEN TO USE: when you need to know what a wiki page is about
      before deciding whether to merge or create a new page.
    WHEN NOT TO USE: don't call when you need the full page body —
      use `read_file` (then `patch_page` for targeted writes) instead.

    Scans `wiki/<category>/<slug>.md` across every wiki category, parses
    the frontmatter, and returns the fields an agent actually needs to
    decide "merge here or make a new page": title, page_type, status,
    first paragraph (≤200 chars), list of H2 headings, source count, and
    last_compiled. Does NOT return the filesystem path — callers should
    treat the slug as the stable identifier.

    Args:
        slug: kebab-case page identifier (without `.md`).
        wiki_dir: Root wiki directory. Default "wiki".

    Returns:
        On hit: ``{"found": True, "slug": str, "title": str, "page_type": str,
        "status": str, "first_paragraph": str, "headings": list[str],
        "source_count": int, "last_compiled": str}``.
        On miss: ``{"found": False, "slug": str, "reason": "not_found"}``.
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
    sources = fm.get("sources") or []
    source_count = len(sources) if isinstance(sources, list) else 0

    return {
        "found": True,
        "slug": slug,
        "title": str(fm.get("title") or slug),
        "page_type": str(fm.get("page_type") or ""),
        "status": str(fm.get("status") or "current"),
        "first_paragraph": _first_paragraph_capped(body, cap=200),
        "headings": _extract_h2_headings(body),
        "source_count": source_count,
        "last_compiled": str(fm.get("last_compiled") or ""),
    }


@tool
def get_thread_context(thread_id: str, limit: int = 50) -> dict[str, Any]:
    """Return chronological messages in a thread with short previews.

    WHEN TO USE: when merging a new email into an existing topic page that
      spans multiple emails — gives you the conversation arc cheaply.
    WHEN NOT TO USE: don't call when you only need one message
      (use the email's raw_path directly) or when the thread isn't
      relevant to the current concept.

    Queries Postgres `messages` for every row matching `thread_id`, ordered
    by `date` ASC, and attaches a 200-char body preview from the raw
    markdown. Missing raw files degrade gracefully (empty preview).
    Caps at `limit` rows to avoid flooding agent context on long threads.

    Args:
        thread_id: Gmail thread identifier.
        limit: Maximum rows to return. Default 50.

    Returns:
        ``{"thread_id": str, "messages": [{"message_id", "subject", "from_addr",
        "date", "raw_path", "first_200_chars", "compile_state"}, ...],
        "truncated": bool}``. Empty list when the thread is unknown.
    """
    from src.db import connect

    with connect() as conn:
        raw_rows = conn.execute(
            """
            SELECT message_id, raw_path, subject, from_address, date, compile_state
              FROM messages
             WHERE thread_id = %s
             ORDER BY date ASC NULLS LAST, message_id ASC
             LIMIT %s
            """,
            (thread_id, limit + 1),
        ).fetchall()
    rows = cast(list[dict[str, Any]], raw_rows)
    truncated = len(rows) > limit
    rows = rows[:limit]

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
                body = _extract_body(text)
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

    return {"thread_id": thread_id, "messages": messages, "truncated": truncated}


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
    - `person_page_heuristic`: when `page_type` is ``person`` or
      ``entity``, the body must contain ≥2 substantive sentences of
      prose (not just headings, wikilinks, or CC-list mentions).
    - `likely_duplicate`: another wiki page already has the same
      (case-insensitive) `title`.

    Args:
        slug: kebab-case identifier for the draft (used to exclude
            self-duplication in the likely-duplicate check).
        body: Markdown body being considered.
        title: Draft title. Without it, the duplicate check is skipped.
        page_type: Draft page type (e.g. ``topic``, ``entity``, ``person``).
            Without it, the person-page heuristic cannot fire.
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


def create_compiler(
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
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
        raw_dir: Path to raw/ directory
        wiki_dir: Path to wiki/ directory

    Returns:
        A compiled LangGraph agent ready to invoke.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    model_name = model_name or settings.llm_model
    logger.info(
        "creating wiki compiler",
        model=model_name,
        via_proxy=bool(settings.litellm_base_url),
    )

    model = _make_chat_model(model_name)

    # Deep Agents defaults to a virtual (in-memory) filesystem. We need real disk
    # so read_file/write_file/edit_file operate on raw/ and wiki/ directly.
    # virtual_mode=True with root_dir="." means:
    # - Absolute paths and ".." traversal are blocked (security guardrail)
    # - Agent must use relative paths like "raw/foo.md", "wiki/topics/bar.md"
    # Per FilesystemBackend docs, this is the right mode for bounded workflows.
    cwd = Path.cwd().resolve()
    backend = FilesystemBackend(root_dir=str(cwd), virtual_mode=True)

    system_prompt = (
        COMPILER_SYSTEM_PROMPT
        + f"\n\n## Context\n\n- raw_dir: {raw_dir}\n- wiki_dir: {wiki_dir}\n"
        + "- ALL file paths MUST be relative (no leading /, no ..). Examples:\n"
        + f"  - GOOD: `{raw_dir}/2026-04-11_subject_abc.md`\n"
        + f"  - GOOD: `{wiki_dir}/topics/my-topic.md`\n"
        + "  - BAD: `/Users/...` (absolute paths are blocked)\n"
        + "  - BAD: `/raw/foo.md` (leading slash means absolute; blocked)\n"
        + "- Do NOT call `ls` on absolute paths or `/`. Use `ls raw` or "
        + f"`ls {wiki_dir}/topics` or use `glob` with patterns.\n"
    )

    # mark_as_compiled, update_wiki_index, append_to_log, and
    # stamp_page_compiled_at are all NOT exposed to the agent — every
    # bookkeeping call is a coordinator concern now. scripts/compile_all.py
    # (a) flips messages.compile_state in Postgres deterministically after
    # run_compilation returns (trusting the LLM leaked ~68% of "processed"
    # emails into permanent-pending state), (b) regenerates wiki/index.md
    # after every run, (c) writes one structured per-batch row to
    # wiki/log.md, and (d) stamps `last_compiled` on every wiki page whose
    # mtime advanced during the run. Letting the LLM make these calls was
    # redundant at best and unreliable at worst (the agent forgot stamp
    # and log calls roughly half the time, leaving pages with stale
    # timestamps and gaps in the audit trail). All four functions remain
    # importable for one-off manual use.
    #
    # `check_my_work` IS exposed — it runs a quality critique over the
    # wiki pages sourcing a just-compiled email and returns a punch list
    # the agent can fix in-session before moving on. It does NOT flip
    # compile state; it's a self-review gate. The coordinator marks
    # programmatically post-batch as before.
    # NOTE: `list_uncompiled_emails` is deliberately NOT exposed to the agent.
    # The coordinator owns the compile queue and already passes the batch file
    # list in the user instruction. Letting the agent browse the whole queue
    # was pure context tax in Langfuse traces.
    # Hard gate on `check_my_work`: live traces show the agent calls it
    # before writing anything in 59-78% of batches, which makes the
    # critique tool useless. The middleware short-circuits the tool when
    # no content-page write has succeeded yet in this session.
    from src.compile.middleware import CheckMyWorkGateMiddleware

    return create_deep_agent(
        model=model,
        tools=[
            find_new_sources,
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
        middleware=[CheckMyWorkGateMiddleware()],
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


def run_compilation(
    instruction: str = "Compile all uncompiled raw emails into wiki pages.",
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    recursion_limit: int = 150,
    cache_stats: Any | None = None,
    tool_log: ToolCallLogHandler | None = None,
    run_name: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
    trace_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Run a compilation pass. Returns the agent's final state.

    recursion_limit of 150 accommodates ~3-10 emails per batch. Each email
    typically takes 10-20 agent steps (read, classify, read existing pages,
    write/edit pages, stamp timestamps, mark compiled). Bump higher if batches
    hit the limit.

    Pass a `CacheStatsCallback` as `cache_stats` to capture per-batch prompt-
    caching metrics (hit rate, cached tokens, total tokens). See
    `src/compile/cache_stats.py`.

    Pass a `ToolCallLogHandler` as `tool_log` to capture per-tool-call
    telemetry (name, inputs, latency, status). See
    `src/compile/tool_call_log.py`.
    """
    agent = create_compiler(model_name=model_name, raw_dir=raw_dir, wiki_dir=wiki_dir)

    callbacks = []
    lf = get_langfuse_handler(update_trace=True)
    if lf:
        callbacks.append(lf)
    if cache_stats is not None:
        callbacks.append(cache_stats)
    if tool_log is not None:
        callbacks.append(tool_log)

    config: dict[str, Any] = {}
    if callbacks:
        config["callbacks"] = callbacks
    config["recursion_limit"] = recursion_limit
    if run_name:
        config["run_name"] = run_name
    if trace_metadata:
        config["metadata"] = trace_metadata
    if trace_tags:
        config["tags"] = trace_tags

    logger.info(
        "running compilation",
        instruction=instruction[:100],
        recursion_limit=recursion_limit,
    )
    return agent.invoke(
        {"messages": [{"role": "user", "content": instruction}]},
        config=config,
    )
