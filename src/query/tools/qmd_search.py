"""QMD subprocess client for hybrid BM25+vector search in the query agent.

Modeled after src/agent/tools/qmd_client.py (Amit's compilation client)
but adapted for query use. Does NOT touch Amit's code.

Calls: qmd query <q> -n <limit> --json --no-rerank
Returns ranked candidates with slug, title, score, snippet.
Falls back gracefully on error/timeout/missing binary.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
QMD_TIMEOUT = int(os.getenv("QMD_QUERY_TIMEOUT_S", "30"))

_QMD_URI_RE = re.compile(r"qmd://[^/]+/(?P<slug>.+?)(?:\.md)?$")


def _find_qmd_binary() -> str | None:
    """Find the qmd binary, checking nvm paths too."""
    import shutil
    binary = shutil.which("qmd")
    if binary:
        return binary
    nvm_dir = os.environ.get("NVM_DIR", os.path.expanduser("~/.nvm"))
    nvm_bin = Path(nvm_dir) / "versions" / "node"
    if nvm_bin.exists():
        for node_dir in sorted(nvm_bin.iterdir(), reverse=True):
            candidate = node_dir / "bin" / "qmd"
            if candidate.exists():
                return str(candidate)
    return None


QMD_BINARY = _find_qmd_binary()


def _extract_slug(qmd_file_uri: str) -> str:
    m = _QMD_URI_RE.search(qmd_file_uri)
    return m.group("slug") if m else qmd_file_uri


def qmd_search(
    query: str,
    limit: int = 10,
    no_rerank: bool = True,
    timeout_s: int | None = None,
) -> list[dict[str, Any]]:
    """Run QMD hybrid search (BM25 + vector + RRF fusion).

    Returns list of {slug, title, score, snippet} dicts.
    Empty list on any error (missing binary, timeout, parse failure).
    """
    if not QMD_BINARY:
        return []

    timeout = timeout_s or QMD_TIMEOUT
    cmd = [QMD_BINARY, "query", query, "-n", str(limit), "--json"]
    if no_rerank:
        cmd.append("--no-rerank")

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(REPO_ROOT),
        )
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []

    if proc.returncode != 0:
        return []

    # QMD may leak cmake/build output to stdout on first run.
    # Extract the JSON array robustly.
    stdout = proc.stdout
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # QMD may prefix stdout with cmake/build noise. Find the JSON array.
        # Look for '[\n  {' pattern (formatted JSON array start)
        match = re.search(r'(\[\s*\{)', stdout)
        if not match:
            return []
        try:
            payload = json.loads(stdout[match.start(1):])
        except json.JSONDecodeError:
            return []

    if not isinstance(payload, list):
        return []

    candidates = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        file_uri = str(item.get("file") or "")
        slug = _extract_slug(file_uri)
        if not slug:
            continue
        candidates.append({
            "slug": slug,
            "title": str(item.get("title") or slug),
            "score": round(float(item["score"]), 4)
            if isinstance(item.get("score"), (int, float))
            else 0.0,
            "snippet": str(item.get("snippet") or "")[:200],
        })

    return candidates
