"""LLM judge — simulate 3 archived audit personas against wiki pages.

Usage::

    # Random 10% sample, all 3 personas
    uv run python scripts/judge_wiki.py --random 10pct

    # Explicit page list, one persona
    uv run python scripts/judge_wiki.py \\
        --pages central-smart-orchestrator-api,seller-isq --persona newbie

    # Dry-run (assemble prompts, skip LLM + DB writes)
    uv run python scripts/judge_wiki.py --pages foo --persona newbie --dry-run

After the deterministic scorer (V12-U0b) ranks pages, this is the second
signal: a Claude-grade audit in the voice of the archived newbie/PM/IA
personas. Writes one row per (page, persona) to ``page_feedback`` and rolls
up findings in ``docs/feedback/judge-<YYYY-MM-DD>.{csv,md}``.

Cost guardrails:
- Hard cap via ``JUDGE_MAX_PAGES_PER_RUN`` env (default 50) — exit code 2.
- Preflight abort if est > $20 and ``--confirm`` not passed — exit code 3.
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import psycopg
import structlog

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compile.judge import JudgeParseError  # noqa: E402
from src.compile.judge import build_system_prompt  # noqa: E402
from src.compile.judge import build_user_prompt  # noqa: E402
from src.compile.judge import call_judge  # noqa: E402
from src.compile.judge import estimate_cost  # noqa: E402
from src.compile.judge import severity_from_score  # noqa: E402
from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.utils import split_frontmatter  # noqa: E402

logger = structlog.get_logger(__name__)


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


ALL_PERSONAS: tuple[str, ...] = ("newbie", "pm", "ia")
DEFAULT_SEED = 20260419


def _resolve_personas(persona: str) -> list[str]:
    if persona == "all":
        return list(ALL_PERSONAS)
    return [persona]


def _enumerate_topic_slugs(wiki_dir: Path) -> list[str]:
    topics_dir = wiki_dir / "topics"
    if not topics_dir.exists():
        return []
    return sorted(p.stem for p in topics_dir.glob("*.md"))


def _resolve_sample(
    *,
    random_pct: int | None,
    pages: str | None,
    all_slugs: list[str],
    seed: int,
) -> list[str]:
    """Pick the page set for this run.

    Exactly one of ``random_pct`` / ``pages`` must be set — the CLI guard
    catches both branches. Uniform sample with a pinned seed for
    reproducibility; explicit ``--pages`` passes through verbatim (even if
    some slugs don't exist yet — we'll warn at load time).
    """
    if pages:
        return [s.strip() for s in pages.split(",") if s.strip()]
    if random_pct is None:
        # Defensive — the CLI guard (``bool(random_pct_str) == bool(pages)``)
        # ensures exactly one of the two is set before calling us. Using a
        # ``ValueError`` instead of ``assert`` so ``python -O`` can't strip
        # the check and let a silent empty-sample run through.
        raise ValueError("random_pct must be set when pages is not")
    count = round(len(all_slugs) * random_pct / 100)
    if count <= 0 or not all_slugs:
        return []
    rng = random.Random(seed)
    return sorted(rng.sample(all_slugs, min(count, len(all_slugs))))


def _load_page(wiki_dir: Path, slug: str) -> tuple[str, str] | None:
    """Return (frontmatter_yaml, body) or None if the page is missing."""
    path = wiki_dir / "topics" / f"{slug}.md"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("judge_page_missing", slug=slug, path=str(path))
        return None
    return split_frontmatter(content)


def _insert_feedback_row(
    *,
    slug: str,
    persona: str,
    parsed: dict[str, Any],
    severity: str,
) -> bool:
    """Insert one row into page_feedback. Returns True on success.

    Fails open ONLY on ``UndefinedTable`` — V12-U0a ships the schema in a
    separate PR and we still want the CSV/markdown outputs to be usable
    pre-schema. Any other ``psycopg.Error`` (auth, connection refused,
    constraint violation) is a real failure that should surface, not be
    logged-and-swallowed into silent data loss.
    """
    try:
        with connect() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO page_feedback (
                  slug, source, severity, score, captured_by, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    slug,
                    f"judge-{persona}",
                    severity,
                    parsed["score"],
                    persona,
                    json.dumps(parsed),
                ),
            )
    except psycopg.errors.UndefinedTable:
        logger.warning("page_feedback_table_missing", slug=slug, persona=persona)
        return False
    return True


def _write_csv(out_path: Path, rows: list[dict[str, Any]]) -> None:
    """Per (page, persona) CSV — one row, counts only (bodies live in the md)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["slug", "persona", "score", "what_works_count", "what_doesnt_count", "missing_count"]
        )
        for row in rows:
            parsed = row["parsed"]
            writer.writerow(
                [
                    row["slug"],
                    row["persona"],
                    parsed["score"],
                    len(parsed.get("what_works", [])),
                    len(parsed.get("what_doesnt", [])),
                    len(parsed.get("missing", [])),
                ]
            )


def _write_markdown(out_path: Path, rows: list[dict[str, Any]]) -> None:
    """Rollup markdown: lowest-5 pages, then per-page per-persona findings.

    The markdown is the *human-readable* output. CSV is for analysis; the
    ``page_feedback`` table is for automated follow-up.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Lowest-scoring rollup — by min score across personas per page.
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_slug.setdefault(row["slug"], []).append(row)
    lowest = sorted(
        by_slug.items(),
        key=lambda kv: min(r["parsed"]["score"] for r in kv[1]),
    )[:5]

    lines: list[str] = [
        f"# Judge findings — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
        "## 5 lowest-scoring pages across personas",
        "",
    ]
    if not lowest:
        lines.append("_No pages audited._")
    for slug, slug_rows in lowest:
        min_score = min(r["parsed"]["score"] for r in slug_rows)
        persona_scores = ", ".join(
            f"{r['persona']}={r['parsed']['score']}"
            for r in sorted(slug_rows, key=lambda r: r["persona"])
        )
        lines.append(f"- **{slug}** — min {min_score} ({persona_scores})")
    lines.append("")

    for slug in sorted(by_slug):
        lines.append(f"## {slug}")
        lines.append("")
        for row in sorted(by_slug[slug], key=lambda r: r["persona"]):
            parsed = row["parsed"]
            lines.append(f"### {row['persona']} (score {parsed['score']})")
            lines.append("")
            for bucket, key in (
                ("What works", "what_works"),
                ("What doesn't", "what_doesnt"),
                ("Missing", "missing"),
            ):
                bullets = parsed.get(key, [])
                lines.append(f"**{bucket}**")
                lines.append("")
                if bullets:
                    for bullet in bullets:
                        lines.append(f"- {bullet}")
                else:
                    lines.append("- _(none)_")
                lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


@click.command()
@click.option(
    "--random",
    "random_pct_str",
    default=None,
    help="Random sample percent (e.g. '10pct'). Mutually exclusive with --pages.",
)
@click.option(
    "--pages",
    default=None,
    help="Comma-separated topic slugs. Mutually exclusive with --random.",
)
@click.option(
    "--persona",
    type=click.Choice(["newbie", "pm", "ia", "all"]),
    default="all",
    help="Persona to run (default: all).",
)
@click.option("--seed", type=int, default=DEFAULT_SEED, help="RNG seed for reproducibility.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Assemble prompts, print the first, exit 0. No LLM calls, no writes.",
)
@click.option(
    "--no-db",
    is_flag=True,
    default=False,
    help="Skip DB writes; still write CSV + markdown.",
)
@click.option(
    "--model",
    default=None,
    help="LiteLLM model id. Defaults to $LITELLM_MODEL or anthropic/claude-sonnet-4-6.",
)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Required if estimated cost > $20. Otherwise the run aborts with exit 3.",
)
def main(
    random_pct_str: str | None,
    pages: str | None,
    persona: str,
    seed: int,
    dry_run: bool,
    no_db: bool,
    model: str | None,
    confirm: bool,
) -> None:
    """Run the LLM judge over a sample of wiki topic pages."""
    _configure_logging()
    # --- Flag validation ---
    if bool(random_pct_str) == bool(pages):
        click.echo("Error: exactly one of --random or --pages must be set.", err=True)
        sys.exit(1)

    random_pct: int | None = None
    if random_pct_str:
        # Accept "10pct" or "10"; strip the "pct" suffix.
        stripped = random_pct_str.lower().removesuffix("pct").strip()
        try:
            random_pct = int(stripped)
        except ValueError:
            click.echo(f"Error: --random must be an int (got {random_pct_str!r}).", err=True)
            sys.exit(1)
        if not 1 <= random_pct <= 100:
            click.echo(f"Error: --random must be 1-100 (got {random_pct}).", err=True)
            sys.exit(1)

    personas = _resolve_personas(persona)
    resolved_model = model or os.environ.get("LITELLM_MODEL", "anthropic/claude-sonnet-4-6")

    wiki_dir = Path(settings.wiki_dir).resolve()
    all_slugs = _enumerate_topic_slugs(wiki_dir)
    sample_slugs = _resolve_sample(
        random_pct=random_pct, pages=pages, all_slugs=all_slugs, seed=seed
    )

    if not sample_slugs:
        click.echo("No pages resolved from sample. Exiting.", err=True)
        sys.exit(1)

    # --- Hard cap ---
    max_pages = int(os.environ.get("JUDGE_MAX_PAGES_PER_RUN", "50"))
    if len(sample_slugs) > max_pages:
        logger.error(
            "judge_sample_exceeds_cap",
            cap=max_pages,
            requested=len(sample_slugs),
        )
        click.echo(
            f"Error: sample of {len(sample_slugs)} exceeds JUDGE_MAX_PAGES_PER_RUN={max_pages}.",
            err=True,
        )
        sys.exit(2)

    # --- Dry-run: print first assembled prompt, exit ---
    # Runs BEFORE the preflight cost gate — dry runs spend nothing, so a
    # ``$20+`` sample with ``--dry-run`` shouldn't be blocked asking for
    # ``--confirm`` on a charge that isn't happening.
    if dry_run:
        first_slug = sample_slugs[0]
        first_persona = personas[0]
        loaded = _load_page(wiki_dir, first_slug)
        if loaded is None:
            click.echo(f"Dry-run: page {first_slug} not found on disk.", err=True)
            sys.exit(1)
        fm_yaml, body = loaded
        system_prompt = build_system_prompt(first_persona)
        user_prompt = build_user_prompt(first_slug, fm_yaml, body)
        click.echo("=== SYSTEM PROMPT ===")
        click.echo(system_prompt)
        click.echo()
        click.echo("=== USER PROMPT ===")
        click.echo(user_prompt)
        logger.info(
            "judge_dry_run_complete",
            pages=len(sample_slugs),
            personas=personas,
            model=resolved_model,
        )
        return

    # --- Preflight cost estimate (live runs only) ---
    estimated = estimate_cost(len(sample_slugs), personas)
    click.echo(
        f"Estimated: {len(sample_slugs)} pages x {len(personas)} personas x ~$0.10 = ~${estimated:.2f}. Proceed?"
    )
    if estimated > 20.0 and not confirm:
        click.echo(
            "Estimated cost > $20. Re-run with --confirm to proceed.",
            err=True,
        )
        sys.exit(3)

    # --- Live pass ---
    rows: list[dict[str, Any]] = []
    rows_written = 0
    skipped_pairs = 0
    for slug in sample_slugs:
        loaded = _load_page(wiki_dir, slug)
        if loaded is None:
            continue
        fm_yaml, body = loaded
        user_prompt = build_user_prompt(slug, fm_yaml, body)
        for persona_name in personas:
            system_prompt = build_system_prompt(persona_name)
            try:
                parsed = call_judge(system_prompt, user_prompt, resolved_model)
            except JudgeParseError:
                logger.warning("judge_parse_failed", slug=slug, persona=persona_name)
                skipped_pairs += 1
                continue
            severity = severity_from_score(parsed["score"])
            rows.append({"slug": slug, "persona": persona_name, "parsed": parsed})
            click.echo(f"{slug} / {persona_name}: score={parsed['score']} severity={severity}")
            if not no_db:
                ok = _insert_feedback_row(
                    slug=slug,
                    persona=persona_name,
                    parsed=parsed,
                    severity=severity,
                )
                if ok:
                    rows_written += 1

    # --- Outputs ---
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    feedback_dir = REPO_ROOT / "docs" / "feedback"
    csv_path = feedback_dir / f"judge-{stamp}.csv"
    md_path = feedback_dir / f"judge-{stamp}.md"
    _write_csv(csv_path, rows)
    _write_markdown(md_path, rows)
    click.echo(f"Wrote {csv_path}")
    click.echo(f"Wrote {md_path}")

    logger.info(
        "judge_done",
        pages=len(sample_slugs),
        personas=personas,
        cost_usd=estimated,
        rows_written=rows_written if not no_db else 0,
        skipped=skipped_pairs,
    )


if __name__ == "__main__":
    main()
