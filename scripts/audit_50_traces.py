"""Reproducible 50-trace audit — the scheduled/on-demand version of PR #81.

Writes a human-readable markdown audit of the last N Langfuse traces,
scoring each on the North-Star rubric: is the compiler moving toward
content-type pages (topic/system/policy/decision) or still filing
emails into entity pages?

Usage::

    uv run python scripts/audit_50_traces.py                # last 50 traces
    uv run python scripts/audit_50_traces.py --limit 10     # smaller run
    uv run python scripts/audit_50_traces.py --since 24h    # docstring tag

Output: ``docs/audits/audit-<ISO>.md``. The script prints the path on exit.

**Resilience**: Langfuse is flaky (524s common). We reuse ``_run_langfuse``
from ``scripts/trace_scorecard.py`` for retry + timeout. If more than 20%
of trace fetches fail, the script exits nonzero with a clear error —
better to abort than produce a misleading audit.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import cast

import click
import psycopg
import structlog

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.trace_scorecard import ENTITY_TOOLS  # noqa: E402
from scripts.trace_scorecard import _coerce_batch_index  # noqa: E402
from scripts.trace_scorecard import _langfuse_env  # noqa: E402
from scripts.trace_scorecard import _run_langfuse  # noqa: E402
from src.compile.categories import AGENT_VISIBLE_CATEGORIES  # noqa: E402
from src.db import connect  # noqa: E402
from src.observability.trace_signals import CONTENT_PAGE_TYPES  # noqa: E402

# Content-page category directories we accept for the broadened
# "attempted content page" denominator. Sourced from the shared
# ``AGENT_VISIBLE_CATEGORIES`` tuple minus ``people`` — person / entity
# pages are filing-cabinet territory and must NOT bump the denominator.
_CONTENT_CATEGORIES: tuple[str, ...] = tuple(c for c in AGENT_VISIBLE_CATEGORIES if c != "people")

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

# Abort threshold — >20% fetch failures means the sample is untrustworthy.
# (We still always write the output file first so the operator can see
# partial progress before the nonzero exit.)
FETCH_FAILURE_ABORT_RATIO = 0.20

# `CONTENT_PAGE_TYPES` is imported from `src.observability.trace_signals`
# so the audit, scorecard, and Langfuse Score paths can never disagree on
# what counts as content-type. Matches `scripts/reconcile_compile_state.py
# ::CONTENT_CATEGORIES` plus the new ontology types from the 4+2 taxonomy.

# Absolute-path detection for resolve_page output. A resolve_page miss
# that returns "/wiki/topics/foo.md" is a tool-contract violation
# even after the virtual-mode migration — output paths should be
# wiki-relative.
_RESOLVE_ABS_PATH_PAT = re.compile(r"""["']path["']\s*:\s*["']/""")

# file_path arg heuristic — same shape as trace_scorecard but we don't
# import the private pattern to keep the script self-describing on the
# narrow set of signals the audit reports. Narrowed to exclude
# ``/raw/...`` and ``/wiki/...`` prefixes because the agent prompt teaches
# those as sanctioned virtual-mode mounts, not host rootfs paths. The
# old pattern fired on every legitimate email read.
_FILE_PATH_ABS_PAT = re.compile(r"""["']file_path["']\s*:\s*["'](?!/(?:raw|wiki)/)/""")

# Per-category wiki write path — used to count `write_file` / `edit_file`
# calls that land on a content-type page (topic/system/policy/decision).
# Built from the shared ``AGENT_VISIBLE_CATEGORIES`` tuple so the
# scanner and the agent contract never drift.
_CONTENT_WRITE_PATH_PAT = re.compile(
    r"""["']file_path["']\s*:\s*["']/wiki/(?:"""
    + "|".join(re.escape(c) for c in _CONTENT_CATEGORIES)
    + r""")/"""
)

# create_entities called with an empty `entities` list or `raw_paths`
# empty is a contract violation — the coordinator would never have
# scheduled the batch that way.
_CREATE_ENTITIES_EMPTY_PAT = re.compile(
    r"""["']entities["']\s*:\s*\[\s*\]|["']raw_paths["']\s*:\s*\[\s*\]"""
)

# log_insight(category="trivial_skip", ...) in the input payload. The agent
# uses this category to flag OOO/auto-reply/junk emails as not worth
# filing — a correct outcome we must not count as synthesis failure.
_TRIVIAL_SKIP_PAT = re.compile(r"""["']category["']\s*:\s*["']trivial_skip["']""")

# log_insight(category="already_captured", ...) — the agent flagged that
# the email's content is already on an existing topic page (typically a
# prior thread-mate already compiled). Also a correct no-op outcome; we
# carve these out from the synthesis-failure denominator alongside
# trivial_skip so the "away from the North Star" signal only fires when
# the agent actually missed a synthesis opportunity (U7).
_ALREADY_CAPTURED_PAT = re.compile(r"""["']category["']\s*:\s*["']already_captured["']""")

# Verdict-paragraph thresholds. Both must clear for the sample to point
# "toward" the North Star. Tuned against PR #81's manual audit baseline.
_VERDICT_ATTEMPTED_SHARE = 0.5
_VERDICT_MEAN_CITATION = 0.5


@dataclass
class TraceAudit:
    """One trace's audit row. Flags are bools — pass/fail rubric bits."""

    trace_id: str
    model: str | None
    name: str | None
    created_at: str | None
    thread_id: str | None
    # Rubric bits — each is a binary pass/fail signal.
    attempted_content_page: bool = False
    filing_cabinet_signal: bool = False  # emails touched but only entity cites
    abs_path_violation: bool = False
    resolve_page_abs: bool = False
    create_entities_empty: bool = False
    log_insight_absent_despite_friction: bool = False
    # Trivial-skip trace: agent called `log_insight(category="trivial_skip")`
    # and made zero content/entity writes. These are GOOD outcomes (agent
    # correctly identified an email as not worth filing — OOO reply,
    # automated notification, etc.) and must NOT count as synthesis
    # failures. Excluded from the `no_content_page_attempt` aggregate and
    # reported on a separate line in the verdict.
    trivial_skip_trace: bool = False
    # Already-captured trace: agent called `log_insight(category="already_captured")`
    # and made zero content/entity writes. Correct no-op — the content
    # is already on an existing topic page (typically a prior thread-mate
    # already compiled). Sibling to trivial_skip; both get carved out of
    # the synthesis-failure denominator (U7).
    already_captured_trace: bool = False
    # Per-trace citation rate: (content-cited messages / touched messages).
    # None when no messages are attributable to this trace (run_id/thread_id
    # missing or no touches recorded).
    content_citation_rate: float | None = None
    touched_messages: int = 0
    content_cited_messages: int = 0
    # Tool-call counts we use for the note sentence.
    tool_calls: int = 0
    write_draft_page_calls: int = 0
    # `patch_page` + `write_file`/`edit_file` on content-type paths.
    # Necessary because the legacy `write_draft_page` proxy misses the
    # modern writing surface entirely.
    content_write_calls: int = 0
    create_entity_calls: int = 0
    log_insight_calls: int = 0
    trivial_skip_calls: int = 0
    already_captured_calls: int = 0
    # Human-readable note — 1 short sentence.
    note: str = ""
    # Rubric labels that fired — populated from the bits. Sorted for
    # deterministic rendering.
    flags: list[str] = field(default_factory=list)


def _parse_since(since: str) -> str:
    """Validate ``--since`` syntax and return a label for the markdown header.

    The actual trace list is "last N" (ordered by recency) — we don't use
    since as a filter, only as a documentation tag. Bad syntax raises so
    operators notice typos immediately.
    """
    if re.fullmatch(r"\d+[hd]", since.strip()) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", since.strip()):
        return since.strip()
    raise click.BadParameter(f"--since must match '24h', '7d', or 'YYYY-MM-DD' (got {since!r})")


def _list_recent_trace_ids(limit: int, env: dict[str, str]) -> list[dict[str, Any]]:
    """Fetch the last `limit` traces from Langfuse. Empty list on persistent error."""
    payload = _run_langfuse(
        ["api", "traces", "list", "--limit", str(limit)],
        env,
    )
    if payload is None:
        return []
    # v0.0.8 returns a list at top level for `traces list`; older shapes
    # wrapped it under body.data. Accept both.
    if isinstance(payload, list):
        return cast("list[dict[str, Any]]", payload)
    body = payload.get("body") if isinstance(payload, dict) else payload
    if isinstance(body, dict):
        data = body.get("data") or body.get("traces") or []
    else:
        data = body if isinstance(body, list) else []
    return cast("list[dict[str, Any]]", data) if isinstance(data, list) else []


def _fetch_trace(trace_id: str, env: dict[str, str]) -> dict[str, Any] | None:
    """Fetch one trace with retry (via `_run_langfuse`). None on persistent failure."""
    payload = _run_langfuse(["api", "traces", "get", trace_id], env)
    if payload is None:
        return None
    return cast("dict[str, Any]", payload)


def _observation_tool_name(obs: dict[str, Any]) -> str | None:
    """Return the TOOL observation's name, or None for non-tool/unnamed ones."""
    if obs.get("type") != "TOOL":
        return None
    name = str(obs.get("name") or "")
    return name or None


def _scan_trace(trace: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Extract the raw signals we score from a trace body.

    Returns ``(signals, counters)`` where signals is a flat dict of
    trace-wide facts and counters is a tool-call count by name.
    """
    body = trace.get("body") or trace
    observations = body.get("observations") or []

    counters: Counter[str] = Counter()
    abs_path = False
    resolve_abs = False
    create_empty = False
    tool_friction = False
    trivial_skip_calls = 0
    already_captured_calls = 0
    content_write_calls = 0

    for obs in observations:
        name = _observation_tool_name(obs)
        if name is None:
            continue
        counters[name] += 1
        raw_input = str(obs.get("input") or "")
        raw_output = str(obs.get("output") or "")

        if _FILE_PATH_ABS_PAT.search(raw_input):
            abs_path = True
        if name == "resolve_page" and _RESOLVE_ABS_PATH_PAT.search(raw_output):
            resolve_abs = True
        if name in ENTITY_TOOLS and _CREATE_ENTITIES_EMPTY_PAT.search(raw_input):
            create_empty = True
        if name == "log_insight" and _TRIVIAL_SKIP_PAT.search(raw_input):
            trivial_skip_calls += 1
        if name == "log_insight" and _ALREADY_CAPTURED_PAT.search(raw_input):
            already_captured_calls += 1
        if name == "patch_page":
            # Every patch_page call targets an existing content page by
            # definition — count it unconditionally.
            content_write_calls += 1
        elif name in {"write_file", "edit_file"} and _CONTENT_WRITE_PATH_PAT.search(raw_input):
            content_write_calls += 1
        # log_insight itself reports friction; treating its error-shaped
        # output as friction would double-count and defeat the metric.
        if (obs.get("level") or "").upper() == "ERROR" or (
            "error" in raw_output.lower() and name != "log_insight"
        ):
            tool_friction = True

    signals = {
        "tool_calls": sum(counters.values()),
        "abs_path_violation": abs_path,
        "resolve_page_abs": resolve_abs,
        "create_entities_empty": create_empty,
        "tool_friction": tool_friction,
        "write_draft_page_calls": counters.get("write_draft_page", 0),
        "content_write_calls": content_write_calls,
        "create_entity_calls": sum(counters.get(n, 0) for n in ENTITY_TOOLS),
        "log_insight_calls": counters.get("log_insight", 0),
        "trivial_skip_calls": trivial_skip_calls,
        "already_captured_calls": already_captured_calls,
    }
    return signals, dict(counters)


def _compute_citation_rate_for_run(
    run_id: str, thread_id: str, batch_index: int = -1
) -> tuple[int, int, float | None]:
    """Return ``(touched, content_cited, rate)`` for a batch within a run.

    "Touched" = distinct message_ids that `message_touched_pages` records
    for any page compiled in this run+thread. "Content-cited" = the subset
    whose touches include at least one content-type page. Rate is None
    when no messages are attributable.

    We bound the query to messages actually claimed by this run to avoid
    double-counting thread-mates processed in a different batch.

    ``batch_index`` is accepted for signature symmetry with
    ``trace_scorecard._list_traces_by_run`` (which keys by batch), but
    Postgres doesn't persist ``compile_batch_index`` anywhere today — the
    value only lives in Langfuse trace metadata. Per-batch citation
    filtering requires a schema change (column on ``compile_attempts``
    or ``messages``) and is explicitly deferred; the argument is carried
    through so callers can pass it unconditionally.
    """
    del batch_index  # intentionally unused; see docstring.
    sql = """
        WITH run_messages AS (
          SELECT message_id
            FROM messages
           WHERE compile_run_id = %s::uuid
             AND thread_id = %s
        )
        SELECT
          COUNT(DISTINCT rm.message_id)                                    AS touched,
          COUNT(DISTINCT rm.message_id) FILTER (
            WHERE EXISTS (
              SELECT 1
                FROM message_touched_pages mtp
                JOIN wiki_pages wp ON wp.page_id = mtp.page_id
               WHERE mtp.message_id = rm.message_id
                 AND wp.page_type = ANY(%s)
            )
          )                                                                AS content_cited
          FROM run_messages rm
    """
    try:
        with connect() as conn:
            raw = conn.execute(
                sql,
                (run_id, thread_id, list(CONTENT_PAGE_TYPES)),
            ).fetchone()
    except psycopg.Error as exc:
        logger.warning("citation_rate_query_failed", error=str(exc), run_id=run_id)
        return (0, 0, None)
    row = cast("dict[str, Any] | None", raw)
    if not row or not row["touched"]:
        return (0, 0, None)
    touched = int(row["touched"])
    cited = int(row["content_cited"])
    rate = cited / touched if touched else None
    return (touched, cited, rate)


def _build_audit(trace: dict[str, Any]) -> TraceAudit:
    """Compose a `TraceAudit` from a raw trace body + DB citation lookup."""
    body = trace.get("body") or trace
    md = body.get("metadata") or {}
    trace_id = str(body.get("id") or body.get("traceId") or "")
    model = md.get("compile_model") or md.get("model")
    run_id = md.get("compile_run_id")
    thread_id = md.get("compile_thread_id")
    batch_index = _coerce_batch_index(md.get("compile_batch_index"))
    name = body.get("name")
    created_at = body.get("createdAt") or body.get("timestamp")

    signals, _ = _scan_trace(trace)

    if run_id and thread_id:
        touched, cited, rate = _compute_citation_rate_for_run(
            str(run_id), str(thread_id), batch_index
        )
    else:
        touched, cited, rate = 0, 0, None

    # Denominator for "attempted content page" is broadened beyond the
    # legacy `write_draft_page` proxy so modern writes (`write_file` /
    # `edit_file` / `patch_page`) and catalog-observable compiles
    # (touched_messages > 0) aren't missed.
    attempted_content = (
        signals["write_draft_page_calls"] > 0 or signals["content_write_calls"] > 0 or touched > 0
    )

    # 0 touched is "couldn't evaluate", not filing-cabinet.
    filing_cabinet = touched > 0 and cited < touched

    log_insight_absent = bool(signals["tool_friction"] and signals["log_insight_calls"] == 0)

    # Trivial-skip / already-captured: agent explicitly flagged this batch's
    # emails as not worth filing (or already captured elsewhere) AND made
    # zero content/entity writes. Correct no-op outcomes — must be excluded
    # from the synthesis-failure denominator (U7).
    trivial_skip = bool(
        signals["trivial_skip_calls"] > 0
        and signals["write_draft_page_calls"] == 0
        and signals["content_write_calls"] == 0
        and signals["create_entity_calls"] == 0
    )
    already_captured = bool(
        signals["already_captured_calls"] > 0
        and signals["write_draft_page_calls"] == 0
        and signals["content_write_calls"] == 0
        and signals["create_entity_calls"] == 0
    )

    audit = TraceAudit(
        trace_id=trace_id,
        model=model,
        name=name,
        created_at=str(created_at) if created_at else None,
        thread_id=str(thread_id) if thread_id else None,
        attempted_content_page=attempted_content,
        filing_cabinet_signal=filing_cabinet,
        abs_path_violation=bool(signals["abs_path_violation"]),
        resolve_page_abs=bool(signals["resolve_page_abs"]),
        create_entities_empty=bool(signals["create_entities_empty"]),
        log_insight_absent_despite_friction=log_insight_absent,
        trivial_skip_trace=trivial_skip,
        already_captured_trace=already_captured,
        content_citation_rate=rate,
        touched_messages=touched,
        content_cited_messages=cited,
        tool_calls=int(signals["tool_calls"]),
        write_draft_page_calls=int(signals["write_draft_page_calls"]),
        content_write_calls=int(signals["content_write_calls"]),
        create_entity_calls=int(signals["create_entity_calls"]),
        log_insight_calls=int(signals["log_insight_calls"]),
        trivial_skip_calls=int(signals["trivial_skip_calls"]),
        already_captured_calls=int(signals["already_captured_calls"]),
    )
    audit.flags = _flag_labels(audit)
    audit.note = _note_for(audit)
    return audit


def _flag_labels(a: TraceAudit) -> list[str]:
    """Turn the rubric bits into stable string labels for aggregate counts.

    Note the no-op carve-out (U7): a trace where the agent explicitly tagged
    ``log_insight(category="trivial_skip")`` OR ``"already_captured"`` and
    then declined to write any page is doing the right thing, NOT failing
    at synthesis. We flag each separately (``trivial_skip`` /
    ``already_captured``) so the operator can see the counts, and we
    suppress ``no_content_page_attempt`` on those traces so the headline
    metric isn't polluted by correct no-op traces.
    """
    out: list[str] = []
    if a.trivial_skip_trace:
        out.append("trivial_skip")
    if a.already_captured_trace:
        out.append("already_captured")
    if a.filing_cabinet_signal:
        out.append("filing_cabinet")
    # Correct no-op traces (trivial_skip / already_captured) are not
    # synthesis failures.
    if not a.attempted_content_page and not a.trivial_skip_trace and not a.already_captured_trace:
        out.append("no_content_page_attempt")
    if a.abs_path_violation:
        out.append("abs_path")
    if a.resolve_page_abs:
        out.append("resolve_page_abs")
    if a.create_entities_empty:
        out.append("create_entities_empty")
    if a.log_insight_absent_despite_friction:
        out.append("log_insight_missed")
    return out


def _note_for(a: TraceAudit) -> str:
    """One-sentence description. Leads with the most-actionable flag."""
    if not a.flags:
        if a.attempted_content_page and a.content_citation_rate == 1.0:
            return "Clean: wrote content page, all touched emails cited."
        if a.tool_calls == 0:
            return "Empty trace — no tool calls (likely init/LangGraph metadata trace)."
        return "No major rubric flags fired in this snapshot."
    lead = a.flags[0]
    match lead:
        case "trivial_skip":
            return (
                f"Trivial skip: {a.trivial_skip_calls} trivial_skip insight(s), "
                "zero page writes — correct outcome, not a synthesis failure."
            )
        case "already_captured":
            return (
                f"Already captured: {a.already_captured_calls} already_captured "
                "insight(s), zero page writes — content already on an existing "
                "topic page, correct no-op."
            )
        case "filing_cabinet":
            return (
                f"Filing-cabinet: {a.content_cited_messages}/{a.touched_messages} "
                f"touched emails cited in a content-type page."
            )
        case "no_content_page_attempt":
            return (
                f"No content page write ({a.write_draft_page_calls} write_draft_page, "
                f"{a.content_write_calls} wiki-path write/edit/patch); "
                f"{a.create_entity_calls} entity calls — filing over synthesis."
            )
        case "abs_path":
            return "Absolute path used in filesystem tool input (should be repo-relative)."
        case "resolve_page_abs":
            return "resolve_page returned absolute wiki paths — reinforces bad path habit."
        case "create_entities_empty":
            return "create_entities called with empty args — contract violation."
        case "log_insight_missed":
            return "Tool friction observed but no log_insight call — reflection channel unused."
        case _:
            return ", ".join(a.flags) + "."


def _aggregate(audits: list[TraceAudit]) -> dict[str, Any]:
    """Compute aggregate counts + per-model breakdown for the markdown header.

    ``trivial_skip_count`` / ``already_captured_count`` / ``non_trivial_total``
    are exposed so the verdict paragraph can report synthesis share over
    the effective denominator (total - trivial_skip - already_captured)
    rather than the full sample — otherwise correct no-op traces
    artificially depress the attempted-content-page share (U7).
    """
    counts: Counter[str] = Counter()
    per_model: dict[str, Counter[str]] = {}
    per_model_totals: Counter[str] = Counter()

    rates: list[float] = []
    for a in audits:
        per_model_totals[a.model or "unknown"] += 1
        for flag in a.flags:
            counts[flag] += 1
            per_model.setdefault(a.model or "unknown", Counter())[flag] += 1
        if a.content_citation_rate is not None:
            rates.append(a.content_citation_rate)

    mean_citation = sum(rates) / len(rates) if rates else None

    attempted = sum(1 for a in audits if a.attempted_content_page)
    trivial_skip_count = sum(1 for a in audits if a.trivial_skip_trace)
    already_captured_count = sum(1 for a in audits if a.already_captured_trace)
    # Effective (a.k.a. "non_trivial") denominator = total minus all valid
    # no-op outcomes. A trace that somehow fires BOTH trivial_skip and
    # already_captured (shouldn't happen by construction — the `_build_audit`
    # check requires zero writes for each flag, and a trace can only have
    # one shape) is counted twice in the `_count` fields; we clamp the
    # effective total at 0 so the verdict never divides by a negative
    # number.
    non_trivial_total = max(len(audits) - trivial_skip_count - already_captured_count, 0)
    return {
        "total": len(audits),
        "flag_counts": dict(counts),
        "per_model_totals": dict(per_model_totals),
        "per_model_flags": {k: dict(v) for k, v in per_model.items()},
        "attempted_content_page": attempted,
        "mean_content_citation_rate": mean_citation,
        "traces_with_citation_data": len(rates),
        "trivial_skip_count": trivial_skip_count,
        "already_captured_count": already_captured_count,
        "non_trivial_total": non_trivial_total,
    }


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _render_markdown(
    audits: list[TraceAudit],
    aggregate: dict[str, Any],
    since_tag: str,
    limit: int,
    fetch_failures: int,
    generated_at: datetime,
    invalid_sample: bool = False,
) -> str:
    """Render the final markdown audit report.

    When ``invalid_sample`` is True — i.e. trace fetch failures exceeded
    ``FETCH_FAILURE_ABORT_RATIO`` — a banner heading is prepended at the
    top of the document so the written file is self-describing. Prior to
    this, the nonzero exit was the only signal and the rendered doc
    looked fine in isolation.
    """
    lines: list[str] = []
    if invalid_sample:
        lines.append(f"## ⚠️ SAMPLE INVALID — {fetch_failures}/{limit} fetches failed")
        lines.append("")
        lines.append(
            "_Trace fetch failure ratio exceeded the "
            f"{FETCH_FAILURE_ABORT_RATIO * 100:.0f}% abort threshold. "
            "The numbers below are unrepresentative — re-run when "
            "Langfuse stabilises._"
        )
        lines.append("")
    lines.append(f"# 50-trace audit — {generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(f"- Limit: `{limit}` · since tag: `{since_tag}`")
    lines.append(f"- Fetched: {len(audits)} / {limit} (fetch failures: {fetch_failures})")
    lines.append(
        f"- Attempted content page: **{aggregate['attempted_content_page']} / {len(audits)}**"
    )
    lines.append(
        f"- Trivial-skip traces (excluded from synthesis denominator): "
        f"**{aggregate['trivial_skip_count']} / {len(audits)}**"
    )
    lines.append(
        f"- Already-captured traces (excluded from synthesis denominator): "
        f"**{aggregate['already_captured_count']} / {len(audits)}**"
    )
    lines.append(
        "- Mean `content_page_citation_rate` (over traces with touches): "
        f"**{_fmt_pct(aggregate['mean_content_citation_rate'])}** "
        f"({aggregate['traces_with_citation_data']} traces)"
    )
    lines.append("")
    lines.append("## Aggregate flag counts")
    lines.append("")
    if aggregate["flag_counts"]:
        lines.append("| Flag | Count |")
        lines.append("|---|---:|")
        for flag, n in sorted(aggregate["flag_counts"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{flag}` | {n} |")
    else:
        lines.append("_No rubric flags fired across the sample._")
    lines.append("")

    lines.append("## Per-model breakdown")
    lines.append("")
    lines.append(
        "| Model | Traces | no_content_page_attempt | filing_cabinet | abs_path |"
        " log_insight_missed |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for model, total in sorted(aggregate["per_model_totals"].items()):
        flags = aggregate["per_model_flags"].get(model, {})
        lines.append(
            f"| `{model}` | {total} | {flags.get('no_content_page_attempt', 0)} |"
            f" {flags.get('filing_cabinet', 0)} | {flags.get('abs_path', 0)} |"
            f" {flags.get('log_insight_missed', 0)} |"
        )
    lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(_verdict_paragraph(audits, aggregate))
    lines.append("")

    lines.append("## Per-trace notes")
    lines.append("")
    for a in audits:
        model = a.model or "?"
        name = a.name or ""
        head = f"- `{a.trace_id}` | `{model}` | `{name}` | `{a.created_at or ''}`"
        lines.append(head)
        citation = (
            f" citation={_fmt_pct(a.content_citation_rate)} "
            f"({a.content_cited_messages}/{a.touched_messages})"
            if a.content_citation_rate is not None
            else ""
        )
        lines.append(f"  {a.note}{citation}")
    lines.append("")
    return "\n".join(lines)


def _verdict_paragraph(audits: list[TraceAudit], aggregate: dict[str, Any]) -> str:
    """Single-paragraph 'are we moving toward the North Star?' readout.

    Uses the effective denominator (total - trivial_skip - already_captured)
    for the synthesis-share computation so correct no-op traces don't
    artificially depress the headline metric. The synthesis verdict fires
    ONLY on non-trivial traces; trivial_skip and already_captured get their
    own dedicated sentence so operators see the counts without attributing
    them to the model's synthesis performance (U7).
    """
    total = len(audits) or 1
    attempted = aggregate["attempted_content_page"]
    trivial_skip = aggregate["trivial_skip_count"]
    already_captured = aggregate["already_captured_count"]
    non_trivial = aggregate["non_trivial_total"] or 1  # avoid /0 on all-noop sample
    mean_rate = aggregate["mean_content_citation_rate"]
    filing = aggregate["flag_counts"].get("filing_cabinet", 0)
    no_attempt = aggregate["flag_counts"].get("no_content_page_attempt", 0)
    abs_path = aggregate["flag_counts"].get("abs_path", 0)
    insight_missed = aggregate["flag_counts"].get("log_insight_missed", 0)

    toward = (
        attempted / non_trivial >= _VERDICT_ATTEMPTED_SHARE
        and (mean_rate or 0.0) >= _VERDICT_MEAN_CITATION
    )
    direction = "toward" if toward else "away from"
    rate_str = _fmt_pct(mean_rate) if mean_rate is not None else "n/a"
    return (
        f"Sample of {total} traces points **{direction}** the North Star. "
        f"Of {non_trivial} non-trivial compile attempts, "
        f"{attempted} ({attempted / non_trivial * 100:.0f}%) attempted a content page; "
        f"{trivial_skip} trivial-skip traces and "
        f"{already_captured} already-captured traces are excluded from "
        f"the synthesis denominator. "
        f"Mean `content_page_citation_rate` = {rate_str}. "
        f"{no_attempt} non-trivial traces made no `write_draft_page` call, "
        f"{filing} showed filing-cabinet behaviour (emails touched without content-type "
        f"citation), {abs_path} used absolute paths, and "
        f"{insight_missed} had visible friction but no `log_insight` call. "
        "See the per-trace notes below for one-sentence diagnoses."
    )


def _audit_path(out_dir: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return out_dir / f"audit-{stamp}.md"


@click.command()
@click.option(
    "--limit",
    default=50,
    show_default=True,
    help="Number of recent traces to audit.",
)
@click.option(
    "--since",
    default="24h",
    show_default=True,
    help="Documentation tag only ('24h'/'7d'/'YYYY-MM-DD'). Trace set is 'last N'.",
)
def main(limit: int, since: str) -> None:
    """Audit the last N Langfuse traces and write a markdown report."""
    since_tag = _parse_since(since)
    env = _langfuse_env()

    out_dir = REPO_ROOT / "docs" / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    out_path = _audit_path(out_dir, now)

    logger.info("audit_start", limit=limit, since=since_tag)
    trace_list = _list_recent_trace_ids(limit, env)
    if not trace_list:
        click.echo("langfuse unreachable or returned no traces", err=True)
        sys.exit(2)

    trace_ids = [str(t.get("id") or t.get("traceId")) for t in trace_list]
    trace_ids = [tid for tid in trace_ids if tid]
    logger.info("trace_list_fetched", count=len(trace_ids))

    audits: list[TraceAudit] = []
    failures = 0
    for i, tid in enumerate(trace_ids, 1):
        logger.info("fetch_trace", tid=tid[:12], progress=f"{i}/{len(trace_ids)}")
        trace = _fetch_trace(tid, env)
        if trace is None:
            failures += 1
            continue
        audits.append(_build_audit(trace))

    aggregate = _aggregate(audits)
    # Compute the invalid-sample flag BEFORE rendering so the banner
    # lands in the written file (not just on stderr). Mirror the
    # post-write threshold check so both paths use the same ratio.
    attempted = len(trace_ids)
    failure_ratio = failures / attempted if attempted else 0.0
    invalid_sample = failure_ratio > FETCH_FAILURE_ABORT_RATIO
    md = _render_markdown(
        audits,
        aggregate,
        since_tag,
        limit,
        failures,
        now,
        invalid_sample=invalid_sample,
    )
    out_path.write_text(md, encoding="utf-8")

    click.echo(str(out_path))

    # Threshold check runs AFTER writing so the operator can see the
    # partial audit before the nonzero exit.
    if invalid_sample:
        click.echo(
            f"ERROR: {failures}/{attempted} trace fetches failed "
            f"({failure_ratio * 100:.0f}% > "
            f"{FETCH_FAILURE_ABORT_RATIO * 100:.0f}% threshold). "
            "Audit likely unrepresentative — re-run when Langfuse stabilises.",
            err=True,
        )
        sys.exit(3)

    logger.info(
        "audit_done",
        path=str(out_path),
        audits=len(audits),
        fetch_failures=failures,
    )


if __name__ == "__main__":
    main()
