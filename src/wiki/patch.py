"""Section-aware markdown mutation helpers.

Supporting module for `patch_page` in `src.agent.tools.pages`. Keeping the
text-munging algorithm isolated from the @tool wrapper so it's trivial to
unit-test without touching the filesystem.

The shape of the contract is: parse the body as a flat sequence of H2
sections, find the target section by case-insensitive title, replace
(or append) its body, return the rewritten body. Other sections and any
leading preamble stay untouched.
"""

from __future__ import annotations

import re

# `##` at start of line, then at least one space, then a non-empty title.
# Matches GFM H2 exactly — we don't try to cover ATX-closed (`## Title ##`)
# because the wiki corpus doesn't use that style.
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _norm(title: str) -> str:
    """Lowercase + strip — case-insensitive compare for section matching."""
    return title.strip().lower()


def replace_section(body: str, section_title: str, new_content: str) -> tuple[str, str]:
    """Replace the body of an H2 section, or append a new section if missing.

    Args:
        body: Full markdown body (no frontmatter).
        section_title: H2 heading text to match (case-insensitive, trimmed).
        new_content: Replacement body for the section. Callers should NOT
            include the `## <title>` line — it's rewritten for them. On a
            replace, the original heading casing is preserved; on a create,
            ``section_title.strip()`` is used verbatim.

    Returns:
        ``(new_body, action)`` where action is ``"replaced"`` or
        ``"created"``. The rewrite preserves trailing newlines for the
        sections before and after the mutated one.
    """
    target = _norm(section_title)

    # Locate the target H2 and the next H2 (EOF if none).
    target_start = -1
    target_end = len(body)
    next_match_start = -1
    matched_heading_text = ""

    for match in _H2_RE.finditer(body):
        heading = _norm(match.group(1))
        if target_start == -1 and heading == target:
            target_start = match.start()
            matched_heading_text = match.group(1).strip()
            # Default span is to EOF; narrow to the NEXT H2 if any.
            continue
        if target_start != -1:
            next_match_start = match.start()
            target_end = next_match_start
            break

    # Normalize new_content trailing — keep one trailing newline so subsequent
    # sections visually break. Strip surrounding blank-line noise from caller.
    new_section_body = new_content.rstrip("\n")

    if target_start == -1:
        # Append a brand-new section at EOF. Preserve existing trailing
        # newline but add a blank line between pre-existing body and the
        # new section for readability.
        prefix = body.rstrip("\n")
        suffix = f"## {section_title.strip()}\n\n{new_section_body}\n"
        new_body = f"{prefix}\n\n{suffix}" if prefix else suffix
        return new_body, "created"

    # Replace in-place. Preserve the original heading casing so we don't
    # silently rewrite `## Current State` to `## current state` just because
    # the caller typed the section name lowercase.
    pre = body[:target_start]
    post = body[target_end:]
    heading_text = matched_heading_text or section_title.strip()
    replacement = f"## {heading_text}\n\n{new_section_body}\n"
    # If there is a trailing section, ensure a blank line separator between
    # our new section and the next heading.
    if (post and not post.startswith("\n")) or (
        post.startswith("\n") and not post.startswith("\n\n")
    ):
        replacement += "\n"
    new_body = pre + replacement + post
    return new_body, "replaced"
