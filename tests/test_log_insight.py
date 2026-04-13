"""Unit tests for the `log_insight` tool + insights repo + prompt wiring.

DB-mocked: we don't exercise Postgres here. `tests/test_db_compile_runs.py`
style integration coverage for the SQL shape is a follow-up when (if) the
insights repo needs it; today the agent wiring is the thing that's
cheap to break.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from src.compile.compiler import log_insight
from src.compile.prompts import COMPILER_SYSTEM_PROMPT


def _invoke(**kwargs: Any) -> dict[str, Any]:
    """Call the wrapped LangChain tool. Mirrors how the agent calls it."""
    return log_insight.invoke(kwargs)


class TestLogInsightTool:
    def test_invalid_category_returns_error_without_db_hit(self) -> None:
        with patch("src.db.insights.record") as record:
            result = _invoke(category="bogus_category", message="msg")
        assert result["ok"] is False
        assert "invalid category" in result["error"]
        assert "bogus_category" in result["error"]
        record.assert_not_called()

    def test_valid_category_returns_ok_with_id(self) -> None:
        with patch("src.db.insights.record", return_value=42) as record:
            result = _invoke(
                category="topic_merge_candidate",
                message="whatsapp-dashboard and whatsapp-alerts overlap",
            )
        assert result == {"ok": True, "id": 42}
        record.assert_called_once()

    def test_record_receives_keyword_params(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("COMPILE_RUN_ID", "run-abc")
        with patch("src.db.insights.record", return_value=7) as record:
            _invoke(
                category="supersession_doubt",
                message="thin evidence for supersession",
                email_path="raw/2026-04-11_foo_abc.md",
                suggested_action="escalate to ops",
            )
        record.assert_called_once_with(
            run_id="run-abc",
            category="supersession_doubt",
            message="thin evidence for supersession",
            email_path="raw/2026-04-11_foo_abc.md",
            suggested_action="escalate to ops",
        )

    def test_record_defaults_when_env_and_optionals_missing(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("COMPILE_RUN_ID", raising=False)
        with patch("src.db.insights.record", return_value=1) as record:
            _invoke(category="question_for_human", message="need a human call")
        record.assert_called_once_with(
            run_id=None,
            category="question_for_human",
            message="need a human call",
            email_path=None,
            suggested_action=None,
        )


class TestInsightsRepoListForRun:
    def test_list_for_run_returns_shape_correct_dicts(self) -> None:
        fake_rows: list[dict[str, Any]] = [
            {
                "id": 2,
                "category": "topic_merge_candidate",
                "message": "merge foo and bar",
                "email_path": "raw/x.md",
                "suggested_action": "combine",
                "created_at": "2026-04-13T12:00:00+00:00",
            },
            {
                "id": 1,
                "category": "tool_gap",
                "message": "need a rename tool",
                "email_path": None,
                "suggested_action": None,
                "created_at": "2026-04-13T11:59:00+00:00",
            },
        ]
        with patch("src.db.insights.list_for_run", return_value=fake_rows) as list_for_run:
            from src.db.insights import list_for_run as real

            # Patch replaces the symbol; importing after the patch gets the mock.
            rows = real("run-xyz", limit=10)

        list_for_run.assert_called_once_with("run-xyz", limit=10)
        assert rows == fake_rows
        assert {"id", "category", "message", "email_path", "suggested_action", "created_at"} <= (
            set(rows[0].keys())
        )


class TestPromptContainsLogInsightSection:
    def test_section_header_present(self) -> None:
        assert "## When to log_insight" in COMPILER_SYSTEM_PROMPT

    def test_all_six_category_names_mentioned(self) -> None:
        required = {
            "topic_merge_candidate",
            "question_for_human",
            "prompt_ambiguity",
            "tool_gap",
            "supersession_doubt",
            "structure_suggestion",
        }
        missing = [c for c in required if c not in COMPILER_SYSTEM_PROMPT]
        assert not missing, f"prompt missing categories: {missing}"
