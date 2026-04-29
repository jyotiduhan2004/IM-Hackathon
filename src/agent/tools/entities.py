"""Person-page resolution agent tool.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C). Backed by
`src.wiki.entities` for the per-entity write logic.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel
from pydantic import Field

from src.agent.run_state import _current_raw_paths


class EntityRequest(BaseModel):
    """One person to resolve/create as a person page.

    `email` is REQUIRED and is the identity — slugs are derived from it
    deterministically. An empty or missing `email` is a schema violation
    and will be rejected before the tool body runs.

    Class name kept as ``EntityRequest`` for backwards compatibility with
    the public ``create_entities`` tool; filename + symbol retired with
    the shim in #67.
    """

    email: str = Field(
        ...,
        description=(
            "The person's email address, e.g. 'amit@indiamart.com'. "
            "Case-insensitive. Must appear literally in one of the batch's "
            "raw email files or the tool will refuse."
        ),
        min_length=5,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+",
    )
    display_name: str = Field(
        default="",
        description=(
            "Stub page title for NEW pages. Ignored when the page already "
            "exists. Leave blank if unknown; the tool falls back to the "
            "email as title."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "Bypass the weak-evidence gate. Only set true when THIS TURN "
            "is also writing multi-sentence content about the person. "
            "Merely linking a CC'd name is not enough."
        ),
    )


@tool
def create_entities(entities: list[EntityRequest]) -> dict[str, Any]:
    """Resolve or create people pages for the humans mentioned in this batch.

    Always use this tool for people pages — do NOT invent slugs or
    `write_file` a new entity markdown directly. The tool derives the
    canonical slug from each email deterministically, checks for existing
    pages (by canonical slug OR by legacy display-name slug with
    `email:` frontmatter), and gates new-page creation on evidence
    strength.

    The coordinator injects `raw_paths` for the current batch automatically
    — you only pass the people you want to resolve or create. Each email
    must appear literally in at least one of the batch's raw files; any
    that don't match are refused with `reason: "email_not_in_raw"`.

    Per-entity outcomes:

    - **Existing page** (by canonical or legacy slug): returns
      `{"ok": True, "slug": ..., "created": False, ...}`. Use the
      returned slug in wikilinks. Enrich via `read_file` + `edit_file`.
    - **New page, strong/medium evidence**: writes a stub and returns
      `{"ok": True, "slug": ..., "created": True, "evidence_level": ...}`.
      Strong = email appears in From/To somewhere; medium = CC'd across
      ≥2 distinct threads.
    - **New page, weak evidence** (`force=false`): refuses with
      `reason: "weak_evidence"`. CC-only on one thread doesn't warrant a
      page. Only set `force=true` if you're writing substantive content
      about this person in the same turn.
    - **Invalid email**: refuses with `reason: "invalid_email"` /
      `"email_not_in_raw"`. Do NOT retry with a guessed variant —
      re-read the raw file if you're unsure of the address.

    Args:
        entities: List of `EntityRequest` objects. **Each item MUST have
            a non-empty `email`.** Do not emit empty objects — the
            schema requires `email`. One entry per person; batching 5-30
            people in a single call is normal.

    Returns:
        {"ok": bool, "validated_raw_paths": [...], "results": [...]}.
        `results[i]` has `ok`/`slug`/`created`/`evidence_level` on
        success, or `ok: false` + `reason` + `guidance` on refusal.
    """
    from src.wiki.entities import create_entity_pages

    raw_paths = _current_raw_paths.get()
    if not raw_paths:
        return {
            "ok": False,
            "error": (
                "no raw_paths in batch context — the coordinator is supposed to "
                "inject them before invoking the agent. If you're testing this "
                "tool directly, call create_entity_pages(raw_paths, entities) "
                "instead."
            ),
            "results": [],
        }

    person_dicts = [e.model_dump() for e in entities]
    return create_entity_pages(raw_paths, person_dicts)
