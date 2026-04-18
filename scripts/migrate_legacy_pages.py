"""Nightly incremental legacy-debt migration (dry-run + report-only + commit).

Recurring counterpart to the one-shot C1 (entities/ → people/ rename) and
C2 (status `current` → `active` backfill) migrations. Those do the bulk
move; this script catches the stragglers that get reintroduced via edge
paths after the bulk runs, or that the one-shots missed.

**Legacy-debt criteria** (ANY triggers the page):

- `status IN ('current', 'contested')` — the old ontology labels.
- `page_type = 'entity'` — the pre-C1 directory name.
- `page_type IN ('topic', 'system')` AND the on-disk frontmatter has no
  `domain:` field — C4 wants every content page pinned to a domain hub.

**Fixes planned per page**:

- `status='current'` → flip to `active` (both DB + frontmatter).
- `status='contested'` → left alone (contested is a human-review state;
  reported but not auto-flipped).
- `page_type='entity'` → rename file to `wiki/people/<slug>.md`, set
  `page_type='person'` in DB, rewrite incoming `[[entities/X]]` and
  bare `[[X]]` wikilinks pointing at the page to `[[people/X]]`.
- Topic/system missing `domain:` → add `domain: unassigned` to the
  frontmatter so downstream consumers always see the field. Humans (or
  the compiler) fill in the real value later.

**Modes**:

- ``--dry-run`` (default): print summary; don't write anywhere.
- ``--report-only``: write ``docs/audits/legacy-debt-<ISO>.md`` AND
  append a SINGLE row to ``wiki/log.md``. Doesn't mutate wiki content or
  DB.
- ``--commit``: perform the migration on at most ``--limit N`` pages
  (default 20). Updates files + DB + writes the same report.

**Never mutates ``wiki/changes.md``** — that file is generated from the
`compile_attempts` table, never edited directly.

Per-file errors isolated via try/except — one bad page doesn't abort the
batch. Idempotent: a clean wiki exits 0 with no-op message.

Usage::

    uv run python scripts/migrate_legacy_pages.py --dry-run --limit 5
    uv run python scripts/migrate_legacy_pages.py --report-only --limit 20
    uv run python scripts/migrate_legacy_pages.py --commit --limit 20
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
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

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.db import connect  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

logger = structlog.get_logger(__name__)

# Wiki category folders we walk when rewriting wikilinks. Mirrors
# scripts/migrate_entity_slugs.py::CATEGORIES + the new "people" folder
# (post-C1 ontology).
_WIKILINK_CATEGORIES = (
    "topics",
    "entities",
    "people",
    "systems",
    "policies",
    "timelines",
    "conflicts",
    "domains",
)

# Which statuses count as legacy debt. `contested` is included as a flag
# so operators see the backlog, but the commit path leaves it alone —
# contested is a human-review state, not an auto-migratable one.
_LEGACY_STATUSES: frozenset[str] = frozenset({"current", "contested"})

# Only these page types require a `domain:` frontmatter field. Entity /
# people pages don't have a domain; they're cross-cutting by nature.
_DOMAIN_REQUIRED_TYPES: frozenset[str] = frozenset({"topic", "system"})

# Post-C1 destination for entity pages.
_PEOPLE_DIR = "people"
_PERSON_PAGE_TYPE = "person"

# Placeholder domain value when a page is missing the field entirely.
# Matches the "unassigned" convention called out in the plan. A follow-up
# compile batch (guided by the D2 touch-it-fix-it hint) resolves this to
# a real domain slug.
_DOMAIN_PLACEHOLDER = "unassigned"

# Default cap on --commit batch size. Keeps a single run from producing a
# huge diff — legacy debt clears in nightly chunks, same pattern as
# migrate_entity_slugs.py.
_DEFAULT_LIMIT = 20


@dataclass
class LegacyPage:
    """One row from the `wiki_pages` catalog flagged as legacy debt.

    `reasons` is the set of why-flagged tags the report renders and the
    commit path branches on.
    """

    slug: str
    path: str
    page_type: str
    status: str
    # Resolved absolute path to the on-disk markdown file. Some DB rows
    # point at pages that have been deleted or moved — we keep the path
    # and rely on `.exists()` at commit time.
    file_path: Path = field(default_factory=Path)
    has_domain: bool = False
    reasons: list[str] = field(default_factory=list)


def _all_wiki_pages() -> list[dict[str, Any]]:
    """Fetch the full wiki_pages catalog so we can scan for legacy debt.

    `canonical_user_email` is intentionally omitted from the SELECT —
    `_classify` and downstream code only inspect slug/path/page_type/
    status, so pulling the email column was a dead read on every full
    scan.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT slug, path, page_type, status
              FROM wiki_pages
             ORDER BY page_type, slug
            """
        ).fetchall()
    return cast("list[dict[str, Any]]", rows)


def _absolute_file_path(rel_path: str) -> Path:
    """Resolve a wiki_pages.path (repo-relative) to an absolute path."""
    p = Path(rel_path)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def _has_domain_value(fm: dict[str, Any]) -> bool:
    """True when the frontmatter declares at least one domain.

    Accepts the singular `domain:` scalar or the plural `domains:`
    list (v10-U2). A non-empty value under either key means the page
    doesn't need the migration placeholder.
    """
    plural = fm.get("domains")
    if isinstance(plural, list) and plural:
        return True
    singular = fm.get("domain")
    if isinstance(singular, str) and singular.strip():
        return True
    return bool(isinstance(singular, list) and singular)


def _classify(row: dict[str, Any]) -> LegacyPage | None:
    """Return a LegacyPage if `row` hits any legacy-debt rule, else None.

    Rules (any triggers):
      1. status in LEGACY_STATUSES
      2. page_type == 'entity'
      3. page_type in DOMAIN_REQUIRED_TYPES and on-disk domain: is missing
    """
    page = LegacyPage(
        slug=str(row["slug"]),
        path=str(row["path"]),
        page_type=str(row["page_type"]),
        status=str(row["status"]),
        file_path=_absolute_file_path(str(row["path"])),
    )
    # Only probe `domain:` for the types that need it — avoids a pointless
    # file read for every entity/person page.
    if page.page_type in _DOMAIN_REQUIRED_TYPES and page.file_path.is_file():
        try:
            fm = extract_frontmatter(page.file_path.read_text(encoding="utf-8"))
            page.has_domain = _has_domain_value(fm)
        except (OSError, UnicodeDecodeError):
            page.has_domain = False

    # Rule 1 — status flip. contested is flagged for visibility but the
    # commit path leaves it alone (human-review state).
    if page.status in _LEGACY_STATUSES:
        page.reasons.append(f"status_{page.status}")

    # Rule 2 — entity → person rename.
    if page.page_type == "entity":
        page.reasons.append("page_type_entity")

    # Rule 3 — missing `domain:` on topic/system.
    if page.page_type in _DOMAIN_REQUIRED_TYPES and not page.has_domain:
        page.reasons.append("missing_domain")

    return page if page.reasons else None


def scan_legacy_debt(rows: list[dict[str, Any]] | None = None) -> list[LegacyPage]:
    """Return the full list of legacy-debt pages, classified.

    `rows` can be supplied in tests to bypass the DB. In normal use we
    pull straight from `wiki_pages`.
    """
    catalog = rows if rows is not None else _all_wiki_pages()
    legacy: list[LegacyPage] = []
    for row in catalog:
        hit = _classify(row)
        if hit is not None:
            legacy.append(hit)
    return legacy


def _rewrite_entity_prefix(wiki_dir: Path, slug: str) -> int:
    """Rewrite `[[entities/<slug>]]` → `[[people/<slug>]]` across the wiki.

    The entity→person rename keeps the slug identity (it's the unique key
    in `wiki_pages`), so bare `[[slug]]` links still resolve. Only the
    `entities/` directory prefix needs rewriting.
    """
    count = 0
    pat = re.compile(rf"\[\[entities/{re.escape(slug)}(\||\]\])")
    replacement = rf"[[{_PEOPLE_DIR}/{slug}\1"
    for cat in _WIKILINK_CATEGORIES:
        cat_dir = wiki_dir / cat
        if not cat_dir.exists():
            continue
        for md in cat_dir.glob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new_content, n = pat.subn(replacement, content)
            if n:
                count += n
                md.write_text(new_content, encoding="utf-8")
    return count


def _rewrite_frontmatter(page_path: Path, mutate: Callable[[dict[str, Any]], bool]) -> bool:
    """Read → apply ``mutate`` → write iff ``mutate`` returned True.

    Shared backbone for the status-flip and domain-placeholder fixes.
    ``mutate`` edits the frontmatter dict in place and returns whether a
    change should be persisted (so both callers get the same idempotency
    + read-error handling).
    """
    try:
        content = page_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm = extract_frontmatter(content)
    if not mutate(fm):
        return False
    body = extract_body(content)
    page_path.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
    return True


def _flip_frontmatter_status(page_path: Path) -> bool:
    """Rewrite `status: current` → `status: active`. Idempotent."""

    def _flip(fm: dict[str, Any]) -> bool:
        if fm.get("status") != "current":
            return False
        fm["status"] = "active"
        return True

    return _rewrite_frontmatter(page_path, _flip)


def _add_domain_placeholder(page_path: Path) -> bool:
    """Add `domain: unassigned` to a page missing the field. Idempotent."""

    def _add(fm: dict[str, Any]) -> bool:
        if _has_domain_value(fm):
            return False
        fm["domain"] = _DOMAIN_PLACEHOLDER
        return True

    return _rewrite_frontmatter(page_path, _add)


def _flip_frontmatter_type_to_person(page_path: Path) -> bool:
    """Rewrite `page_type: entity` → `page_type: person` in frontmatter.

    Idempotent: no-op when the page is already `person` (or has no
    `page_type` at all). Scoped to the YAML frontmatter block by
    `extract_frontmatter` / `render_with_frontmatter`, so body mentions
    of the word "entity" are untouched.
    """

    def _flip(fm: dict[str, Any]) -> bool:
        if fm.get("page_type") != "entity":
            return False
        fm["page_type"] = _PERSON_PAGE_TYPE
        return True

    return _rewrite_frontmatter(page_path, _flip)


def _rename_entity_to_people(
    conn: psycopg.Connection,
    page: LegacyPage,
    wiki_dir: Path,
) -> tuple[bool, int]:
    """Move `wiki/entities/<slug>.md` to `wiki/people/<slug>.md` + update DB.

    Returns (renamed, wikilinks_rewritten). `renamed=False` when the move
    was skipped (missing source file or target collision).
    Mutates `page.file_path` to the new location on success so later
    fixes (status-flip, domain-add) read from the right place.

    Crash-safety: the file move happens BEFORE the DB UPDATE, but if the
    DB work (or any step after the move) fails we rename the file back
    to its original path before re-raising. Without this rollback the
    SAVEPOINT in ``apply_fixes`` would un-do the DB change while leaving
    the file orphaned under ``wiki/people/`` — DB row still points at
    ``wiki/entities/``, on-disk file is at ``wiki/people/``.
    """
    if not page.file_path.is_file():
        return False, 0
    people_dir = wiki_dir / _PEOPLE_DIR
    target = people_dir / f"{page.slug}.md"
    if target.exists():
        logger.warning("people_target_collision", slug=page.slug, target=str(target))
        return False, 0
    people_dir.mkdir(parents=True, exist_ok=True)
    src_path = page.file_path
    dst_path = target
    src_path.rename(dst_path)
    try:
        # Keep the slug as the unique identity so message_touched_pages +
        # downstream joins don't break; just update page_type + path.
        new_rel = dst_path.resolve().relative_to(REPO_ROOT)
        conn.execute(
            """
            UPDATE wiki_pages
               SET page_type = %s,
                   path = %s
             WHERE slug = %s
            """,
            (_PERSON_PAGE_TYPE, str(new_rel), page.slug),
        )
        # Flip the on-disk frontmatter `page_type: entity` → `person` so
        # validate_wiki.py's directory→type check is satisfied. DB and
        # frontmatter must agree; otherwise the moved page lives under
        # `people/` but still declares `page_type: entity` in YAML.
        _flip_frontmatter_type_to_person(dst_path)
        page.file_path = dst_path
        return True, _rewrite_entity_prefix(wiki_dir, page.slug)
    except (psycopg.Error, OSError, UnicodeDecodeError):
        # Revert the filesystem move so the SAVEPOINT rollback in
        # apply_fixes doesn't leave a DB/disk split-brain. Re-raise so
        # the outer handler rolls back the DB side + counts the error.
        try:
            dst_path.rename(src_path)
        except OSError:
            logger.exception(
                "migrate_legacy_pages_rename_rollback_failed",
                slug=page.slug,
                src=str(src_path),
                dst=str(dst_path),
            )
        raise


def _flip_db_status(conn: psycopg.Connection, slug: str) -> int:
    """UPDATE wiki_pages SET status='active' for the given slug.

    Returns the rowcount. 0 when the row is already `active` (idempotent).
    """
    cur = conn.execute(
        """
        UPDATE wiki_pages
           SET status = 'active'
         WHERE slug = %s
           AND status = 'current'
        """,
        (slug,),
    )
    return cur.rowcount or 0


def _apply_one(
    conn: psycopg.Connection,
    page: LegacyPage,
    wiki_dir: Path,
    tally: dict[str, int],
) -> None:
    """Execute the planned fixes for one page.

    Called under try/except in `apply_fixes` so a single malformed page
    doesn't sink the batch. Tally is mutated in place — the caller owns
    both the tally and the error count.
    """
    # Entity → person rename runs first because it moves the file, and
    # the later status-flip / domain-add fixes need to read from the new
    # location. `_rename_entity_to_people` updates page.file_path.
    if page.page_type == "entity":
        renamed, rewrites = _rename_entity_to_people(conn, page, wiki_dir)
        if renamed:
            tally["entities_renamed"] += 1
            tally["wikilinks_rewritten"] += rewrites

    if page.status == "current" and page.file_path.is_file():
        if _flip_frontmatter_status(page.file_path):
            tally["status_flipped"] += 1
        _flip_db_status(conn, page.slug)

    if (
        page.page_type in _DOMAIN_REQUIRED_TYPES
        and not page.has_domain
        and page.file_path.is_file()
        and _add_domain_placeholder(page.file_path)
    ):
        tally["domain_added"] += 1


def apply_fixes(pages: list[LegacyPage], wiki_dir: Path) -> dict[str, int]:
    """Apply the planned fixes to `pages`. Returns a tally dict.

    Per-page errors are isolated via a SAVEPOINT around each page so:

    1. A psycopg failure on page N rolls back only that page's DB
       work (psycopg3 puts the connection into ``InFailedSqlTransaction``
       after any SQL error — without the savepoint, every subsequent
       page's DB call would also raise, AND the final ``conn.commit()``
       would crash the CLI after files had already been mutated).
    2. OSError / UnicodeDecodeError on filesystem ops is caught the
       same way, matching the existing isolation contract.

    Mirrors the pattern in ``scripts/compile_all.py::_sync_wiki_catalog``.
    """
    tally: dict[str, int] = {
        "status_flipped": 0,
        "entities_renamed": 0,
        "domain_added": 0,
        "wikilinks_rewritten": 0,
        "errors": 0,
    }
    if not pages:
        return tally
    with connect() as conn:
        for page in pages:
            try:
                conn.execute("SAVEPOINT migrate_page")
                _apply_one(conn, page, wiki_dir, tally)
                conn.execute("RELEASE SAVEPOINT migrate_page")
            except psycopg.Error as exc:
                # DB failure — roll back the savepoint to clear
                # InFailedSqlTransaction, then keep going. The final
                # conn.commit() below will succeed because the outer
                # transaction is still in a good state.
                conn.execute("ROLLBACK TO SAVEPOINT migrate_page")
                tally["errors"] += 1
                logger.warning(
                    "migrate_legacy_pages_row_failed",
                    slug=page.slug,
                    error=str(exc),
                    error_type="psycopg",
                )
            except (OSError, UnicodeDecodeError) as exc:
                # Filesystem failure — also roll back the savepoint
                # because _apply_one may have done partial DB work
                # before the file op blew up.
                conn.execute("ROLLBACK TO SAVEPOINT migrate_page")
                tally["errors"] += 1
                logger.warning(
                    "migrate_legacy_pages_row_failed",
                    slug=page.slug,
                    error=str(exc),
                    error_type="filesystem",
                )
        conn.commit()
    return tally


def _render_report(
    pages: list[LegacyPage],
    *,
    mode: str,
    scanned: int,
    generated_at: datetime,
) -> str:
    """Render the markdown report the operator reads tomorrow morning."""
    lines: list[str] = []
    lines.append(
        f"# Legacy-debt nightly migration report — {generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    lines.append("")
    lines.append(f"Mode: {mode}  |  Pages scanned: {scanned}  |  Legacy debt found: {len(pages)}")
    lines.append("")

    lines.append("## Summary by reason")
    lines.append("")
    counts: dict[str, int] = {}
    for p in pages:
        for r in p.reasons:
            counts[r] = counts.get(r, 0) + 1
    if not counts:
        lines.append("_No legacy-debt pages found — wiki is clean._")
        lines.append("")
        return "\n".join(lines)
    # Stable human-readable ordering: status_* first, then page_type,
    # then missing_domain.
    order = sorted(counts.items(), key=lambda kv: (_reason_sort_key(kv[0]), kv[0]))
    for reason, n in order:
        lines.append(f"- {_reason_label(reason)}: {n} pages")
    lines.append("")

    lines.append("## Top 20 pages by multiple flags")
    lines.append("")
    lines.append("| slug | page_type | status | has_domain | flags |")
    lines.append("|---|---|---|---|---|")
    ranked = sorted(pages, key=lambda p: (-len(p.reasons), p.slug))
    for p in ranked[:20]:
        flags = ", ".join(sorted(p.reasons))
        lines.append(
            f"| `{p.slug}` | {p.page_type} | {p.status} "
            f"| {'yes' if p.has_domain else 'no'} | {flags} |"
        )
    lines.append("")

    lines.append("## Next steps")
    lines.append("")
    lines.append(
        "- Run `scripts/migrate_legacy_pages.py --commit --limit N` to apply the "
        "nightly incremental sweep — this script flips `status=current → active`, "
        "renames `entity → person`, and adds `domain: unassigned` placeholders in "
        "one pass."
    )
    lines.append(
        "- Re-run with a larger `--limit` once the DB-side counters have stabilised, "
        "or schedule it nightly via cron until the report shows 0 legacy debt."
    )
    lines.append(
        "- Hand-edit `domain:` on topic/system pages where the placeholder "
        "(`unassigned`) needs replacing with the real domain slug."
    )
    lines.append("")
    return "\n".join(lines)


def _reason_sort_key(reason: str) -> int:
    # Sort groups: statuses → page types → missing_domain. Keeps the
    # report grouped the way an operator reads it top-to-bottom.
    if reason.startswith("status_"):
        return 0
    if reason.startswith("page_type_"):
        return 1
    if reason == "missing_domain":
        return 2
    return 3


def _reason_label(reason: str) -> str:
    # Human-readable labels — `status_current` prints as `status=current`
    # etc. Matches the example output in the plan.
    if reason.startswith("status_"):
        return f"status={reason.removeprefix('status_')}"
    if reason.startswith("page_type_"):
        return f"page_type={reason.removeprefix('page_type_')}"
    if reason == "missing_domain":
        return "missing `domain:` field"
    return reason


# Mirrors scripts/compile_all.py::_LOG_HEADER so our appended rows slot
# into the same markdown table. Keep this in sync with compile_all.py.
_LOG_HEADER = (
    "# Compilation Log\n\n"
    "| Timestamp | Batch | N Emails | Thread ID | Outcome | Notes |\n"
    "|---|---|---|---|---|---|\n"
)


def _append_log_row(wiki_dir: Path, *, timestamp: str, outcome: str, notes: str) -> None:
    """Append ONE 6-column row to wiki/log.md. Creates file + header if missing.

    Matches the schema in scripts/compile_all.py::_append_batch_log so
    compile rows and migration rows share the same table. Single row per
    run — full detail lives in docs/audits/legacy-debt-<ISO>.md.
    """
    wiki_dir.mkdir(parents=True, exist_ok=True)
    log_path = wiki_dir / "log.md"
    if not log_path.exists():
        log_path.write_text(_LOG_HEADER, encoding="utf-8")
    # Pipes in notes would break markdown table parsing — escape them.
    safe_notes = notes.replace("|", r"\|").replace("\n", " ").strip()
    row = f"| {timestamp} | migrate | 0 |  | {outcome} | {safe_notes} |\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(row)


def _report_path(out_dir: Path, now: datetime) -> Path:
    return out_dir / f"legacy-debt-{now.strftime('%Y%m%dT%H%M%SZ')}.md"


def _print_summary(pages: list[LegacyPage], scanned: int) -> None:
    click.echo(f"Pages scanned: {scanned}")
    click.echo(f"Legacy debt found: {len(pages)}")
    if not pages:
        return
    counts: dict[str, int] = {}
    for p in pages:
        for r in p.reasons:
            counts[r] = counts.get(r, 0) + 1
    click.echo("By reason:")
    for reason, n in sorted(counts.items(), key=lambda kv: (_reason_sort_key(kv[0]), kv[0])):
        click.echo(f"  {_reason_label(reason)}: {n}")


@click.command()
@click.option(
    "--limit",
    type=int,
    default=_DEFAULT_LIMIT,
    show_default=True,
    help="Max pages to commit per run. --report-only ignores this for reports.",
)
@click.option(
    "--dry-run",
    "mode_dry_run",
    is_flag=True,
    help="Preview only. Print summary; don't write files or DB.",
)
@click.option(
    "--report-only",
    "mode_report_only",
    is_flag=True,
    help=(
        "Write docs/audits/legacy-debt-<ISO>.md + a row to wiki/log.md. "
        "Don't mutate wiki content or DB."
    ),
)
@click.option(
    "--commit",
    "mode_commit",
    is_flag=True,
    help=("Apply fixes to at most --limit pages. Writes the same report --report-only would."),
)
@click.option("--wiki-dir", default=None, help="wiki/ root (default settings.wiki_dir)")
def main(
    limit: int,
    mode_dry_run: bool,
    mode_report_only: bool,
    mode_commit: bool,
    wiki_dir: str | None,
) -> None:
    """Nightly incremental legacy-debt migration — dry-run by default."""
    # Mutually-exclusive modes. Default (no flag) maps to --dry-run.
    modes = [mode_dry_run, mode_report_only, mode_commit]
    if sum(modes) > 1:
        click.echo(
            "ERROR: --dry-run, --report-only, and --commit are mutually exclusive",
            err=True,
        )
        sys.exit(2)
    if not any(modes):
        mode_dry_run = True
    mode = "dry-run" if mode_dry_run else "report-only" if mode_report_only else "commit"

    wiki_root = Path(wiki_dir) if wiki_dir else settings.wiki_dir
    if not wiki_root.is_absolute():
        wiki_root = (REPO_ROOT / wiki_root).resolve()
    if not wiki_root.exists():
        click.echo(f"ERROR: wiki directory not found: {wiki_root}", err=True)
        sys.exit(2)

    now = datetime.now(UTC)
    catalog = _all_wiki_pages()
    scanned = len(catalog)
    legacy = scan_legacy_debt(catalog)

    _print_summary(legacy, scanned)

    if not legacy:
        click.echo("\nNothing to migrate — wiki is clean.")
        return

    if mode == "dry-run":
        click.echo("\nDry-run only. Re-run with --report-only or --commit.")
        return

    # Report-only + commit both produce the same report file. Commit
    # additionally applies the fixes to --limit pages.
    if mode == "commit":
        batch = legacy[:limit]
        click.echo(f"\nCommitting fixes to {len(batch)} / {len(legacy)} pages...")
        tally = apply_fixes(batch, wiki_root)
        click.echo(
            f"  status flipped: {tally['status_flipped']}, "
            f"entities renamed: {tally['entities_renamed']}, "
            f"domain added: {tally['domain_added']}, "
            f"wikilinks rewritten: {tally['wikilinks_rewritten']}, "
            f"errors: {tally['errors']}"
        )
        reported_pages = batch
    else:
        reported_pages = legacy

    out_dir = REPO_ROOT / "docs" / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = _report_path(out_dir, now)
    report = _render_report(reported_pages, mode=mode, scanned=scanned, generated_at=now)
    report_path.write_text(report, encoding="utf-8")
    click.echo(f"\nReport: {report_path}")

    # Single log row pointing at the report — keeps log.md terse.
    rel_report = report_path.relative_to(REPO_ROOT)
    notes = f"legacy-debt migration ({mode}): {len(reported_pages)} pages reported -> {rel_report}"
    _append_log_row(
        wiki_root,
        timestamp=now.isoformat(timespec="seconds"),
        outcome=mode,
        notes=notes,
    )
    click.echo(f"Appended row to {wiki_root / 'log.md'}")


if __name__ == "__main__":
    main()
