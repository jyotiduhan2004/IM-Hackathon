"""MkDocs hooks — post-process pages before rendering.

Adds a "Sources" section at the bottom of every wiki page, pulling the
`sources:` list from frontmatter. Each source renders as a collapsible
<details> block showing the raw email's headers + body inline — so you can
verify the compilation against the original without leaving the page.

Frontmatter is still the default source of truth. Set
``NS_CATALOG_SOURCES=1`` (or ``true``/``yes``) to instead pull sources via
``message_touched_pages JOIN messages`` keyed on the page slug; the
frontmatter path stays the fallback when the DB is unavailable or the slug
has no rows yet.
"""

from __future__ import annotations

import os
import re
from datetime import date
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent


def _catalog_sources_enabled() -> bool:
    """Is the DB-backed sources path turned on?

    Read the env var on every call so tests can toggle via
    ``monkeypatch.setenv`` without reloading the module.
    """
    value = os.environ.get("NS_CATALOG_SOURCES", "").strip().lower()
    return value in {"1", "true", "yes"}


def _fetch_catalog_sources(slug: str) -> list[str] | None:
    """Return `raw_path` strings for a slug, newest-first, or None on failure.

    Isolates the DB import so mkdocs can run in environments that don't
    have the catalog package installed (CI, docs-only builds). Any
    exception from the import or the query path is swallowed and reported
    as None so the caller can fall back to frontmatter.

    Empty result (known slug, zero touches) also returns None — we treat
    "no catalog evidence" the same as "catalog unavailable" so the viewer
    still shows the frontmatter-derived list when the backfill hasn't
    caught up yet.
    """
    try:
        from src.db.touched_pages import get_sources_for_slug
    except ImportError:
        return None
    try:
        rows = get_sources_for_slug(slug)
    except Exception:  # noqa: BLE001 — viewer must never fail the build
        return None
    paths = [row["raw_path"] for row in rows if row.get("raw_path")]
    return paths or None


def _extract_frontmatter(content: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) from a markdown string."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].lstrip("\n")


def _render_raw_source(raw_path: Path, page_email: str | None = None) -> str | None:
    """Render a raw/*.md file as a collapsible <details> block.

    If `page_email` is given (the email address of the wiki page's subject),
    we annotate HOW that person appears in the raw email — From, To, CC, or
    body mention — so it's clear why this email is listed as a source.
    Addresses the "why is this email from someone else on Bharat's page?"
    confusion: Bharat was CC'd, so the email is on his page; the From field
    is someone else.
    """
    if not raw_path.exists():
        return None
    try:
        raw_content = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fm, body = _extract_frontmatter(raw_content)
    subject = fm.get("subject", raw_path.stem)
    sender = fm.get("from", "")
    to_list = fm.get("to") or []
    cc_list = fm.get("cc") or []
    date = fm.get("date", "")

    # Where does the page's owner show up? (From / To / CC / body mention)
    role_tag = ""
    if page_email:
        pe = page_email.lower()
        if pe in sender.lower():
            role_tag = "✍️ Sent by this person"
        elif any(pe in (t or "").lower() for t in to_list):
            role_tag = "📬 Sent to this person"
        elif any(pe in (c or "").lower() for c in cc_list):
            role_tag = "📋 CC'd"
        elif pe in body.lower():
            role_tag = "💬 Mentioned in body"

    body = body.strip()
    truncated = len(body) > 2500
    if truncated:
        body = body[:2500] + "\n\n*[truncated — see raw file]*"

    summary = f"📧 {subject}"
    meta: list[str] = []
    if role_tag:
        meta.append(role_tag)
    if sender:
        meta.append(f"**From:** {sender}")
    if date:
        meta.append(f"**Date:** {date}")
    meta_line = " · ".join(meta) if meta else ""

    return (
        f'<details markdown="1">\n'
        f"<summary>{summary}</summary>\n\n"
        f"{meta_line}\n\n"
        f"```\n{body}\n```\n"
        f"</details>"
    )


def _fix_list_gaps(body: str) -> str:
    """Insert a blank line before any bullet/numbered list when the preceding
    line is text. MkDocs' strict markdown parser treats `text\n- item` as a
    single paragraph with literal dashes rather than a list. Writing good
    markdown requires a blank line before the list.
    """
    lines = body.splitlines(keepends=False)
    out: list[str] = []
    list_start = re.compile(r"^[ \t]*([-*+]\s|\d+\.\s)")
    for i, line in enumerate(lines):
        if (
            i > 0
            and list_start.match(line)
            and lines[i - 1].strip() != ""
            and not list_start.match(lines[i - 1])
            and lines[i - 1].strip().endswith((":", ",", ";"))
            is not False  # odd condition kept for clarity; see below
        ):
            pass  # unreachable branch — see simpler version below
        out.append(line)

    # Simpler rewrite: walk and emit a blank when previous non-blank line is
    # text AND current is a list item AND previous is NOT a list item
    out = []
    prev = ""
    for line in lines:
        is_list = bool(list_start.match(line))
        prev_is_list = bool(list_start.match(prev)) if prev.strip() else False
        if is_list and prev.strip() != "" and not prev_is_list:
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out) + ("\n" if body.endswith("\n") else "")


def _find_h1_index(lines: list[str]) -> int:
    """Return the index of the first ``# H1`` line, or ``-1`` if none."""
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return i
    return -1


def _render_external_badge(fm: dict) -> str:
    """Return the external-contact badge HTML, or empty string.

    Entity / person pages carry `is_external: true` in frontmatter when the
    subject is not an @indiamart.com address. Reviewers asked for a visible
    cue so external contributors are obvious at a glance from the page title.
    """
    if not fm.get("is_external"):
        return ""
    return '<span class="ext-badge" title="External contact (not @indiamart.com)">external</span>'


# Status → (label, css key). North Star values are active/superseded/archived;
# legacy `current` maps to the active pill, legacy `contested` keeps its own
# color until Week-2 backfill replaces it.
_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "active": ("Active", "active"),
    "superseded": ("Superseded", "superseded"),
    "archived": ("Archived", "archived"),
    "current": ("Active", "active"),
    "contested": ("Contested", "contested"),
}


def _render_status_badge(fm: dict) -> str:
    """Return the status pill HTML, or empty string if status is missing/unknown."""
    raw_status = fm.get("status")
    if not isinstance(raw_status, str):
        return ""
    entry = _STATUS_LABELS.get(raw_status)
    if entry is None:
        return ""
    label, css_key = entry
    return f'<span class="ns-status ns-status-{css_key}">{label}</span>'


# Three ref shapes we rewrite — each captures the filename in group 1. The
# link pattern uses a `(?<!!)` lookbehind so it doesn't double-match the
# markdown image form, which shares the `[…](…)` tail.
_ATTACHMENT_REWRITES: tuple[re.Pattern[str], ...] = (
    re.compile(r"!\[[^\]]*\]\(raw/attachments/([^\s\")]+)\)"),  # ![alt](raw/attachments/x.png)
    re.compile(r"(?<!!)\[[^\]]*\]\(raw/attachments/([^\s\")]+)\)"),  # [text](raw/attachments/x.pdf)
    re.compile(  # <img src="raw/attachments/x.jpg" …>
        r"""<img\b[^>]*\bsrc=["']raw/attachments/([^"']+)["'][^>]*/?>""",
        flags=re.IGNORECASE,
    ),
)


def _attachment_marker(match: re.Match[str]) -> str:
    """Inline marker shown in place of an excluded attachment reference.

    The viewer container excludes `raw/attachments/` (see `.dockerignore`)
    so live wiki pages would render broken images / dead links without a
    cue. We preserve the filename so curious readers know what was elided.
    """
    filename = match.group(1)
    return (
        f"> 📎 *attachment `{filename}` not published on the viewer* "
        "([why?](https://github.com/indiamart-ai/email-knowledge-base/issues/46))"
    )


def _replace_attachment_refs(body: str) -> str:
    """Swap every `raw/attachments/...` ref in `body` with a visible marker."""
    for pattern in _ATTACHMENT_REWRITES:
        body = pattern.sub(_attachment_marker, body)
    return body


def _page_metadata_banner(fm: dict, *, sources_count_override: int | None = None) -> str:
    """One-line provenance banner at the top of each page.

    Renders: `N sources · last compiled YYYY-MM-DD · status: current`

    All three fields always render — missing `sources` becomes "0 sources",
    missing `status` becomes "current" (the default), missing `last_compiled`
    becomes "last compiled unknown". Stub pages (no real compilation yet)
    surface `last compiled stub` rather than a fake date so readers can tell
    backfilled-but-empty pages apart from freshly compiled ones.

    YAML parsers coerce bare ISO timestamps into `datetime` objects, so the
    last_compiled normalizer handles strings and datetime/date alike.

    When `sources_count_override` is not None, uses that value instead of the
    frontmatter length. Caller passes this when the Sources block is rendered
    from a different source than the frontmatter (e.g. catalog-driven mode
    under `NS_CATALOG_SOURCES=1`) so the banner count matches the block.
    """
    if sources_count_override is not None:
        sources_count = sources_count_override
    else:
        sources_raw = fm.get("sources") or []
        sources_count = len(sources_raw) if isinstance(sources_raw, list) else 0

    last_compiled_str = _format_last_compiled(fm.get("last_compiled"))

    status = fm.get("status") or "current"

    noun = "source" if sources_count == 1 else "sources"
    line = f"{sources_count} {noun} · last compiled {last_compiled_str} · status: {status}"
    return line + "\n\n"


def _format_last_compiled(value: object) -> str:
    """Normalize `last_compiled` for display. Accepts strings, datetime, date,
    None, or stub markers — always returns a short printable string.
    """
    if value is None or value == "":
        return "unknown"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if text in ("stub", "stub-backfilled"):
        return text
    # Bare ISO strings like "2026-04-13T10:30:00+00:00" — keep only the date.
    return text.split("T")[0] if "T" in text else text


def on_page_markdown(markdown: str, *, page, config, files) -> str:
    """Called by MkDocs for every page before rendering.

    Normalizes markdown (blank line before lists) then appends a "## Sources"
    section with collapsible blocks showing each raw source email's content
    inline.
    """
    src_path = str(page.file.src_path)
    if src_path in ("index.md", "log.md"):
        return markdown
    # Decorate: legacy 6 dirs + new north-star dirs + generated top-level pages.
    # `log.md` (legacy chronological log) stays skipped above; `changes.md`
    # (new generated changelog) gets decorated via the top-level allowlist.
    # `people/` is accepted alongside `entities/` during the C1 migration.
    in_wiki_dir = any(
        src_path.startswith(p + "/")
        for p in (
            "topics",
            "entities",
            "people",
            "systems",
            "policies",
            "timelines",
            "conflicts",
            "domains",
            "decisions",
        )
    )
    if not in_wiki_dir and src_path not in {"home.md", "glossary.md", "changes.md"}:
        return markdown

    # MkDocs strips YAML frontmatter before calling this hook; the parsed
    # metadata lives on page.meta, and `markdown` is body-only.
    fm = dict(page.meta) if hasattr(page, "meta") and page.meta else {}
    body = markdown

    # Fix markdown rendering issues at the source
    body = _fix_list_gaps(body)

    # Swap excluded `raw/attachments/...` refs for a visible marker — the
    # viewer container ships without those binaries so a literal img/link
    # would render as a broken icon with no explanation. See issue #46.
    body = _replace_attachment_refs(body)

    # Inject an external-contact badge near the title when flagged.
    # Entity pages rely on MkDocs Material to auto-generate the h1 from
    # frontmatter, so there's no h1 line in the body to splice into — we
    # prepend the badge paragraph which renders right under the auto h1.
    # Pages with an explicit h1 get the badge appended to that line so it
    # sits next to the title visually.
    badge_html = _render_external_badge(fm)
    if badge_html:
        body_lines = body.splitlines(keepends=False)
        h1_idx = _find_h1_index(body_lines)
        if h1_idx >= 0:
            body_lines[h1_idx] = body_lines[h1_idx] + " " + badge_html
            body = "\n".join(body_lines)
        else:
            body = badge_html + "\n\n" + body

    # Resolve the sources list upfront (catalog-driven or frontmatter) so the
    # banner's count matches whatever the Sources block will render below.
    # Catalog path (flag ON) queries message_touched_pages and returns a
    # newest-first list. Frontmatter path (default or catalog miss/error)
    # is oldest-first by compiler convention.
    catalog_sources: list[str] | None = None
    if _catalog_sources_enabled():
        catalog_sources = _fetch_catalog_sources(Path(src_path).stem)

    if catalog_sources is not None:
        sources_list = catalog_sources
        newest_first = True
    else:
        sources_list = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        newest_first = False

    # Inject a metadata banner at the top. Splice right after the h1 when
    # there is one; otherwise prepend (entity pages rely on Material to
    # auto-generate the h1 from `title:`, so there's nothing in the body to
    # splice next to — the banner renders directly under the auto-h1).
    # Skip injection if a banner already exists at the top of the body —
    # MkDocs may invoke this hook more than once during plugin chains.
    banner = _page_metadata_banner(fm, sources_count_override=len(sources_list))
    body_head = "\n".join(body.splitlines()[:10])
    if "· last compiled " not in body_head:
        body_lines = body.splitlines(keepends=False)
        h1_idx = _find_h1_index(body_lines)
        if h1_idx >= 0:
            body_lines.insert(h1_idx + 1, "")
            body_lines.insert(h1_idx + 2, banner.rstrip())
            body = "\n".join(body_lines)
        else:
            body = banner + body

    # Inject a colored status pill immediately under the h1 — must run AFTER
    # the banner so the pill sits closest to the title (visual precedence:
    # H1 → pill → banner → body). Idempotency mirrors the banner's.
    status_html = _render_status_badge(fm)
    if status_html and "ns-status-" not in "\n".join(body.splitlines()[:10]):
        body_lines = body.splitlines(keepends=False)
        h1_idx = _find_h1_index(body_lines)
        if h1_idx >= 0:
            body_lines.insert(h1_idx + 1, "")
            body_lines.insert(h1_idx + 2, status_html)
            body = "\n".join(body_lines)
        else:
            body = status_html + "\n\n" + body

    if not sources_list or re.search(r"^##\s+Sources\b", body, flags=re.MULTILINE):
        return body

    # For entity/person pages, try to recover the person's email so each
    # source can show HOW they appear in it (From/To/CC/body). Frontmatter
    # rarely carries `email:` explicitly; compiler convention is to write
    # "Email: x@y" as the first body line. Both page_types are treated the
    # same during the C1 migration.
    page_email: str | None = fm.get("email") if isinstance(fm.get("email"), str) else None
    if not page_email and fm.get("page_type") in ("entity", "person"):
        m = re.search(
            r"(?mi)^\s*(?:\*\*)?email(?:\*\*)?[:\s]+"
            r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+)",
            body,
        )
        if m:
            page_email = m.group(1)

    sources_block = _render_sources_block(
        sources_list,
        page_email=page_email,
        is_person_page=fm.get("page_type") in ("entity", "person"),
        newest_first=newest_first,
    )
    return body.rstrip() + "\n" + sources_block + "\n"


def _render_sources_block(
    sources_list: list[str],
    *,
    page_email: str | None,
    is_person_page: bool,
    newest_first: bool,
) -> str:
    """Render the collapsible `## Sources` block as a string.

    `sources_list` is ordered; `newest_first=True` means element 0 is the
    newest (catalog path). Frontmatter convention is oldest-first, so the
    entity/person-page "show newest 10" cap slices the tail (or head, when
    newest_first). Keeping both paths in one renderer avoids drift between
    the flag-on and flag-off output.
    """
    show_recent = 10
    cap_at = 20
    older_count = 0
    sources_to_render = sources_list
    if is_person_page and len(sources_list) > cap_at:
        if newest_first:
            sources_to_render = sources_list[:show_recent]
        else:
            sources_to_render = sources_list[-show_recent:]
        older_count = len(sources_list) - show_recent

    blocks = [
        "",
        "---",
        "",
        '<details markdown="1">',
        f"<summary>📚 Sources ({len(sources_list)})</summary>",
        "",
    ]
    for src in sources_to_render:
        rendered = _render_raw_source(REPO_ROOT / src, page_email)
        if rendered:
            blocks.append(rendered)
            blocks.append("")
        else:
            blocks.append(f"- `{src}` *(file missing)*")

    if older_count:
        blocks.append("")
        blocks.append(f"> *+{older_count} older sources not shown — expand above to see all.*")

    blocks.append("")
    blocks.append("</details>")
    return "\n".join(blocks)
