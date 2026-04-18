"""One-shot: rebuild ``source_threads:`` + ``message_touched_pages`` from disk.

**Inferred history.** Bug A's prior overwrites destroyed some provenance,
so we can only recover what's currently in each page's frontmatter
``sources:`` list. That list is per-message (``raw/YYYY-MM-DD_<subj>_<id>.md``);
this script translates each such path into the owning message's
``thread_id`` via the ``messages`` catalog, deduplicates the thread ids
per page, and writes them back as ``source_threads:`` on the page.
Alongside the frontmatter write, we pre-seed the
``message_touched_pages`` catalog with the (message_id, page_id) pairs
those same ``sources:`` imply. U1 handles the forward write path for
new batches; this unit fills in the baseline for pages that already
exist on disk.

Algorithm
---------

For every ``.md`` file under the canonical content folders in
``wiki/`` (see ``_CONTENT_DIRS`` — ``topics/`` / ``systems/`` /
``policies/`` / ``people/`` / ``entities/`` / ``decisions/`` /
``domains/`` / ``glossary/`` / ``timelines/`` / ``conflicts/``,
whichever exist):

1. Read frontmatter. Skip if no ``sources:`` list.
2. For each ``raw_path`` in ``sources:``:
   - ``messages.find_by_raw_path`` → ``message_id``.
   - One follow-up query resolves ``thread_id`` (we already have the
     connection open; a single ``SELECT thread_id FROM messages WHERE
     message_id = %s`` keeps the lookup table simple).
   - Drift case (no matching ``messages`` row): log, continue.
3. ``wiki_pages.find_by_slug`` → ``page_id`` for the current file.
   Missing row is logged + the page is skipped (catalog sync needed
   first).
4. For each resolved ``(message_id, page_id)`` pair: ``insert_touch``
   inside a per-row SAVEPOINT (mirrors ``compile_all.py::_sync_wiki_catalog``
   — one bad row can't poison the outer transaction).
5. Compute ``dedup_threads = sorted(set(thread_ids))`` and overwrite
   the page's frontmatter ``source_threads:`` field. Existing
   ``sources:`` is preserved verbatim — during the U5 transition we
   read both, so the pages stay compatible.

Idempotence
-----------

- ``message_touched_pages`` has ``PRIMARY KEY (message_id, page_id)``,
  so ``insert_touch`` is ``ON CONFLICT DO NOTHING`` — a re-run inserts
  zero rows.
- The frontmatter write is dedup-sorted, so re-running produces the
  same YAML bytes and round-trips through ``yaml.safe_dump``; we short-
  circuit the disk write when the computed list already matches what's
  on the page.

Flags
-----

- ``--dry-run``: preview only. Default — prints counts, writes a plan
  file to ``docs/audits/source-threads-backfill-plan-<ISO>.md``, no
  writes to disk or DB.
- ``--commit``: apply everything. DB inserts + frontmatter writes.
- ``--limit N``: process only the first ``N`` pages (incremental rollout).
- ``--repo-root PATH``: override the repo root (used by the test suite).

Usage
-----

    uv run python scripts/backfill_source_threads_and_touches.py --dry-run
    uv run python scripts/backfill_source_threads_and_touches.py --commit
    uv run python scripts/backfill_source_threads_and_touches.py --limit 50 --commit

Lifecycle: bootstrap-recovery — not on hot path, but required to rebuild from scratch. Do NOT delete.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
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

from src.compile.categories import WIKI_CATEGORIES  # noqa: E402
from src.db import connect  # noqa: E402
from src.db.touched_pages import insert_touch  # noqa: E402
from src.db.wiki_pages import find_by_slug  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

logger = structlog.get_logger(__name__)

# Content-carrying wiki folders. Built off the shared ``WIKI_CATEGORIES``
# tuple so adding a new category in one place picks it up here. We
# intentionally include ``glossary`` so a future ``wiki/glossary/*.md``
# tree (mentioned in the spec) would be scanned if it ever lands — the
# folder simply doesn't exist today (glossary is a top-level file) and
# ``_iter_wiki_pages`` skips missing folders. ``index.md`` files + top-
# level landing surfaces (``home.md`` / ``changes.md`` / ``glossary.md``)
# are skipped — they don't cite raw emails.
_CONTENT_DIRS: tuple[str, ...] = (*WIKI_CATEGORIES, "glossary")


@dataclass
class PagePlan:
    """Resolved backfill plan for a single wiki page."""

    path: Path
    slug: str
    page_id: int | None
    # (raw_path, message_id, thread_id) resolved from the messages table.
    resolved: list[tuple[str, str, str | None]]
    # raw_paths that didn't resolve to any messages row (drift).
    unresolved: list[str]
    # Deduplicated sorted thread_ids — what we write to source_threads:.
    thread_ids: list[str]
    # Existing source_threads value on the page (if any) — used to
    # short-circuit the write when the list is already correct.
    existing_source_threads: list[str]


_DISTRIBUTION_BUCKETS: tuple[str, ...] = ("0", "1", "2", ">=3")


@dataclass
class BackfillReport:
    """Aggregate counts across the whole run."""

    pages_scanned: int = 0
    pages_with_sources: int = 0
    pages_skipped_no_page_id: int = 0
    pages_updated: int = 0  # frontmatter rewrite happened (or would happen)
    pages_write_skipped_idempotent: int = 0  # source_threads matched already
    touches_inserted: int = 0  # new (message, page) rows
    touches_already_present: int = 0  # ON CONFLICT no-ops
    unresolved_raw_paths: int = 0
    unresolved_samples: list[str] = field(default_factory=list)
    thread_distribution: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_DISTRIBUTION_BUCKETS, 0)
    )


def _iter_wiki_pages(wiki_dir: Path) -> Iterable[Path]:
    """Yield every content-carrying wiki markdown file. Deterministic order."""
    if not wiki_dir.exists():
        return
    for sub in _CONTENT_DIRS:
        d = wiki_dir / sub
        if not d.exists():
            continue
        for page in sorted(d.glob("*.md")):
            if page.name == "index.md":
                continue
            yield page


def _read_str_list(fm: dict[str, Any], key: str) -> list[str]:
    """Return ``fm[key]`` as ``list[str]``, coercing scalars + filtering junk.

    Used for both ``sources:`` (list of raw_paths) and ``source_threads:``
    (list of thread_ids). Matches the ``fm.get("sources") or []`` idiom
    used elsewhere in ``scripts/``, but with a narrow type coercion so a
    single-string ``sources: "raw/..."`` still round-trips.
    """
    raw = fm.get(key)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def _resolve_raw_path_to_thread(conn: Any, raw_path: str) -> tuple[str, str | None] | None:
    """One query: ``raw_path`` → ``(message_id, thread_id)``. None on drift.

    ``conn`` is typed ``Any`` because ``psycopg.Connection`` without a
    concrete ``row_factory`` generic parameter triggers mypy overload
    errors on ``row["col"]`` lookups. ``src.db.connect()`` pins
    ``dict_row`` at runtime; the type signature just can't express that.
    Matches the same shim used in ``scripts/backfill_status_active.py``.
    """
    row = conn.execute(
        "SELECT message_id, thread_id FROM messages WHERE raw_path = %s",
        (raw_path,),
    ).fetchone()
    if row is None:
        return None
    return (row["message_id"], row["thread_id"])


def _build_plan_for_page(conn: psycopg.Connection, path: Path) -> PagePlan | None:
    """Read frontmatter + resolve every raw_path. ``None`` when the
    page has no ``sources:`` list (nothing to back-fill)."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("backfill.read_failed", path=str(path), error=str(exc))
        return None
    fm = extract_frontmatter(content)
    sources = _read_str_list(fm, "sources")
    if not sources:
        return None

    slug = path.stem
    page_row = find_by_slug(slug)
    page_id = int(page_row["page_id"]) if page_row else None

    resolved: list[tuple[str, str, str | None]] = []
    unresolved: list[str] = []
    for raw_path in sources:
        hit = _resolve_raw_path_to_thread(conn, raw_path)
        if hit is None:
            unresolved.append(raw_path)
            continue
        message_id, thread_id = hit
        resolved.append((raw_path, message_id, thread_id))

    # sorted(set(...)) — stable lexicographic order across runs keeps
    # the frontmatter write byte-identical on re-runs.
    thread_ids = sorted({tid for _r, _m, tid in resolved if tid is not None})
    existing = _read_str_list(fm, "source_threads")

    return PagePlan(
        path=path,
        slug=slug,
        page_id=page_id,
        resolved=resolved,
        unresolved=unresolved,
        thread_ids=thread_ids,
        existing_source_threads=existing,
    )


def _apply_frontmatter_update(path: Path, thread_ids: list[str]) -> None:
    """Overwrite ``source_threads:`` on the page. Preserves every other
    frontmatter key + body via ``yaml.safe_dump``."""
    content = path.read_text(encoding="utf-8")
    fm = extract_frontmatter(content)
    body = extract_body(content)
    fm["source_threads"] = list(thread_ids)
    path.write_text(render_with_frontmatter(fm, body), encoding="utf-8")


def _execute_touches(
    conn: psycopg.Connection,
    plans: list[PagePlan],
    report: BackfillReport,
) -> None:
    """Insert every ``(message_id, page_id)`` pair via per-row SAVEPOINT.

    Mirrors ``compile_all.py::_sync_wiki_catalog``: a single constraint
    violation rolls back the SAVEPOINT, leaving the outer transaction
    alive so the rest of the run proceeds. We also dedupe per page so
    multiple ``sources:`` entries pointing at the same message only
    produce one insert attempt.
    """
    for plan in plans:
        if plan.page_id is None:
            continue
        seen_message_ids: set[str] = set()
        for _raw, message_id, _tid in plan.resolved:
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            try:
                conn.execute("SAVEPOINT backfill_touch")
                inserted = insert_touch(
                    conn,
                    message_id=message_id,
                    page_id=plan.page_id,
                )
                conn.execute("RELEASE SAVEPOINT backfill_touch")
                if inserted:
                    report.touches_inserted += 1
                else:
                    report.touches_already_present += 1
            except psycopg.Error as exc:
                conn.execute("ROLLBACK TO SAVEPOINT backfill_touch")
                logger.warning(
                    "backfill.touch_failed",
                    message_id=message_id,
                    page_id=plan.page_id,
                    error=str(exc),
                )


def _bucket(n: int) -> str:
    """Distribution bucket label."""
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    return ">=3"


def _update_distribution(report: BackfillReport, plans: list[PagePlan]) -> None:
    """Populate ``report.thread_distribution`` from the resolved plans."""
    for plan in plans:
        key = _bucket(len(plan.thread_ids))
        report.thread_distribution[key] = report.thread_distribution.get(key, 0) + 1


def _render_plan_file(report: BackfillReport, plans: list[PagePlan], timestamp: str) -> str:
    """Render the dry-run audit trail. Truncates preview lists at 50."""
    lines: list[str] = []
    lines.append(f"# source_threads backfill plan ({timestamp})\n")
    lines.append("## Summary\n")
    lines.append(f"- pages scanned: **{report.pages_scanned}**")
    lines.append(f"- pages with `sources:`: **{report.pages_with_sources}**")
    lines.append(f"- pages skipped (no wiki_pages row): **{report.pages_skipped_no_page_id}**")
    lines.append(f"- pages that would be updated: **{report.pages_updated}**")
    lines.append(f"- pages already up to date: **{report.pages_write_skipped_idempotent}**")
    lines.append(f"- touches to insert: **{report.touches_inserted}**")
    lines.append(f"- touches already present: **{report.touches_already_present}**")
    lines.append(f"- unresolvable raw_paths: **{report.unresolved_raw_paths}**\n")

    lines.append("## Thread count distribution\n")
    total_scanned_with_sources = report.pages_with_sources or 0
    for key in _DISTRIBUTION_BUCKETS:
        n = report.thread_distribution.get(key, 0)
        pct = (100.0 * n / total_scanned_with_sources) if total_scanned_with_sources else 0.0
        lines.append(f"- {key} threads: {n} ({pct:.1f}%)")
    lines.append("")

    if report.unresolved_samples:
        lines.append("## Drift samples (first 50 unresolvable raw_paths)\n")
        for sample in report.unresolved_samples[:50]:
            lines.append(f"- `{sample}`")
        if report.unresolved_raw_paths > len(report.unresolved_samples):
            lines.append("")
            lines.append(
                f"*({report.unresolved_raw_paths - len(report.unresolved_samples)} more omitted)*"
            )
        lines.append("")

    if plans:
        preview = [p for p in plans if p.thread_ids][:20]
        lines.append(f"## Preview (first {len(preview)} pages with resolved threads)\n")
        lines.append("| page | page_id | threads | unresolved |")
        lines.append("|---|---|---|---|")
        for p in preview:
            try:
                rel = p.path.relative_to(REPO_ROOT)
            except ValueError:
                rel = p.path
            lines.append(
                f"| `{rel}` | {p.page_id or '—'} | {len(p.thread_ids)} | {len(p.unresolved)} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def _write_plan_file(report: BackfillReport, plans: list[PagePlan], root: Path) -> Path:
    """Persist the plan file under ``docs/audits/``."""
    audits_dir = root / "docs" / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    plan_path = audits_dir / f"source-threads-backfill-plan-{timestamp}.md"
    plan_path.write_text(_render_plan_file(report, plans, timestamp), encoding="utf-8")
    return plan_path


def _count_touch_inserts_preview(plans: list[PagePlan]) -> int:
    """Upper-bound count of touches the commit phase would try to insert.

    Used only for the dry-run summary — the real insert happens inside
    SAVEPOINTs in ``_execute_touches`` and counts real inserts vs.
    conflicts. Here we just deduplicate the (message_id, page_id) pairs
    per plan.
    """
    n = 0
    for plan in plans:
        if plan.page_id is None:
            continue
        seen: set[str] = set()
        for _raw, message_id, _tid in plan.resolved:
            if message_id in seen:
                continue
            seen.add(message_id)
            n += 1
    return n


def _would_rewrite_frontmatter(plan: PagePlan) -> bool:
    """True when the computed ``source_threads`` list differs from what
    the page already carries — skip the disk write otherwise so re-runs
    are byte-level idempotent."""
    return plan.thread_ids != plan.existing_source_threads


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    help="Preview only (default). Writes plan to docs/audits/.",
)
@click.option(
    "--commit",
    is_flag=True,
    help="Apply DB inserts + rewrite .md frontmatter.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process only the first N pages (incremental rollout).",
)
@click.option(
    "--repo-root",
    default=None,
    help="Override repo root for the wiki/ scan (tests).",
)
def main(dry_run: bool, commit: bool, limit: int | None, repo_root: str | None) -> None:
    """Backfill ``source_threads:`` + ``message_touched_pages`` from disk."""
    # Click's default-True ``--dry-run`` stays True unless ``--commit``
    # flips it — there's no ``--no-dry-run`` in the click signature.
    if commit:
        dry_run = False

    root = Path(repo_root).resolve() if repo_root else REPO_ROOT
    wiki_dir = root / "wiki"

    report = BackfillReport()
    plans: list[PagePlan] = []

    with connect() as conn:
        pages = list(_iter_wiki_pages(wiki_dir))
        if limit is not None:
            pages = pages[:limit]

        for path in pages:
            report.pages_scanned += 1
            plan = _build_plan_for_page(conn, path)
            if plan is None:
                continue
            report.pages_with_sources += 1
            plans.append(plan)
            if plan.page_id is None:
                report.pages_skipped_no_page_id += 1
                logger.warning(
                    "backfill.missing_wiki_page_row",
                    slug=plan.slug,
                    path=str(plan.path),
                )
            for raw in plan.unresolved:
                report.unresolved_raw_paths += 1
                if len(report.unresolved_samples) < 50:
                    report.unresolved_samples.append(raw)

        _update_distribution(report, plans)

        # Pre-compute the write-side partitioning once; the commit phase
        # overwrites ``touches_*`` with real observed counts, but the
        # ``pages_updated`` / ``pages_write_skipped_idempotent`` split
        # is purely content-driven so we set it once and only the
        # commit phase refines it on actual write failures.
        for p in plans:
            if p.page_id is None:
                continue
            if _would_rewrite_frontmatter(p):
                report.pages_updated += 1
            else:
                report.pages_write_skipped_idempotent += 1

        if dry_run:
            # Dry-run still reports *potential* inserts — real ON CONFLICT
            # math can't be known without running the SAVEPOINT dance.
            report.touches_inserted = _count_touch_inserts_preview(plans)
            plan_path = _write_plan_file(report, plans, root)
            click.echo(
                f"pages scanned: {report.pages_scanned} · "
                f"with sources: {report.pages_with_sources} · "
                f"would update: {report.pages_updated} · "
                f"idempotent: {report.pages_write_skipped_idempotent} · "
                f"would insert touches: {report.touches_inserted} · "
                f"drift: {report.unresolved_raw_paths}"
            )
            try:
                rel = plan_path.relative_to(root)
            except ValueError:
                rel = plan_path
            click.echo(f"plan: {rel}")
            return

        # --commit: DB inserts first, then frontmatter writes. Same
        # ordering as backfill_status_active.py: the DB is cheap to
        # retry, so finish that leg of the work before risking a
        # partial file rewrite.
        # Reset the preview-only count so the commit leg starts from 0.
        report.touches_inserted = 0
        _execute_touches(conn, plans, report)
        conn.commit()

        rewritten = 0
        skipped_idempotent = 0
        for plan in plans:
            if plan.page_id is None:
                continue
            if not _would_rewrite_frontmatter(plan):
                skipped_idempotent += 1
                continue
            try:
                _apply_frontmatter_update(plan.path, plan.thread_ids)
                rewritten += 1
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(
                    "backfill.write_failed",
                    path=str(plan.path),
                    error=str(exc),
                )
        report.pages_updated = rewritten
        report.pages_write_skipped_idempotent = skipped_idempotent

        plan_path = _write_plan_file(report, plans, root)
        click.echo(
            f"committed: scanned={report.pages_scanned} · "
            f"with_sources={report.pages_with_sources} · "
            f"updated={report.pages_updated} · "
            f"idempotent={report.pages_write_skipped_idempotent} · "
            f"touches_inserted={report.touches_inserted} · "
            f"touches_already_present={report.touches_already_present} · "
            f"drift={report.unresolved_raw_paths}"
        )
        try:
            rel = plan_path.relative_to(root)
        except ValueError:
            rel = plan_path
        click.echo(f"plan: {rel}")


if __name__ == "__main__":
    main()
