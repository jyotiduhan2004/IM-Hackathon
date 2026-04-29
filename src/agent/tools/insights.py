"""log_insight agent tool — record meta-observations during compile.

Extracted from the legacy `src/compile/compiler.py` (Phase 1C).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.agent.run_state import _autoheal_email_path
from src.agent.run_state import _current_raw_paths

_VALID_INSIGHT_CATEGORIES = frozenset(
    {
        "topic_merge_candidate",
        "question_for_human",
        "prompt_ambiguity",
        "tool_gap",
        "supersession_doubt",
        "structure_suggestion",
        "trivial_skip",
        "already_captured",
        # V12 audit fix-C (2026-04-23): escape hatch for the
        # "substantive, not captured, no obvious target page" case the
        # terminal-decision guard surfaced. Before this category the
        # agent either fabricated a topic page or exited silently; now
        # it can declare "this needs human triage" and the coordinator
        # records the skip with a distinct reason humans can grep.
        "insufficient_decision",
    }
)

# Categories that the coordinator uses to mark a message ``skipped``.
# For these the insight MUST name the specific raw path it applies to —
# otherwise the coordinator can't correlate the insight back to a message
# and the decision is silently lost. See Cycle 4 Case #2 audit.
# ``insufficient_decision`` joins the skip set so the batch doesn't sit
# pending waiting for a re-queue that can only produce the same
# "no obvious target" answer — see terminal_decision_guard middleware.
_SKIP_INSIGHT_CATEGORIES = frozenset({"trivial_skip", "already_captured", "insufficient_decision"})


@tool
def log_insight(
    category: str,
    message: str,
    email_path: str | None = None,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    """Record a structured meta-observation during compile.

    Use this when you need to flag something for human review — uncertain
    between page updates, weird thread structure, possible policy
    supersession, missing tool. The coordinator surfaces the top few at
    batch-end in the audit log.

    Args:
        category: One of 'topic_merge_candidate', 'question_for_human',
            'prompt_ambiguity', 'tool_gap', 'supersession_doubt',
            'structure_suggestion', 'trivial_skip', 'already_captured',
            or 'insufficient_decision'.

            Note the semantic split between the three "no page delta"
            categories (all three mark the email ``skipped`` in the
            coordinator):

            - ``trivial_skip``: the email is **not substantive** — e.g.
              a one-line confirmation ("Yes, please"), out-of-office
              auto-reply, calendar ack. There's no content worth
              capturing anywhere.
            - ``already_captured``: the email **is substantive** (real
              stats, decisions, dates), but every fact it carries is
              already on the existing topic page — typically because
              a prior message in the same thread was already compiled.
              No new page delta needed, but the signal is different
              from ``trivial_skip`` and we want to preserve it.
            - ``insufficient_decision``: the email is substantive AND
              not captured elsewhere, but there's no obvious target
              page to land it on. Use sparingly — this means a human
              needs to triage. The terminal-decision guard accepts
              this as a commitment so the batch can exit cleanly
              instead of looping.
        message: 1-2 sentence observation.
        email_path: Raw email path this insight is about (e.g.
            ``raw/2026-04-11_subject_abc.md``). **Required** for
            ``trivial_skip`` and ``already_captured`` — the coordinator
            uses it to materialize the skip. Optional for investigatory
            categories. In single-email batches the path is inferred
            from the coordinator's batch scope when omitted; multi-email
            batches still require explicit selection.
        suggested_action: Optional concrete fix the human could take.

    Returns:
        ``{"ok": True, "id": <int>}`` on success, or
        ``{"ok": False, "error": "..."}`` on invalid category or a
        skip-category call that omitted ``email_path`` in a non-single-
        email batch.
    """
    import os

    from src.db.insights import record

    if category not in _VALID_INSIGHT_CATEGORIES:
        return {
            "ok": False,
            "error": (
                f"invalid category {category!r}; must be one of {sorted(_VALID_INSIGHT_CATEGORIES)}"
            ),
        }

    inferred_from_batch: str | None = None
    if category in _SKIP_INSIGHT_CATEGORIES and not email_path:
        # Self-heal: in a single-email batch the coordinator already knows
        # which email is in scope, so we can infer it instead of looping
        # on the structured error. Multi-email batches still need explicit
        # selection — we can't guess which of N messages the insight is
        # about.
        batch_paths = _current_raw_paths.get() or []
        if len(batch_paths) == 1:
            email_path = batch_paths[0]
            inferred_from_batch = email_path
        else:
            return {
                "ok": False,
                "error": (
                    f"email_path is required for category={category!r} — "
                    f"call log_insight once per email you're skipping, with "
                    f"email_path='raw/YYYY-MM-DD_..._hash.md'. Without it the "
                    f"coordinator can't mark the message skipped and the "
                    f"decision is lost."
                ),
            }

    original_path = email_path
    if email_path and inferred_from_batch is None:
        email_path = _autoheal_email_path(email_path)

    run_id = os.environ.get("COMPILE_RUN_ID")
    new_id = record(
        run_id=run_id,
        category=category,
        message=message,
        email_path=email_path,
        suggested_action=suggested_action,
    )
    result: dict[str, Any] = {"ok": True, "id": new_id}
    if inferred_from_batch is not None:
        result["auto_corrected"] = {
            "inferred_from_batch": inferred_from_batch,
            "note": (
                "email_path inferred from single-email batch scope — pass it explicitly next time."
            ),
        }
    elif original_path and original_path != email_path:
        result["auto_corrected"] = {
            "from": original_path,
            "to": email_path,
            "note": (
                "email_path normalized (leading slash stripped). The next "
                "call should use the unrooted form directly."
            ),
        }
    return result
