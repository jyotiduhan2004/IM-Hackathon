"""Per-page wiki validation primitives.

Provides `validate_page` (and supporting helpers) so the post-batch
coordinator can validate a single page in-process without shelling out.
The CLI wrapper in ``scripts/validate_wiki.py`` imports from here for
the per-page check, while keeping its own wiki-wide checks
(`check_duplicates`, `check_duplicate_suffix_variants`, etc.) local.

Direction: `src/coordinator/post_batch.py` imports `Error` and
`validate_page` from here. Scripts may import too — there is no
reverse dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.utils import split_frontmatter

REQUIRED_FIELDS = {"title", "page_type", "status"}
# Legacy (current/contested) + North-Star (active/superseded/archived). Both
# are accepted during the migration; the rename to active/archived ships in
# Workstream D Slice 2.
VALID_STATUSES = {"current", "superseded", "contested", "active", "archived"}
# Legacy types + North-Star generated-page types (domain hubs, glossary,
# decision stubs, person pages). Generated pages live outside CATEGORY_TO_TYPE
# today, but listing them keeps the validator honest if/when the generators
# emit pages into a categorised subdir.
VALID_PAGE_TYPES = {
    "topic",
    "entity",
    "system",
    "policy",
    "timeline",
    "conflict",
    "index",
    "domain",
    "glossary",
    "decision",
    "person",
    "home",
    "changes",
    "coordinator_notes",
}
CATEGORY_TO_TYPE = {
    "topics": "topic",
    "entities": "entity",
    "people": "person",
    "systems": "system",
    "policies": "policy",
    "timelines": "timeline",
    "conflicts": "conflict",
}

# Gmail thread_id is a 16-char lowercase hex string (see src/db/schema.sql
# threads.thread_id — TEXT but every observed value matches this shape).
# Used for validating `source_threads:` frontmatter entries on wiki pages.
_THREAD_ID_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass
class Error:
    page: Path
    reason: str


class _DuplicateKeyLoader(yaml.SafeLoader):
    """PyYAML loader that raises on duplicate mapping keys.

    Default SafeLoader silently keeps the last value, hiding corruption like
    `last_compiled:` appearing twice. We need to flag those for humans.
    """


def _construct_mapping_strict(loader: yaml.Loader, node: yaml.nodes.MappingNode) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key: {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_strict
)


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Line-aware frontmatter extraction. See src.utils.split_frontmatter."""
    fm_text, body = split_frontmatter(content)
    if not fm_text:
        return {}, body
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def _count_fm_fences(content: str) -> int:
    """Count how many lines are exactly `---` in the raw file content.

    A well-formed page has exactly two: one opening + one closing the
    frontmatter block. Three or more means the file got corrupted (see
    `tech-security-team.md` in the 2026-04-14 audit — a newline split a
    filename across what looked like a second `---\n...\n---` block,
    producing a page with two frontmatter sections that partially parse).
    Zero or one means the frontmatter is broken.
    """
    return sum(1 for line in content.splitlines() if line == "---")


def validate_page(path: Path) -> list[Error]:
    errors: list[Error] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Error(path, f"unreadable: {e}")]

    # Fence count check — must be exactly two `---` lines. Run this before
    # frontmatter parse so corrupted two-block files still surface the
    # concrete fence count rather than a "no parseable YAML" message.
    fence_count = _count_fm_fences(content)
    if fence_count != 2:
        errors.append(
            Error(
                path,
                f"malformed frontmatter: expected 2 --- fences, found {fence_count}",
            )
        )

    fm, body = _extract_frontmatter(content)

    if not fm:
        if not errors:
            errors.append(Error(path, "no parseable YAML frontmatter"))
        return errors

    # Detect orphan body: only last_compiled present means auto-stamp
    # recovered from a broken frontmatter. Caller should re-compile this page.
    if set(fm.keys()) == {"last_compiled"}:
        errors.append(Error(path, "orphan frontmatter (only last_compiled present)"))
        return errors

    # Required fields
    missing = REQUIRED_FIELDS - set(fm.keys())
    if missing:
        errors.append(Error(path, f"missing required fields: {sorted(missing)}"))

    # page_type valid
    pt = fm.get("page_type")
    if pt and pt not in VALID_PAGE_TYPES:
        errors.append(Error(path, f"invalid page_type: {pt!r}"))

    # status valid
    st = fm.get("status")
    if st and st not in VALID_STATUSES:
        errors.append(Error(path, f"invalid status: {st!r}"))

    # page_type matches directory (skip nav-only index pages)
    category = path.parent.name
    want = CATEGORY_TO_TYPE.get(category)
    if want and pt and pt != want and pt != "index":
        errors.append(Error(path, f"in {category}/ but page_type={pt!r}, expected {want!r}"))

    # Systems directory is for products/services — a populated `email:`
    # field means it's actually a human and belongs in entities/ (see
    # issue #43). Hard error — `scripts/audit_systems_entities.py` will
    # relocate these when run with --confirm.
    if category == "systems":
        email = fm.get("email")
        if isinstance(email, str) and email.strip():
            errors.append(
                Error(path, "page has email: field but lives in systems/; move to entities/")
            )

    # `source_threads:` — Phase A U5 page-level citation field. Replaces the
    # per-message `sources:` list (which the agent used to destructively
    # overwrite every batch). Each entry must be a 16-char hex Gmail
    # thread_id; malformed entries are errors regardless of any flag since
    # they represent concrete corruption, not ontology drift.
    raw_threads = fm.get("source_threads")
    if raw_threads is not None:
        if not isinstance(raw_threads, list):
            errors.append(
                Error(path, f"source_threads must be a list, got {type(raw_threads).__name__}")
            )
        else:
            bad: list[str] = []
            for t in raw_threads:
                if not isinstance(t, str) or not _THREAD_ID_RE.match(t):
                    bad.append(repr(t))
            if bad:
                preview = ", ".join(bad[:3])
                more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
                errors.append(
                    Error(
                        path,
                        f"source_threads has {len(bad)} invalid thread_id(s) "
                        f"(expected 16-char hex): {preview}{more}",
                    )
                )

    # Body exists (empty body is suspicious)
    if not body.strip():
        errors.append(Error(path, "empty body"))

    return errors
