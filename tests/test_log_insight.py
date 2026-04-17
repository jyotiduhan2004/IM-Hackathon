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

    def test_leading_slash_on_email_path_autohealed(self) -> None:
        # Bug L — Cycle 6. Agent sees raw/ as /raw/ via virtual-mode FS
        # and passes the virtual path. Coordinator's skip-path matcher
        # compares to messages.raw_path which stores the unrooted form,
        # so /raw/... never correlates. log_insight normalizes AND
        # surfaces the correction in the response so the agent can
        # adjust on the next call.
        with (
            patch("src.db.messages.find_by_raw_path", return_value={"message_id": "abc"}),
            patch("src.db.insights.record", return_value=133) as record,
        ):
            result = _invoke(
                category="already_captured",
                message="Already on [[abc-test]] from the Jan-10 announcement.",
                email_path="/raw/2026-01-13_abc_test_f15459f0.md",
            )
        assert result["ok"] is True
        assert result["id"] == 133
        assert result["auto_corrected"] == {
            "from": "/raw/2026-01-13_abc_test_f15459f0.md",
            "to": "raw/2026-01-13_abc_test_f15459f0.md",
            "note": (
                "email_path normalized (leading slash stripped). The next "
                "call should use the unrooted form directly."
            ),
        }
        # Slash stripped before hitting the DB write.
        call = record.call_args
        assert call.kwargs["email_path"] == "raw/2026-01-13_abc_test_f15459f0.md"

    def test_canonical_path_no_auto_corrected_field(self) -> None:
        # When the agent gets it right, no noise in the response.
        with (
            patch("src.db.messages.find_by_raw_path", return_value={"message_id": "abc"}),
            patch("src.db.insights.record", return_value=1),
        ):
            result = _invoke(
                category="trivial_skip",
                message="OOO auto-reply.",
                email_path="raw/2026-04-15_ooo_abc.md",
            )
        assert "auto_corrected" not in result

    def test_unknown_path_is_logged_but_still_persisted(self) -> None:
        # When DB + FS both disagree the path exists, we persist anyway
        # (the coordinator's batch-end skip-materialization is the
        # authoritative gate, not this tool). We log a warning so the
        # operator can trace drift. Patch `Path.is_file` so the test
        # doesn't depend on what happens to live on disk under the
        # fixture dir at test time.
        with (
            patch("src.db.messages.find_by_raw_path", return_value=None),
            patch("pathlib.Path.is_file", return_value=False),
            patch("src.db.insights.record", return_value=50) as record,
        ):
            result = _invoke(
                category="already_captured",
                message="Covered elsewhere.",
                email_path="raw/bogus-not-in-db.md",
            )
        assert result == {"ok": True, "id": 50}
        record.assert_called_once()

    def test_already_captured_category_accepted_by_validator(self) -> None:
        # 'already_captured' marks substantive emails whose facts are
        # already on the topic page — distinct from 'trivial_skip' which
        # marks non-substantive emails (OOO, one-line confirmations).
        with patch("src.db.insights.record", return_value=99) as record:
            result = _invoke(
                category="already_captured",
                message=(
                    "Email restates Q4 revenue figures already captured on "
                    "[[revenue-q4-2026]] from thread-root message."
                ),
                email_path="raw/2026-04-15_q4_revenue_followup_xyz.md",
            )
        assert result == {"ok": True, "id": 99}
        record.assert_called_once()

    def test_trivial_skip_with_email_path_is_accepted(self) -> None:
        with patch("src.db.insights.record", return_value=100) as record:
            result = _invoke(
                category="trivial_skip",
                message="OOO auto-reply from Alice, no content.",
                email_path="raw/2026-04-15_alice_ooo_xyz.md",
            )
        assert result == {"ok": True, "id": 100}
        record.assert_called_once()

    def test_trivial_skip_without_email_path_is_rejected(self) -> None:
        # Cycle 4 Case #2: agent called log_insight('trivial_skip') with no
        # email_path 22 times in a single run; coordinator couldn't correlate
        # any of them back to messages, so decided-skip batches were mis-marked
        # as failed/pending. Tool must refuse at the boundary.
        with patch("src.db.insights.record") as record:
            result = _invoke(
                category="trivial_skip",
                message="One-line 'Thanks!' reply, no content.",
            )
        assert result["ok"] is False
        assert "email_path" in result["error"]
        assert "trivial_skip" in result["error"]
        record.assert_not_called()

    def test_already_captured_without_email_path_is_rejected(self) -> None:
        with patch("src.db.insights.record") as record:
            result = _invoke(
                category="already_captured",
                message="Facts already on [[some-topic]] from thread root.",
            )
        assert result["ok"] is False
        assert "email_path" in result["error"]
        assert "already_captured" in result["error"]
        record.assert_not_called()

    def test_investigatory_category_without_email_path_still_accepted(self) -> None:
        # email_path is only required for the two skip categories; investigatory
        # insights (topic_merge_candidate, structure_suggestion, etc.) can still
        # be logged without a specific raw email.
        with patch("src.db.insights.record", return_value=5) as record:
            result = _invoke(
                category="topic_merge_candidate",
                message="whatsapp-dashboard and whatsapp-alerts look mergeable",
            )
        assert result == {"ok": True, "id": 5}
        record.assert_called_once()


class TestInsightsRepoListForRun:
    """list_for_run hits real SQL via the test-schema fixture — the previous
    mock-of-itself pattern validated nothing (the import-after-patch rebound
    `real` to the mock, so the assertion tested the mock's call signature,
    not the query that would run against Postgres)."""

    def test_list_for_run_filters_by_run_id(self, db_conn: Any) -> None:
        from src.db import compile_runs as runs_repo
        from src.db import insights as insights_repo

        run_a = runs_repo.start_run(model="test", notes="a")
        run_b = runs_repo.start_run(model="test", notes="b")

        insights_repo.record(
            run_id=run_a,
            category="tool_gap",
            message="run-a first",
        )
        insights_repo.record(
            run_id=run_b,
            category="tool_gap",
            message="run-b decoy",
        )
        insights_repo.record(
            run_id=run_a,
            category="prompt_ambiguity",
            message="run-a second",
        )

        rows = insights_repo.list_for_run(run_a, limit=10)
        messages = {r["message"] for r in rows}
        assert messages == {"run-a first", "run-a second"}

    def test_list_for_run_since_id_filters_out_earlier_rows(self, db_conn: Any) -> None:
        from src.db import compile_runs as runs_repo
        from src.db import insights as insights_repo

        run = runs_repo.start_run(model="test", notes="since_id test")
        first_id = insights_repo.record(run_id=run, category="tool_gap", message="batch 1")
        second_id = insights_repo.record(run_id=run, category="tool_gap", message="batch 2")

        # since_id = first_id → only rows with id > first_id (i.e., second_id).
        rows = insights_repo.list_for_run(run, limit=10, since_id=first_id)
        assert [r["id"] for r in rows] == [second_id]

    def test_max_id_for_run_returns_latest_id(self, db_conn: Any) -> None:
        from src.db import compile_runs as runs_repo
        from src.db import insights as insights_repo

        run = runs_repo.start_run(model="test", notes="max_id test")
        assert insights_repo.max_id_for_run(run) == 0
        first = insights_repo.record(run_id=run, category="tool_gap", message="one")
        second = insights_repo.record(run_id=run, category="tool_gap", message="two")
        assert insights_repo.max_id_for_run(run) == max(first, second)

    def test_already_captured_insert_passes_db_check(self, db_conn: Any) -> None:
        # Guard against schema-vs-code drift. If the test-schema CHECK
        # constraint in conftest.py omits 'already_captured', this insert
        # raises psycopg.errors.CheckViolation and the test fails loudly
        # — exactly the failure mode that bit Cycle 1 for trivial_skip.
        from src.db import compile_runs as runs_repo
        from src.db import insights as insights_repo

        run = runs_repo.start_run(model="test", notes="already_captured test")
        new_id = insights_repo.record(
            run_id=run,
            category="already_captured",
            message="Thread reply restates facts already on [[topic-page]].",
            email_path="raw/2026-04-15_followup_abc.md",
        )
        assert new_id > 0


class TestPromptContainsLogInsightSection:
    def test_log_insight_is_mentioned(self) -> None:
        # After the Tier A wholesale rewrite the guidance is inline in
        # <tool_guidance> rather than a standalone `## When to log_insight`
        # section — just assert the tool is named.
        assert "log_insight" in COMPILER_SYSTEM_PROMPT

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
