"""qmd subprocess client for semantic wiki retrieval.

Thin wrapper around ``qmd query <q> -n <limit> --json``. Returns
relevance-ranked candidates with line-numbered snippets so the caller
(``resolve_page``) can surface *why* each page matched without a
follow-up ``read_file``.

Design notes:
- Subprocess over HTTP MCP daemon for Phase 1. Simpler, no daemon
  lifecycle. First-call latency (~16s cold) is acceptable for the
  tool-rounds-per-miss reduction we expect. HTTP daemon is a Phase
  1.5 optimisation if latency becomes a production concern.
- Returns a structured shape the caller can drop straight into the
  existing resolve_page envelope. No exception propagation — errors
  come back as ``{"error": "...", "candidates": []}`` so the caller
  can fall back to SQL cleanly.
- Spike audit (docs/audits/qmd-spike-2026-04-23.md) validated 85%
  top-5 sensibility on 40 real Langfuse queries, 9/9 on fixture
  backstop. ``QMD_TIMEOUT_S`` is the per-call hard cap; default is
  generous (60s) to cover the worst cold-start reranker warm-up.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

from src.config import settings

# `qmd://<collection>/<slug>.md` — matches the shape qmd returns in
# `file` fields on query output. `.md` suffix is optional so future
# qmd changes don't silently break slug extraction.
_QMD_URI_RE = re.compile(r"qmd://[^/]+/(?P<slug>.+?)(?:\.md)?$")


def _extract_slug(qmd_file_uri: str) -> str:
    m = _QMD_URI_RE.search(qmd_file_uri)
    return m.group("slug") if m else qmd_file_uri


def is_enabled() -> bool:
    """True when resolve_page should run the semantic retriever.

    Default off. Set ``USE_SEMANTIC_RESOLVE=1`` in ``.env`` to enable.
    Flipping to off is the manual rollback path — no code change
    needed, just env + restart.
    """
    return settings.use_semantic_resolve


def query_qmd(
    query: str,
    limit: int = 5,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Run ``qmd query <q> -n <limit> --json`` and parse results.

    Returns a dict with one of two shapes:

    Success::

        {
            "candidates": [{"slug": str, "title": str, "score": float, "snippet": str}, ...],
            "latency_s": float,
            "retriever": "qmd",
        }

    Failure::

        {
            "candidates": [],
            "latency_s": float,
            "retriever": "qmd",
            "error": "<timeout|rc=N|parse|missing_binary>",
        }

    Caller is expected to fall back to SQL on any error path.
    """
    t0 = time.perf_counter()
    timeout = timeout_s if timeout_s is not None else settings.qmd_timeout_s
    try:
        proc = subprocess.run(
            ["qmd", "query", query, "-n", str(limit), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            "candidates": [],
            "latency_s": round(time.perf_counter() - t0, 3),
            "retriever": "qmd",
            "error": "missing_binary",
        }
    except subprocess.TimeoutExpired:
        return {
            "candidates": [],
            "latency_s": timeout,
            "retriever": "qmd",
            "error": "timeout",
        }
    latency = round(time.perf_counter() - t0, 3)
    if proc.returncode != 0:
        return {
            "candidates": [],
            "latency_s": latency,
            "retriever": "qmd",
            "error": f"rc={proc.returncode}",
        }
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, json.JSONDecodeError):
        return {
            "candidates": [],
            "latency_s": latency,
            "retriever": "qmd",
            "error": "parse",
        }
    if not isinstance(payload, list):
        return {
            "candidates": [],
            "latency_s": latency,
            "retriever": "qmd",
            "error": "unexpected_shape",
        }
    candidates: list[dict[str, Any]] = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        file_uri = str(item.get("file") or "")
        slug = _extract_slug(file_uri)
        if not slug:
            continue
        candidates.append(
            {
                "slug": slug,
                "title": str(item.get("title") or ""),
                "score": float(item["score"])
                if isinstance(item.get("score"), (int, float))
                else None,
                # Line-numbered excerpt from the page body showing why it
                # matched. Format: '@@ -LINE,N @@ (B before, A after) | ...'
                # Caller may want to trim further; we pass through unchanged.
                "snippet": str(item.get("snippet") or ""),
            }
        )
    return {
        "candidates": candidates,
        "latency_s": latency,
        "retriever": "qmd",
    }
