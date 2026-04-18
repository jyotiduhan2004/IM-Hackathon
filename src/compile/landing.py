"""Landing-page generators — glossary, home, changes.

Pure-fs generators called by `scripts/compile_all.py::_regenerate_landing_surfaces`.
Extracted from `src/compile/compiler.py` for code locality; compiler re-exports
the public names at the bottom of that module so existing callers
(`from src.compile.compiler import _generate_glossary`) keep working.
"""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from src.utils import render_with_frontmatter

logger = structlog.get_logger(__name__)


# Expansion table for acronyms whose in-text definition we can't always find.
# Keep short and high-signal — the glossary tool pulls the rest from running
# text. Extend as the corpus reveals new canonical acronyms.
_APPROVED_ALIASES: dict[str, str] = {
    "MCAT": "Microcatalog",
    "ISQ": "Item Searchable Quantity",
    "KYC": "Know Your Customer",
    "GST": "Goods & Services Tax",
}

# Anchored to `\b` on both sides — avoids matching the `TL` in `TL;DR` or
# the CONST in `MAX_LIMIT` inside code samples. 2+ letters keeps `OK` / `US`
# out; the glossary would be noise otherwise.
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")

# "(<expansion>)" immediately following the acronym — whitespace-tolerant.
# Captures anything other than ')' up to 80 chars so run-on sentences
# don't balloon a single definition.
_ACRONYM_DEFINITION_RE = re.compile(r"\(([^)]{1,80})\)")


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
    from src.compile.compiler import _GENERATED_MARKER
    from src.compile.compiler import _atomic_write_text
    from src.compile.compiler import _iter_content_pages
    from src.compile.compiler import _read_page

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
    _atomic_write_text(path, render_with_frontmatter(fm, "\n".join(lines)))
    logger.info("generated", kind="glossary", terms=len(entries))
    return path


def _recent_page_entries(wiki_dir: Path, limit: int = 10) -> list[tuple[Path, float]]:
    """Return the `limit` most recently modified topic+system pages.

    Uses filesystem mtime (not frontmatter) so a manually-touched page
    still surfaces. Sorted newest-first so callers render them in order.
    """
    from src.compile.compiler import _iter_content_pages

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
    from src.compile.compiler import _DOMAINS
    from src.compile.compiler import _GENERATED_MARKER
    from src.compile.compiler import _atomic_write_text

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
    _atomic_write_text(path, render_with_frontmatter(fm, "\n".join(lines)))
    logger.info("generated", kind="home", recent_count=len(recent))
    return path


def _generate_changes(wiki_dir: Path, db_conn: Any | None = None) -> Path:
    """Emit `wiki/changes.md` — last 30 days of compile activity from Postgres.

    `db_conn` is optional so tests and no-DB environments still produce a
    stub page (with "No recent activity"). On DB errors we log and fall
    through to the stub — landing pages should never fail the compile run.
    """
    from src.compile.compiler import _GENERATED_MARKER
    from src.compile.compiler import _atomic_write_text

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
    _atomic_write_text(path, render_with_frontmatter(fm, "\n".join(lines)))
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
