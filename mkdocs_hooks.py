"""MkDocs hooks — post-process pages before rendering.

Adds a "Sources" section at the bottom of every wiki page, pulling the
`sources:` list from frontmatter. Each source renders as a collapsible
<details> block showing the raw email's headers + body inline — so you can
verify the compilation against the original without leaving the page.

Frontmatter stays the single source of truth; human-visible citations appear
at render time only.
"""

from __future__ import annotations

import re
from datetime import date
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent


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


def _render_external_badge(fm: dict) -> str:
    """Return the external-contact badge HTML, or empty string.

    Entity pages carry `is_external: true` in frontmatter when the subject is
    not an @indiamart.com address. Reviewers asked for a visible cue so
    external contributors are obvious at a glance from the page title.
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


def _page_metadata_banner(fm: dict) -> str:
    """One-line provenance banner at the top of each page.

    Renders: `N sources · last compiled YYYY-MM-DD · status: current`

    All three fields always render — missing `sources` becomes "0 sources",
    missing `status` becomes "current" (the default), missing `last_compiled`
    becomes "last compiled unknown". Stub pages (no real compilation yet)
    surface `last compiled stub` rather than a fake date so readers can tell
    backfilled-but-empty pages apart from freshly compiled ones.

    YAML parsers coerce bare ISO timestamps into `datetime` objects, so the
    last_compiled normalizer handles strings and datetime/date alike.
    """
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
    if not any(
        src_path.startswith(p + "/")
        for p in ("topics", "entities", "systems", "policies", "timelines", "conflicts")
    ):
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
        h1_idx = -1
        for i, line in enumerate(body_lines):
            if line.startswith("# "):
                h1_idx = i
                break
        if h1_idx >= 0:
            body_lines[h1_idx] = body_lines[h1_idx] + " " + badge_html
            body = "\n".join(body_lines)
        else:
            body = badge_html + "\n\n" + body

    # Inject a metadata banner at the top. Splice right after the h1 when
    # there is one; otherwise prepend (entity pages rely on Material to
    # auto-generate the h1 from `title:`, so there's nothing in the body to
    # splice next to — the banner renders directly under the auto-h1).
    # Skip injection if a banner already exists at the top of the body —
    # MkDocs may invoke this hook more than once during plugin chains.
    banner = _page_metadata_banner(fm)
    body_head = "\n".join(body.splitlines()[:10])
    if "· last compiled " not in body_head:
        body_lines = body.splitlines(keepends=False)
        h1_idx = -1
        for i, line in enumerate(body_lines):
            if line.startswith("# "):
                h1_idx = i
                break
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
        h1_idx = -1
        for i, line in enumerate(body_lines):
            if line.startswith("# "):
                h1_idx = i
                break
        if h1_idx >= 0:
            body_lines.insert(h1_idx + 1, "")
            body_lines.insert(h1_idx + 2, status_html)
            body = "\n".join(body_lines)
        else:
            body = status_html + "\n\n" + body

    sources = fm.get("sources") or []
    if not sources or re.search(r"^##\s+Sources\b", body, flags=re.MULTILINE):
        return body

    # For entity pages, try to recover the person's email so each source can
    # show HOW they appear in it (From/To/CC/body). Frontmatter rarely carries
    # `email:` explicitly; compiler convention is to write "Email: x@y" as
    # the first body line.
    page_email: str | None = fm.get("email") if isinstance(fm.get("email"), str) else None
    if not page_email and fm.get("page_type") == "entity":
        m = re.search(
            r"(?mi)^\s*(?:\*\*)?email(?:\*\*)?[:\s]+"
            r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+)",
            body,
        )
        if m:
            page_email = m.group(1)

    # Cap entity pages with many sources — show newest 10 inside the
    # collapsed Sources block. Sources are stored chronologically
    # oldest-first, so newest = tail.
    is_entity = fm.get("page_type") == "entity"
    show_recent = 10
    cap_at = 20
    sources_to_render = sources
    older_count = 0
    if is_entity and len(sources) > cap_at:
        sources_to_render = sources[-show_recent:]
        older_count = len(sources) - show_recent

    blocks = [
        "",
        "---",
        "",
        '<details markdown="1">',
        f"<summary>📚 Sources ({len(sources)})</summary>",
        "",
    ]
    for src in sources_to_render:
        if not isinstance(src, str):
            continue
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

    return body.rstrip() + "\n" + "\n".join(blocks) + "\n"
