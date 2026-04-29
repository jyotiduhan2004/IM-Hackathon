"""Deprecated agent tools the prompt still mentions.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). `mark_as_compiled`,
`stamp_page_compiled_at`, and `append_to_log` are kept importable for
manual ops but the coordinator handles them deterministically post-run.
`check_my_work` is still bound to the agent surface — it's the
post-write critique loop.
"""

from __future__ import annotations

import hashlib
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from langchain_core.tools import tool

from src.agent import run_state as _run_state
from src.config import settings
from src.utils import extract_body as _extract_body
from src.utils import extract_frontmatter as _extract_frontmatter
from src.utils import render_with_frontmatter as _render_with_frontmatter

logger = structlog.get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@tool
def stamp_page_compiled_at(file_path: str) -> dict[str, str]:
    """Set last_compiled on a wiki page to the current real-world IST time.

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

    now_iso = datetime.now(IST).isoformat(timespec="seconds")
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
    from src.agent.critique import critique_pages
    from src.agent.critique import find_touched_pages
    from src.agent.critique import write_audit

    repo_root = Path.cwd()
    wiki_dir = repo_root / "wiki"
    audit_dir = repo_root / "docs" / "audits"

    # Sorted ack list so (['b', 'a'],) hashes the same as (['a', 'b'],).
    ack_hash = hashlib.sha256((",".join(sorted(acknowledge or []))).encode("utf-8")).hexdigest()
    cache_key = (raw_email_path, _run_state._write_epoch, ack_hash)  # noqa: SLF001
    cached = _run_state._check_my_work_cache.get(cache_key)  # noqa: SLF001
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
            write_epoch=_run_state._write_epoch,  # noqa: SLF001
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
        _run_state._check_my_work_cache[cache_key] = payload  # noqa: SLF001
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
    _run_state._check_my_work_cache[cache_key] = payload  # noqa: SLF001
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
