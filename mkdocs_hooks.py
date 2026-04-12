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


def on_page_markdown(markdown: str, *, page, config, files) -> str:
    """Called by MkDocs for every page before rendering.

    Appends a "## Sources" section with collapsible blocks showing each
    raw source email's content inline.
    """
    import sys
    src_path = str(page.file.src_path)
    print(f"[hook] on_page_markdown called: {src_path}", file=sys.stderr)
    if src_path in ("index.md", "log.md"):
        return markdown
    if not any(
        src_path.startswith(p + "/")
        for p in ("topics", "entities", "systems", "policies", "timelines", "conflicts")
    ):
        return markdown

    fm, body = _extract_frontmatter(markdown)
    sources = fm.get("sources") or []
    if not sources:
        return markdown

    if re.search(r"^##\s+Sources\b", body, flags=re.MULTILINE):
        return markdown

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

    fm_yaml = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, width=120
    ).rstrip()
    return f"---\n{fm_yaml}\n---\n\n{body.rstrip()}\n" + "\n".join(blocks) + "\n"
