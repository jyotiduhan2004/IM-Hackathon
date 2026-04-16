"""Unit tests for src/observability/langfuse_scores.py.

Pure-function tests over synthetic Langfuse trace observations + a
mock Langfuse client. No real Langfuse calls; no live trace fetches.
The DB-backed ``content_page_cited`` lookup is exercised against the
test schema via the standard fixtures.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any
from typing import cast
from unittest.mock import MagicMock

import psycopg
import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.observability import langfuse_scores  # noqa: E402
from src.observability.langfuse_scores import _classify_compile_outcome  # noqa: E402
from src.observability.langfuse_scores import _content_cited_lookup  # noqa: E402
from src.observability.langfuse_scores import _extract_metric_values  # noqa: E402
from src.observability.langfuse_scores import _is_content_page_cited  # noqa: E402
from src.observability.langfuse_scores import emit_scores_for_run  # noqa: E402
from src.observability.langfuse_scores import emit_scores_for_trace  # noqa: E402


def _mk_tool_obs(
    name: str, output: str = "", inputs: str = "", level: str = "DEFAULT"
) -> dict[str, Any]:
    return {"type": "TOOL", "name": name, "output": output, "input": inputs, "level": level}


# ============================ _extract_metric_values ===========================


def test_extract_metric_values_all_signals_present() -> None:
    """One trace with auto-correct, early todos, verdict=pass, gate reject."""
    observations = [
        _mk_tool_obs("write_todos", "[]"),
        _mk_tool_obs(
            "read_file",
            "(auto_corrected_from='/.claude/raw/x.md' -> auto_corrected_to='/raw/x.md')",
        ),
        _mk_tool_obs("task", '{"verdict": "pass", "blockers": []}'),
        _mk_tool_obs(
            "check_my_work",
            "Rejected: call check_my_work only after you've successfully edited content-page files",
        ),
    ]
    values = _extract_metric_values(observations)
    assert values["auto_corrected"] is True
    assert values["wrote_todos_early"] is True
    assert values["reviewer_verdict"] == "pass"
    assert values["gate_rejected_check_my_work"] == 1


def test_extract_metric_values_passive_defaults() -> None:
    """Pre-Tier-A/D1 trace: every signal stays at its off value."""
    observations = [
        _mk_tool_obs("ls", "[]"),
        _mk_tool_obs("read_file", "raw content"),
        _mk_tool_obs("write_file", "ok"),
    ]
    values = _extract_metric_values(observations)
    assert values["auto_corrected"] is False
    assert values["wrote_todos_early"] is False
    assert values["reviewer_verdict"] is None
    assert values["gate_rejected_check_my_work"] == 0


def test_extract_metric_values_skips_unnamed_tool_events() -> None:
    """Unnamed TOOL observations don't shift the wrote_todos_early window.

    Mirrors `_extract_trace_metrics` in the scorecard, so per-trace
    Score values match the scorecard's aggregated rates.
    """
    observations: list[dict[str, Any]] = [
        {"type": "TOOL", "name": "", "output": "", "input": ""},
        {"type": "TOOL", "name": None, "output": "", "input": ""},
        _mk_tool_obs("write_todos", "[]"),  # would be index 2 if unnamed counted
    ]
    values = _extract_metric_values(observations)
    assert values["wrote_todos_early"] is True


def test_extract_metric_values_gate_rejection_counted_per_call() -> None:
    """check_my_work rejected 3 times => value=3 (NUMERIC, not BOOLEAN)."""
    rejection = (
        "Rejected: call check_my_work only after you've successfully edited "
        "content-page files in this session."
    )
    observations = [
        _mk_tool_obs("check_my_work", rejection),
        _mk_tool_obs("check_my_work", rejection),
        _mk_tool_obs("write_file", "ok"),
        _mk_tool_obs("check_my_work", rejection),
    ]
    values = _extract_metric_values(observations)
    assert values["gate_rejected_check_my_work"] == 3


def test_extract_metric_values_check_my_work_success_not_counted() -> None:
    """A passing check_my_work call (no Rejected: prefix) doesn't bump count."""
    observations = [
        _mk_tool_obs("check_my_work", "Validation passed: 2 pages OK."),
    ]
    values = _extract_metric_values(observations)
    assert values["gate_rejected_check_my_work"] == 0


def test_extract_metric_values_first_verdict_wins() -> None:
    observations = [
        _mk_tool_obs("task", '{"verdict": "block"}'),
        _mk_tool_obs("task", '{"verdict": "pass"}'),
    ]
    values = _extract_metric_values(observations)
    assert values["reviewer_verdict"] == "block"


def test_extract_metric_values_auto_correct_in_input_side() -> None:
    observations = [
        _mk_tool_obs("read_file", output="contents", inputs="auto_corrected_from='/.claude/x.md'"),
    ]
    values = _extract_metric_values(observations)
    assert values["auto_corrected"] is True


# ============================ _classify_compile_outcome ========================


def test_classify_outcome_content_page_wins() -> None:
    """content_page_cited=True short-circuits — the trace wrote real content."""
    # Even if the trace ALSO logged a trivial_skip (weird), content_page wins.
    obs = [
        _mk_tool_obs(
            "log_insight",
            inputs='{"category": "trivial_skip", "message": "OOO"}',
        ),
    ]
    assert _classify_compile_outcome(obs, content_page_cited=True) == "content_page"


def test_classify_outcome_trivial_skip() -> None:
    """No writes + trivial_skip insight => trivial_skip bin."""
    obs = [
        _mk_tool_obs(
            "log_insight",
            inputs='{"category": "trivial_skip", "message": "OOO"}',
        ),
    ]
    assert _classify_compile_outcome(obs, content_page_cited=False) == "trivial_skip"


def test_classify_outcome_already_captured() -> None:
    """No writes + already_captured insight => already_captured bin (U7)."""
    obs = [
        _mk_tool_obs(
            "log_insight",
            inputs='{"category": "already_captured", "message": "on topic page X"}',
        ),
    ]
    assert _classify_compile_outcome(obs, content_page_cited=False) == "already_captured"


def test_classify_outcome_filing_cabinet_when_entity_write_only() -> None:
    """Wrote an entity stub but not a content page => filing_cabinet."""
    obs = [
        _mk_tool_obs("create_entity"),
    ]
    assert _classify_compile_outcome(obs, content_page_cited=False) == "filing_cabinet"


def test_classify_outcome_filing_cabinet_when_draft_but_not_cited() -> None:
    """Agent wrote a draft but the DB says no content-page citation => filing_cabinet."""
    obs = [
        _mk_tool_obs("write_draft_page"),
    ]
    assert _classify_compile_outcome(obs, content_page_cited=False) == "filing_cabinet"


def test_classify_outcome_ghost_when_no_writes_no_insight() -> None:
    """No writes, no insight, no citation => ghost — worst outcome."""
    obs = [_mk_tool_obs("read_file")]
    assert _classify_compile_outcome(obs, content_page_cited=False) == "ghost"


def test_classify_outcome_ghost_when_no_observations() -> None:
    """Empty trace + no citation => ghost."""
    assert _classify_compile_outcome([], content_page_cited=False) == "ghost"


def test_classify_outcome_trivial_skip_wins_over_write() -> None:
    """Weird trace with BOTH trivial_skip insight and a write => trivial_skip.

    The explicit intent signal (log_insight category) is more specific than
    the "did the agent call a writer tool" proxy.
    """
    obs = [
        _mk_tool_obs(
            "log_insight",
            inputs='{"category": "trivial_skip"}',
        ),
        _mk_tool_obs("create_entity"),
    ]
    assert _classify_compile_outcome(obs, content_page_cited=False) == "trivial_skip"


def test_classify_outcome_missing_content_page_flag_falls_back_to_obs() -> None:
    """When content_page_cited is None, we rely on observation signals.

    The content_page bin is unreachable in that case (we couldn't verify
    the citation), so a ghost-shaped trace stays ghost.
    """
    assert _classify_compile_outcome([_mk_tool_obs("read_file")], None) == "ghost"


# ============================ emit_scores_for_trace ============================


def _capture_scores(client: MagicMock) -> dict[str, dict[str, Any]]:
    """Pull `create_score` call kwargs into a {name: kwargs} dict for asserts."""
    out: dict[str, dict[str, Any]] = {}
    for call in client.create_score.call_args_list:
        kwargs = call.kwargs
        out[kwargs["name"]] = kwargs
    return out


def test_emit_scores_pushes_all_six_when_message_id_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All six score names land on Langfuse when message_id is supplied (U7)."""
    # No DB hit needed — short-circuit the citation lookup with False.
    monkeypatch.setattr(langfuse_scores, "_is_content_page_cited", lambda _c, _m: True)
    client = MagicMock()
    observations = [
        _mk_tool_obs("write_todos", "[]"),
        _mk_tool_obs("task", '{"verdict": "revise"}'),
    ]
    emit_scores_for_trace(client, "trace-1", observations, message_id="m1")

    scores = _capture_scores(client)
    assert set(scores) == {
        "content_page_cited",
        "gate_rejected_check_my_work",
        "auto_corrected",
        "wrote_todos_early",
        "reviewer_verdict",
        "compile_outcome",
    }
    # content_page_cited honours the lookup — we stubbed True, expect 1.0
    assert scores["content_page_cited"]["value"] == 1.0
    assert scores["content_page_cited"]["data_type"] == "BOOLEAN"
    # Verdict + early-todos derived from observations
    assert scores["reviewer_verdict"]["value"] == "revise"
    assert scores["reviewer_verdict"]["data_type"] == "CATEGORICAL"
    assert scores["wrote_todos_early"]["value"] == 1.0
    assert scores["wrote_todos_early"]["data_type"] == "BOOLEAN"
    # Defaults
    assert scores["auto_corrected"]["value"] == 0.0
    assert scores["gate_rejected_check_my_work"]["value"] == 0.0
    assert scores["gate_rejected_check_my_work"]["data_type"] == "NUMERIC"
    # All scores attached to the same trace_id
    for kw in scores.values():
        assert kw["trace_id"] == "trace-1"


def test_emit_scores_omits_content_page_when_no_message_id() -> None:
    """Without message_id we can't compute content_page_cited — skip it."""
    client = MagicMock()
    emit_scores_for_trace(client, "trace-2", [_mk_tool_obs("ls")], message_id=None)
    scores = _capture_scores(client)
    assert "content_page_cited" not in scores
    # The other 5 still emit (includes compile_outcome added in U7)
    assert set(scores) == {
        "gate_rejected_check_my_work",
        "auto_corrected",
        "wrote_todos_early",
        "reviewer_verdict",
        "compile_outcome",
    }


def test_emit_scores_verdict_none_when_reviewer_didnt_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No verdict in observations => emit 'none' (not None or empty string)."""
    monkeypatch.setattr(langfuse_scores, "_is_content_page_cited", lambda _c, _m: False)
    client = MagicMock()
    emit_scores_for_trace(client, "trace-3", [_mk_tool_obs("ls")], message_id="m1")
    scores = _capture_scores(client)
    assert scores["reviewer_verdict"]["value"] == "none"


def test_emit_scores_swallows_create_score_failures() -> None:
    """One create_score raising shouldn't block the rest of the scores."""
    client = MagicMock()
    # First call raises; subsequent calls succeed.
    client.create_score.side_effect = [
        RuntimeError("Langfuse 524 Origin Time-out"),
        None,
        None,
        None,
        None,
        None,
    ]
    observations = [_mk_tool_obs("ls")]
    # Must not raise
    emit_scores_for_trace(client, "trace-4", observations, message_id=None)
    # All five push attempts made (no message_id so content_page_cited is skipped),
    # even though the first failed
    assert client.create_score.call_count == 5


def test_emit_scores_db_error_falls_back_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB failure on the citation lookup => content_page_cited = 0.0."""

    def _raise(*_args: Any, **_kwargs: Any) -> bool:
        raise psycopg.Error("connection refused")

    monkeypatch.setattr(langfuse_scores, "_is_content_page_cited", _raise)
    client = MagicMock()
    emit_scores_for_trace(client, "trace-5", [_mk_tool_obs("ls")], message_id="m1")
    scores = _capture_scores(client)
    assert scores["content_page_cited"]["value"] == 0.0


def test_emit_scores_precomputed_flag_skips_db_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `content_page_cited` is passed explicitly, no DB hit happens."""

    def _should_not_run(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("DB lookup should be skipped when flag is precomputed")

    monkeypatch.setattr(langfuse_scores, "_is_content_page_cited", _should_not_run)
    client = MagicMock()
    emit_scores_for_trace(
        client,
        "trace-pre",
        [_mk_tool_obs("ls")],
        message_id="m1",
        content_page_cited=True,
    )
    scores = _capture_scores(client)
    assert scores["content_page_cited"]["value"] == 1.0


# ============================ _is_content_page_cited ===========================


def _seed_message(conn: psycopg.Connection, message_id: str) -> None:
    conn.execute(
        """
        INSERT INTO messages (message_id, raw_path)
             VALUES (%s, %s)
        """,
        (message_id, f"raw/{message_id}.md"),
    )
    conn.commit()


def _seed_page(conn: psycopg.Connection, slug: str, page_type: str) -> int:
    raw = conn.execute(
        """
        INSERT INTO wiki_pages (slug, path, title, page_type, status)
             VALUES (%s, %s, %s, %s, 'current')
          RETURNING page_id
        """,
        (slug, f"wiki/{page_type}s/{slug}.md", slug.replace("-", " ").title(), page_type),
    ).fetchone()
    conn.commit()
    assert raw is not None
    # conftest's _scoped_connect pins row_factory=dict_row; mypy can't
    # see that through psycopg.Connection, so cast to the runtime type.
    row = cast("dict[str, Any]", raw)
    return int(row["page_id"])


def _seed_touch(conn: psycopg.Connection, message_id: str, page_id: int) -> None:
    conn.execute(
        """
        INSERT INTO message_touched_pages (message_id, page_id)
             VALUES (%s, %s)
        """,
        (message_id, page_id),
    )
    conn.commit()


def test_is_content_page_cited_true_for_topic_page(db_conn: psycopg.Connection) -> None:
    """Citation in a topic page counts."""
    _seed_message(db_conn, "msg-topic")
    pid = _seed_page(db_conn, "topic-test", "topic")
    _seed_touch(db_conn, "msg-topic", pid)
    assert _is_content_page_cited(db_conn, "msg-topic") is True


def test_is_content_page_cited_false_for_entity_page_only(
    db_conn: psycopg.Connection,
) -> None:
    """Entity-only citation doesn't count — that's filing-cabinet behaviour."""
    _seed_message(db_conn, "msg-entity")
    pid = _seed_page(db_conn, "alice-example", "entity")
    _seed_touch(db_conn, "msg-entity", pid)
    assert _is_content_page_cited(db_conn, "msg-entity") is False


def test_is_content_page_cited_true_when_both_entity_and_topic(
    db_conn: psycopg.Connection,
) -> None:
    """Mixed citation — at least one content-type page is enough."""
    _seed_message(db_conn, "msg-mixed")
    entity_pid = _seed_page(db_conn, "bob-example", "entity")
    topic_pid = _seed_page(db_conn, "topic-mixed", "topic")
    _seed_touch(db_conn, "msg-mixed", entity_pid)
    _seed_touch(db_conn, "msg-mixed", topic_pid)
    assert _is_content_page_cited(db_conn, "msg-mixed") is True


def test_is_content_page_cited_false_when_no_touches(
    db_conn: psycopg.Connection,
) -> None:
    """No row in message_touched_pages => False (not a DB error)."""
    _seed_message(db_conn, "msg-orphan")
    assert _is_content_page_cited(db_conn, "msg-orphan") is False


def test_content_cited_lookup_batches_in_one_query(
    db_conn: psycopg.Connection,
) -> None:
    """Batch lookup returns the right True/False per id in a single round-trip."""
    _seed_message(db_conn, "msg-A")
    _seed_message(db_conn, "msg-B")
    _seed_message(db_conn, "msg-C")
    topic = _seed_page(db_conn, "topic-batch", "topic")
    entity = _seed_page(db_conn, "person-batch", "entity")
    _seed_touch(db_conn, "msg-A", topic)  # cited in content page
    _seed_touch(db_conn, "msg-B", entity)  # cited only in entity
    # msg-C has no touches at all
    result = _content_cited_lookup(["msg-A", "msg-B", "msg-C"])
    assert result == {"msg-A": True, "msg-B": False, "msg-C": False}


def test_content_cited_lookup_empty_input_returns_empty() -> None:
    """No ids => empty dict, no DB hit needed."""
    assert _content_cited_lookup([]) == {}


# ============================ emit_scores_for_run ==============================


def test_emit_scores_for_run_no_client_skips_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _build_client returns None we just log + return 0."""
    monkeypatch.setattr(langfuse_scores, "_build_client", lambda: None)
    result = emit_scores_for_run(uuid.uuid4())
    assert result == 0


def test_emit_scores_for_run_no_batches_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with no compile_attempts rows => 0 traces emitted."""
    monkeypatch.setattr(langfuse_scores, "_build_client", lambda: MagicMock())
    monkeypatch.setattr(langfuse_scores, "_list_run_batches", lambda _r: [])
    result = emit_scores_for_run(uuid.uuid4())
    assert result == 0


def test_emit_scores_for_run_pushes_one_trace_per_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two messages on the same thread => one trace fetched + scored."""
    client = MagicMock()
    monkeypatch.setattr(langfuse_scores, "_build_client", lambda: client)
    monkeypatch.setattr(
        langfuse_scores,
        "_list_run_batches",
        lambda _r: [("m1", "thread-A"), ("m2", "thread-A")],
    )
    monkeypatch.setattr(
        langfuse_scores,
        "_resolve_traces",
        lambda _c, _r: {"thread-A": "trace-A"},
    )
    monkeypatch.setattr(
        langfuse_scores,
        "_fetch_trace_observations",
        lambda _c, _t: [_mk_tool_obs("ls")],
    )
    # Batched citation lookup — emit_scores_for_run calls this once with
    # every message_id, then passes the precomputed flag through to
    # emit_scores_for_trace.
    monkeypatch.setattr(
        langfuse_scores,
        "_content_cited_lookup",
        lambda mids: dict.fromkeys(mids, True),
    )

    result = emit_scores_for_run(uuid.uuid4())
    assert result == 1
    # 6 score names per trace (U7 added compile_outcome), 1 trace scored
    assert client.create_score.call_count == 6
    client.flush.assert_called_once()


def test_emit_scores_for_run_handles_missing_trace_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trace fetch returning None (Langfuse 524) is skipped, not crashed on."""
    client = MagicMock()
    monkeypatch.setattr(langfuse_scores, "_build_client", lambda: client)
    monkeypatch.setattr(
        langfuse_scores,
        "_list_run_batches",
        lambda _r: [("m1", "thread-A")],
    )
    monkeypatch.setattr(
        langfuse_scores,
        "_resolve_traces",
        lambda _c, _r: {"thread-A": "trace-A"},
    )
    monkeypatch.setattr(
        langfuse_scores,
        "_fetch_trace_observations",
        lambda _c, _t: None,
    )
    result = emit_scores_for_run(uuid.uuid4())
    assert result == 0
    client.create_score.assert_not_called()


def test_emit_scores_for_run_resilient_when_resolve_traces_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Langfuse trace search returning no matches => 0 emitted, no crash."""
    client = MagicMock()
    monkeypatch.setattr(langfuse_scores, "_build_client", lambda: client)
    monkeypatch.setattr(langfuse_scores, "_list_run_batches", lambda _r: [("m1", "thread-A")])
    monkeypatch.setattr(langfuse_scores, "_resolve_traces", lambda _c, _r: {})
    result = emit_scores_for_run(uuid.uuid4())
    assert result == 0
    client.create_score.assert_not_called()
    client.flush.assert_called_once()
