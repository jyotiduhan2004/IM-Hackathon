"""Unit tests for src/compile/reviewer.py.

Covers the spec shape (name/description/system_prompt/tools/model/
response_format) and the Pydantic schema. End-to-end invocation of the
subagent is exercised by the live compile recipe.
"""

from __future__ import annotations

import pytest
from langchain.agents.structured_output import ToolStrategy
from src.compile.reviewer import REVIEWER_MODEL
from src.compile.reviewer import REVIEWER_NAME
from src.compile.reviewer import ReviewFinding
from src.compile.reviewer import ReviewReport
from src.compile.reviewer import build_reviewer_subagent


def test_review_finding_validates_required_fields() -> None:
    finding = ReviewFinding(
        slug="buylead",
        rule="missing_tldr",
        message="No TL;DR in the first 500 chars of body.",
    )
    assert finding.slug == "buylead"
    assert finding.rule == "missing_tldr"


def test_review_report_pass_with_empty_issues_is_valid() -> None:
    report = ReviewReport(verdict="pass", summary="Page looks good.")
    assert report.verdict == "pass"
    assert report.blockers == []
    assert report.warnings == []
    assert report.merge_candidates == []
    assert report.editorial_notes == []
    assert report.draft_recommended is False


def test_review_report_accepts_editorial_notes_with_free_form_rule() -> None:
    # The editor upgrade: reviewer can surface free-form observations
    # without being forced into block/revise, AND coin rule names
    # outside the canonical list when specificity helps the writer.
    report = ReviewReport(
        verdict="revise",
        warnings=[
            ReviewFinding(
                slug="qdrant-vector-recommendations-poc",
                rule="cta-decline-contradicts-ctr-claim",
                message=(
                    "Early Impact claims +7% CTR uplift but the Call Clicks "
                    "column shows 198→202 and Enq Clicks dropped 1656→1619. "
                    "The uplift is driven by a PV drop, not CTA rise."
                ),
            ),
        ],
        editorial_notes=[
            "Scaling Decision names 5 subcategories planned but only 1 specified; dropped thread?",
            "Open Questions has grown across 3 reviews with no items resolved — status-tracking gap.",
        ],
        summary="Two editorial calls + one factual-tension warning.",
    )
    assert report.verdict == "revise"
    assert len(report.warnings) == 1
    assert report.warnings[0].rule == "cta-decline-contradicts-ctr-claim"
    assert len(report.editorial_notes) == 2


def test_review_report_block_with_blockers() -> None:
    report = ReviewReport(
        verdict="block",
        blockers=[ReviewFinding(slug="x", rule="fabrication", message="Not in source.")],
        summary="Do not ship — fabricated content.",
    )
    assert report.verdict == "block"
    assert len(report.blockers) == 1


def test_review_report_verdict_is_restricted() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ReviewReport(verdict="approve", summary="x")  # type: ignore[arg-type]


def test_build_reviewer_subagent_returns_spec_shape() -> None:
    spec = build_reviewer_subagent()
    assert spec["name"] == REVIEWER_NAME
    assert isinstance(spec["description"], str) and spec["description"].strip()
    assert isinstance(spec["system_prompt"], str)
    # The reviewer is read-only — prompt must convey this. "EDITOR"
    # (per the editor-upgrade rewrite) or "READ" (legacy phrasing)
    # both count.
    sp = spec["system_prompt"]
    assert "EDITOR" in sp or "READ" in sp
    tools = spec["tools"]
    assert isinstance(tools, list)
    tool_names = {getattr(t, "name", None) for t in tools}
    assert "get_page_summary" in tool_names
    assert "resolve_page" in tool_names
    # No write tools should leak.
    assert not any(
        name in {"write_file", "edit_file", "create_entities"} for name in tool_names if name
    )
    # Model should be a BaseChatModel instance (ready for deepagents).
    from langchain_core.language_models.chat_models import BaseChatModel

    assert isinstance(spec["model"], BaseChatModel)
    # Response format should use ToolStrategy with ReviewReport.
    assert isinstance(spec["response_format"], ToolStrategy)


def test_build_reviewer_subagent_honours_model_override() -> None:
    # If we pass a model override it should be used. We can't easily
    # inspect the resolved model's name string across LangChain versions,
    # so just assert it still produces a valid spec.
    spec = build_reviewer_subagent(model_name=REVIEWER_MODEL)
    assert spec["name"] == REVIEWER_NAME


def test_reviewer_prompt_scopes_to_observable_surface() -> None:
    # Codex audit P2 (2026-04-17): reviewer prompt previously told the
    # reviewer to pass when the agent had logged `trivial_skip` /
    # `already_captured` insights — but the reviewer has no tool
    # access to the insight log or agent transcript. The rule asked
    # for state the reviewer can't see.
    #
    # Updated guidance: reviewer is scoped to the file it can
    # `read_file`. If the main agent took a skip path, it never
    # invokes the reviewer, so the rule was moot anyway.
    spec = build_reviewer_subagent()
    prompt = spec["system_prompt"]
    assert isinstance(prompt, str)
    # Must teach the scope limitation (cannot see transcript / insight log).
    assert (
        "CANNOT see the agent's transcript" in prompt
        or "cannot see the agent's transcript" in prompt.lower()
    )
    # Must tell the reviewer not to penalise for absent work it can't verify.
    lowered = prompt.lower()
    assert "no way to confirm" in lowered or "only what's in the file" in lowered


def test_reviewer_prompt_uses_new_status_vocabulary() -> None:
    # Codex audit P2: legacy `status: current` references removed.
    # Writers emit active / superseded / archived only; reviewer
    # guidance must not mention the retired vocabulary in a way that
    # would teach the reviewer to flag valid active pages as stale.
    spec = build_reviewer_subagent()
    prompt = spec["system_prompt"]
    # Legacy vocab must not appear as a positive example. The rule
    # may reference "current" in the context of explicitly retired.
    # Specifically the old `stale_status — "current" on a one-off`
    # pattern must be gone.
    assert "stale_status` — `current` on a one-off" not in prompt
    # New stale-check rule exists and names active/superseded/archived.
    assert "stale_page" in prompt
    assert "active" in prompt and "superseded" in prompt and "archived" in prompt


def test_reviewer_prompt_frames_reviewer_as_editor_not_linter() -> None:
    # The editor upgrade: prompt must tell the reviewer it's reading
    # at four levels (narrative, evidence, reader, structure) and
    # that rules are examples, not an exhaustive checklist. Without
    # this framing the reviewer stays bounded to the listed rules
    # and misses content-level judgement calls.
    spec = build_reviewer_subagent()
    prompt = spec["system_prompt"]
    assert isinstance(prompt, str)
    # Four reading levels named.
    for level in ("Narrative", "Evidence", "Reader", "Structure"):
        assert level in prompt, f"reviewer prompt missing reading level: {level}"
    # Explicit "editor not linter" framing.
    assert "EDITOR, not a linter" in prompt or "editor, not a linter" in prompt.lower()
    # Rules are examples, not exhaustive.
    assert "not an exhaustive list" in prompt or "examples, not an exhaustive" in prompt
    # Editorial notes escape hatch for things that don't map to
    # block/revise.
    assert "editorial_notes" in prompt


def test_reviewer_prompt_teaches_filing_cabinet_thread_subject_signal() -> None:
    """v11-U7: `filing_cabinet` rule must name the THREAD-SUBJECT
    TEMPLATING signal — without concrete examples (Launch Announcement,
    Bug report, QA Testing Results, etc.) the rule is too fuzzy to fire
    on the failure pattern Cycle 10 surfaced (3/3 topic pages with zero
    canonical H2s)."""
    spec = build_reviewer_subagent()
    prompt = spec["system_prompt"]
    assert isinstance(prompt, str)
    assert "filing_cabinet" in prompt
    # The expanded rule must call out thread-subject templating
    # explicitly so the reviewer recognises the pattern.
    assert "THREAD-SUBJECT" in prompt or "thread-subject" in prompt.lower()
    # Concrete examples from the audit so the rule has anchors to fire on.
    examples = ("Launch Announcement", "Bug report", "QA Testing Results")
    present = [ex for ex in examples if ex in prompt]
    assert len(present) >= 2, (
        f"reviewer prompt must name at least 2 thread-subject H2 examples; got {present}"
    )


def test_reviewer_prompt_teaches_structure_mismatch_rule() -> None:
    """v11-U7: new `structure_mismatch` rule. Reviewer evaluates whether
    the chosen H2 structure fits the page — flag (a) zero canonical H2s
    + thread-subject templating, PASS (b) coherent custom structures."""
    spec = build_reviewer_subagent()
    prompt = spec["system_prompt"]
    assert isinstance(prompt, str)
    assert "structure_mismatch" in prompt
    # The rule must teach BOTH flavors so the reviewer doesn't
    # over-trigger on legitimate alternative structures.
    lowered = prompt.lower()
    assert "canonical" in lowered
    assert "alternative structure" in lowered or "different shape" in lowered


def test_reviewer_prompt_teaches_structural_integrity_checks() -> None:
    # Deep-audit follow-up: the reviewer must flag edit_file corruption
    # patterns the deterministic coordinator can't reliably catch. Four
    # rules, each with a concrete BAD example drawn from the
    # 2026-04-17 deep audit:
    #   - duplicate_section       (photosearch: dup H2 at lines 96 + 261)
    #   - dated_h2               (seo-rework: Bug F filing-cabinet H2s)
    #   - orphan_fragment        (bl-purchase: "d impact data" tail)
    #   - table_boundary_lost    (smart-orchestrator: TP rows in wrong H2)
    # Without all four the reviewer can't surface the corruption classes
    # the LLM writer is producing today.
    spec = build_reviewer_subagent()
    prompt = spec["system_prompt"]
    assert isinstance(prompt, str)
    for rule in ("duplicate_section", "dated_h2", "orphan_fragment", "table_boundary_lost"):
        assert rule in prompt, f"reviewer prompt missing rule: {rule}"
    # Concrete BAD example anchors — these are the cases the reviewer
    # saw in the corpus; removing them would turn the rule into a
    # vague heuristic.
    # Prompt text wraps at 100 chars, so "SEO Recommendations\n  (Amarinder"
    # can span lines. Check the two tokens separately.
    assert "Feedback Frequency Design" in prompt  # duplicate-section example
    assert "SEO Recommendations" in prompt and "Amarinder Dhaliwal" in prompt  # dated_h2
    assert "d impact data" in prompt  # orphan_fragment example
    # Line wraps can split "Meeting Minutes" — check both tokens.
    assert "Meeting" in prompt and "Minutes" in prompt  # table_boundary_lost
    # Cross-level rule upgrade: duplicate_section must match across
    # heading depths, not just H2==H2. See
    # docs/audits/cycle-7-case-photosearch-duplicate-section.md
    # (photosearch has H3 + H2 with identical title, same bug shape).
    assert "ANY LEVEL" in prompt or "any level" in prompt or "AT ANY LEVEL" in prompt
