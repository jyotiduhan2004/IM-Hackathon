"""Post-run metrics dipstick — does the prompt revamp actually land?

Runs at the END of every ``make compile`` invocation (or standalone) to
emit a markdown dipstick of 10 metrics tracking whether the behaviors
the new prompt is supposed to teach are showing up in production.

Tier 1 (must-land):
    M1  % of new pages with ``owner:`` frontmatter (target ≥80%)
    M2  % of new pages with ≥2-sentence lead paragraph mentioning a
        number (target ≥70%)
    M3  log_insight non-skip categories per batch (target ≥1/batch)
    M4  check_my_work pre-write rate (target 0% — middleware enforces)

Tier 2 (regression watch):
    M5  % pages with ``## TL;DR`` H2 (target → 0)
    M6  % pages with strikethrough ``~~`` (target 0)
    M7  median people-wikilinks per page (target ≤2-3)
    M8  reviewer pass-on-first-cycle rate (target ≥60%)

Tier 3 (cost / shape):
    M9  prompt tokens per batch (delta vs prior baseline)
    M10 archetype distribution of new pages

Usage::

    # post-compile (auto-wired in scripts/compile_all.py)
    uv run python scripts/post_run_metrics.py --run-id <UUID>

    # baseline against the current wiki (no run-id; whole-wiki snapshot)
    uv run python scripts/post_run_metrics.py

    # tighter window — last 1 hour of mtimes counts as "new"
    uv run python scripts/post_run_metrics.py --since "1 hour ago"
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from statistics import median
from typing import Any
from typing import cast
from uuid import UUID

import click
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.nightly_trace_audit import _fetch_trace  # noqa: E402
from scripts.nightly_trace_audit import _list_recent_traces  # noqa: E402
from src.config import settings  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402

logger = structlog.get_logger(__name__)

# Audit dir is the canonical home for repo-side dashboards.
AUDITS_DIR = REPO_ROOT / "docs" / "audits"
REPORT_PREFIX = "post-run-metrics"
HISTORY_PATH = AUDITS_DIR / "metrics-history.jsonl"
DASHBOARD_PATH = AUDITS_DIR / "dashboard.md"
PROMPTS_FILE = REPO_ROOT / "src" / "compile" / "prompts.py"

# Trace-derivable metrics we push to Langfuse as scores. Wiki-side
# metrics (M1, M2, M5, M6, M7, M10) live only in the JSONL — they don't
# fit the trace-score model.
TRACE_METRICS_TO_PUSH: tuple[tuple[str, str], ...] = (
    ("M3", "log_insight_active_teaching_per_email"),
    ("M4", "check_my_work_pre_write_rate"),
    ("M8", "reviewer_pass_first_cycle_rate"),
    ("M9", "prompt_tokens_avg_per_trace"),
)

# Page categories that count as "content" for archetype + lead-paragraph
# checks. People + glossary are explicitly excluded (Q12.1 + glossary is
# auto-generated).
CONTENT_CATEGORIES: tuple[str, ...] = ("topics", "systems", "policies", "decisions")

# Detection patterns
TLDR_PAT = re.compile(r"^##\s+TL;DR\s*$", re.MULTILINE | re.IGNORECASE)
STRIKETHROUGH_PAT = re.compile(r"~~[^~\n]+~~")
# M2 quality signal — a "real number" in the lead paragraph means a page
# is grounded in measurable state (rollout %, latency ms, conversion x or
# Unicode multiplication sign, count of segments). Plain `\d` was too
# loose: order IDs, message-id hashes, PR numbers (`#260`), and bare ISO
# dates would all pass. This pattern requires either:
#   - a percentage / multiplier / unit suffix (`12%`, `1.4x`, `42ms`, `2.5s`,
#     `1.2GB`, `100bps`, `40INR`, `5k`, `2M`, `3B`)
#   - a comparison context (`>3s`, `< 50%`, `>=80%`)
#   - a comma-grouped large number (`12,400`)
#   - a decimal (`3.14`)
# Bare integers >=4 digits are rejected unless they're flagged as a count
# via nearby unit/keyword — the goal is to reward "Live on 12% of buyers"
# over "raw/2026-01-08_1234abcd.md" or "PR #260".
# Unit suffixes ordered LONGEST-FIRST inside each character family. Critical:
# regex alternation tries left-to-right and stops at the first match. Without
# longest-first, `42ms` would match `42m` (and burn the trailing `s`); `100bps`
# would match `100b`. Multi-char units must precede their single-char prefixes.
# The trailing `(?=\W|$|\d)` lookahead prevents `5 days` from matching `5 d`
# (single-letter unit eats the first letter of an English word).
_NUMBER_UNIT_RE = (
    r"(?:"
    # currency / per-mille / percentage — unambiguous, no boundary needed
    r"%|‱|‰|\$|₹|€|£|¢"
    r"|"
    # multipliers (2-letter must precede 1-letter)
    r"bps|bp|"
    r"ms|min|mo|m"  # ms / min / mo before bare m
    r"|"
    r"hr|h|"
    r"qtr|"
    r"wk|d|s|"
    r"GB|MB|KB|TB|"
    r"INR|USD|EUR|GBP|"
    r"k|K|M|B|x|×"  # noqa: RUF001 — U+00D7 matches "1.4x" prose
    r")"
)
NUMBER_PAT = re.compile(
    r"(?:"
    # decimal (3.14, 0.5)
    r"\d+\.\d+"
    r"|"
    # comma-grouped (12,400)
    r"\d{1,3}(?:,\d{3})+"
    r"|"
    # integer + unit (12%, 42ms, 5k, 2M) — must end on word boundary OR digit
    # (so 1.2GB / 5k matches but `5 days` doesn't catch `5 d`).
    r"\d+\s*" + _NUMBER_UNIT_RE + r"(?=\W|$|\d)"
    r"|"
    # comparison + integer (>3, <50, ≥80, ≤100)
    r"[<>≤≥]\s*\d+"
    r")"
)
PEOPLE_WIKILINK_PAT = re.compile(r"\[\[people/[^\]|]+(?:\|[^\]]+)?\]\]")
SENTENCE_END_PAT = re.compile(r"[.!?](?=\s|$)")

# Archetype tag synonyms — drives the M10 distribution. Order matters:
# first hit wins (so ``decision`` beats ``launch`` if a page is tagged
# both — decisions are the more specific archetype).
ARCHETYPE_TAG_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("decision", ("decision", "decisions")),
    ("policy", ("policy", "policies")),
    ("bug", ("bug", "bugs", "incident", "outage", "regression")),
    ("launch", ("launch", "rollout", "release", "ga")),
    ("system", ("system", "platform", "tool")),
)

# Non-skip log_insight categories that signal the agent's actively
# teaching us — Q17 active-teaching loop.
ACTIVE_TEACHING_CATEGORIES: frozenset[str] = frozenset(
    {"prompt_ambiguity", "tool_gap", "structure_suggestion", "question_for_human"}
)

NOOP_INSIGHT_CATEGORIES: frozenset[str] = frozenset({"trivial_skip", "already_captured"})


@dataclass
class MetricResult:
    """One metric's numeric value + supporting evidence count.

    `unit` controls fmt: "pct" → multiply by 100 + append %; "raw" →
    plain float. We don't infer from `target` because targets like
    "≥1/batch" or "≤3" are NOT percentages even though they start with
    a comparison glyph.
    """

    name: str
    label: str
    value: float | None
    target: str
    sample_size: int
    unit: str = "raw"  # "pct" | "raw"
    note: str = ""

    def fmt_value(self) -> str:
        if self.value is None:
            return "-"
        if self.unit == "pct":
            return f"{self.value * 100:.1f}%"
        return f"{self.value:.2f}"


@dataclass
class Report:
    run_id: str | None
    generated_at: str
    since: str | None
    new_pages_total: int
    metrics: list[MetricResult] = field(default_factory=list)
    archetype_dist: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Page selection
# --------------------------------------------------------------------------


def _new_pages_since(wiki_dir: Path, since: datetime | None) -> list[Path]:
    """Return content-page paths created/modified after `since`.

    When `since` is None → returns every content page on disk (baseline mode).
    """
    pages: list[Path] = []
    cutoff = since.timestamp() if since else None
    for cat in CONTENT_CATEGORIES:
        d = wiki_dir / cat
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            if cutoff is None:
                pages.append(p)
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            if stat.st_mtime >= cutoff or stat.st_ctime >= cutoff:
                pages.append(p)
    return pages


# --------------------------------------------------------------------------
# Filesystem-driven metrics (M1, M2, M5, M6, M7, M10)
# --------------------------------------------------------------------------


def _has_owner_frontmatter(fm: dict[str, Any]) -> bool:
    """M1: is `owner:` set to a non-empty string in frontmatter?"""
    owner = fm.get("owner")
    if isinstance(owner, str):
        return bool(owner.strip())
    if isinstance(owner, list):
        return any(isinstance(x, str) and x.strip() for x in owner)
    return False


def _lead_paragraph(body: str) -> str:
    """Return the first non-empty, non-heading paragraph after the title."""
    paragraph_lines: list[str] = []
    seen_text = False
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        # Skip leading blanks until the first paragraph starts.
        if not line:
            if seen_text:
                break
            continue
        # Headings / lists / tables / blockquotes don't count as a lead
        # paragraph — Q1.1 wants prose with a number.
        if line.lstrip().startswith(("#", "-", "*", "|", ">", "```")):
            if seen_text:
                break
            continue
        seen_text = True
        paragraph_lines.append(line)
    return " ".join(paragraph_lines).strip()


def _lead_has_number_and_two_sentences(body: str) -> bool:
    """M2: ≥2 sentences AND at least one digit in the lead paragraph."""
    lead = _lead_paragraph(body)
    if not lead:
        return False
    if not NUMBER_PAT.search(lead):
        return False
    # Count sentence-enders. Two terminal punctuation marks → ≥2 sentences.
    return len(SENTENCE_END_PAT.findall(lead)) >= 2


def _has_tldr_h2(body: str) -> bool:
    """M5: page contains ``## TL;DR`` (case-insensitive)."""
    return TLDR_PAT.search(body) is not None


def _has_strikethrough(body: str) -> bool:
    """M6: page contains ``~~strikethrough~~``."""
    return STRIKETHROUGH_PAT.search(body) is not None


def _people_wikilink_count(body: str) -> int:
    """M7: count ``[[people/...]]`` wikilinks on a page."""
    return len(PEOPLE_WIKILINK_PAT.findall(body))


def _detect_archetype(fm: dict[str, Any], body: str, page_dir: str) -> str:
    """M10: classify a page into an archetype bucket.

    Order: directory-derived > tags > body-keyword fallback > 'other'.
    """
    if page_dir == "decisions":
        return "decision"
    if page_dir == "policies":
        return "policy"
    if page_dir == "systems":
        return "system"
    tags_raw = fm.get("tags") or []
    tags = {str(t).lower() for t in tags_raw if isinstance(t, str)}
    for archetype, synonyms in ARCHETYPE_TAG_MAP:
        if any(syn in tags for syn in synonyms):
            return archetype
    # Body-keyword fallback for topic pages — looks for a "## Launch" /
    # "## Bug" / "## Incident" H2. Cheap heuristic.
    head_section_pat = re.compile(r"^##\s+(\w+)", re.MULTILINE)
    for match in head_section_pat.finditer(body):
        head = match.group(1).lower()
        if head in ("launch", "rollout", "release"):
            return "launch"
        if head in ("bug", "incident", "outage"):
            return "bug"
    return "other"


def compute_filesystem_metrics(pages: list[Path]) -> dict[str, Any]:
    """Walk new pages and emit M1, M2, M5, M6, M7, M10."""
    if not pages:
        return {
            "owner_rate": None,
            "lead_with_number_rate": None,
            "tldr_rate": None,
            "strikethrough_rate": None,
            "people_link_median": None,
            "archetype_dist": {},
            "people_link_counts": [],
        }
    owner_hits = 0
    lead_hits = 0
    tldr_hits = 0
    strike_hits = 0
    people_counts: list[int] = []
    archetypes: dict[str, int] = {}
    for p in pages:
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = extract_frontmatter(content)
        body = extract_body(content)
        if _has_owner_frontmatter(fm):
            owner_hits += 1
        if _lead_has_number_and_two_sentences(body):
            lead_hits += 1
        if _has_tldr_h2(body):
            tldr_hits += 1
        if _has_strikethrough(body):
            strike_hits += 1
        people_counts.append(_people_wikilink_count(body))
        bucket = _detect_archetype(fm, body, p.parent.name)
        archetypes[bucket] = archetypes.get(bucket, 0) + 1
    n = len(pages)
    return {
        "owner_rate": owner_hits / n,
        "lead_with_number_rate": lead_hits / n,
        "tldr_rate": tldr_hits / n,
        "strikethrough_rate": strike_hits / n,
        "people_link_median": median(people_counts) if people_counts else 0,
        "archetype_dist": archetypes,
        "people_link_counts": people_counts,
    }


# --------------------------------------------------------------------------
# DB-driven metrics (M3 from compile_insights)
# --------------------------------------------------------------------------


def _insight_active_teaching_per_batch(
    run_id: UUID | None, since: datetime | None
) -> tuple[float | None, int, str]:
    """M3: average count of non-skip log_insight calls per batch in the run.

    Reads ``compile_insights`` rows for the run. "Per batch" is approximated
    by counting calls divided by the run's emails_processed; for pure
    "did the agent teach us anything?" signal a per-run total is also
    fine — we report both.

    Returns (rate_per_batch_or_run, total_calls, note).
    """
    import psycopg

    try:
        from src.db import connect
    except ImportError as exc:
        return None, 0, f"db module unavailable: {exc}"
    note = ""
    raw_row: Any = None
    emails_row_raw: Any = None
    try:
        with connect() as conn:
            if run_id is not None:
                raw_row = conn.execute(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE category = ANY(%s))      AS active,
                      COUNT(*) FILTER (WHERE category = ANY(%s))      AS noop,
                      COUNT(*)                                        AS total
                      FROM compile_insights
                     WHERE run_id = %s
                    """,
                    (
                        list(ACTIVE_TEACHING_CATEGORIES),
                        list(NOOP_INSIGHT_CATEGORIES),
                        run_id,
                    ),
                ).fetchone()
                emails_row_raw = conn.execute(
                    "SELECT emails_processed FROM compile_runs WHERE run_id = %s",
                    (run_id,),
                ).fetchone()
                emails_row = cast("dict[str, Any] | None", emails_row_raw)
                emails = (
                    int(emails_row["emails_processed"])
                    if emails_row and emails_row.get("emails_processed")
                    else 0
                )
                note = f"emails_processed={emails}"
            else:
                # Baseline: last 24h of insights — ignores run_id.
                cutoff = since or (datetime.now(UTC) - timedelta(hours=24))
                raw_row = conn.execute(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE category = ANY(%s))      AS active,
                      COUNT(*) FILTER (WHERE category = ANY(%s))      AS noop,
                      COUNT(*)                                        AS total
                      FROM compile_insights
                     WHERE created_at >= %s
                    """,
                    (
                        list(ACTIVE_TEACHING_CATEGORIES),
                        list(NOOP_INSIGHT_CATEGORIES),
                        cutoff,
                    ),
                ).fetchone()
                emails = 0
                note = f"baseline since={cutoff.isoformat()}"
    except psycopg.Error as exc:
        logger.warning("insight_query_failed", error=str(exc))
        return None, 0, f"db query failed: {exc}"
    row = cast("dict[str, Any] | None", raw_row)
    if row is None:
        return 0.0, 0, note
    active = int(row.get("active", 0) or 0)
    total = int(row.get("total", 0) or 0)
    if run_id is not None and emails > 0:
        return active / emails, total, note
    # Baseline / no emails recorded → return raw count as the value.
    return float(active), total, note


# --------------------------------------------------------------------------
# Langfuse-driven metrics (M4, M8, M9)
# --------------------------------------------------------------------------
#
# The fetch / list helpers are imported from `scripts.nightly_trace_audit`
# so the langfuse-cli pin, retry policy, and env handling stay in one
# place. We only own the run-filter + signal extraction here.


def _trace_belongs_to_run(trace: dict[str, Any], run_id: UUID | None) -> bool:
    if run_id is None:
        return True
    body = trace.get("body") or trace
    metadata = body.get("metadata") or {}
    return str(metadata.get("compile_run_id") or "") == str(run_id)


def _trace_signals(trace: dict[str, Any]) -> dict[str, Any]:
    """Pull tool sequence + reviewer verdict + token usage from one trace.

    `reviewer_verdict` captures the FIRST `task(reviewer)` verdict found
    in observation order — that's first-cycle semantics. If the agent
    re-invokes the reviewer after a fix, those subsequent verdicts are
    intentionally ignored: M8 is "did the page pass on the first
    review attempt?". Re-review-after-fix counts as the agent
    recovering, not as a first-cycle pass.
    """
    from src.observability.trace_signals import REVIEWER_VERDICT_PAT

    body = trace.get("body") or trace
    observations = body.get("observations") or []
    tool_seq: list[str] = []
    reviewer_verdict: str | None = None
    cmw_first_call_before_write = False
    seen_write = False
    seen_check = False
    prompt_tokens = 0
    completion_tokens = 0
    for obs in observations:
        otype = obs.get("type")
        if otype == "GENERATION":
            usage = obs.get("usage") or {}
            prompt_tokens += int(usage.get("input") or usage.get("promptTokens") or 0)
            completion_tokens += int(usage.get("output") or usage.get("completionTokens") or 0)
            continue
        if otype != "TOOL":
            continue
        name = str(obs.get("name") or "")
        if not name:
            continue
        tool_seq.append(name)
        out = str(obs.get("output") or "")
        if reviewer_verdict is None:
            verdict_match = REVIEWER_VERDICT_PAT.search(out)
            if verdict_match:
                reviewer_verdict = verdict_match.group(1).lower()
        if name in {"write_file", "edit_file", "patch_page", "write_draft_page"}:
            if not seen_check:
                seen_write = True
        elif name == "check_my_work" and not seen_write and not seen_check:
            cmw_first_call_before_write = True
            seen_check = True
    return {
        "tool_seq": tool_seq,
        "reviewer_verdict": reviewer_verdict,
        "cmw_premature": cmw_first_call_before_write,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def compute_langfuse_metrics(run_id: UUID | None, limit: int = 50) -> dict[str, Any]:
    """M4 (cmw pre-write rate), M8 (reviewer first-cycle pass rate), M9 (prompt tokens).

    `reviewer_pass_rate`: of traces that had a reviewer verdict at all,
    fraction whose FIRST verdict was "pass". Subsequent re-reviews
    after a fix don't bump the numerator — see `_trace_signals`.

    Returns a dict with rates + a warning string when langfuse is
    unreachable. Never raises.
    """
    out: dict[str, Any] = {
        "cmw_premature_rate": None,
        "reviewer_pass_rate": None,
        "prompt_tokens_avg": None,
        "trace_count": 0,
        "warning": "",
    }
    listed = _list_recent_traces(limit)
    if not listed:
        out["warning"] = "langfuse list empty / unreachable"
        return out
    matched = []
    for t in listed:
        tid = str(t.get("id") or t.get("tid") or "")
        if not tid:
            continue
        full = _fetch_trace(tid)
        if full.get("error"):
            continue
        if not _trace_belongs_to_run(full, run_id):
            continue
        matched.append(_trace_signals(full))
    if not matched:
        out["warning"] = "no traces matched run_id" if run_id else "no traces fetched"
        return out
    n = len(matched)
    out["trace_count"] = n
    cmw_premature_n = sum(1 for s in matched if s["cmw_premature"])
    out["cmw_premature_rate"] = cmw_premature_n / n
    pass_n = sum(1 for s in matched if s["reviewer_verdict"] == "pass")
    has_verdict = sum(1 for s in matched if s["reviewer_verdict"] is not None)
    out["reviewer_pass_rate"] = (pass_n / has_verdict) if has_verdict else None
    out["prompt_tokens_avg"] = sum(s["prompt_tokens"] for s in matched) / n if n else None
    return out


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------


def _build_metrics(
    fs: dict[str, Any],
    insights: tuple[float | None, int, str],
    lf: dict[str, Any],
) -> list[MetricResult]:
    insight_rate, insight_total, insight_note = insights
    n_pages = fs.get("sample_size_pages", 0)
    n_traces = lf["trace_count"]
    return [
        MetricResult(
            "M1",
            "% new pages with `owner:` frontmatter",
            fs["owner_rate"],
            "≥80%",
            sample_size=n_pages,
            unit="pct",
        ),
        MetricResult(
            "M2",
            "% new pages with ≥2-sentence lead paragraph + a number",
            fs["lead_with_number_rate"],
            "≥70%",
            sample_size=n_pages,
            unit="pct",
        ),
        MetricResult(
            "M3",
            "log_insight active-teaching calls per email",
            insight_rate,
            "≥1/batch",
            sample_size=insight_total,
            note=insight_note,
        ),
        MetricResult(
            "M4",
            "check_my_work pre-write rate",
            lf["cmw_premature_rate"],
            "0%",
            sample_size=n_traces,
            unit="pct",
            note=lf.get("warning", ""),
        ),
        MetricResult(
            "M5",
            "% pages with `## TL;DR` H2",
            fs["tldr_rate"],
            "→0%",
            sample_size=n_pages,
            unit="pct",
        ),
        MetricResult(
            "M6",
            "% pages with strikethrough `~~`",
            fs["strikethrough_rate"],
            "0%",
            sample_size=n_pages,
            unit="pct",
        ),
        MetricResult(
            "M7",
            "median people-wikilinks per page",
            float(fs["people_link_median"]) if fs["people_link_median"] is not None else None,
            "≤3",
            sample_size=n_pages,
        ),
        MetricResult(
            "M8",
            "reviewer pass-on-first-cycle rate",
            lf["reviewer_pass_rate"],
            "≥60%",
            sample_size=n_traces,
            unit="pct",
            note=lf.get("warning", ""),
        ),
        MetricResult(
            "M9",
            "average prompt tokens per trace",
            lf["prompt_tokens_avg"],
            "track",
            sample_size=n_traces,
            note=lf.get("warning", ""),
        ),
        MetricResult(
            "M10",
            "archetype distribution (see table below)",
            None,
            "balanced",
            sample_size=n_pages,
        ),
    ]


def build_report(
    run_id: UUID | None,
    since: datetime | None,
    wiki_dir: Path,
    skip_langfuse: bool = False,
) -> Report:
    pages = _new_pages_since(wiki_dir, since)
    fs = compute_filesystem_metrics(pages)
    fs["sample_size_pages"] = len(pages)
    insights = _insight_active_teaching_per_batch(run_id, since)
    if skip_langfuse:
        lf: dict[str, Any] = {
            "cmw_premature_rate": None,
            "reviewer_pass_rate": None,
            "prompt_tokens_avg": None,
            "trace_count": 0,
            "warning": "skipped (--skip-langfuse)",
        }
    else:
        lf = compute_langfuse_metrics(run_id)
    metrics = _build_metrics(fs, insights, lf)
    warnings: list[str] = []
    if lf.get("warning"):
        warnings.append(f"langfuse: {lf['warning']}")
    if insights[2].startswith("db query failed"):
        warnings.append(f"insights: {insights[2]}")
    return Report(
        run_id=str(run_id) if run_id else None,
        generated_at=datetime.now(UTC).isoformat(),
        since=since.isoformat() if since else None,
        new_pages_total=len(pages),
        metrics=metrics,
        archetype_dist=fs["archetype_dist"],
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Comparison vs prior report
# --------------------------------------------------------------------------


def _find_prior_report(audits_dir: Path, exclude: Path) -> Path | None:
    """Most recent ``post-run-metrics-*.md`` in audits_dir, excluding `exclude`."""
    if not audits_dir.exists():
        return None
    candidates = [
        p for p in audits_dir.glob(f"{REPORT_PREFIX}-*.md") if p.resolve() != exclude.resolve()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _parse_prior_metrics(path: Path) -> dict[str, float]:
    """Pull a name→value map from a prior report's metrics table.

    Looks for lines like ``| M1 | ... | 82.3% | ... |`` — the first
    percentage / number after the metric name. Best-effort: returns
    {} if the table is absent or malformed.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, float] = {}
    row_pat = re.compile(r"^\|\s*(M\d+)\s*\|[^|]+\|\s*([0-9.]+)%?\s*\|", re.MULTILINE)
    for match in row_pat.finditer(content):
        name = match.group(1)
        value = float(match.group(2))
        # Heuristic: anything ending with % in the source row was a rate.
        if "%" in match.group(0):
            value = value / 100
        out[name] = value
    return out


def _delta_str(current: float | None, prior: float | None, is_pct: bool) -> str:
    if current is None or prior is None:
        return "-"
    delta = current - prior
    sign = "+" if delta >= 0 else ""
    if is_pct:
        return f"{sign}{delta * 100:.1f}pp"
    return f"{sign}{delta:.2f}"


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------


def render_markdown(report: Report, prior: dict[str, float] | None) -> str:
    lines: list[str] = []
    title = f"# Post-run metrics — {report.generated_at}"
    lines.extend([title, ""])
    if report.run_id:
        lines.append(f"**Run ID**: `{report.run_id}`")
    if report.since:
        lines.append(f"**Window since**: {report.since}")
    lines.append(f"**New content pages this window**: {report.new_pages_total}")
    if report.warnings:
        lines.append("")
        lines.append("> Partial report — collection warnings:")
        for w in report.warnings:
            lines.append(f"> - {w}")
    lines.extend(["", "## Metrics", ""])
    if prior:
        lines.append("| ID | Metric | Value | Target | n | Δ vs prior | Notes |")
        lines.append("|---|---|---:|---|---:|---:|---|")
    else:
        lines.append("| ID | Metric | Value | Target | n | Notes |")
        lines.append("|---|---|---:|---|---:|---|")
    for m in report.metrics:
        v = m.fmt_value()
        cells = [m.name, m.label, v, m.target, str(m.sample_size)]
        if prior:
            delta = _delta_str(
                m.value,
                prior.get(m.name),
                is_pct=(m.unit == "pct"),
            )
            cells.append(delta)
        cells.append(m.note or "")
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "## M10 — archetype distribution", ""])
    if not report.archetype_dist:
        lines.append("_(no pages in window)_")
    else:
        lines.append("| Archetype | Count | Share |")
        lines.append("|---|---:|---:|")
        total = sum(report.archetype_dist.values())
        for arch, n in sorted(report.archetype_dist.items(), key=lambda kv: -kv[1]):
            share = (n / total * 100) if total else 0.0
            lines.append(f"| {arch} | {n} | {share:.1f}% |")
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Layer 1 — Append-only JSONL history
# --------------------------------------------------------------------------
#
# Every run's metrics row is appended to ``docs/audits/metrics-history.jsonl``
# tagged with the prompt-version SHA so pre-revamp vs post-revamp runs are
# queryable as discrete cohorts. The dashboard renderer reads this file.


def _prompt_commit_sha() -> str:
    """Short SHA of the last commit touching ``src/compile/prompts.py``.

    Returns the literal string ``"unknown"`` (not ``None``) on any
    failure so the JSONL field is always a string. Tests + CI without
    the prompts file in git history shouldn't crash the dipstick.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--abbrev=7", "--format=%h", "--", str(PROMPTS_FILE)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha or "unknown"


def _resolve_run_meta(run_id: UUID | None) -> dict[str, Any]:
    """Look up model + emails_processed for a run, if available.

    Best-effort — DB hiccups return empty dict. Used to enrich JSONL
    rows so the dashboard can group/filter by model.
    """
    if run_id is None:
        return {}
    import psycopg

    try:
        from src.db import connect

        with connect() as conn:
            row_raw = conn.execute(
                "SELECT model, emails_processed FROM compile_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
    except (psycopg.Error, ImportError) as exc:
        logger.warning("run_meta_lookup_failed", run_id=str(run_id), error=str(exc))
        return {}
    row = cast("dict[str, Any] | None", row_raw)
    if not row:
        return {}
    return {
        "model": row.get("model") or "unknown",
        "pages_compiled_this_run": int(row.get("emails_processed") or 0),
    }


def _wiki_pages_total(wiki_dir: Path) -> int:
    """Count content pages on disk — coarse 'how big is the wiki' signal."""
    n = 0
    for cat in CONTENT_CATEGORIES:
        d = wiki_dir / cat
        if d.exists():
            n += sum(1 for _ in d.glob("*.md"))
    return n


def _metrics_dict(report: Report) -> dict[str, float | None]:
    """Flatten ``report.metrics`` into a name→value dict for JSONL/scores.

    M10 is the archetype distribution — it has no scalar value, so it's
    skipped here. The distribution itself ships separately on the row
    via ``archetype_dist``.
    """
    out: dict[str, float | None] = {}
    for m in report.metrics:
        if m.name == "M10":
            continue
        out[m.name] = float(m.value) if m.value is not None else None
    return out


def append_to_history(
    report: Report,
    run_id: UUID | None,
    prompt_commit_sha: str,
    wiki_dir: Path,
    history_path: Path = HISTORY_PATH,
) -> Path:
    """Append one JSON line per run to ``metrics-history.jsonl``.

    Idempotent-ish: we don't dedupe by run_id (the dipstick is meant
    to be called once per completed run). If you re-run the dipstick
    on the same run_id you'll get two rows — the dashboard's "latest"
    view will pick the newer one by timestamp.
    """
    meta = _resolve_run_meta(run_id)
    metrics = _metrics_dict(report)
    row: dict[str, Any] = {
        "run_id": str(run_id) if run_id else None,
        "timestamp": report.generated_at,
        "prompt_commit_sha": prompt_commit_sha,
        "model": meta.get("model", "unknown"),
        "pages_total": _wiki_pages_total(wiki_dir),
        "pages_compiled_this_run": meta.get("pages_compiled_this_run", 0),
        "new_pages_window": report.new_pages_total,
        "metrics": metrics,
        "archetype_dist": dict(report.archetype_dist),
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return history_path


def read_history(history_path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    """Read the JSONL history file, skipping malformed lines.

    Returns rows in file order (oldest first). The dashboard renderer
    handles its own sorting.
    """
    if not history_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("metrics_history_bad_line", line=line[:120])
            continue
    return rows


# --------------------------------------------------------------------------
# Layer 3 — Langfuse score push (trace-derived metrics only)
# --------------------------------------------------------------------------


def push_langfuse_scores(
    report: Report,
    run_id: UUID | None,
    prompt_commit_sha: str,
) -> int:
    """Push trace-derivable metrics as Langfuse scores. Returns count pushed.

    Failure mode: any langfuse error logs a warning + returns 0. We
    NEVER block the JSONL append or compile loop on score-push.

    Scoring strategy: scores attach to a daily aggregate session
    ``compile-metrics-YYYY-MM-DD`` so the Langfuse UI can show
    time-series charts of metric values across runs without
    polluting individual compile traces.
    """
    metrics = _metrics_dict(report)
    pushable = [
        (mid, score_name, metrics.get(mid))
        for mid, score_name in TRACE_METRICS_TO_PUSH
        if metrics.get(mid) is not None
    ]
    if not pushable:
        return 0
    # Reuse the existing Langfuse client builder + safe-flush helpers from
    # `src.observability.langfuse_scores`. Returns None when Langfuse is
    # disabled or keys are missing — we silently no-op rather than crash.
    # `create_score` is called inline because metrics scores are session-
    # scoped (`session_id=compile-metrics-YYYY-MM-DD`) whereas the helper
    # `_push_score` is trace-scoped — different shape.
    from src.observability.langfuse_scores import _build_client
    from src.observability.langfuse_scores import _safe_flush

    client = _build_client()
    if client is None:
        return 0

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    session_id = f"compile-metrics-{today}"
    comment = f"run_id={run_id} prompt_sha={prompt_commit_sha}"
    pushed = 0
    for _mid, name, value in pushable:
        try:
            client.create_score(
                session_id=session_id,
                name=name,
                value=float(value) if value is not None else 0.0,
                data_type="NUMERIC",
                comment=comment,
            )
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse_metric_score_push_failed",
                name=name,
                error=str(exc)[:200],
            )
    _safe_flush(client)
    return pushed


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_since(arg: str | None) -> datetime | None:
    if not arg:
        return None
    lowered = arg.strip().lower()
    if lowered in ("none", "all"):
        return None
    match = re.match(r"^(\d+)\s+(second|minute|hour|day)s?\s+ago$", lowered)
    now = datetime.now(UTC)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        delta = {
            "second": timedelta(seconds=n),
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
        }[unit]
        return now - delta
    try:
        return datetime.fromisoformat(lowered.replace("z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter(f"unrecognized --since value: {arg}") from exc


def _resolve_run_started_at(run_id: UUID) -> datetime | None:
    """Look up `compile_runs.started_at` so the FS window matches the run."""
    import psycopg

    try:
        from src.db import connect

        with connect() as conn:
            raw_row = conn.execute(
                "SELECT started_at FROM compile_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
    except (psycopg.Error, ImportError) as exc:
        logger.warning("run_lookup_failed", run_id=str(run_id), error=str(exc))
        return None
    row = cast("dict[str, Any] | None", raw_row)
    if row and row.get("started_at"):
        ts: datetime = row["started_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    return None


@click.command()
@click.option(
    "--run-id",
    default=None,
    help="UUID from compile_runs. Tightens the FS window + langfuse trace filter.",
)
@click.option(
    "--since",
    default=None,
    help="FS window override — '1 hour ago' or ISO. Defaults: run start, or whole-wiki baseline.",
)
@click.option(
    "--skip-langfuse",
    is_flag=True,
    help="Skip M4/M8/M9 (offline mode).",
)
@click.option(
    "--no-compare",
    is_flag=True,
    help="Skip the delta-vs-prior comparison.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output path. Default: docs/audits/post-run-metrics-<id-or-baseline>.md.",
)
def main(
    run_id: str | None,
    since: str | None,
    skip_langfuse: bool,
    no_compare: bool,
    output: Path | None,
) -> None:
    """Emit a post-run metrics dipstick."""
    rid: UUID | None = None
    if run_id:
        try:
            rid = UUID(run_id)
        except ValueError as exc:
            raise click.BadParameter(f"--run-id must be a UUID: {exc}") from exc
    since_dt = _parse_since(since)
    # If run_id is set and --since wasn't, prefer the run's start time.
    if rid is not None and since_dt is None:
        since_dt = _resolve_run_started_at(rid)

    report = build_report(
        run_id=rid,
        since=since_dt,
        wiki_dir=settings.wiki_dir,
        skip_langfuse=skip_langfuse,
    )

    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    label = (
        f"{REPORT_PREFIX}-{rid}"
        if rid
        else f"{REPORT_PREFIX}-baseline-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    )
    out_path = output or (AUDITS_DIR / f"{label}.md")

    prior_metrics: dict[str, float] | None = None
    if not no_compare:
        prior = _find_prior_report(AUDITS_DIR, exclude=out_path)
        if prior is not None:
            prior_metrics = _parse_prior_metrics(prior)

    md = render_markdown(report, prior_metrics)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")

    # JSON sidecar for downstream tooling.
    json_path = out_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(asdict(report), indent=2, default=str),
        encoding="utf-8",
    )

    # Layer 1 — append longitudinal row + Layer 2 — re-render dashboard +
    # Layer 3 — push trace-derived metrics as Langfuse scores. Each step
    # is best-effort: failures log a warning and the next step still runs.
    sha = _prompt_commit_sha()
    try:
        append_to_history(report, rid, sha, settings.wiki_dir)
    except (OSError, TypeError, ValueError) as exc:
        # OSError: filesystem failures.
        # TypeError / ValueError: JSON-encoding failures on a non-encodable
        # field. Either way: log + continue; never block compile.
        logger.warning("metrics_history_append_failed", error=str(exc))
    try:
        from scripts.render_metrics_dashboard import render_dashboard

        render_dashboard()
    except Exception as exc:  # noqa: BLE001 — dashboard render never blocks
        logger.warning("dashboard_render_failed", error=str(exc)[:200])
    if not skip_langfuse:
        push_langfuse_scores(report, rid, sha)

    click.echo(md)
    click.echo(f"Wrote: {out_path}")


def emit_for_run(run_id: UUID, *, skip_langfuse: bool = False) -> Path | None:
    """Library entry-point — used by scripts/compile_all.py.

    Never raises. Returns the report path on success or None on failure
    (so the compile loop never blocks on metrics collection).

    On success, also appends a row to ``metrics-history.jsonl``,
    regenerates the dashboard, and pushes trace-derived metrics as
    Langfuse scores. Each follow-up step is best-effort.
    """
    # Guard against test stubs that pass a non-UUID string. Without
    # this, ``compile_all`` integration tests pollute the real
    # ``docs/audits/metrics-history.jsonl``.
    if not isinstance(run_id, UUID):
        try:
            run_id = UUID(str(run_id))
        except (ValueError, TypeError):
            logger.warning("post_run_metrics_invalid_run_id", run_id=str(run_id))
            return None
    try:
        since_dt = _resolve_run_started_at(run_id)
        report = build_report(
            run_id=run_id,
            since=since_dt,
            wiki_dir=settings.wiki_dir,
            skip_langfuse=skip_langfuse,
        )
        AUDITS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = AUDITS_DIR / f"{REPORT_PREFIX}-{run_id}.md"
        prior = _find_prior_report(AUDITS_DIR, exclude=out_path)
        prior_metrics = _parse_prior_metrics(prior) if prior else None
        md = render_markdown(report, prior_metrics)
        out_path.write_text(md, encoding="utf-8")
        out_path.with_suffix(".json").write_text(
            json.dumps(asdict(report), indent=2, default=str),
            encoding="utf-8",
        )
        sha = _prompt_commit_sha()
        try:
            append_to_history(report, run_id, sha, settings.wiki_dir)
        except (OSError, TypeError, ValueError) as exc:
            # Match `main()` scope — OSError (filesystem) +
            # TypeError/ValueError (json.dumps on a non-encodable
            # field). Don't let a JSONL append failure cascade and
            # skip the dashboard render + Langfuse push that follow.
            logger.warning("metrics_history_append_failed", error=str(exc))
        try:
            from scripts.render_metrics_dashboard import render_dashboard

            render_dashboard()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard_render_failed", error=str(exc)[:200])
        if not skip_langfuse:
            push_langfuse_scores(report, run_id, sha)
        return out_path
    except Exception as exc:  # noqa: BLE001 — never block the compile loop
        logger.warning("post_run_metrics_failed", run_id=str(run_id), error=str(exc))
        return None


# Module-level export to keep `from scripts.post_run_metrics import ...`
# clean from tests + scripts/compile_all.py wiring.
__all__ = [
    "DASHBOARD_PATH",
    "HISTORY_PATH",
    "TRACE_METRICS_TO_PUSH",
    "MetricResult",
    "Report",
    "_detect_archetype",
    "_has_owner_frontmatter",
    "_lead_has_number_and_two_sentences",
    "_metrics_dict",
    "_prompt_commit_sha",
    "_wiki_pages_total",
    "append_to_history",
    "build_report",
    "compute_filesystem_metrics",
    "compute_langfuse_metrics",
    "emit_for_run",
    "push_langfuse_scores",
    "read_history",
    "render_markdown",
]


if __name__ == "__main__":
    main()
