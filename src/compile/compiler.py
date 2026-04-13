"""Wiki compiler — Deep Agents workflow that compiles raw emails into wiki pages."""

from __future__ import annotations

import re
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import structlog
import yaml
from langchain_core.tools import tool

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
        return {"error": f"limit must be ≥1 and offset must be ≥0 (got limit={limit}, offset={offset})"}

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
def mark_as_compiled(file_path: str) -> dict[str, str | int]:
    """Mark a raw email as compiled. Call ONLY after the email's content has
    been merged into the correct wiki pages.

    Records the transition in the Postgres `messages` table. The raw file
    itself is NOT modified — the legacy `compiled:` field in raw frontmatter
    is dead state as of the catalog migration.

    Args:
        file_path: Path to the raw email markdown file
            (e.g., "raw/2026-04-11_foo_abc12345.md").

    Returns:
        Dict with "ok" (bool), "remaining_uncompiled" (int count), "path" (str).
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
    pct = (100 * compiled / total) if total else 0.0

    lines: list[str] = [
        "## Compile progress",
        "",
        f"**{compiled:,} of {total:,} emails compiled** ({pct:.1f}%). "
        f"{pending:,} pending, {failed:,} failed, {claimed:,} in-flight.",
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

    return (
        f"updated index: {total} pages across "
        f"{sum(1 for v in categories.values() if v)} categories; "
        f"auto-stamped {stamped} pages with last_compiled"
    )


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
def resolve_page(
    slug: str | None = None,
    title: str | None = None,
    canonical_user_email: str | None = None,
) -> dict[str, Any]:
    """Find the canonical wiki page for a slug, title, or entity email.

    Use this BEFORE creating a new page — consults the `wiki_pages` catalog
    so the agent stops duplicating pages by grepping the filesystem.

    Args (at least one required):
        slug: Try exact slug match first (confidence 1.0).
        title: Case-insensitive exact title match (confidence 0.9).
        canonical_user_email: Exact match on entity-page email (confidence 1.0).

    Returns:
        Dict with keys {exists, slug, title, page_type, path, status, confidence}.
        `status` surfaces `current`/`superseded`/`contested` so the agent can
        decide whether to create a replacement page.
        When nothing matches, `exists` is False and the ID fields are None.
        When called with no arguments, returns exists=False with an `error`
        describing the missing input.
    """
    # deferred to avoid circular import at module load time
    from src.db.wiki_pages import lookup_page

    if slug is None and title is None and canonical_user_email is None:
        return {
            "exists": False,
            "slug": None,
            "title": None,
            "page_type": None,
            "path": None,
            "status": None,
            "confidence": 0.0,
            "error": "provide at least one of: slug, title, canonical_user_email",
        }

    row = lookup_page(slug=slug, title=title, canonical_user_email=canonical_user_email)
    if row is None:
        return {
            "exists": False,
            "slug": None,
            "title": None,
            "page_type": None,
            "path": None,
            "status": None,
            "confidence": 0.0,
        }
    return {
        "exists": True,
        "slug": row["slug"],
        "title": row["title"],
        "page_type": row["page_type"],
        "path": row["path"],
        "status": row["status"],
        "confidence": float(row["confidence"]),
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


@tool
def create_entity(email: str, display_name: str = "") -> dict[str, Any]:
    """Resolve or create an entity page by EMAIL. Call this INSTEAD of
    inventing entity slugs yourself.

    The returned `slug` is deterministic — identical email always gives
    the identical slug. Use it in every `[[wikilink]]` that references
    this person. Never hand-craft entity slugs.

    - If an entity page already exists for this email (by canonical slug
      OR by legacy display-name slug with `email:` frontmatter), the
      existing slug is returned. `created: false`.
    - If no page exists, a minimal stub page is written with
      `title`, `page_type: entity`, `status: current`, `email:`, empty
      `sources` and `related`. `created: true`. Enrich it with
      `read_file` + `edit_file` afterward as you do today.

    Args:
        email: Required. The person's email address, e.g.
            "amit@indiamart.com". Case-insensitive.
        display_name: Optional. Used as the stub page title only when a
            new page is created. Ignored for existing pages. Examples:
            "Amit Jain", "Ruchi Gupta".

    Returns:
        On success: {"ok": True, "slug": "amit-indiamart-com",
        "path": "wiki/entities/amit-indiamart-com.md",
        "created": True|False, "email": "amit@indiamart.com"}.
        On invalid email: {"ok": False, "error": "..."}.
    """
    from src.compile.entities import create_entity_page

    return create_entity_page(email, display_name or None)


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
    """
    if settings.litellm_base_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            base_url=settings.litellm_base_url,
            api_key=settings.openai_api_key or "dummy",
        )

    # Fallback: use langchain's provider inference
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
    return create_deep_agent(
        model=model,
        tools=[
            find_new_sources,
            list_uncompiled_emails,
            list_wiki_pages,
            resolve_page,
            create_entity,
            write_draft_page,
            log_insight,
        ],
        system_prompt=system_prompt,
        backend=backend,
    )


def get_langfuse_handler() -> Any | None:
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

    return CallbackHandler()


def run_compilation(
    instruction: str = "Compile all uncompiled raw emails into wiki pages.",
    model_name: str | None = None,
    raw_dir: str = "raw",
    wiki_dir: str = "wiki",
    recursion_limit: int = 150,
    cache_stats: Any | None = None,
    tool_log: ToolCallLogHandler | None = None,
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
    lf = get_langfuse_handler()
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

    logger.info(
        "running compilation",
        instruction=instruction[:100],
        recursion_limit=recursion_limit,
    )
    return agent.invoke(
        {"messages": [{"role": "user", "content": instruction}]},
        config=config,
    )
