"""Before/after evaluator for the North-Star compile pipeline.

Loads tests/fixtures/north_star_golden.yaml (30 pinned cases), snapshots
wiki quality metrics via ``scripts.wiki_quality_metrics.collect_metrics``,
and optionally recompiles the 30 golden messages end-to-end so each PR
gets a same-day diff against a fixed baseline. See unit 1 in
``/Users/amtagrwl/.claude/plans/sparkling-skipping-fiddle.md``.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import structlog
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.wiki_quality_metrics import collect_metrics  # noqa: E402
from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.db.messages import reset_to_pending_by_path  # noqa: E402

logger = structlog.get_logger(__name__)

GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "north_star_golden.yaml"
EXPECTED_CASE_COUNT = 30
REQUIRED_CASE_KEYS = ("message_id", "case_type", "description")
VALID_CASE_TYPES = {
    "merge",
    "new_topic",
    "new_system",
    "supersession",
    "ambiguity",
    "trivial",
}


def _load_fixture(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    """Parse the golden YAML, returning the ``cases`` list. Raises on bad shape."""
    if not path.exists():
        raise click.ClickException(f"golden fixture not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "cases" not in data:
        raise click.ClickException(f"{path} must be a mapping with a top-level 'cases' list")
    cases = data["cases"]
    if not isinstance(cases, list):
        raise click.ClickException(f"{path}: 'cases' must be a list, got {type(cases).__name__}")
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            raise click.ClickException(f"case {i} is not a mapping")
        for key in REQUIRED_CASE_KEYS:
            if key not in c:
                raise click.ClickException(f"case {i} missing required key: {key}")
        if c["case_type"] not in VALID_CASE_TYPES:
            raise click.ClickException(
                f"case {i} has invalid case_type {c['case_type']!r}; "
                f"expected one of {sorted(VALID_CASE_TYPES)}"
            )
    return [dict(c) for c in cases]


def _case_distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = dict.fromkeys(VALID_CASE_TYPES, 0)
    for c in cases:
        dist[c["case_type"]] += 1
    return dist


def _fetch_case_db_state(message_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Pull compile_state + raw_path for each golden message_id.

    Runs one SELECT regardless of list size; returns a dict keyed by message_id.
    Missing ids get a dict with ``found=False`` so the caller can flag drift
    (e.g. message was deleted/re-ingested after the fixture was pinned).
    """
    if not message_ids:
        return {}
    with connect() as conn:
        # dict_row is wired in src.db.connect; cast placates mypy-strict on
        # the tuple-vs-dict overload error that shows up repo-wide (same
        # pattern as scripts/reconcile_compile_state.py).
        rows: list[dict[str, Any]] = conn.execute(
            """
            SELECT message_id, raw_path, compile_state, thread_id, subject
              FROM messages
             WHERE message_id = ANY(%s)
            """,
            (message_ids,),
        ).fetchall()  # type: ignore[assignment]
    state: dict[str, dict[str, Any]] = {
        r["message_id"]: {
            "found": True,
            "raw_path": r["raw_path"],
            "compile_state": r["compile_state"],
            "thread_id": r["thread_id"],
            "subject": r["subject"],
        }
        for r in rows
    }
    for mid in message_ids:
        state.setdefault(mid, {"found": False})
    return state


def _count_pages_citing(wiki_dir: Path, raw_paths: list[str]) -> dict[str, int]:
    """Count how many wiki pages cite each raw_path in their frontmatter/body.

    Cheap substring scan — not a full YAML parse — because we only care
    whether the path appears anywhere in the file. Mirrors what the
    compile coordinator's citation-based reconcile does.
    """
    counts: dict[str, int] = dict.fromkeys(raw_paths, 0)
    if not wiki_dir.exists() or not raw_paths:
        return counts
    for page in wiki_dir.rglob("*.md"):
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for rp in raw_paths:
            if rp and rp in text:
                counts[rp] += 1
    return counts


def _snapshot(cases: list[dict[str, Any]], wiki_dir: Path) -> dict[str, Any]:
    """Compute overall + per-case metrics for the current wiki state."""
    overall: dict[str, Any] = {**collect_metrics(wiki_dir)}
    # ``page_count`` aliases ``total_pages`` so the eval-harness JSON contract
    # stays stable even if wiki_quality_metrics renames its output keys.
    overall["page_count"] = overall["total_pages"]
    db_state = _fetch_case_db_state([c["message_id"] for c in cases])
    raw_paths = [db_state[c["message_id"]].get("raw_path") or "" for c in cases]
    cite_counts = _count_pages_citing(wiki_dir, [p for p in raw_paths if p])

    per_case: list[dict[str, Any]] = []
    for c in cases:
        st = db_state.get(c["message_id"], {"found": False})
        rp = st.get("raw_path") or ""
        per_case.append(
            {
                "message_id": c["message_id"],
                "case_type": c["case_type"],
                "description": c["description"],
                "db_found": st.get("found", False),
                "thread_id": st.get("thread_id"),
                "subject": st.get("subject"),
                "raw_path": rp,
                "wiki_metrics_before": {
                    "compile_state": st.get("compile_state"),
                    "pages_citing_raw": cite_counts.get(rp, 0) if rp else 0,
                },
            }
        )
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "wiki_dir": str(wiki_dir.resolve()),
        "case_distribution": _case_distribution(cases),
        "cases": per_case,
        "overall": overall,
    }


def _diff_overall(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Numeric delta for top-level metric fields. Non-numeric fields are passed through."""
    diff: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if isinstance(b, int | float) and isinstance(a, int | float):
            diff[key] = {"before": b, "after": a, "delta": a - b}
        elif isinstance(b, dict) and isinstance(a, dict):
            diff[key] = _diff_overall(b, a)
        else:
            diff[key] = {"before": b, "after": a}
    return diff


def _run_compile(limit: int) -> int:
    """Invoke scripts/compile_all.py with fixed --limit/--batch-size. Returns exit code.

    Streams stdout/stderr straight to the parent so operators see the live
    compile log; we only care about the exit code here.
    """
    cmd = [
        "uv",
        "run",
        "python",
        str(REPO_ROOT / "scripts" / "compile_all.py"),
        "--limit",
        str(limit),
        "--batch-size",
        "1",
    ]
    logger.info("eval_compile_start", cmd=cmd)
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    logger.info("eval_compile_done", returncode=result.returncode)
    return result.returncode


def _render_markdown(diff: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> str:
    """Tiny markdown summary for copy-paste into a PR description."""
    lines = [
        "# Eval harness compare report",
        "",
        f"- Baseline captured: `{before.get('captured_at')}`",
        f"- After captured:    `{after.get('captured_at')}`",
        f"- Wiki dir:          `{after.get('wiki_dir')}`",
        "",
        "## Overall metric deltas",
        "",
        "| metric | before | after | delta |",
        "| --- | --- | --- | --- |",
    ]
    overall_diff = diff.get("overall", {})
    if isinstance(overall_diff, dict):
        for key, entry in sorted(overall_diff.items()):
            if isinstance(entry, dict) and "delta" in entry:
                lines.append(
                    f"| {key} | {entry['before']} | {entry['after']} | {entry['delta']:+} |"
                )
    return "\n".join(lines) + "\n"


@click.command()
@click.option("--dry-run", is_flag=True, help="Validate fixture only — no DB or wiki calls.")
@click.option(
    "--baseline",
    is_flag=True,
    help="Capture a before-snapshot (metrics only) and write it to --out.",
)
@click.option(
    "--compare",
    "compare_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a baseline JSON from a previous --baseline run. Triggers recompile + diff.",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output path for JSON (required with --baseline and --compare).",
)
@click.option(
    "--manual-read",
    type=click.IntRange(min=0),
    default=0,
    help="Print N random wiki page slugs for eyeballing (used with --compare).",
)
def main(
    dry_run: bool,
    baseline: bool,
    compare_path: Path | None,
    out: Path | None,
    manual_read: int,
) -> None:
    """North-star eval harness — before/after metrics on a 30-case golden set."""
    cases = _load_fixture()

    if dry_run:
        if len(cases) != EXPECTED_CASE_COUNT:
            raise click.ClickException(f"expected {EXPECTED_CASE_COUNT} cases, got {len(cases)}")
        click.echo(f"OK: {len(cases)} cases loaded from {GOLDEN_PATH}")
        click.echo(f"distribution: {json.dumps(_case_distribution(cases))}")
        return

    wiki_dir = settings.wiki_dir
    if not wiki_dir.is_absolute():
        wiki_dir = (REPO_ROOT / wiki_dir).resolve()

    if baseline:
        if out is None:
            raise click.ClickException("--baseline requires --out <path>")
        snapshot = _snapshot(cases, wiki_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        click.echo(
            f"baseline written: {out} "
            f"(pages={snapshot['overall']['total_pages']}, cases={len(cases)})"
        )
        return

    if compare_path is not None:
        baseline_data = json.loads(compare_path.read_text(encoding="utf-8"))
        raw_paths = [c["raw_path"] for c in baseline_data.get("cases", []) if c.get("raw_path")]
        if not raw_paths:
            raise click.ClickException(
                f"baseline {compare_path} has no raw_paths — cannot reset messages"
            )
        reset_count = reset_to_pending_by_path(raw_paths)
        logger.info("eval_reset", reset=reset_count, expected=len(raw_paths))

        rc = _run_compile(limit=len(raw_paths))
        if rc != 0:
            logger.warning("eval_compile_nonzero_exit", returncode=rc)

        after = _snapshot(cases, wiki_dir)
        diff = {
            "overall": _diff_overall(baseline_data.get("overall", {}), after["overall"]),
            "case_distribution": {
                "before": baseline_data.get("case_distribution"),
                "after": after["case_distribution"],
            },
            "compile_returncode": rc,
            "reset_count": reset_count,
        }

        report = {"baseline": baseline_data, "after": after, "diff": diff}
        md = _render_markdown(diff, baseline_data, after)

        if out is None:
            out = (
                REPO_ROOT
                / "docs"
                / "audits"
                / (f"eval-compare-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        md_path = out.with_suffix(".md")
        md_path.write_text(md, encoding="utf-8")
        click.echo(md)
        click.echo(f"compare JSON: {out}\ncompare MD:   {md_path}")

        if manual_read > 0:
            slugs = _sample_page_slugs(wiki_dir, manual_read)
            click.echo("\nmanual read suggestions:")
            for s in slugs:
                click.echo(f"  - {s}")
        return

    raise click.ClickException(
        "supply one of: --dry-run, --baseline --out <path>, or --compare <baseline.json>"
    )


def _sample_page_slugs(wiki_dir: Path, n: int) -> list[str]:
    """Return up to ``n`` random `<category>/<slug>` entries from ``wiki_dir``."""
    candidates = [
        f"{p.parent.name}/{p.stem}"
        for p in wiki_dir.rglob("*.md")
        if p.name not in {"index.md", "home.md", "about.md"}
    ]
    if not candidates:
        return []
    return random.sample(candidates, min(n, len(candidates)))


if __name__ == "__main__":
    main()
