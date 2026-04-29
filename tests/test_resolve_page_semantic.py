"""Integration tests for resolve_page + semantic retriever (qmd).

Covers the qmd-first + exact-match-wins flow added in Phase 1 of the
qmd integration. Mocks ``query_qmd`` so tests run without the real CLI.

Key behaviours under test:
- Flag off → pre-qmd behaviour preserved (retriever flips to "fuzzy"
  on a miss instead of being absent).
- Flag on + exact slug/title hit → `retriever: "exact"` wins regardless
  of what semantic returned.
- Flag on + no exact + semantic candidates → `retriever: "semantic"`
  with snippets pass-through.
- Flag on + semantic error → `retriever: "fuzzy"` (SQL ILIKE fallback).
- Email queries skip semantic (qmd doesn't index people).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from src.agent import compiler_agent as compiler_mod
from src.db import wiki_pages as repo


def _resolve(query: str) -> dict[str, Any]:
    return compiler_mod.resolve_page.invoke({"query": query})


def _semantic_ok(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {"candidates": candidates, "latency_s": 1.23, "retriever": "qmd"}


def _semantic_err(error: str) -> dict[str, Any]:
    return {"candidates": [], "latency_s": 0.01, "retriever": "qmd", "error": error}


# ---------------------------------------------------------------------------
# Flag off: behaviour preserved, `retriever` field added
# ---------------------------------------------------------------------------


def test_flag_off_exact_hit_retriever_exact(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="buylead",
        path="wiki/topics/buylead.md",
        title="BuyLead",
        page_type="topic",
    )
    db_conn.commit()

    result = _resolve("buylead")
    assert result["exists"] is True
    assert result["slug"] == "buylead"
    assert result["retriever"] == "exact"
    assert "snippet" not in result


def test_flag_off_miss_retriever_fuzzy(db_conn: psycopg.Connection) -> None:
    repo.upsert_wiki_page(
        db_conn,
        slug="whatsapp-onboarding",
        path="wiki/topics/whatsapp-onboarding.md",
        title="WhatsApp Onboarding",
        page_type="topic",
    )
    db_conn.commit()

    result = _resolve("whatsapp-something-different")
    assert result["exists"] is False
    assert result["retriever"] == "fuzzy"


def test_response_has_no_operational_telemetry_fields(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latency + internal retriever state must stay out of the LLM view."""
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    repo.upsert_wiki_page(
        db_conn,
        slug="foo",
        path="wiki/topics/foo.md",
        title="Foo",
        page_type="topic",
    )
    db_conn.commit()

    semantic_result = _semantic_ok(
        [{"slug": "foo", "title": "Foo", "score": 0.9, "snippet": "body"}],
    )
    with patch("src.agent.tools.qmd_client.query_qmd", return_value=semantic_result):
        result = _resolve("foo")

    # Exact-hit envelope — no latency should leak.
    for forbidden in ("semantic_latency_s", "latency_s", "qmd_latency_s"):
        assert forbidden not in result, f"{forbidden} leaked into tool return"


# ---------------------------------------------------------------------------
# Flag on: exact match always wins
# ---------------------------------------------------------------------------


def test_flag_on_exact_slug_wins_even_when_semantic_missed(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if semantic doesn't return the page, the exact SQL lookup wins."""
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    repo.upsert_wiki_page(
        db_conn,
        slug="marketplace-launch",
        path="wiki/systems/marketplace-launch.md",
        title="Marketplace Launch",
        page_type="system",
    )
    # Also seed a red herring so semantic has something to return.
    repo.upsert_wiki_page(
        db_conn,
        slug="unrelated-topic",
        path="wiki/topics/unrelated-topic.md",
        title="Unrelated",
        page_type="topic",
    )
    db_conn.commit()

    # Semantic returns ONLY unrelated results — exact match is not in
    # semantic's output.
    semantic_result = _semantic_ok(
        [
            {
                "slug": "unrelated-topic",
                "title": "Unrelated",
                "score": 0.88,
                "snippet": "context",
            },
        ]
    )

    with patch("src.agent.tools.qmd_client.query_qmd", return_value=semantic_result):
        result = _resolve("marketplace-launch")

    assert result["exists"] is True
    assert result["slug"] == "marketplace-launch"
    assert result["retriever"] == "exact"
    # No snippet because semantic didn't find this specific page.
    assert "snippet" not in result


def test_flag_on_exact_hit_carries_snippet_when_semantic_also_found_it(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    repo.upsert_wiki_page(
        db_conn,
        slug="seller-isq",
        path="wiki/topics/seller-isq.md",
        title="Seller ISQ",
        page_type="topic",
    )
    db_conn.commit()

    semantic_result = _semantic_ok(
        [
            {
                "slug": "seller-isq",
                "title": "Seller ISQ",
                "score": 0.93,
                "snippet": "@@ -12,4 @@ (11 before, 5 after) | body excerpt here",
            },
        ]
    )

    with patch("src.agent.tools.qmd_client.query_qmd", return_value=semantic_result):
        result = _resolve("seller-isq")

    assert result["exists"] is True
    assert result["retriever"] == "exact"
    assert "body excerpt here" in result["snippet"]


# ---------------------------------------------------------------------------
# Flag on: no exact → semantic candidates returned with snippets
# ---------------------------------------------------------------------------


def test_flag_on_no_exact_returns_semantic_candidates(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    for slug, title in [
        ("seller-bl-api-optimization", "Seller BL API Optimization"),
        ("seller-bl-api-hit-optimisation", "Seller BL API Hit Optimisation"),
    ]:
        repo.upsert_wiki_page(
            db_conn,
            slug=slug,
            path=f"wiki/topics/{slug}.md",
            title=title,
            page_type="topic",
        )
    db_conn.commit()

    semantic_result = _semantic_ok(
        [
            {
                "slug": "seller-bl-api-optimization",
                "title": "Seller BL API Optimization",
                "score": 0.88,
                "snippet": "matching body excerpt 1",
            },
            {
                "slug": "seller-bl-api-hit-optimisation",
                "title": "Seller BL API Hit Optimisation",
                "score": 0.55,
                "snippet": "matching body excerpt 2",
            },
        ]
    )

    with patch("src.agent.tools.qmd_client.query_qmd", return_value=semantic_result):
        result = _resolve("how do we speed up seller BL API calls")

    assert result["exists"] is False
    assert result["retriever"] == "semantic"
    cand_slugs = [c["slug"] for c in result["candidates"]]
    assert cand_slugs == [
        "seller-bl-api-optimization",
        "seller-bl-api-hit-optimisation",
    ]
    assert result["candidates"][0]["snippet"] == "matching body excerpt 1"
    assert result["candidates"][0]["score"] == 0.88


def test_flag_on_semantic_skips_candidates_not_in_catalog(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If semantic returns a slug the catalog doesn't know, drop it.

    Happens when the semantic index is ahead of the post-batch catalog
    sync — we don't want to fabricate page_type / status for a ghost.
    """
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    repo.upsert_wiki_page(
        db_conn,
        slug="real-page",
        path="wiki/topics/real-page.md",
        title="Real Page",
        page_type="topic",
    )
    db_conn.commit()

    semantic_result = _semantic_ok(
        [
            {"slug": "ghost-page", "title": "Ghost", "score": 0.9, "snippet": "..."},
            {"slug": "real-page", "title": "Real Page", "score": 0.8, "snippet": "..."},
        ]
    )

    with patch("src.agent.tools.qmd_client.query_qmd", return_value=semantic_result):
        result = _resolve("some query")

    assert result["exists"] is False
    assert result["retriever"] == "semantic"
    assert [c["slug"] for c in result["candidates"]] == ["real-page"]


# ---------------------------------------------------------------------------
# Flag on + semantic error → fuzzy fallback
# ---------------------------------------------------------------------------


def test_flag_on_semantic_error_falls_back_to_fuzzy(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    repo.upsert_wiki_page(
        db_conn,
        slug="whatsapp-onboarding",
        path="wiki/topics/whatsapp-onboarding.md",
        title="WhatsApp Onboarding",
        page_type="topic",
    )
    db_conn.commit()

    with patch(
        "src.agent.tools.qmd_client.query_qmd",
        return_value=_semantic_err("missing_binary"),
    ):
        result = _resolve("whatsapp-something")

    assert result["exists"] is False
    assert result["retriever"] == "fuzzy"
    # ILIKE fallback surfaces the real whatsapp page as a candidate.
    assert any(c["slug"] == "whatsapp-onboarding" for c in result["candidates"])


def test_flag_on_semantic_empty_falls_back_to_fuzzy(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Semantic returned OK but with zero candidates — still fall back."""
    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    repo.upsert_wiki_page(
        db_conn,
        slug="whatsapp-onboarding",
        path="wiki/topics/whatsapp-onboarding.md",
        title="WhatsApp Onboarding",
        page_type="topic",
    )
    db_conn.commit()

    with patch(
        "src.agent.tools.qmd_client.query_qmd",
        return_value=_semantic_ok([]),
    ):
        result = _resolve("whatsapp-something")

    assert result["exists"] is False
    assert result["retriever"] == "fuzzy"


# ---------------------------------------------------------------------------
# Email queries skip semantic (qmd doesn't index people)
# ---------------------------------------------------------------------------


def test_email_query_uses_sql_path_even_with_flag_on(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.db import users as users_repo

    monkeypatch.setattr("src.config.settings.use_semantic_resolve", True)
    users_repo.upsert_user(db_conn, email="alice@example.com", display_name="Alice")
    repo.upsert_wiki_page(
        db_conn,
        slug="alice-example-com",
        path="wiki/entities/alice-example-com.md",
        title="Alice",
        page_type="entity",
        canonical_user_email="alice@example.com",
    )
    db_conn.commit()

    # Patch query_qmd as a safety net — if the flow incorrectly calls it,
    # the test would see candidates from the mock and the assertion below
    # would fail unmistakably.
    bad_semantic = _semantic_ok(
        [
            {"slug": "wrong-page", "title": "W", "score": 0.9, "snippet": "s"},
        ]
    )
    with patch(
        "src.agent.tools.qmd_client.query_qmd",
        return_value=bad_semantic,
    ) as mock_qmd:
        result = _resolve("alice@example.com")

    assert result["exists"] is True
    assert result["slug"] == "alice-example-com"
    assert result["retriever"] == "exact"
    assert result["why_matched"] == "email"
    # And crucially, we never asked the semantic retriever.
    mock_qmd.assert_not_called()
