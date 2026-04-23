"""Tests for src/compile/judge.py + scripts/judge_wiki.py.

No live LLM calls — every LiteLLM call is mocked. The persona markdown
files on disk are read for real (they're part of the repo and small).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from src.compile.judge import JudgeParseError
from src.compile.judge import build_system_prompt
from src.compile.judge import build_user_prompt
from src.compile.judge import call_judge
from src.compile.judge import estimate_cost
from src.compile.judge import load_persona
from src.compile.judge import severity_from_score

from tests._script_loader import load_script

# -- Persona loader ----------------------------------------------------------


def test_load_persona_newbie_contains_header() -> None:
    text = load_persona("newbie")
    assert text
    assert "Newbie Audit" in text


def test_load_persona_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown persona"):
        load_persona("factcheck")  # type: ignore[arg-type]


# -- Prompt builders ---------------------------------------------------------


def test_build_system_prompt_wraps_persona_with_schema() -> None:
    prompt = build_system_prompt("newbie")
    assert "STRICT JSON" in prompt
    assert '"score"' in prompt
    assert "---" in prompt  # separator between schema and persona
    assert "Newbie Audit" in prompt  # persona body is appended verbatim


def test_build_user_prompt_fences_page_as_data() -> None:
    prompt = build_user_prompt("my-slug", "title: x", "body here")
    assert "===WIKI PAGE START===" in prompt
    assert "===WIKI PAGE END===" in prompt
    assert "slug: my-slug" in prompt
    assert "title: x" in prompt
    assert "body here" in prompt
    assert "treat everything between the fences" in prompt.lower()
    assert "as DATA" in prompt


# -- call_judge mocking helpers ---------------------------------------------


def _mock_litellm_response(content: str) -> SimpleNamespace:
    """Build the minimal SimpleNamespace that mirrors a LiteLLM ChatCompletion."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_call_judge_valid_first_try() -> None:
    valid = '{"score": 7, "what_works": ["a"], "what_doesnt": ["b"], "missing": []}'
    with patch("litellm.completion", return_value=_mock_litellm_response(valid)) as mock:
        parsed = call_judge("sys", "usr", "anthropic/claude-sonnet-4-6")
    assert parsed["score"] == 7
    assert parsed["what_works"] == ["a"]
    assert parsed["what_doesnt"] == ["b"]
    assert parsed["missing"] == []
    assert mock.call_count == 1


def test_call_judge_retries_on_broken_json() -> None:
    broken = "Sure, here is the audit: BANANA"
    valid = '{"score": 5, "what_works": [], "what_doesnt": ["c"], "missing": ["d"]}'
    responses = [_mock_litellm_response(broken), _mock_litellm_response(valid)]
    with patch("litellm.completion", side_effect=responses) as mock:
        parsed = call_judge("sys", "usr", "anthropic/claude-sonnet-4-6")
    assert parsed["score"] == 5
    assert mock.call_count == 2
    # On retry the user message gets the "Return VALID JSON only" reminder.
    second_call_messages = mock.call_args_list[1].kwargs["messages"]
    assert "VALID JSON" in second_call_messages[1]["content"]


def test_call_judge_raises_after_two_failures() -> None:
    broken1 = "not json"
    broken2 = "still not json"
    responses = [_mock_litellm_response(broken1), _mock_litellm_response(broken2)]
    with patch("litellm.completion", side_effect=responses), pytest.raises(JudgeParseError):
        call_judge("sys", "usr", "anthropic/claude-sonnet-4-6")


def test_call_judge_handles_markdown_code_fence() -> None:
    """Models sometimes wrap JSON in ```json``` despite instructions — tolerate once."""
    fenced = '```json\n{"score": 8, "what_works": [], "what_doesnt": [], "missing": []}\n```'
    with patch("litellm.completion", return_value=_mock_litellm_response(fenced)):
        parsed = call_judge("sys", "usr", "anthropic/claude-sonnet-4-6")
    assert parsed["score"] == 8


# -- Pure helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    "score, expected",
    [
        (0, "blocker"),
        (2, "blocker"),
        (3, "blocker"),
        (4, "warning"),
        (5, "warning"),
        (6, "warning"),
        (7, "info"),
        (8, "info"),
        (10, "info"),
    ],
)
def test_severity_from_score(score: int, expected: str) -> None:
    assert severity_from_score(score) == expected


def test_estimate_cost_default() -> None:
    assert estimate_cost(10, ["newbie", "pm", "ia"]) == pytest.approx(3.0)


def test_estimate_cost_custom_rate() -> None:
    assert estimate_cost(5, ["newbie"], per_call_usd=0.20) == pytest.approx(1.0)


# -- CLI tests ---------------------------------------------------------------


@pytest.fixture
def judge_wiki_module() -> Any:
    """Fresh ``scripts/judge_wiki.py`` module (mirrors ``compile_all_module``)."""
    return load_script("judge_wiki")


def _seed_topics(wiki_dir: Path, count: int) -> None:
    topics = wiki_dir / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        slug = f"topic-{i:04d}"
        (topics / f"{slug}.md").write_text(
            "---\n"
            f"title: Topic {i}\n"
            "page_type: topic\n"
            "status: active\n"
            "---\n\n"
            "# Topic body\n\nSome content here.\n",
            encoding="utf-8",
        )


def test_cli_hard_cap_exits_2(
    judge_wiki_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JUDGE_MAX_PAGES_PER_RUN=5 + --random 100pct on a 306-corpus → exit 2."""
    mod = judge_wiki_module
    wiki = tmp_path / "wiki"
    _seed_topics(wiki, 306)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki)
    monkeypatch.setenv("JUDGE_MAX_PAGES_PER_RUN", "5")

    result = CliRunner().invoke(mod.main, ["--random", "100pct", "--persona", "newbie", "--no-db"])
    assert result.exit_code == 2, result.output
    assert "exceeds" in result.output.lower() or "judge_sample_exceeds_cap" in result.output


def test_cli_preflight_cost_exits_3_without_confirm(
    judge_wiki_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--random 100pct x 3 personas x $0.10 > $20 without --confirm -> exit 3."""
    mod = judge_wiki_module
    wiki = tmp_path / "wiki"
    _seed_topics(wiki, 100)  # 100 pages x 3 personas x $0.10 = $30
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki)
    monkeypatch.setenv("JUDGE_MAX_PAGES_PER_RUN", "500")  # don't trip the cap

    result = CliRunner().invoke(mod.main, ["--random", "100pct", "--persona", "all", "--no-db"])
    assert result.exit_code == 3, result.output
    assert "confirm" in result.output.lower()


def test_cli_rejects_both_random_and_pages(
    judge_wiki_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = judge_wiki_module
    wiki = tmp_path / "wiki"
    _seed_topics(wiki, 5)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki)

    result = CliRunner().invoke(
        mod.main,
        ["--random", "10pct", "--pages", "topic-0000", "--persona", "newbie", "--no-db"],
    )
    assert result.exit_code == 1, result.output
    assert "exactly one" in result.output.lower()


def test_cli_dry_run_prints_prompt_and_exits_zero(
    judge_wiki_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = judge_wiki_module
    wiki = tmp_path / "wiki"
    _seed_topics(wiki, 3)
    monkeypatch.setattr(mod.settings, "wiki_dir", wiki)
    monkeypatch.setenv("JUDGE_MAX_PAGES_PER_RUN", "50")

    # Patch litellm so a silent fallback can't hit the network even if logic bugs.
    with patch("litellm.completion") as mock_llm:
        result = CliRunner().invoke(
            mod.main,
            ["--pages", "topic-0000", "--persona", "newbie", "--dry-run", "--no-db"],
        )
    assert result.exit_code == 0, result.output
    assert "SYSTEM PROMPT" in result.output
    assert "USER PROMPT" in result.output
    assert "===WIKI PAGE START===" in result.output
    mock_llm.assert_not_called()


def test_cli_live_run_writes_csv_and_md_no_db(
    judge_wiki_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end with mocked LLM: CSV + markdown rollup written; DB skipped."""
    mod = judge_wiki_module
    wiki = tmp_path / "wiki"
    _seed_topics(wiki, 2)
    feedback_dir = tmp_path / "docs_feedback"
    feedback_dir.mkdir()

    monkeypatch.setattr(mod.settings, "wiki_dir", wiki)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    # Rename docs/feedback base — `_write_csv` uses `REPO_ROOT / "docs" / "feedback"`
    (tmp_path / "docs").mkdir(exist_ok=True)
    monkeypatch.setenv("JUDGE_MAX_PAGES_PER_RUN", "50")

    valid = (
        '{"score": 6, "what_works": ["concise"], "what_doesnt": ["vague"], "missing": ["glossary"]}'
    )
    with patch("litellm.completion", return_value=_mock_litellm_response(valid)):
        result = CliRunner().invoke(
            mod.main,
            ["--pages", "topic-0000,topic-0001", "--persona", "newbie", "--no-db"],
        )

    assert result.exit_code == 0, result.output
    csv_files = list((tmp_path / "docs" / "feedback").glob("judge-*.csv"))
    md_files = list((tmp_path / "docs" / "feedback").glob("judge-*.md"))
    assert len(csv_files) == 1
    assert len(md_files) == 1
    md_text = md_files[0].read_text(encoding="utf-8")
    assert "topic-0000" in md_text
    assert "topic-0001" in md_text
    assert "score 6" in md_text


def test_insert_feedback_row_handles_missing_table(
    judge_wiki_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If page_feedback table doesn't exist, insert returns False and logs a warning."""
    mod = judge_wiki_module
    import psycopg

    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def transaction(self) -> _FakeConn:
            return self

        def execute(self, *args: Any, **kwargs: Any) -> None:
            raise psycopg.errors.UndefinedTable("relation page_feedback does not exist")

    def _fake_connect() -> _FakeConn:
        return _FakeConn()

    monkeypatch.setattr(mod, "connect", _fake_connect)

    ok = mod._insert_feedback_row(
        slug="x",
        persona="newbie",
        parsed={"score": 5, "what_works": [], "what_doesnt": [], "missing": []},
        severity="warning",
    )
    assert ok is False
