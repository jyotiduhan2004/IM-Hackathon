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


# User-facing blurbs for the home-page domain cards. Keyed by slug so
# copy can evolve without touching `_DOMAINS` (which drives keyword
# matching); missing keys render as an empty line.
_DOMAIN_BLURBS: dict[str, str] = {
    "buyer-experience": "BuyMer, BuyLeads, search UX, and buyer-side WhatsApp.",
    "seller-experience": "AuditMate, seller dashboard, specs, compliance.",
    "marketplace-discovery": "MCAT, ISQ, photo search, ranking, categorization.",
    "platform-reliability": "GKE, Mesh PG, DB ops, API framework, performance.",
    "trust-safety": "KYC, GST, fraud, moderation, payment protection.",
    "ai-automation": "CrashAgent, WhatsApp 9696, autonomous assistants.",
    "growth-monetization": "Export, ads, affiliates, Google Merchant, tenders.",
    "engineering-productivity": "CI/CD, code quality, testing, dev tools.",
}

_UNCATEGORIZED_SLUG = "uncategorized"
_UNCATEGORIZED_TITLE = "Uncategorized"
_UNCATEGORIZED_BLURB = "Pages without an explicit `domain:` (or `domains:`) frontmatter."


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


def _page_recency_key(fm: dict[str, Any], md_file: Path) -> float:
    """Return a sortable recency timestamp for a wiki page.

    Prefers the frontmatter `last_compiled` ISO string (what the
    coordinator stamps via `_stamp_recently_modified_pages`). Falls back
    to filesystem mtime so manually-edited pages still rank. On parse
    failure, returns 0.0 so bad frontmatter buckets to the bottom.
    """
    raw = fm.get("last_compiled")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            pass
    elif isinstance(raw, datetime):
        return raw.timestamp()
    try:
        return md_file.stat().st_mtime
    except OSError:
        return 0.0


def _bucket_pages_by_domain(
    wiki_dir: Path,
) -> dict[str, list[tuple[Path, float]]]:
    """Group topic + system pages by assigned domain slug.

    Multi-domain pages (via `domains:` list, `tags:` with multiple
    domain slugs, or future extensions) appear in every matching
    bucket. Pages without any assignable domain land in
    `_UNCATEGORIZED_SLUG` so readers still find them. Returns
    `{domain_slug: [(md_file, recency_ts), ...]}` with entries sorted
    newest-first per bucket.
    """
    from src.compile.compiler import _DOMAIN_BY_SLUG
    from src.compile.compiler import _assign_domains
    from src.compile.compiler import _iter_content_pages
    from src.compile.compiler import _read_page

    buckets: dict[str, list[tuple[Path, float]]] = {slug: [] for slug in _DOMAIN_BY_SLUG}
    buckets[_UNCATEGORIZED_SLUG] = []

    for md_file in _iter_content_pages(wiki_dir):
        read = _read_page(md_file)
        if read is None:
            continue
        fm, body = read
        recency = _page_recency_key(fm, md_file)
        slugs = _assign_domains(fm, body)
        if not slugs:
            buckets[_UNCATEGORIZED_SLUG].append((md_file, recency))
            continue
        # _assign_domains only returns slugs validated against
        # _DOMAIN_BY_SLUG, so every slug here is a bucket key.
        for slug in slugs:
            buckets[slug].append((md_file, recency))

    for entries in buckets.values():
        entries.sort(key=lambda e: e[1], reverse=True)
    return buckets


def _render_domain_card(
    slug: str,
    title: str,
    blurb: str,
    entries: list[tuple[Path, float]],
    top_n: int = 3,
) -> list[str]:
    """Render one domain card as markdown lines.

    Cards always emit — an empty domain still gets a header so the home
    page reliably shows all 8 (or 9 with Uncategorized) sections. Each
    entry renders the page slug + the date portion of its recency
    timestamp; empty domains show a single italic placeholder.
    """
    lines = [
        f"## [{title}](domains/{slug}.md)",
        "",
        blurb,
        "",
        "**Top pages**:",
    ]
    if not entries:
        lines.append("- *No pages yet.*")
    else:
        for md_file, recency in entries[:top_n]:
            category = md_file.parent.name
            stamp = datetime.fromtimestamp(recency, tz=UTC).strftime("%Y-%m-%d")
            lines.append(f"- [[{category}/{md_file.stem}]] — updated {stamp}")
    total = len(entries)
    noun = "page" if total == 1 else "pages"
    lines.extend(["", f"<small>{total} {noun} total</small>", ""])
    return lines


def _generate_home(wiki_dir: Path) -> Path:
    """Overwrite `wiki/home.md` with the North-Star 8-domain card layout.

    One card per canonical domain plus an "Uncategorized" card for pages
    without `domain:` / `domains:` frontmatter. Cards show the domain
    blurb, the 3 most-recently-compiled pages (by `last_compiled` DESC,
    falling back to file mtime), and a total count. Runs after
    `_regenerate_domain_hubs` so `domains/<slug>.md` already exists for
    the header links.
    """
    from src.compile.compiler import _DOMAINS
    from src.compile.compiler import _GENERATED_MARKER
    from src.compile.compiler import _atomic_write_text

    buckets = _bucket_pages_by_domain(wiki_dir)

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
        blurb = _DOMAIN_BLURBS.get(slug, "")
        lines.extend(_render_domain_card(slug, title, blurb, buckets.get(slug, [])))

    uncategorized = buckets.get(_UNCATEGORIZED_SLUG, [])
    if uncategorized:
        lines.extend(
            _render_domain_card(
                _UNCATEGORIZED_SLUG,
                _UNCATEGORIZED_TITLE,
                _UNCATEGORIZED_BLURB,
                uncategorized,
            )
        )

    recent = _recent_page_entries(wiki_dir, limit=10)
    lines.extend(["## Recent changes", ""])
    if recent:
        for md_file, mtime in recent:
            category = md_file.parent.name
            stamp = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d")
            lines.append(f"- {stamp} — [[{category}/{md_file.stem}]]")
    else:
        lines.append("*No pages compiled yet.*")

    lines.extend(["", _GENERATED_MARKER, ""])

    fm = {"title": "Home", "page_type": "home", "status": "active"}
    path = wiki_dir / "home.md"
    _atomic_write_text(path, render_with_frontmatter(fm, "\n".join(lines)))
    populated_domains = sum(1 for slug, _t, _k in _DOMAINS if buckets.get(slug))
    logger.info(
        "generated",
        kind="home",
        recent_count=len(recent),
        populated_domains=populated_domains,
        uncategorized=len(uncategorized),
    )
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
