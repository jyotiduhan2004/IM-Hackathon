"""Unit tests for src/compile/tools/qmd_client.

Mocks subprocess.run so we never actually shell out to qmd. Covers the
shape-parsing and error paths — the real qmd CLI is exercised in the
phase-0 spike and (in Phase 2) integration tests gated by
``QMD_INTEGRATION=1``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from src.compile.tools import qmd_client


# ---------------------------------------------------------------------------
# is_enabled — flag comes from pydantic-settings. Conftest's autouse fixture
# resets settings.use_semantic_resolve to False before every test.
# ---------------------------------------------------------------------------


def test_is_enabled_defaults_off() -> None:
    assert qmd_client.is_enabled() is False


def test_is_enabled_true_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import settings as _s

    monkeypatch.setattr(_s, "use_semantic_resolve", True)
    assert qmd_client.is_enabled() is True


# ---------------------------------------------------------------------------
# query_qmd — happy path
# ---------------------------------------------------------------------------


def _fake_proc(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["qmd", "query"],
        returncode=rc,
        stdout=stdout,
        stderr=stderr,
    )


def _qmd_payload(*rows: dict[str, Any]) -> str:
    return json.dumps(list(rows))


def test_query_qmd_parses_valid_json_response() -> None:
    payload = _qmd_payload(
        {
            "docid": "#abc",
            "score": 0.91,
            "file": "qmd://wiki-topics/seller-isq.md",
            "title": "Seller ISQ",
            "snippet": "@@ -12,4 @@ (11 before, 5 after) | ...body...",
        },
        {
            "docid": "#def",
            "score": 0.55,
            "file": "qmd://wiki-systems/marketplace-launch.md",
            "title": "Marketplace Launch",
            "snippet": "@@ -1,4 @@ (0 before, 9 after) | ...",
        },
    )
    with patch("subprocess.run", return_value=_fake_proc(stdout=payload)):
        result = qmd_client.query_qmd("seller isq", limit=5)

    assert result["retriever"] == "qmd"
    assert "error" not in result
    assert len(result["candidates"]) == 2
    top = result["candidates"][0]
    assert top["slug"] == "seller-isq"
    assert top["title"] == "Seller ISQ"
    assert top["score"] == 0.91
    assert "body" in top["snippet"]


def test_query_qmd_respects_limit() -> None:
    # qmd returned more rows than we asked for — caller must cap.
    payload = _qmd_payload(
        *[
            {"docid": f"#{i}", "score": 0.5, "file": f"qmd://wiki-topics/p{i}.md", "title": f"P{i}"}
            for i in range(10)
        ]
    )
    with patch("subprocess.run", return_value=_fake_proc(stdout=payload)):
        result = qmd_client.query_qmd("anything", limit=3)
    assert len(result["candidates"]) == 3


def test_query_qmd_accepts_missing_score_as_none() -> None:
    payload = _qmd_payload(
        {"docid": "#x", "file": "qmd://w/p.md", "title": "P", "snippet": "s"},
    )
    with patch("subprocess.run", return_value=_fake_proc(stdout=payload)):
        result = qmd_client.query_qmd("q", limit=5)
    assert result["candidates"][0]["score"] is None


def test_query_qmd_accepts_missing_snippet_as_empty_string() -> None:
    payload = _qmd_payload({"docid": "#x", "file": "qmd://w/p.md", "title": "P", "score": 0.5})
    with patch("subprocess.run", return_value=_fake_proc(stdout=payload)):
        result = qmd_client.query_qmd("q", limit=5)
    assert result["candidates"][0]["snippet"] == ""


def test_query_qmd_skips_rows_without_parseable_slug() -> None:
    payload = _qmd_payload(
        {"docid": "#1", "file": "qmd://w/valid.md", "title": "T", "score": 0.5},
        {"docid": "#2", "file": "", "title": "T2", "score": 0.4},  # no uri
        "not-a-dict",  # malformed row
    )
    with patch("subprocess.run", return_value=_fake_proc(stdout=payload)):
        result = qmd_client.query_qmd("q", limit=5)
    # Only the one valid row survives filtering.
    assert [c["slug"] for c in result["candidates"]] == ["valid"]


# ---------------------------------------------------------------------------
# query_qmd — error paths (each returns {"candidates": [], "error": ...})
# ---------------------------------------------------------------------------


def test_query_qmd_handles_missing_binary() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("qmd not installed")):
        result = qmd_client.query_qmd("q", limit=5)
    assert result["candidates"] == []
    assert result["error"] == "missing_binary"


def test_query_qmd_handles_timeout() -> None:
    err = subprocess.TimeoutExpired(cmd=["qmd"], timeout=0.1)
    with patch("subprocess.run", side_effect=err):
        result = qmd_client.query_qmd("q", limit=5, timeout_s=0)
    assert result["candidates"] == []
    assert result["error"] == "timeout"


def test_query_qmd_handles_nonzero_exit() -> None:
    with patch(
        "subprocess.run",
        return_value=_fake_proc(stdout="", stderr="boom", rc=2),
    ):
        result = qmd_client.query_qmd("q", limit=5)
    assert result["candidates"] == []
    assert result["error"] == "rc=2"


def test_query_qmd_handles_non_json_stdout() -> None:
    with patch("subprocess.run", return_value=_fake_proc(stdout="this is not json\n")):
        result = qmd_client.query_qmd("q", limit=5)
    assert result["candidates"] == []
    assert result["error"] == "parse"


def test_query_qmd_handles_non_list_payload() -> None:
    with patch("subprocess.run", return_value=_fake_proc(stdout='{"not": "a list"}')):
        result = qmd_client.query_qmd("q", limit=5)
    assert result["candidates"] == []
    assert result["error"] == "unexpected_shape"
