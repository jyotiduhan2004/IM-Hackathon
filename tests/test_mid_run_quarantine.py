"""Tests for the mid-run pool quarantine refresh in compile_all.py (#194).

Run 02c9d536 (2026-04-28 smoke) showed kimi-k2.6 burning 60/106 attempts
before the run-start ``_healthy_pool`` could re-evaluate. The fix calls
``_healthy_pool`` from a per-batch helper (``_refresh_pool_for_batch``)
so a model crossing the 4h fail-rate threshold mid-run gets quarantined
on the next batch's pool prep, not the next run.

These tests cover the contract:
- Helper calls ``_healthy_pool`` once per batch (proving the dispatcher
  isn't using a frozen pool).
- The initial pool — not the previously filtered pool — is the input,
  so a quarantine-recovered model can re-enter mid-run.
- The 401/403 ``unauthorized`` set is subtracted before ``_healthy_pool``
  runs (so an unauthorized model never randomly gets picked again, but
  can be re-evaluated for quarantine if proxy auth is restored).
- Each (model, reason) pair logs the operator-facing auto-exclusion
  line exactly once per run, not once per batch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


def test_refresh_pool_calls_healthy_pool_each_batch(compile_all_module: Any) -> None:
    """Drive 3 batches; the helper must call ``_healthy_pool`` 3 times.

    Mocks ``_healthy_pool`` to return progressively smaller pools so we
    can also assert that what the helper hands back tracks the latest
    DB state, not the previous batch's filtered slice.
    """
    mod = compile_all_module
    initial_pool = ["model-a", "model-b", "model-c"]
    unauthorized: set[str] = set()
    announced: set[tuple[str, str]] = set()

    # Three calls, three different filtered pools — simulates an in-flight
    # run where ``compile_attempts`` keeps trickling in new failures.
    mock_returns = [
        (["model-a", "model-b", "model-c"], []),
        (
            ["model-a", "model-b"],
            [
                {
                    "compile_model": "model-c",
                    "reason": "quarantined (4h)",
                    "window_hours": 4,
                    "fail_rate": 1.0,
                    "total": 5,
                    "failed": 5,
                    "failed_hard": 5,
                }
            ],
        ),
        (
            ["model-a"],
            [
                {
                    "compile_model": "model-c",
                    "reason": "quarantined (4h)",
                    "window_hours": 4,
                    "fail_rate": 1.0,
                    "total": 5,
                    "failed": 5,
                    "failed_hard": 5,
                },
                {
                    "compile_model": "model-b",
                    "reason": "quarantined (4h)",
                    "window_hours": 4,
                    "fail_rate": 0.85,
                    "total": 7,
                    "failed": 6,
                    "failed_hard": 6,
                },
            ],
        ),
    ]

    with patch.object(mod, "_healthy_pool", side_effect=mock_returns) as mock_hp:
        results = []
        for batch_idx in range(1, 4):
            results.append(
                mod._refresh_pool_for_batch(initial_pool, unauthorized, announced, batch_idx)
            )

    # Once per batch — not once per run.
    assert mock_hp.call_count == 3
    # Each call sees the FULL initial pool (so recovered models can
    # re-enter), not the previous batch's filtered slice.
    for call_args in mock_hp.call_args_list:
        assert call_args[0][0] == initial_pool

    # Returned pool tracks ``_healthy_pool``'s output, not a frozen value.
    assert results[0] == ["model-a", "model-b", "model-c"]
    assert results[1] == ["model-a", "model-b"]
    assert results[2] == ["model-a"]


def test_refresh_pool_announces_quarantine_once_per_model(
    compile_all_module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Operator-facing log fires exactly once per (model, reason) pair —
    a model excluded for the rest of the run shouldn't spam the console
    once per batch.
    """
    mod = compile_all_module
    initial_pool = ["bad-model", "good-model"]
    unauthorized: set[str] = set()
    announced: set[tuple[str, str]] = set()

    excluded_record = {
        "compile_model": "bad-model",
        "reason": "quarantined (4h)",
        "window_hours": 4,
        "fail_rate": 1.0,
        "total": 5,
        "failed": 5,
        "failed_hard": 5,
    }
    # Same exclusion record returned across 3 batches.
    mock_returns = [(["good-model"], [excluded_record])] * 3

    with patch.object(mod, "_healthy_pool", side_effect=mock_returns):
        for batch_idx in range(1, 4):
            mod._refresh_pool_for_batch(initial_pool, unauthorized, announced, batch_idx)

    captured = capsys.readouterr().out
    # Auto-exclusion line printed exactly once across 3 batches.
    assert captured.count("mid-run auto-exclusion: bad-model") == 1
    # Internal dedupe set carries the announcement key.
    assert ("bad-model", "quarantined (4h)") in announced


def test_refresh_pool_subtracts_unauthorized_before_healthy_pool(
    compile_all_module: Any,
) -> None:
    """A model on the cross-batch 401/403 ``unauthorized`` list must be
    filtered out BEFORE ``_healthy_pool`` runs — the helper shouldn't
    even re-evaluate models the proxy is rejecting for this team key.
    """
    mod = compile_all_module
    initial_pool = ["auth-ok", "no-auth"]
    unauthorized: set[str] = {"no-auth"}
    announced: set[tuple[str, str]] = set()

    with patch.object(mod, "_healthy_pool", return_value=(["auth-ok"], [])) as mock_hp:
        result = mod._refresh_pool_for_batch(initial_pool, unauthorized, announced, 1)

    mock_hp.assert_called_once_with(["auth-ok"])
    assert result == ["auth-ok"]


def test_refresh_pool_returns_empty_when_initial_pool_exhausted(
    compile_all_module: Any,
) -> None:
    """If every model is unauthorized, skip ``_healthy_pool`` and return
    empty so the dispatcher falls back to ``resolved_model``.
    """
    mod = compile_all_module
    initial_pool = ["a", "b"]
    unauthorized: set[str] = {"a", "b"}
    announced: set[tuple[str, str]] = set()

    with patch.object(mod, "_healthy_pool") as mock_hp:
        result = mod._refresh_pool_for_batch(initial_pool, unauthorized, announced, 1)

    mock_hp.assert_not_called()
    assert result == []



def test_refresh_pool_does_not_announce_when_fails_open(
    compile_all_module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """When ``_healthy_pool`` fails open (would empty the pool, returns
    full input + exclusion records), no auto-exclusion line should fire
    for models still present in the returned pool. Claude review on PR #252.
    """
    mod = compile_all_module
    initial_pool = ["a"]
    unauthorized: set[str] = set()
    announced: set[tuple[str, str]] = set()
    excluded_record = {
        "compile_model": "a",
        "reason": "quarantined (4h)",
        "window_hours": 4,
        "fail_rate": 1.0,
        "total": 5,
        "failed": 5,
        "failed_hard": 5,
    }
    # fails-open: pool keeps "a" AND the excluded record references "a"
    with patch.object(mod, "_healthy_pool", return_value=(["a"], [excluded_record])):
        result = mod._refresh_pool_for_batch(initial_pool, unauthorized, announced, 1)

    assert result == ["a"]
    assert announced == set()
    captured = capsys.readouterr().out
    assert "mid-run auto-exclusion" not in captured
