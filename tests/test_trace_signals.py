"""Unit tests for `src/observability/trace_signals.py`.

Pure-function tests over synthetic tool-output strings. No Langfuse,
no DB, no network — every assertion pins a single predicate against a
fixture payload shaped like the ToolMessage content the live tracer
produces.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.observability.trace_signals import extract_reviewer_merge_count  # noqa: E402
from src.observability.trace_signals import is_alphabetical_candidate_list  # noqa: E402
from src.observability.trace_signals import is_glob_timeout  # noqa: E402

# =============================== is_alphabetical_candidate_list ================


def test_alphabetical_candidates_fires_on_sorted_miss() -> None:
    """≥3 slugs in lexicographic order on a miss => True."""
    payload = {
        "exists": False,
        "candidates": [
            {"slug": "apple", "title": "A"},
            {"slug": "banana", "title": "B"},
            {"slug": "cherry", "title": "C"},
        ],
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is True


def test_alphabetical_candidates_with_duplicate_leading_letters_still_sorted() -> None:
    """Monotonic-non-decreasing lets equal-prefix slugs through."""
    payload = {
        "exists": False,
        "candidates": [
            {"slug": "alpha"},
            {"slug": "alpha-beta"},
            {"slug": "beta"},
            {"slug": "gamma"},
        ],
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is True


def test_alphabetical_candidates_false_when_not_sorted() -> None:
    """Out-of-order slug => False (the fuzzy-search happy path)."""
    payload = {
        "exists": False,
        "candidates": [
            {"slug": "zebra"},
            {"slug": "apple"},
            {"slug": "mango"},
        ],
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is False


def test_alphabetical_candidates_false_when_hit() -> None:
    """`exists: True` is never a candidate-list miss."""
    payload = {
        "exists": True,
        "slug": "seller-isq",
        "title": "Seller ISQ",
        "page_type": "topic",
        "status": "active",
        "confidence": 1.0,
        "why_matched": "slug",
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is False


def test_alphabetical_candidates_false_when_below_threshold() -> None:
    """Two sorted candidates aren't enough — could be coincidence."""
    payload = {
        "exists": False,
        "candidates": [
            {"slug": "apple"},
            {"slug": "banana"},
        ],
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is False


def test_alphabetical_candidates_false_when_candidates_missing() -> None:
    """`candidates` key absent (catalog-empty branch) => False."""
    payload = {"exists": False, "catalog_empty_or_stale": True}
    assert is_alphabetical_candidate_list(json.dumps(payload)) is False


def test_alphabetical_candidates_false_on_non_json_output() -> None:
    """Malformed output (non-JSON string) => False, no exception."""
    assert is_alphabetical_candidate_list("not json at all") is False
    assert is_alphabetical_candidate_list("") is False


def test_alphabetical_candidates_case_insensitive() -> None:
    """Upper/lower mix still counts as sorted if lowercased form is sorted."""
    payload = {
        "exists": False,
        "candidates": [
            {"slug": "Apple"},
            {"slug": "BANANA"},
            {"slug": "cherry"},
        ],
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is True


def test_alphabetical_candidates_rejects_malformed_candidate_entries() -> None:
    """A candidate without a string `slug` key => False (not a crash)."""
    payload = {
        "exists": False,
        "candidates": [
            {"slug": "apple"},
            {"title": "missing slug"},
            {"slug": "cherry"},
        ],
    }
    assert is_alphabetical_candidate_list(json.dumps(payload)) is False


# =================================== is_glob_timeout ===========================


def test_glob_timeout_true_for_deepagents_prefix() -> None:
    """Matches the literal string `deepagents.middleware.filesystem` emits."""
    msg = "Error: glob timed out after 30s. Try a more specific pattern or a narrower path."
    assert is_glob_timeout(msg) is True


def test_glob_timeout_true_with_trailing_drift() -> None:
    """Prefix-only match survives minor wording changes in the suffix."""
    msg = "Error: glob timed out after 45s. Different suffix text."
    assert is_glob_timeout(msg) is True


def test_glob_timeout_false_for_unrelated_error() -> None:
    """A non-timeout glob error => False."""
    assert is_glob_timeout("Error: Path outside root directory: /foo") is False
    assert is_glob_timeout("No files found") is False
    assert is_glob_timeout("") is False


def test_glob_timeout_false_when_embedded_mid_string() -> None:
    """The timeout marker must appear at the start, not buried in text."""
    msg = "Ok no issues. Note: glob timed out after 30s in a subcall"
    assert is_glob_timeout(msg) is False


# ================================ extract_reviewer_merge_count =================


def test_reviewer_merge_count_from_pure_json_output() -> None:
    """When the output is the ReviewReport as JSON, return len(merge_candidates)."""
    report = {
        "verdict": "revise",
        "blockers": [],
        "warnings": [],
        "merge_candidates": ["slug-a", "slug-b", "slug-c"],
        "editorial_notes": [],
        "draft_recommended": False,
        "summary": "Overlaps with three existing pages.",
    }
    assert extract_reviewer_merge_count(json.dumps(report)) == 3


def test_reviewer_merge_count_zero_when_empty_list() -> None:
    """`merge_candidates: []` => 0 (the reviewer ran and found no debt)."""
    report = {
        "verdict": "pass",
        "merge_candidates": [],
        "summary": "Clean page.",
    }
    assert extract_reviewer_merge_count(json.dumps(report)) == 0


def test_reviewer_merge_count_zero_when_field_missing() -> None:
    """No merge_candidates field => 0 (not a crash)."""
    report = {"verdict": "pass", "summary": "Fine."}
    assert extract_reviewer_merge_count(json.dumps(report)) == 0


def test_reviewer_merge_count_finds_trailing_json_block() -> None:
    """AI preamble prose + a trailing JSON block is still parseable."""
    preamble = "Here is the review you requested:\n\n"
    report = {"verdict": "revise", "merge_candidates": ["x", "y"], "summary": "Two merge dupes."}
    combined = preamble + json.dumps(report)
    assert extract_reviewer_merge_count(combined) == 2


def test_reviewer_merge_count_handles_prose_with_nested_objects() -> None:
    """Preamble + JSON containing nested objects (warnings list) still works."""
    report = {
        "verdict": "revise",
        "warnings": [{"slug": "a", "rule": "x", "message": "y"}],
        "merge_candidates": ["slug-a", "slug-b"],
        "summary": "nested",
    }
    combined = "Prose header noting issues.\n" + json.dumps(report)
    assert extract_reviewer_merge_count(combined) == 2


def test_reviewer_merge_count_zero_on_non_json() -> None:
    """Malformed output => 0 (never raise)."""
    assert extract_reviewer_merge_count("not json") == 0
    assert extract_reviewer_merge_count("") == 0


def test_reviewer_merge_count_zero_when_candidates_not_a_list() -> None:
    """`merge_candidates: "slug-a"` (misconfigured schema) => 0, not a crash."""
    report = {"verdict": "revise", "merge_candidates": "slug-a"}
    assert extract_reviewer_merge_count(json.dumps(report)) == 0
