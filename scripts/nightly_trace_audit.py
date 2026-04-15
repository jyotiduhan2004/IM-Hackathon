"""Nightly trace audit — sample last 20 Langfuse traces and grade them.

Emits ``docs/audits/nightly-YYYYMMDD.json`` with a per-trace rubric score and a
summary of grade + issue histograms. Intended to run from cron daily.

Rubric (from the recent 50-trace review):

- **A**: clean single-concept merge (low tool-call count, no synthesis issues).
- **B**: clean but tool-call count above batch median.
- **C**: filing-cabinet behavior (>3 blockquotes, no synthesis markers).
- **D**: hit recursion limit OR empty output.
- **F**: errored out (fetch failed or trace has ERROR observations).

Usage::

    uv run python scripts/nightly_trace_audit.py
    uv run python scripts/nightly_trace_audit.py --limit 50
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any
from typing import TypedDict

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402

logger = structlog.get_logger(__name__)

# Tunables
LIST_TIMEOUT_S = 60
GET_TIMEOUT_S = 90
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = 3
BLOCKQUOTE_THRESHOLD = 3
SYNTHESIS_MARKERS = ("## ", "### ", "TL;DR", "Summary", "Current state", "Decision")
RECURSION_PATTERNS = ("recursion", "GraphRecursionError", "step 120", "recursion_limit")


class TraceRubric(TypedDict):
    tid: str
    grade: str
    issues: list[str]
    tool_seq_first_5: list[str]
    output_chars: int
    tool_calls: int
    model: str | None
    thread_id: str | None
    run_id: str | None


def _cli_env() -> dict[str, str]:
    """Inherit current env + inject Langfuse creds from settings (loaded from .env)."""
    env = os.environ.copy()
    if settings.langfuse_public_key:
        env["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    if settings.langfuse_secret_key:
        env["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    env["LANGFUSE_HOST"] = settings.langfuse_host
    return env


def _run_cli(args: list[str], timeout_s: int) -> tuple[str, str, int]:
    """Run ``npx langfuse-cli`` and return (stdout, stderr, returncode)."""
    proc = subprocess.run(
        ["npx", "langfuse-cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=_cli_env(),
    )
    return proc.stdout, proc.stderr, proc.returncode


def _list_recent_traces(limit: int) -> list[dict[str, Any]]:
    """Fetch list of most recent traces. Returns ``[]`` on error."""
    try:
        stdout, stderr, rc = _run_cli(
            ["api", "traces", "list", "--limit", str(limit), "--json"],
            timeout_s=LIST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.warning("trace_list_timeout", limit=limit)
        return []
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("trace_list_failed", error=str(exc))
        return []
    if rc != 0:
        logger.warning("trace_list_nonzero", rc=rc, stderr=stderr[:400])
        return []
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        logger.warning("trace_list_parse_failed", error=str(exc))
        return []
    body = payload.get("body") if isinstance(payload, dict) else payload
    if isinstance(body, dict):
        data = body.get("data") or body.get("traces") or []
    else:
        data = body if isinstance(body, list) else []
    return data if isinstance(data, list) else []


def _fetch_trace(tid: str) -> dict[str, Any]:
    """Fetch one trace with retry + backoff. Returns ``{"error": "..."}`` on final fail."""
    last_err = "unreachable"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            stdout, stderr, rc = _run_cli(
                ["api", "traces", "get", tid, "--json"],
                timeout_s=GET_TIMEOUT_S,
            )
            if rc != 0:
                last_err = f"cli rc={rc}: {stderr[:200]}"
            else:
                try:
                    return json.loads(stdout)  # type: ignore[no-any-return]
                except json.JSONDecodeError as exc:
                    last_err = f"parse failed: {exc}"
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {GET_TIMEOUT_S}s"
        except (OSError, subprocess.SubprocessError) as exc:
            last_err = f"subprocess error: {exc}"
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_S * attempt)
    return {"error": last_err}


def _extract_tool_seq(observations: list[dict[str, Any]]) -> list[str]:
    return [o.get("name") or "?" for o in observations if o.get("type") == "TOOL"]


def _scan_output_issues(output_text: str) -> list[str]:
    """Detect filing-cabinet / recursion / path hygiene issues in raw output text."""
    issues: list[str] = []
    blockquote_lines = sum(1 for line in output_text.splitlines() if line.lstrip().startswith(">"))
    if blockquote_lines > BLOCKQUOTE_THRESHOLD:
        issues.append(f"blockquotes_{blockquote_lines}")
    if not any(marker in output_text for marker in SYNTHESIS_MARKERS):
        issues.append("no_synthesis_markers")
    return issues


def _scan_tool_issues(
    observations: list[dict[str, Any]],
    tool_seq: list[str],
) -> list[str]:
    issues: list[str] = []
    # Absolute-path fs calls
    abs_path_hits = 0
    for o in observations:
        if o.get("type") != "TOOL":
            continue
        tin = str(o.get("input") or "")
        if re.search(r'"file_path":\s*"/', tin) or re.search(r"'file_path':\s*'/", tin):
            abs_path_hits += 1
    if abs_path_hits:
        issues.append(f"abs_path_call_{abs_path_hits}")
    # check_my_work before first write
    first_write_idx = next(
        (i for i, n in enumerate(tool_seq) if n in {"write_file", "edit_file", "patch_page"}),
        -1,
    )
    first_check_idx = next((i for i, n in enumerate(tool_seq) if n == "check_my_work"), -1)
    if first_check_idx >= 0 and (first_write_idx < 0 or first_check_idx < first_write_idx):
        issues.append("checked_work_before_write")
    # resolve_page flail (>=3 calls)
    resolve_count = sum(1 for n in tool_seq if n == "resolve_page")
    if resolve_count >= 3:
        issues.append(f"resolve_page_flail_{resolve_count}x")
    # create_entity without a content-type page write
    if "create_entity" in tool_seq and not any(
        n in {"write_file", "edit_file", "patch_page", "write_draft_page"} for n in tool_seq
    ):
        issues.append("created_entity_cc_only")
    return issues


def _grade(
    output_text: str,
    tool_calls: int,
    tool_call_median: float,
    err_obs_count: int,
    output_issues: list[str],
) -> str:
    if err_obs_count > 0:
        return "F"
    if not output_text.strip():
        return "D"
    if any(pat.lower() in output_text.lower() for pat in RECURSION_PATTERNS):
        return "D"
    blockquote_issue = next((i for i in output_issues if i.startswith("blockquotes_")), None)
    if blockquote_issue and "no_synthesis_markers" in output_issues:
        return "C"
    if tool_calls > tool_call_median:
        return "B"
    return "A"


def _score_trace(
    trace: dict[str, Any],
    tool_call_median: float,
) -> TraceRubric:
    tid = str(trace.get("tid") or trace.get("id") or "")
    if trace.get("error"):
        return TraceRubric(
            tid=tid,
            grade="F",
            issues=[f"fetch_error:{str(trace['error'])[:80]}"],
            tool_seq_first_5=[],
            output_chars=0,
            tool_calls=0,
            model=None,
            thread_id=None,
            run_id=None,
        )
    body = trace.get("body") or trace
    metadata = body.get("metadata") or {}
    observations = body.get("observations") or []
    output_text = str(body.get("output") or "")
    tool_seq = _extract_tool_seq(observations)
    err_obs_count = sum(1 for o in observations if o.get("level") == "ERROR")
    output_issues = _scan_output_issues(output_text)
    tool_issues = _scan_tool_issues(observations, tool_seq)
    grade = _grade(
        output_text=output_text,
        tool_calls=len(tool_seq),
        tool_call_median=tool_call_median,
        err_obs_count=err_obs_count,
        output_issues=output_issues,
    )
    return TraceRubric(
        tid=tid,
        grade=grade,
        issues=[*output_issues, *tool_issues],
        tool_seq_first_5=tool_seq[:5],
        output_chars=len(output_text),
        tool_calls=len(tool_seq),
        model=metadata.get("compile_model"),
        thread_id=metadata.get("compile_thread_id"),
        run_id=metadata.get("compile_run_id"),
    )


def _summarize(rubrics: list[TraceRubric]) -> dict[str, Any]:
    grades: dict[str, int] = {}
    common_issues: dict[str, int] = {}
    for r in rubrics:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1
        for issue in r["issues"]:
            common_issues[issue] = common_issues.get(issue, 0) + 1
    return {
        "grades": dict(sorted(grades.items())),
        "common_issues": dict(sorted(common_issues.items(), key=lambda kv: -kv[1])),
    }


def _audit_path(out_dir: Path, today: datetime) -> Path:
    return out_dir / f"nightly-{today.strftime('%Y%m%d')}.json"


def _window_label(now: datetime, trace_count: int) -> str:
    return f"last_{trace_count}_traces_at_{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"


@click.command()
@click.option("--limit", default=20, show_default=True, help="Number of recent traces to audit.")
def main(limit: int) -> None:
    """Fetch, score, and write the nightly trace audit."""
    out_dir = REPO_ROOT / "docs" / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    out_path = _audit_path(out_dir, now)

    recent = _list_recent_traces(limit)
    if not recent:
        payload: dict[str, Any] = {
            "window": _window_label(now, 0),
            "traces": [],
            "summary": {"grades": {}, "common_issues": {}},
            "error": "langfuse unreachable or returned no traces",
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.warning("nightly_audit_empty", path=str(out_path))
        click.echo(str(out_path))
        return

    trace_ids = [str(t.get("id") or t.get("tid")) for t in recent if t.get("id") or t.get("tid")]
    logger.info("nightly_audit_start", trace_count=len(trace_ids))

    fetched: list[dict[str, Any]] = []
    for i, tid in enumerate(trace_ids, 1):
        logger.info("fetch_trace", tid=tid[:12], progress=f"{i}/{len(trace_ids)}")
        d = _fetch_trace(tid)
        d.setdefault("tid", tid)
        fetched.append(d)

    # First pass: collect tool-call counts for median
    tool_call_counts = [
        len(_extract_tool_seq((t.get("body") or {}).get("observations") or []))
        for t in fetched
        if not t.get("error")
    ]
    tool_call_median = median(tool_call_counts) if tool_call_counts else 0.0

    rubrics = [_score_trace(t, tool_call_median) for t in fetched]

    payload = {
        "window": _window_label(now, len(rubrics)),
        "traces": rubrics,
        "summary": _summarize(rubrics),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "nightly_audit_done",
        path=str(out_path),
        traces=len(rubrics),
        grades=payload["summary"]["grades"],
    )
    click.echo(str(out_path))


if __name__ == "__main__":
    main()
