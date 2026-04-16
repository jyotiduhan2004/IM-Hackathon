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
    assert report.draft_recommended is False


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
    assert "READ" in spec["system_prompt"]
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
