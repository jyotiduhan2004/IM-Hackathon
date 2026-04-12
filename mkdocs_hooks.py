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


def _render_raw_source(raw_path: Path) -> str | None:
    """Render a raw/*.md file as a collapsible <details> block for embedding."""
    if not raw_path.exists():
        return None
    try:
        raw_content = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fm, body = _extract_frontmatter(raw_content)
    subject = fm.get("subject", raw_path.stem)
    sender = fm.get("from", "")
    date = fm.get("date", "")

    # Trim body to first ~2500 chars; email threads can be huge
    body = body.strip()
    truncated = len(body) > 2500
    if truncated:
        body = body[:2500] + "\n\n*[truncated — see raw file]*"

    summary = f"📧 {subject}"
    meta = []
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
            and not lines[i - 1].strip().endswith((":", ",", ";"))
            is False  # odd condition kept for clarity; see below
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


def _page_metadata_banner(fm: dict) -> str:
    """Small metadata banner at the top of each page: last updated, source count."""
    last_compiled = fm.get("last_compiled", "")
    sources_count = len(fm.get("sources") or [])
    status = fm.get("status", "current")
    parts: list[str] = []
    if last_compiled and last_compiled != "stub":
        # Trim fractional seconds for display
        date_part = last_compiled.split("T")[0] if "T" in last_compiled else last_compiled
        parts.append(f"**Last updated:** {date_part}")
    if sources_count:
        parts.append(f"**Sources:** {sources_count}")
    if status != "current":
        parts.append(f"**Status:** {status}")
    if not parts:
        return ""
    return " · ".join(parts) + "\n\n"


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

    # Inject a metadata banner at the top (after the h1)
    banner = _page_metadata_banner(fm)
    if banner:
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

    sources = fm.get("sources") or []
    if not sources or re.search(r"^##\s+Sources\b", body, flags=re.MULTILINE):
        return body

    blocks = ["", "---", "", "## Sources", ""]
    for src in sources:
        if not isinstance(src, str):
            continue
        rendered = _render_raw_source(REPO_ROOT / src)
        if rendered:
            blocks.append(rendered)
            blocks.append("")
        else:
            blocks.append(f"- `{src}` *(file missing)*")

    return body.rstrip() + "\n" + "\n".join(blocks) + "\n"
