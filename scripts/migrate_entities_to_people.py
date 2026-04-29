"""One-shot migration: ``wiki/entities/*.md`` → ``wiki/people/*.md``.

Tier A renamed the canonical directory from ``entities`` to ``people`` so
the on-disk ontology matches the North-Star proposal — ``people`` are
humans, ``systems`` are products/platforms/services, ``entities`` is
deprecated. The live writer already emits ``wiki/people/`` for new pages;
this script moves every legacy page + rewrites every incoming link +
updates the ``wiki_pages`` catalog so the old name stops leaking into
new traces.

What it does
------------

1. Plan phase (both modes):
   - Enumerate every ``wiki/entities/*.md`` except ``index.md`` /
     ``.gitkeep`` (placeholders; the destination ``wiki/people/index.md``
     already exists and must not be overwritten).
   - Per page: read frontmatter, plan the rename to
     ``wiki/people/<slug>.md``, skip on invalid frontmatter, skip on
     destination collision.
   - Scan every wiki file for incoming references that need rewriting:
     ``[[entities/X]]`` → ``[[people/X]]`` (bare ``[[X]]`` stays valid
     — wikilinks are slug-indexed, not path-indexed), markdown-style
     ``[text](entities/X.md)`` / ``[text](../entities/X.md)`` → the
     same with ``people/``.
   - Plan the DB update: every ``wiki_pages`` row with
     ``page_type='entity'`` moves to ``page_type='person'`` and its
     ``path`` gets rewritten from ``wiki/entities/`` → ``wiki/people/``.

2. Execute phase (``--commit`` only):
   - For each planned rename: ``git mv`` if the file is tracked,
     ``Path.rename`` otherwise. Wiki markdown files are gitignored in
     this repo, so the fallback is the common path — but the branch
     exists so this script works in forks that do track the wiki.
   - Rewrite every incoming reference across every wiki file (not just
     the category folders — top-level ``home.md`` / ``changes.md`` /
     ``glossary.md`` count too).
   - UPDATE ``wiki_pages`` row-by-row with a per-row SAVEPOINT so one
     bad row doesn't abort the whole migration (mirrors the pattern in
     ``scripts/compile_all.py::_sync_wiki_catalog``).

Idempotence
-----------

Safe to re-run. If ``wiki/entities/`` has no renamable files AND the
catalog has no ``page_type='entity'`` rows, the script exits 0 with
"nothing to migrate". Wikilink rewrites are also idempotent — the
regex won't match strings that have already been rewritten.

Usage
-----

    uv run python scripts/migrate_entities_to_people.py                     # defaults to --dry-run
    uv run python scripts/migrate_entities_to_people.py --dry-run
    uv run python scripts/migrate_entities_to_people.py --commit --yes-really

``--commit`` is gated on ``--yes-really``; passing ``--commit`` alone
errors out. Extra safety rail because this run mutates BOTH the
filesystem (wiki pages) and the ``wiki_pages`` catalog in one shot,
and the migration is one-shot — a wrong run has to be undone by
hand. Dry-run writes a plan markdown to
``docs/audits/migration-plan-<ISO>.md`` and touches nothing.

After all moves succeed and the source directory is empty, the
directory itself is removed (``rmdir wiki/entities/``) so fresh writes
can't target the legacy path by habit.

One-shot lifecycle:
- Classification: one-shot (not yet run)
- Safe to delete after: 2026-06-18
- Deletion gate: the coordinator-authorised production run lands + a
  follow-up check confirms ``wiki/entities/`` is gone and
  ``wiki_pages`` holds zero ``page_type='entity'`` rows for 7
  consecutive days.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import psycopg
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import settings  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.wiki.categories import WIKI_CATEGORIES  # noqa: E402

# Files inside wiki/entities/ that are not real entity pages. Keep the
# corresponding files in wiki/people/ untouched; never move these.
_PLACEHOLDER_NAMES = frozenset({"index.md", ".gitkeep"})

# Non-category top-level wiki files that can still contain `entities/`
# references — home.md / glossary.md / changes.md aren't under any
# category folder so they don't come through WIKI_CATEGORIES.
_TOP_LEVEL_FILES: tuple[str, ...] = ("home.md", "glossary.md", "changes.md")


# `[[entities/slug]]` — prefixed-path wikilink form. The bare `[[slug]]`
# form doesn't need rewriting: wikilinks are slug-indexed in the viewer
# and `slug` doesn't change, only the folder does. We keep the trailing
# group so `[[entities/slug|Alias]]` preserves its alias.
_WIKILINK_PREFIX_RE = re.compile(r"\[\[entities/([^\]|]+)(\|[^\]]+)?\]\]")

# `[text](entities/slug.md)` or `[text](./entities/slug.md)` or
# `[text](../entities/slug.md)` — any markdown link whose target resolves
# to a path that starts with `entities/`. We're intentionally lenient
# on the leading prefix so relative-path variants from sibling-directory
# pages all flip over.
_MARKDOWN_LINK_RE = re.compile(
    r"(\]\(\s*(?:\.{1,2}/)*?)entities/([^)\s]+)",
)

# `page_type: entity` line inside a YAML frontmatter block. Restricted to
# the leading `---\n...\n---` region via `_rewrite_frontmatter_type` so we
# don't rewrite stray mentions in the body. Tolerant of a trailing
# comment (`page_type: entity  # legacy`) — the spaces + any trailing
# content after the match are preserved.
_FRONTMATTER_TYPE_RE = re.compile(r"^(\s*page_type:\s*)entity(\s*)$", re.MULTILINE)


@dataclass(frozen=True)
class MovePlan:
    """Planned rename of a single entity page."""

    src: Path
    dst: Path


@dataclass(frozen=True)
class Skip:
    """A file we refused to move + the reason why."""

    path: Path
    reason: str


@dataclass(frozen=True)
class LinkRewrite:
    """A file that contains references needing rewrite + the rewritten content.

    We stash the fully-rewritten body on the plan object so the execute
    phase is a pure write — no second disk read, no risk of reading
    from a path that moved out from under us during the move phase.
    """

    path: Path
    new_content: str
    wikilink_count: int
    markdown_count: int


@dataclass
class Plan:
    """Full migration plan for a dry-run or commit run."""

    moves: list[MovePlan]
    skips: list[Skip]
    rewrites: list[LinkRewrite]
    catalog_rows: int  # count of wiki_pages rows with page_type='entity'

    @property
    def wikilink_total(self) -> int:
        return sum(r.wikilink_count for r in self.rewrites)

    @property
    def markdown_total(self) -> int:
        return sum(r.markdown_count for r in self.rewrites)

    @property
    def is_noop(self) -> bool:
        # Skips count as work-to-report. A run where every remaining
        # entity is blocked by a collision must NOT early-exit with
        # "nothing to migrate" — the operator needs to see the skip
        # list so they can unblock the migration.
        return not self.moves and not self.rewrites and not self.skips and self.catalog_rows == 0


def _iter_entity_pages(entities_dir: Path) -> Iterable[Path]:
    """Yield every ``entities/*.md`` file worth moving.

    Sorted output keeps the plan deterministic across runs — helpful
    when the dry-run plan file is diffed between attempts.
    """
    if not entities_dir.exists():
        return
    for page in sorted(entities_dir.glob("*.md")):
        if page.name in _PLACEHOLDER_NAMES:
            continue
        yield page


def _build_move_plan(entities_dir: Path, people_dir: Path) -> tuple[list[MovePlan], list[Skip]]:
    """Walk ``wiki/entities/*.md`` and return (moves, skips).

    Skips:
      - unreadable file (OS error / bad UTF-8)
      - frontmatter parse error — the file is skipped + logged so the
        operator can repair it before the next run
      - destination collision (``wiki/people/<slug>.md`` already exists)
    """
    moves: list[MovePlan] = []
    skips: list[Skip] = []
    for src in _iter_entity_pages(entities_dir):
        try:
            content = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skips.append(Skip(src, f"unreadable: {exc}"))
            continue
        # Parse frontmatter defensively — we don't require valid FM to
        # move a file (the filename carries the slug, that's enough),
        # but if the parser crashes we want to know. The narrow set
        # matches what yaml.safe_load can raise on malformed input
        # (YAMLError) plus the type coercion failures an unexpected
        # content shape could trigger (ValueError / TypeError).
        try:
            extract_frontmatter(content)
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            skips.append(Skip(src, f"frontmatter parse error: {exc}"))
            continue
        dst = people_dir / src.name
        if dst.exists():
            skips.append(Skip(src, f"destination exists: {dst.name}"))
            continue
        moves.append(MovePlan(src=src, dst=dst))
    return moves, skips


def _rewrite_content(content: str) -> tuple[str, int, int]:
    """Rewrite `entities/` references. Returns (new_content, wikilinks, markdown)."""
    new_content, wl_count = _WIKILINK_PREFIX_RE.subn(
        lambda m: f"[[people/{m.group(1)}{m.group(2) or ''}]]", content
    )
    new_content, md_count = _MARKDOWN_LINK_RE.subn(
        lambda m: f"{m.group(1)}people/{m.group(2)}", new_content
    )
    return new_content, wl_count, md_count


def _iter_scan_targets(wiki_dir: Path) -> Iterable[Path]:
    """Yield every wiki markdown file we should scan for references."""
    for sub in WIKI_CATEGORIES:
        d = wiki_dir / sub
        if not d.exists():
            continue
        yield from sorted(d.glob("*.md"))
    for name in _TOP_LEVEL_FILES:
        p = wiki_dir / name
        if p.exists():
            yield p


def _build_rewrite_plan(wiki_dir: Path, move_map: dict[Path, Path]) -> list[LinkRewrite]:
    """Find every file with incoming `entities/` references.

    ``move_map`` maps each ``entities/X.md`` source to its planned
    ``people/X.md`` destination. When we record a rewrite on a file
    that's about to move, the recorded path is the POST-move location —
    otherwise the rewrite phase (which runs after the moves) would try
    to write to a path that no longer exists.
    """
    rewrites: list[LinkRewrite] = []
    for page in _iter_scan_targets(wiki_dir):
        try:
            content = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_content, wl_count, md_count = _rewrite_content(content)
        if wl_count or md_count:
            final_path = move_map.get(page, page)
            rewrites.append(LinkRewrite(final_path, new_content, wl_count, md_count))
    return rewrites


def _count_catalog_entities() -> int:
    """Count wiki_pages rows with page_type='entity'. Returns 0 on DB failure
    so dry-runs never hard-fail in environments without DB access.

    Catches only the narrow set we actually expect in a dry-run without
    DB access: connection failure (``psycopg.OperationalError``),
    schema missing (``psycopg.ProgrammingError``), and the driver not
    being importable (``ImportError`` from ``src.db.connect``)."""
    try:
        from src.db import connect
    except ImportError as exc:
        click.echo(f"WARNING: DB driver unavailable ({exc}); assuming 0 entity rows", err=True)
        return 0

    try:
        with connect() as conn:
            row: Any = conn.execute(
                "SELECT COUNT(*)::int AS n FROM wiki_pages WHERE page_type = 'entity'"
            ).fetchone()
            return int(row["n"]) if row else 0
    except (psycopg.OperationalError, psycopg.ProgrammingError) as exc:
        click.echo(f"WARNING: DB query failed ({exc}); assuming 0 entity rows", err=True)
        return 0


def _build_plan(wiki_dir: Path) -> Plan:
    """Assemble the full migration plan by walking the filesystem + DB."""
    entities_dir = wiki_dir / "entities"
    people_dir = wiki_dir / "people"
    moves, skips = _build_move_plan(entities_dir, people_dir)
    move_map = {m.src: m.dst for m in moves}
    rewrites = _build_rewrite_plan(wiki_dir, move_map)
    catalog_rows = _count_catalog_entities()
    return Plan(moves=moves, skips=skips, rewrites=rewrites, catalog_rows=catalog_rows)


def _is_git_tracked(path: Path) -> bool:
    """True if `git ls-files` reports the path as tracked.

    Wiki markdown is gitignored in this repo, so for most files this
    returns False and the caller falls back to ``Path.rename``. Forks
    or downstream consumers that do track the wiki get proper
    history-preserving renames via ``git mv``.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _git_mv(src: Path, dst: Path) -> None:
    """Run `git mv` and raise on nonzero exit.

    Caller must have already verified the source is tracked — `git mv`
    on an untracked file prints "fatal: not under version control" and
    exits 128, which we surface as a RuntimeError.
    """
    result = subprocess.run(
        ["git", "mv", str(src), str(dst)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git mv {src} → {dst} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _rewrite_frontmatter_type(path: Path) -> bool:
    """Flip ``page_type: entity`` → ``page_type: person`` in the frontmatter.

    The rewrite is scoped to the YAML frontmatter block so a body
    mention of the word "entity" never mutates. Returns True if the
    file changed; False on read failure or when the frontmatter doesn't
    contain ``page_type: entity`` (idempotent). Best-effort — a failure
    here logs a warning rather than aborting the migration, matching
    the resilience semantics of ``_execute_rewrites``.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        click.echo(f"WARNING: unable to rewrite page_type in {path} ({exc})", err=True)
        return False
    # Scope the substitution to the leading frontmatter block only.
    if not content.startswith("---\n"):
        return False
    close_idx = content.find("\n---", 4)
    if close_idx == -1:
        return False
    fm_block = content[: close_idx + 4]
    rest = content[close_idx + 4 :]
    new_fm, n = _FRONTMATTER_TYPE_RE.subn(r"\1person\2", fm_block)
    if not n:
        return False
    try:
        path.write_text(new_fm + rest, encoding="utf-8")
    except OSError as exc:
        click.echo(f"WARNING: unable to write page_type fix to {path} ({exc})", err=True)
        return False
    return True


def _execute_moves(moves: list[MovePlan]) -> tuple[int, list[Skip]]:
    """Perform the filesystem moves; return (moved_count, skipped_mid_flight).

    Errors on a single move are collected as additional Skips rather
    than raising — partial migration is recoverable via a second
    ``--commit`` run once the operator addresses the failure. Never
    leaves the filesystem in an undefined state: each move is atomic.

    After a successful move we also flip ``page_type: entity`` →
    ``page_type: person`` in the moved file's frontmatter so compile
    + validator tooling (which cross-checks page_type against the
    parent directory) doesn't flag every migrated page as a type
    mismatch.
    """
    moved = 0
    skipped: list[Skip] = []
    for plan in moves:
        plan.dst.parent.mkdir(parents=True, exist_ok=True)
        if plan.dst.exists():
            # Race: another run or the operator created it between plan
            # and execute. Skip + log; safer than clobbering.
            skipped.append(Skip(plan.src, f"destination appeared mid-run: {plan.dst.name}"))
            continue
        try:
            if _is_git_tracked(plan.src):
                _git_mv(plan.src, plan.dst)
            else:
                plan.src.rename(plan.dst)
            moved += 1
        except (OSError, RuntimeError) as exc:
            skipped.append(Skip(plan.src, f"move failed: {exc}"))
            continue
        # Successful move — flip the frontmatter page_type in place.
        _rewrite_frontmatter_type(plan.dst)
    return moved, skipped


def _execute_rewrites(rewrites: list[LinkRewrite], moved_dsts: set[Path]) -> tuple[int, int]:
    """Apply all rewrites; return ``(wikilinks_rewritten, markdown_rewritten)``.

    For files that were NOT moved, we trust the precomputed
    ``rw.new_content`` — the plan-phase buffer is still a faithful copy
    of what's on disk.

    For files that WERE moved (``rw.path in moved_dsts``), the
    plan-phase buffer is stale: ``_execute_moves`` rewrites the moved
    file's frontmatter in place (``_rewrite_frontmatter_type``), and
    writing the precomputed buffer would clobber that fix. Re-read the
    current on-disk content and apply the link regex fresh so both
    mutations land.

    Returning the two counts separately lets the CLI surface them in
    the same shape as the dry-run plan summary so operators don't have
    to wonder whether the aggregate total includes markdown links.
    """
    wikilinks = 0
    markdown = 0
    for rw in rewrites:
        try:
            if rw.path in moved_dsts:
                current = rw.path.read_text(encoding="utf-8")
                new_content, wl, md = _rewrite_content(current)
                rw.path.write_text(new_content, encoding="utf-8")
                wikilinks += wl
                markdown += md
            else:
                rw.path.write_text(rw.new_content, encoding="utf-8")
                wikilinks += rw.wikilink_count
                markdown += rw.markdown_count
        except (OSError, UnicodeDecodeError) as exc:
            click.echo(f"WARNING: skipping rewrite of {rw.path} ({exc})", err=True)
    return wikilinks, markdown


def _execute_catalog_update() -> tuple[int, int]:
    """Update every entity row to page_type='person' + fixed path.

    Returns (updated, failed). Each row gets its own SAVEPOINT so one
    bad row (e.g. a UNIQUE collision on `slug` if someone already
    manually inserted a `person` row for the same slug) doesn't abort
    the whole migration. This mirrors ``_sync_wiki_catalog``.
    """
    from src.db import connect

    updated = 0
    failed = 0
    with connect() as conn:
        rows: list[Any] = list(
            conn.execute(
                "SELECT page_id, slug, path FROM wiki_pages WHERE page_type = 'entity'"
            ).fetchall()
        )
        for row in rows:
            page_id = int(row["page_id"])
            old_path = str(row["path"]) if row["path"] else ""
            new_path = old_path.replace("wiki/entities/", "wiki/people/", 1)
            try:
                conn.execute("SAVEPOINT migrate_row")
                conn.execute(
                    "UPDATE wiki_pages SET page_type = 'person', path = %s WHERE page_id = %s",
                    (new_path, page_id),
                )
                conn.execute("RELEASE SAVEPOINT migrate_row")
                updated += 1
            except psycopg.Error as exc:
                conn.execute("ROLLBACK TO SAVEPOINT migrate_row")
                click.echo(
                    f"WARNING: catalog update failed for page_id={page_id} "
                    f"slug={row['slug']}: {exc}",
                    err=True,
                )
                failed += 1
        conn.commit()
    return updated, failed


def _render_plan_markdown(plan: Plan, timestamp: str) -> str:
    """Render the dry-run plan into a markdown file we save under docs/audits/."""
    lines: list[str] = []
    lines.append(f"# Migration plan — entities/ → people/ ({timestamp})\n")
    lines.append("## Summary\n")
    lines.append(f"- Files to move: **{len(plan.moves)}**")
    lines.append(f"- Files to skip: **{len(plan.skips)}**")
    lines.append(f"- Files containing incoming references: **{len(plan.rewrites)}**")
    lines.append(f"  - `[[entities/...]]` wikilinks: **{plan.wikilink_total}**")
    lines.append(f"  - `[...](entities/...)` markdown links: **{plan.markdown_total}**")
    lines.append(f"- Catalog rows to update (`page_type='entity'`): **{plan.catalog_rows}**\n")

    if plan.skips:
        lines.append("## Skips\n")
        for skip in plan.skips:
            lines.append(f"- `{skip.path}` — {skip.reason}")
        lines.append("")

    if plan.moves:
        move_preview = plan.moves[:20]
        lines.append(f"## Move preview (first {len(move_preview)} of {len(plan.moves)})\n")
        for m in move_preview:
            lines.append(f"- `{m.src}` → `{m.dst}`")
        lines.append("")

    if plan.rewrites:
        rewrite_preview = plan.rewrites[:20]
        lines.append(f"## Rewrite preview (first {len(rewrite_preview)} of {len(plan.rewrites)})\n")
        for r in rewrite_preview:
            lines.append(
                f"- `{r.path}` — {r.wikilink_count} wikilink(s), "
                f"{r.markdown_count} markdown link(s)"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def _print_plan_summary(plan: Plan) -> None:
    click.echo(
        f"{len(plan.moves)} files would move · {len(plan.skips)} would skip · "
        f"{plan.wikilink_total} wikilink rewrites · "
        f"{plan.markdown_total} markdown link rewrites · "
        f"{plan.catalog_rows} catalog rows"
    )
    for skip in plan.skips:
        click.echo(f"  skip: {skip.path} — {skip.reason}")


def _write_plan_file(plan: Plan, timestamp: str) -> Path:
    """Write the dry-run plan markdown under docs/audits/; return the path."""
    audits_dir = REPO_ROOT / "docs" / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    plan_path = audits_dir / f"migration-plan-{timestamp}.md"
    plan_path.write_text(_render_plan_markdown(plan, timestamp), encoding="utf-8")
    return plan_path


def _cleanup_entities_dir(entities_dir: Path) -> bool:
    """Remove ``wiki/entities/`` if it's effectively empty, else leave alone.

    "Effectively empty" = no ``*.md`` files other than the placeholders
    (``index.md`` / ``.gitkeep``) we intentionally refuse to move. Those
    placeholders are removed here, then the directory is removed.

    Returns True if we removed the directory, False otherwise. A False
    return is not an error — it means migration is still partial (some
    entity pages remain, e.g. blocked by collisions) and the directory
    needs to stick around for the retry.
    """
    if not entities_dir.exists():
        return False
    remaining_md = [p for p in entities_dir.glob("*.md") if p.name not in _PLACEHOLDER_NAMES]
    if remaining_md:
        return False
    for placeholder in entities_dir.iterdir():
        if placeholder.name in _PLACEHOLDER_NAMES:
            try:
                placeholder.unlink()
            except OSError as exc:
                click.echo(f"WARNING: unable to remove {placeholder} ({exc})", err=True)
                return False
    try:
        entities_dir.rmdir()
    except OSError as exc:
        # Non-placeholder file snuck in (subdir, stray file) — bail.
        click.echo(f"WARNING: unable to rmdir {entities_dir} ({exc})", err=True)
        return False
    return True


@click.command()
@click.option(
    "--dry-run/--commit",
    default=True,
    help="Dry-run (default, safe) or commit. Dry-run writes no files and no DB changes.",
)
@click.option(
    "--yes-really",
    is_flag=True,
    default=False,
    help="Second confirmation required to pair with --commit. Without it, --commit errors out.",
)
def main(dry_run: bool, yes_really: bool) -> None:
    if not dry_run and not yes_really:
        click.echo(
            "refusing to --commit without --yes-really. "
            "This run would mutate the filesystem + the wiki_pages catalog; "
            "re-invoke with both flags to proceed.",
            err=True,
        )
        sys.exit(2)
    wiki_dir = (
        REPO_ROOT / settings.wiki_dir if not settings.wiki_dir.is_absolute() else settings.wiki_dir
    )
    plan = _build_plan(wiki_dir)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    if plan.is_noop:
        click.echo("nothing to migrate — entities/ is empty and catalog has no entity rows")
        return

    _print_plan_summary(plan)

    if dry_run:
        plan_path = _write_plan_file(plan, timestamp)
        click.echo(f"plan saved: {plan_path}")
        click.echo("dry-run — no files moved, no DB changes")
        return

    # --commit: execute in order: moves → rewrites → DB.
    # Order matters: moves first so the rewrites can be verified against
    # the new people/ directory in a re-run; DB last because it's the
    # cheapest to retry in isolation.
    moved, mid_flight_skips = _execute_moves(plan.moves)
    total_skips = plan.skips + mid_flight_skips
    # If a move was skipped mid-flight (e.g. the destination appeared
    # between plan and execute), the source file is still sitting at
    # wiki/entities/<slug>.md — but plan.rewrites recorded the POST-move
    # destination path for any rewrites on that page. Writing the
    # rewritten entity content to wiki/people/<slug>.md would clobber
    # the file the mid-flight guard was protecting. Drop those
    # rewrites; they'll get retried on the next `--commit` once the
    # collision is resolved.
    move_map_by_src = {m.src: m.dst for m in plan.moves}
    guarded_dsts = {move_map_by_src[s.path] for s in mid_flight_skips if s.path in move_map_by_src}
    safe_rewrites = [rw for rw in plan.rewrites if rw.path not in guarded_dsts]
    # Moved destinations — for these, _execute_rewrites re-reads from
    # disk and applies the regex fresh so the frontmatter rewrite done
    # by _execute_moves isn't clobbered by the stale plan-phase buffer.
    moved_dsts = {m.dst for m in plan.moves} - guarded_dsts
    wikilinks_rewritten, markdown_rewritten = _execute_rewrites(safe_rewrites, moved_dsts)
    catalog_updated, catalog_failed = _execute_catalog_update()
    # Cleanup: remove wiki/entities/ when migration is clean. Mid-flight
    # skips keep the directory around so the retry has somewhere to go;
    # plan-time skips (collision) are caught inside `_cleanup_entities_dir`
    # by the `remaining_md` guard.
    directory_removed = False
    if not mid_flight_skips:
        directory_removed = _cleanup_entities_dir(wiki_dir / "entities")

    click.echo("")
    click.echo("=== migration complete ===")
    click.echo(f"entity_pages_moved:        {moved}")
    click.echo(f"wikilinks_rewritten:       {wikilinks_rewritten}")
    click.echo(f"markdown_links_rewritten:  {markdown_rewritten}")
    click.echo(f"catalog_rows_updated:      {catalog_updated}")
    click.echo(f"entities_dir_removed:      {directory_removed}")
    if catalog_failed:
        click.echo(f"catalog_rows_failed:   {catalog_failed}")
    if total_skips:
        click.echo(f"files_skipped:         {len(total_skips)}")
        for skip in total_skips:
            click.echo(f"  - {skip.path}: {skip.reason}")

    if mid_flight_skips:
        click.echo("")
        click.echo(
            "WARNING: partial migration — some filesystem moves failed mid-run. "
            "Review the skips above, resolve, then re-run `--commit`. "
            "If you need to roll back, `git checkout -- wiki/` restores tracked "
            "state; untracked files that were renamed stay at their new path."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
