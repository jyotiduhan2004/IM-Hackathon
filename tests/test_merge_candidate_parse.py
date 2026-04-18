"""Unit tests for reviewer-merge-candidate parsing + the apply script.

Covers two layers:

1. ``_extract_merge_candidates`` in ``src/compile/compiler.py`` — given a
   synthetic agent result dict (mimicking the shape deepagents returns
   from ``task(subagent_type="reviewer", ...)``), confirm the parser
   pulls out every reviewer-flagged pair with its note.
2. ``scripts/apply_merge_candidate.py`` — dry-run against a mini wiki
   fixture, assert the diff includes the merged section AND the loser
   flip to ``status: superseded``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.compiler import _extract_merge_candidates  # noqa: E402

from tests._script_loader import load_script  # noqa: E402

# ---------------------------------------------------------------------------
# _extract_merge_candidates
# ---------------------------------------------------------------------------


def _reviewer_tool_message(payload: dict) -> dict:
    """Synthesise a ToolMessage-shaped dict matching what deepagents returns.

    The main agent calls ``task(subagent_type="reviewer", ...)`` and
    deepagents' subagents middleware injects the reviewer's final AI
    message text as a ToolMessage in the parent state (see
    ``deepagents.middleware.subagents._return_command_with_state_update``).
    The content is plain-text JSON for structured-output reviewers.
    """
    return {
        "type": "tool",
        "name": "task",
        "content": json.dumps(payload),
    }


def test_extract_merge_candidates_pulls_all_pairs() -> None:
    """Reviewer returned 3 merge candidates → parser emits 3 pairs."""
    payload = {
        "verdict": "block",
        "blockers": [
            {
                "slug": "bl-seller-email-alert",
                "rule": "duplicate_page",
                "message": "Overlaps with bl-notif and bl-sms-alert.",
            }
        ],
        "warnings": [],
        "merge_candidates": ["bl-notif", "bl-sms-alert", "bl-email-district"],
        "editorial_notes": [],
        "summary": "Three duplicates of the same Seller BuyLead-alert concept.",
    }
    result = {"messages": [_reviewer_tool_message(payload)]}

    pairs = _extract_merge_candidates(result)

    assert len(pairs) == 3
    slugs_b = {p["slug_b"] for p in pairs}
    assert slugs_b == {"bl-notif", "bl-sms-alert", "bl-email-district"}
    # Every pair is anchored at the reviewed slug.
    assert {p["slug_a"] for p in pairs} == {"bl-seller-email-alert"}
    # Note falls through to summary.
    assert all("Seller BuyLead" in p["note"] for p in pairs)


def test_extract_merge_candidates_empty_on_pass_verdict() -> None:
    """Reviewer passed a page → merge_candidates stays []."""
    payload = {
        "verdict": "pass",
        "blockers": [],
        "warnings": [],
        "merge_candidates": [],
        "summary": "Page is good.",
    }
    result = {"messages": [_reviewer_tool_message(payload)]}
    assert _extract_merge_candidates(result) == []


def test_extract_merge_candidates_empty_when_no_reviewer_ran() -> None:
    """A trace with no reviewer invocation returns []."""
    result = {
        "messages": [
            {"type": "human", "content": "Compile thread abc123."},
            {"type": "ai", "content": "Working on it..."},
        ]
    }
    assert _extract_merge_candidates(result) == []


def test_extract_merge_candidates_deduplicates_repeat_runs() -> None:
    """If the agent invoked the reviewer twice with the same verdict,
    we emit each distinct pair only once."""
    payload = {
        "verdict": "revise",
        "blockers": [],
        "warnings": [{"slug": "photosearch", "rule": "duplicate_section", "message": "dup"}],
        "merge_candidates": ["photosearch-v2"],
        "summary": "Duplicate section.",
    }
    result = {
        "messages": [
            _reviewer_tool_message(payload),
            _reviewer_tool_message(payload),
        ]
    }
    pairs = _extract_merge_candidates(result)
    assert len(pairs) == 1
    assert pairs[0]["slug_a"] == "photosearch"
    assert pairs[0]["slug_b"] == "photosearch-v2"


def test_extract_merge_candidates_dedupes_swapped_pairs() -> None:
    """``(foo, bar)`` and ``(bar, foo)`` are the same pair — emit once.

    Two reviewer runs, each from the opposite side of the pair, must not
    double-count in the merge queue. The dedup key is canonicalised by
    sorting the slug pair.
    """
    payload_a = {
        "verdict": "revise",
        "warnings": [{"slug": "foo", "rule": "duplicate_page", "message": "Overlaps bar."}],
        "merge_candidates": ["bar"],
        "summary": "Foo duplicates bar.",
    }
    payload_b = {
        "verdict": "revise",
        "warnings": [{"slug": "bar", "rule": "duplicate_page", "message": "Overlaps foo."}],
        "merge_candidates": ["foo"],
        "summary": "Bar duplicates foo.",
    }
    result = {
        "messages": [
            _reviewer_tool_message(payload_a),
            _reviewer_tool_message(payload_b),
        ]
    }
    pairs = _extract_merge_candidates(result)
    assert len(pairs) == 1
    assert {pairs[0]["slug_a"], pairs[0]["slug_b"]} == {"foo", "bar"}


def test_extract_merge_candidates_handles_malformed_json_gracefully() -> None:
    """A tool message with invalid JSON shouldn't blow up the coordinator."""
    result = {
        "messages": [
            {"type": "tool", "name": "task", "content": '{"verdict": "pass", bogus'},
            _reviewer_tool_message(
                {
                    "verdict": "block",
                    "blockers": [{"slug": "a", "rule": "fabrication", "message": "x"}],
                    "merge_candidates": ["b"],
                    "summary": "Valid one.",
                }
            ),
        ]
    }
    pairs = _extract_merge_candidates(result)
    assert len(pairs) == 1
    assert pairs[0]["slug_a"] == "a"
    assert pairs[0]["slug_b"] == "b"


def test_extract_merge_candidates_empty_when_no_messages() -> None:
    """Missing messages key or non-list returns []."""
    assert _extract_merge_candidates({}) == []
    assert _extract_merge_candidates({"messages": None}) == []
    assert _extract_merge_candidates({"messages": []}) == []


def test_extract_merge_candidates_note_uses_blocker_when_summary_missing() -> None:
    """When the reviewer's summary is empty, we fall back to the first
    blocker/warning message — the queue reader still gets a reason."""
    payload = {
        "verdict": "block",
        "blockers": [
            {"slug": "x", "rule": "duplicate_page", "message": "Overlaps with y."},
        ],
        "merge_candidates": ["y"],
        "summary": "",
    }
    result = {"messages": [_reviewer_tool_message(payload)]}
    pairs = _extract_merge_candidates(result)
    assert pairs[0]["note"] == "Overlaps with y."


def test_extract_merge_candidates_truncates_long_notes() -> None:
    """Notes are capped at 200 chars so the queue stays readable."""
    payload = {
        "verdict": "revise",
        "warnings": [{"slug": "z", "rule": "dup", "message": "x"}],
        "merge_candidates": ["z-v2"],
        "summary": "x" * 500,
    }
    result = {"messages": [_reviewer_tool_message(payload)]}
    pairs = _extract_merge_candidates(result)
    assert len(pairs[0]["note"]) == 200


def test_extract_merge_candidates_handles_nested_objects() -> None:
    """A nested inner object shouldn't confuse the enclosing-brace walker.

    Reviewer output wrapped in a tool-call response might carry nested
    structure before the verdict key. The parser must find the outer
    brace, not a sibling's.
    """
    inner_payload = {
        "verdict": "block",
        "blockers": [{"slug": "x", "rule": "dup", "message": "m"}],
        "merge_candidates": ["y"],
        "summary": "Duplicates y.",
    }
    # Simulate the reviewer JSON embedded inside a larger structured response.
    wrapper = {"tool_call_id": "abc", "data": inner_payload}
    result = {"messages": [{"type": "tool", "name": "task", "content": json.dumps(wrapper)}]}

    pairs = _extract_merge_candidates(result)
    assert len(pairs) == 1
    assert pairs[0]["slug_b"] == "y"


def test_extract_merge_candidates_accepts_langchain_message_objects() -> None:
    """Real traces carry LangChain ``BaseMessage`` instances, not dicts.

    Parser must handle ``getattr(msg, 'content', '')`` in addition to the
    dict shape used in tests above.
    """

    class _FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    payload = {
        "verdict": "revise",
        "warnings": [{"slug": "foo", "rule": "dup", "message": "bar"}],
        "merge_candidates": ["foo-old"],
        "summary": "Merge foo-old into foo.",
    }
    result = {"messages": [_FakeMessage(json.dumps(payload))]}
    pairs = _extract_merge_candidates(result)
    assert len(pairs) == 1
    assert pairs[0]["slug_b"] == "foo-old"


# ---------------------------------------------------------------------------
# apply_merge_candidate --dry-run
# ---------------------------------------------------------------------------


def _write_page(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_apply_merge_candidate_dry_run_preview(tmp_path: Path) -> None:
    """Dry-run prints a diff showing keeper gains a loser-only section and
    the loser becomes ``status: superseded``. No files are written."""
    wiki = tmp_path / "wiki"
    keeper = wiki / "topics" / "bl-notif.md"
    loser = wiki / "topics" / "bl-sms.md"
    _write_page(
        keeper,
        "---\n"
        "title: BuyLead notification\n"
        "page_type: topic\n"
        "status: active\n"
        "source_threads:\n"
        "  - T1\n"
        "---\n\n"
        "BuyLead notifications are the primary alert channel.\n\n"
        "## Current state\n\nActive across all categories.\n",
    )
    _write_page(
        loser,
        "---\n"
        "title: BuyLead SMS\n"
        "page_type: topic\n"
        "status: active\n"
        "source_threads:\n"
        "  - T2\n"
        "---\n\n"
        "SMS is one flavour of BuyLead notification.\n\n"
        "## SMS-specific quirks\n\n"
        "Delivery is throttled at 20 rps per carrier.\n",
    )

    keeper_before = keeper.read_text(encoding="utf-8")
    loser_before = loser.read_text(encoding="utf-8")

    module = load_script("apply_merge_candidate")
    runner = CliRunner()
    res = runner.invoke(
        module.main,
        [
            "--pair",
            "bl-notif,bl-sms",
            "--keep",
            "bl-notif",
            "--wiki-dir",
            str(wiki),
            "--dry-run",
        ],
    )
    assert res.exit_code == 0, res.output
    # Diff output should mention the SMS-specific section being added to keeper.
    assert "SMS-specific quirks" in res.output
    # Loser should show status: superseded + superseded_by in the diff.
    assert "superseded" in res.output
    assert "bl-notif" in res.output
    # No-op on disk — dry-run.
    assert keeper.read_text(encoding="utf-8") == keeper_before
    assert loser.read_text(encoding="utf-8") == loser_before


def test_apply_merge_candidate_rejects_missing_keeper(tmp_path: Path) -> None:
    """Unknown slug in --keep surfaces as a click error."""
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    module = load_script("apply_merge_candidate")
    runner = CliRunner()
    res = runner.invoke(
        module.main,
        [
            "--pair",
            "does-not-exist,also-missing",
            "--keep",
            "does-not-exist",
            "--wiki-dir",
            str(wiki),
            "--dry-run",
        ],
    )
    assert res.exit_code != 0
    # ClickException message goes to stderr via click's output capture.
    assert "not found" in res.output or "Error" in res.output


def test_apply_merge_candidate_rejects_bad_pair(tmp_path: Path) -> None:
    """--pair must be exactly two comma-separated slugs."""
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    module = load_script("apply_merge_candidate")
    runner = CliRunner()
    res = runner.invoke(
        module.main,
        [
            "--pair",
            "only-one-slug",
            "--keep",
            "only-one-slug",
            "--wiki-dir",
            str(wiki),
            "--dry-run",
        ],
    )
    assert res.exit_code != 0
    assert "two comma-separated slugs" in res.output or "Error" in res.output


def test_apply_merge_candidate_rejects_keep_not_in_pair(tmp_path: Path) -> None:
    """--keep must name one of the --pair slugs."""
    wiki = tmp_path / "wiki"
    (wiki / "topics").mkdir(parents=True)
    module = load_script("apply_merge_candidate")
    runner = CliRunner()
    res = runner.invoke(
        module.main,
        [
            "--pair",
            "a,b",
            "--keep",
            "c",
            "--wiki-dir",
            str(wiki),
            "--dry-run",
        ],
    )
    assert res.exit_code != 0
    assert "match" in res.output or "Error" in res.output


def test_merge_bodies_skips_duplicate_h2_titles(tmp_path: Path) -> None:
    """When keeper + loser both have ``## Current state``, keeper wins."""
    module = load_script("apply_merge_candidate")
    keeper_body = (
        "Preamble.\n\n"
        "## Current state\n\nKeeper says active everywhere.\n\n"
        "## Related\n\n- [[other]]\n"
    )
    loser_body = (
        "Stub.\n\n"
        "## Current state\n\nLoser says deprecated.\n\n"
        "## History\n\nPrior rollouts listed here.\n"
    )
    merged = module._merge_bodies(keeper_body, loser_body)
    # Keeper's "Current state" survives; loser's is dropped.
    assert "active everywhere" in merged
    assert "deprecated" not in merged
    # Loser-only section appended.
    assert "Prior rollouts listed here" in merged


def test_merge_list_field_dedupe(tmp_path: Path) -> None:
    """source_threads from both pages merge, order preserved, dedupe."""
    module = load_script("apply_merge_candidate")
    merged = module._merge_list_field(["T1", "T2"], ["T2", "T3"])
    assert merged == ["T1", "T2", "T3"]

    # None + list behaves as list.
    assert module._merge_list_field(None, ["X"]) == ["X"]
    # Both empty/None → [].
    assert module._merge_list_field(None, None) == []
