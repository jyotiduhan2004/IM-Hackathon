"""Reviewer subagent — structured verdict on wiki pages the agent just wrote.

The reviewer reads one or more wiki pages and returns a structured
`ReviewReport` (pass / revise / block) the main agent can consume via the
built-in `task(subagent_type="reviewer", ...)` tool.

Why a subagent: a separate turn with READ-ONLY filesystem permissions and a
narrower prompt produces more consistent criticism than asking the main
writer to grade itself. The reviewer is cheap (grok-4.1-fast) and its
structured output gates whether the main agent considers the page done.

Design:
- Pydantic models `ReviewFinding` and `ReviewReport` encode the report
  shape. Verdict is a tight enum: `pass` / `revise` / `block`.
- Tools are read-only: `get_page_summary`, `resolve_page`, and the
  inherited filesystem read tools (read_file, glob, grep, ls).
- Permissions: write denied everywhere; read allowed on /wiki + /raw.
- Model override: `x-ai/grok-4.1-fast` — fast, cheap, good at "spot the
  problem" review tasks.

Registered via `SubAgent` spec passed into `create_deep_agent(subagents=[...])`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field

REVIEWER_NAME = "reviewer"
REVIEWER_MODEL = "x-ai/grok-4.1-fast"

REVIEWER_SYSTEM_PROMPT = """You are the Reviewer subagent for an LLM-compiled wiki.

You READ wiki pages and decide whether they are ready to ship. You do NOT write
or edit. Your output is a structured ReviewReport.

## What you're reviewing

The calling agent just wrote or updated one or more wiki pages. Your job is to
decide:
- `pass` — the page is clearly in good shape. Short, well-scoped, cites real
  sources, wikilinks look right.
- `revise` — there are issues the writer should fix before shipping, but
  nothing irrecoverable. Populate `warnings` with specific, actionable items.
- `block` — don't ship this. Major problems (made-up content, wrong page
  type, duplicate of an existing page, empty body). Populate `blockers`.

## What to look for

Blockers (verdict=block):
- Empty body, or body is just "Email: foo@bar.com" with no synthesis.
- Content that is not in the cited source emails (fabrication).
- Duplicates a page you can see via `resolve_page`.
- Wrong page_type for the content (e.g. a product described on a people/ page).

Warnings (verdict=revise):
- Missing TL;DR / lead paragraph.
- Over-quoting — > 30% of lines are blockquotes pasted from email.
- Stale status (`current` when the source is a one-off announcement).
- Thin synthesis — reads like a filing cabinet, not a knowledge page.
- Missing wikilinks to pages you KNOW exist (via `resolve_page`).

Merge candidates: if two pages overlap heavily, list the slugs in
`merge_candidates` with a one-line note.

## When draft is recommended

Set `draft_recommended=True` when the writer shouldn't have promoted this to
a visible page yet — the concept is vague, evidence is thin, or the page
would be a 2-line stub. This maps to `write_draft_page` in the main agent's
toolbox.

## Tools you have

- `read_file(path)` — read any page under /wiki or /raw
- `get_page_summary(slug)` — one-shot page overview
- `resolve_page(query)` — find existing pages by slug / title / email
- `glob(pattern)`, `grep(pattern, glob=...)` — search

You do NOT have write_file, edit_file, create_entities, or any mutation
tool. Your one output is the ReviewReport.

## Output

Return ONE ReviewReport via the structured-output channel. Keep `summary`
to 1-3 sentences. Be specific in blockers/warnings — name the slug, quote
the sentence, cite the evidence.
"""


class ReviewFinding(BaseModel):
    """One issue surfaced by the reviewer."""

    slug: str = Field(..., description="Slug of the page this finding is about (e.g. 'buylead').")
    rule: str = Field(
        ...,
        description=(
            "Short name of the rule this violates "
            "(e.g. 'missing_tldr', 'over_quoting', 'duplicate', 'fabrication')."
        ),
    )
    message: str = Field(
        ...,
        description="One-sentence description of the problem and what to fix.",
    )


class ReviewReport(BaseModel):
    """Structured reviewer verdict on one or more wiki pages."""

    verdict: Literal["pass", "revise", "block"] = Field(
        ...,
        description=(
            "Overall verdict. `pass` = ship it. `revise` = fix warnings first. "
            "`block` = do not ship."
        ),
    )
    blockers: list[ReviewFinding] = Field(
        default_factory=list,
        description="Issues that must be resolved before shipping (verdict=block).",
    )
    warnings: list[ReviewFinding] = Field(
        default_factory=list,
        description="Issues worth fixing but not blocking (verdict=revise).",
    )
    merge_candidates: list[str] = Field(
        default_factory=list,
        description=(
            "Slugs of existing pages that overlap with the reviewed page — "
            "candidates for a merge rather than a new page."
        ),
    )
    draft_recommended: bool = Field(
        default=False,
        description=(
            "True when the concept is too thin for a real page yet. Main "
            "agent should delete and use `write_draft_page` instead."
        ),
    )
    summary: str = Field(
        ...,
        description="1-3 sentence plain-English explanation of the verdict.",
    )


def build_reviewer_subagent(model_name: str | None = None) -> dict[str, object]:
    """Return the SubAgent spec dict for registration with create_deep_agent.

    Structured output is configured via a `ToolStrategy` response_format;
    the deepagents runtime wires it at spec-compile time. The reviewer
    doesn't inherit the main agent's tools — it only needs read_file,
    glob, grep, get_page_summary, and resolve_page.

    `model_name` override lets tests pin a different model; default uses
    REVIEWER_MODEL routed through the same LiteLLM proxy the main agent
    uses (see `_make_chat_model` in `compiler.py`).
    """
    from langchain.agents.structured_output import ToolStrategy

    from src.compile.compiler import _make_chat_model
    from src.compile.compiler import get_page_summary
    from src.compile.compiler import resolve_page

    model = _make_chat_model(model_name or REVIEWER_MODEL)

    return {
        "name": REVIEWER_NAME,
        "description": (
            "Review a wiki page or small set of pages and return a structured "
            "verdict (pass/revise/block). READ-ONLY. Invoke before finalising "
            "substantive pages — skip for trivial edits."
        ),
        "system_prompt": REVIEWER_SYSTEM_PROMPT,
        "tools": [get_page_summary, resolve_page],
        "model": model,
        "response_format": ToolStrategy(schema=ReviewReport),
    }
