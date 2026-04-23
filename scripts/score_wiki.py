"""Rank wiki topic pages by 4 cheap heuristics — the human shortlist.

Ships as V12-U0b. With 300+ topic pages, random spot-checks miss the
pages that most need rewriting. This scorer runs concept-shape,
summary-currency, source-density, and graph-health over every topic
page and writes a CSV + a top-10 / bottom-10 markdown digest under
``docs/feedback/scorer-<YYYY-MM-DD>.{csv,md}``.

Deterministic, free, repeatable. No LLM calls. Full design in
``docs/audits/v12-north-star-2026-04-19.md``.

Usage::

    uv run python scripts/score_wiki.py                       # all topics
    uv run python scripts/score_wiki.py --limit 30
    uv run python scripts/score_wiki.py --pages foo,bar --no-db
"""

from __future__ import annotations

import csv
import json
import sys
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import psycopg
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.scoring import build_wikilink_incoming_index  # noqa: E402
from src.compile.scoring import score_concept_shape  # noqa: E402
from src.compile.scoring import score_graph_health  # noqa: E402
from src.compile.scoring import score_source_density  # noqa: E402
from src.compile.scoring import score_summary_currency  # noqa: E402
from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.utils import extract_body  # noqa: E402

logger = structlog.get_logger(__name__)

# Hub-page stems we never want in the scored topic set — generated listings
# (``index.md``) and the legacy ``home``/``changes`` if they drift into
# ``wiki/topics/`` at some point. Mirrors ``_GENERATED_HUB_STEMS`` in
# ``src.compile.scoring`` but kept local because ``score_wiki`` owns its
# selection policy.
_KNOWN_HUB_STEMS: frozenset[str] = frozenset({"index", "home", "changes"})


def _configure_logging() -> None:
    """Idempotent structlog setup. Called from ``main()`` so test imports
    don't mutate the global structlog config as a side effect.
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


CSV_COLUMNS: tuple[str, ...] = (
    "slug",
    "concept_shape",
    "summary_currency",
    "source_density",
    "graph_health",
    "mean",
    "sum",
)


@click.command()
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Score only the first N topic pages (alphabetical). Default: all.",
)
@click.option(
    "--pages",
    type=str,
    default=None,
    help="Comma-separated slugs to score (overrides --limit). Example: foo,bar",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("docs/feedback"),
    help="Where to write scorer-<date>.{csv,md}. Default: docs/feedback/",
)
@click.option(
    "--no-db",
    is_flag=True,
    default=False,
    help="Skip page_feedback inserts even if the table exists.",
)
def main(limit: int | None, pages: str | None, output_dir: Path, no_db: bool) -> None:
    """Score topic pages and emit ranked CSV + markdown digest."""
    _configure_logging()
    wiki_dir = settings.wiki_dir
    topics_dir = wiki_dir / "topics"
    if not topics_dir.is_dir():
        raise click.ClickException(f"topics dir not found: {topics_dir}")

    selected = _select_topic_paths(topics_dir, pages=pages, limit=limit)
    if not selected:
        raise click.ClickException("no topic pages selected")

    logger.info(
        "scorer_start",
        wiki_dir=str(wiki_dir),
        selected=len(selected),
        output_dir=str(output_dir),
        no_db=no_db,
    )

    wikilink_index, known_slugs = build_wikilink_incoming_index(wiki_dir)

    rows: list[dict[str, Any]] = []
    for path in selected:
        slug = path.stem
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("scorer_page_read_failed", slug=slug, error=str(exc))
            continue
        body = extract_body(content)
        cs, cs_dbg = score_concept_shape(body)
        sc, sc_dbg = score_summary_currency(body)
        sd, sd_dbg = score_source_density(body)
        gh, gh_dbg = score_graph_health(slug, body, wikilink_index, known_slugs)
        scores = [cs, sc, sd, gh]
        total = sum(scores)
        mean = total / len(scores)
        rows.append(
            {
                "slug": slug,
                "concept_shape": cs,
                "summary_currency": sc,
                "source_density": sd,
                "graph_health": gh,
                "mean": mean,
                "sum": total,
                "_debug": {
                    "concept_shape": cs_dbg,
                    "summary_currency": sc_dbg,
                    "source_density": sd_dbg,
                    "graph_health": gh_dbg,
                },
            }
        )

    if not rows:
        raise click.ClickException("no pages scored (all reads failed?)")

    today = datetime.now(UTC).date().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"scorer-{today}.csv"
    md_path = output_dir / f"scorer-{today}.md"
    _write_csv(csv_path, rows)
    _write_markdown(md_path, rows, today=today)

    db_rows = 0
    if not no_db:
        db_rows = _maybe_write_db(rows)

    mean_of_means = sum(r["mean"] for r in rows) / len(rows)
    logger.info(
        "scorer_done",
        pages=len(rows),
        mean_of_means=round(mean_of_means, 2),
        csv=str(csv_path),
        md=str(md_path),
        db_rows_written=db_rows,
    )


def _select_topic_paths(topics_dir: Path, *, pages: str | None, limit: int | None) -> list[Path]:
    """Resolve --pages / --limit to a sorted list of topic .md paths.

    --pages wins over --limit. A missing slug in --pages is a hard error
    so operators catch typos instead of silently scoring zero pages.

    Generated hub pages (``index.md``, ``home.md``, ``changes.md``) inside
    ``wiki/topics/`` are filtered out of the default ``--all`` sweep —
    they're listings of siblings, not concept pages, and would dilute the
    top/bottom-10 rankings. Explicit ``--pages index`` still resolves
    (operators asking for a hub by name have their reasons).
    """
    all_paths = sorted(topics_dir.glob("*.md"))
    if pages:
        by_slug = {p.stem: p for p in all_paths}
        requested = [s.strip() for s in pages.split(",") if s.strip()]
        missing = [s for s in requested if s not in by_slug]
        if missing:
            raise click.ClickException(f"unknown slugs: {', '.join(missing)}")
        return [by_slug[s] for s in requested]
    filtered = [p for p in all_paths if p.stem not in _KNOWN_HUB_STEMS]
    if limit is not None:
        return filtered[:limit]
    return filtered


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows in the canonical column order — debug fields excluded."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _csv_value(row[col]) for col in CSV_COLUMNS})


def _csv_value(value: object) -> object:
    """Round mean to 2 decimals for readability; other values pass through."""
    if isinstance(value, float):
        return round(value, 2)
    return value


def _write_markdown(path: Path, rows: list[dict[str, Any]], *, today: str) -> None:
    """Emit a summary table + top-10 / bottom-10 sections for human review."""
    n = len(rows)
    mean_of_means = sum(r["mean"] for r in rows) / n
    per_heuristic_mean = {
        col: round(sum(r[col] for r in rows) / n, 2)
        for col in ("concept_shape", "summary_currency", "source_density", "graph_health")
    }
    sorted_rows = sorted(rows, key=lambda r: (-r["mean"], r["slug"]))
    top = sorted_rows[:10]
    bottom = list(reversed(sorted_rows[-10:]))

    lines: list[str] = []
    lines.append(f"# Wiki scorer run — {today}")
    lines.append("")
    lines.append(f"Scored {n} topic page(s). Mean-of-means: {mean_of_means:.2f}/10.")
    lines.append("")
    lines.append("| Heuristic | Mean |")
    lines.append("|---|---|")
    for col, mean in per_heuristic_mean.items():
        lines.append(f"| {col} | {mean} |")
    lines.append("")
    lines.append("### Top 10")
    lines.append("")
    for r in top:
        lines.append(_format_row_line(r))
    lines.append("")
    lines.append("### Bottom 10")
    lines.append("")
    for r in bottom:
        lines.append(_format_row_line(r))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _format_row_line(r: dict[str, Any]) -> str:
    """One-line-per-page human digest with the concept-shape bad list hint."""
    bad = r["_debug"]["concept_shape"]["bad_matches"]
    bad_str = f"  bad_h2={bad}" if bad else ""
    return (
        f"- {r['slug']} mean={r['mean']:.1f}  "
        f"concept={r['concept_shape']} currency={r['summary_currency']} "
        f"source={r['source_density']} graph={r['graph_health']}{bad_str}"
    )


def _maybe_write_db(rows: list[dict[str, Any]]) -> int:
    """Insert one page_feedback row per (slug, heuristic); 4 rows per page.

    If the table doesn't exist yet (V12-U0a hasn't merged), log a
    structured warning and return 0 — the CSV + MD outputs are still
    written, so the scorer is independently shippable.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    heuristics = ("concept_shape", "summary_currency", "source_density", "graph_health")
    inserted = 0
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    for h in heuristics:
                        raw_json = {"heuristic": h, **row["_debug"][h]}
                        cur.execute(
                            """
                            INSERT INTO page_feedback
                                (run_id, slug, heuristic, score, source,
                                 severity, captured_by, raw_json, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                run_id,
                                row["slug"],
                                h,
                                row[h],
                                "scorer",
                                "info",
                                "heuristic",
                                json.dumps(raw_json),
                                now,
                            ),
                        )
                        inserted += 1
            conn.commit()
    except psycopg.errors.UndefinedTable:
        logger.warning(
            "scorer_db_skipped_table_absent",
            reason="page_feedback table does not exist yet",
        )
        return 0
    except psycopg.Error as exc:
        logger.warning("scorer_db_insert_failed", error=str(exc))
        return 0
    return inserted


if __name__ == "__main__":
    main()
