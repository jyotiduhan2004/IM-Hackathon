"""Shared utilities — frontmatter parsing done right.

`content.split("---", 2)` looks tempting but breaks when any content
contains `---` (email subjects with "Informational---Something" produce
raw filenames like `_informational---transforming-sonarqube.md` that end
up in wiki `sources:` lists, confusing naive splits). Always use
`_split_frontmatter` from here.
"""

from __future__ import annotations

from typing import Any

import yaml


def split_frontmatter(content: str) -> tuple[str, str]:
    """Return (yaml_text, body). Empty yaml_text if frontmatter is missing/malformed.

    `---` is a YAML frontmatter delimiter only when it's on its own line,
    matching how real YAML frontmatter is written. Prior bugs: naive split
    treated `---` inside filenames as a delimiter, destroying pages.
    """
    if not content.startswith("---"):
        return "", content
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return "", content
    fm_lines: list[str] = []
    body_start = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            body_start = i + 1
            break
        fm_lines.append(line)
    if body_start == -1:
        return "", content
    body = "".join(lines[body_start:]).lstrip("\n")
    return "".join(fm_lines), body


def extract_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter; return {} if missing or malformed."""
    fm_text, _ = split_frontmatter(content)
    if not fm_text:
        return {}
    try:
        fm = yaml.safe_load(fm_text)
        return fm if isinstance(fm, dict) else {}
    except yaml.YAMLError:
        return {}


def extract_body(content: str) -> str:
    """Return the body (everything after frontmatter)."""
    _, body = split_frontmatter(content)
    return body


def render_with_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Render markdown with YAML frontmatter + body."""
    yaml_block = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, width=120
    ).rstrip()
    return f"---\n{yaml_block}\n---\n\n{body}"
