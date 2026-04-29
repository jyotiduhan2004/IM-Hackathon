"""Post-batch coordinator hooks for the compile loop.

After the agent finishes a batch, this module:

1. detects which content / non-content pages mtime advanced over the batch
2. stamps ``last_compiled`` / ``updated_by`` / ``update_count`` frontmatter
   plus the catalog mirror in ``wiki_pages.last_compiled_at``
3. runs the formatter + mdlint autofix passes on touched pages
4. mirrors touched-page state into ``wiki_pages`` (catalog) and
   ``message_touched_pages`` (per-batch authority)
5. validates touched pages so format drift surfaces in the batch notes
6. handles the North-Star landing surfaces (``index.md``, ``changes.md``,
   ``domains/*``, ``decisions/*``) which are derived, not authoritative

These run AFTER ``run_compilation`` returns and BEFORE the batch state
flips in ``src.coordinator.batch_state`` — order is load-bearing because
the state-flip queries read the catalog rows this module writes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import click
import psycopg
import structlog

from src.coordinator.batch_state import CONTENT_PAGE_TYPES
from src.db.touched_pages import insert_touch
from src.db.users import upsert_user
from src.db.wiki_pages import find_by_slug
from src.db.wiki_pages import upsert_wiki_page
from src.utils import extract_body
from src.utils import extract_frontmatter
from src.utils import render_with_frontmatter
from src.wiki.categories import WIKI_CATEGORIES as _WIKI_CATEGORIES
from src.wiki.landing import _generate_changes
from src.wiki.landing import _generate_home
from src.wiki.landing import _regenerate_decision_stubs
from src.wiki.landing import _regenerate_domain_hubs
from src.wiki.page_formatter import format_page
from src.wiki.page_validator import Error as ValidationError
from src.wiki.page_validator import validate_page

logger = structlog.get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")


REPO_ROOT = Path(__file__).resolve().parents[2]


_CONTENT_PAGE_DIRS: tuple[str, ...] = (
    "topics",
    "systems",
    "policies",
    "decisions",
    "timelines",
    "conflicts",
)


_CATEGORY_BY_FOLDER: dict[str, str] = {
    "topics": "topic",
    "entities": "entity",
    "people": "person",
    "systems": "system",
    "policies": "policy",
    "timelines": "timeline",
    "conflicts": "conflict",
    "domains": "domain",
    "decisions": "decision",
}
# Matches the wiki_pages status CHECK (widened 2026-04-16 via migration
# 202604161200_wiki_pages_new_ontology.sql). 'active' / 'archived' are
# the North-Star values the landing generators emit.
_VALID_WIKI_STATUS = {"current", "superseded", "contested", "active", "archived"}

# Regenerated every run by `_regenerate_landing_surfaces`. Live outside
# the category folders, so the per-batch `_sync_wiki_catalog` skips
# them — `_sync_and_stamp_landing_surfaces` handles these separately.
# Each maps to its semantic page_type (CHECK accepts all three after
# 202604161300_wiki_pages_home_changes.sql).
# Post-2026-04-24 routing:
#   - `index.md` serves `/` (the 8-domain reader home; was `home.md`).
#   - `changes.md` stays the same (product activity feed; rewritten in PR).
#   - `compile-status.md` is the ops view (was `index.md`); intentionally
#     NOT cataloged — it's internal ops, not something `resolve_page` should
#     surface and the `wiki_pages.page_type` CHECK has no matching value.
# Glossary was removed in the same PR — do not resurrect by mapping here.
_TOP_LEVEL_LANDING_PAGE_TYPES: dict[str, str] = {
    "index.md": "home",
    "changes.md": "changes",
}


def _regenerate_landing_surfaces(wiki_dir: str) -> None:
    """Run the North-Star landing-page generators after the index rebuild.

    Always invoked after `update_wiki_index` so the index-level stamping
    pass has already settled `last_compiled`. Each generator is idempotent;
    failures are logged but never abort the compile run — landing surfaces
    are derived, not authoritative.
    """
    wiki_path = Path(wiki_dir)
    jobs: tuple[tuple[str, Callable[[Path], Any], Callable[[Any], str]], ...] = (
        (
            "domain-hubs",
            _regenerate_domain_hubs,
            lambda r: f"Regenerated {len(r)} domain hubs.",
        ),
        # Glossary removed 2026-04-24 — regex extractor produced misleading
        # definitions (`BL = "Monolith + Modular"`, `DAU = "January 8, 2026"`).
        ("home", _generate_home, lambda _r: "Regenerated index.md (reader home)."),
        ("changes", _generate_changes, lambda _r: "Regenerated changes.md."),
        (
            "decisions",
            _regenerate_decision_stubs,
            lambda r: f"Regenerated {len(r)} decision stubs.",
        ),
    )
    # Silent-failure trap learned 2026-04-24: a prior revision logged
    # landing-gen exceptions only through structlog, so when
    # `_regenerate_domain_hubs` silently no-op'd the 8 domain pages never
    # materialized — and the failure was invisible to the operator running
    # `make publish`. The pipeline must not abort (derived surfaces aren't
    # authoritative), but humans need to see the failure on stderr so they
    # can diagnose before the next publish.
    for name, fn, fmt in jobs:
        try:
            click.echo(fmt(fn(wiki_path)))
        except Exception as exc:  # noqa: BLE001 — landing gen must never abort a run
            logger.warning("landing-failed", generator=name, error=str(exc))
            click.echo(f"WARN: landing generator {name!r} failed: {exc}", err=True)


def _stamp_frontmatter_compiled(fm: dict[str, Any], now_iso: str, model_name: str) -> None:
    """Stamp a page's frontmatter with compile metadata. Mutates in place."""
    fm["last_compiled"] = now_iso
    fm["updated_by"] = model_name
    fm["update_count"] = int(fm.get("update_count") or 0) + 1


def _stamp_recently_modified_pages(
    wiki_dir: str, since_timestamp: float, model_name: str
) -> tuple[int, int]:
    """Stamp `last_compiled`/`updated_by`/`update_count` on pages touched
    after `since_timestamp` (POSIX seconds).

    The LLM agent has a `stamp_page_compiled_at` tool but routinely forgets
    to call it on every page it touched. This coordinator-side pass walks
    the wiki after the batch loop and stamps every page whose mtime is
    newer than the run start time. `update_wiki_index` has a similar
    fallback for missing-stamp pages, but it only fires when `last_compiled`
    is absent — pages updated by the agent in this run already have a
    stale stamp from a previous compile, so the index pass skips them.

    Also updates the catalog mirror — ``wiki_pages.last_compiled_at`` — so
    `resolve_page` ordering + Langfuse freshness scores reflect the
    just-completed run (#165). The frontmatter and DB stamp are written
    together; a DB blip logs a warning but does not roll back the
    on-disk frontmatter (the catalog self-heals on the next sync).

    Returns (stamped_count, skipped_count). `skipped` covers pages whose
    frontmatter looks corrupt (missing `title`/`page_type` after extraction)
    — same guard `update_wiki_index` uses to avoid clobbering mangled pages.
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return 0, 0

    now_iso = datetime.now(IST).isoformat(timespec="seconds")
    stamped, skipped = 0, 0
    stamped_slugs: list[str] = []

    for category in _WIKI_CATEGORIES:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                if md_file.stat().st_mtime <= since_timestamp:
                    continue
                content = md_file.read_text(encoding="utf-8")
                fm = extract_frontmatter(content)
                # Mirror update_wiki_index's "looks like a real page" guard
                # — a page with neither title nor page_type is either an
                # orphan or got mangled by the agent's edit_file. Don't
                # overwrite it from the coordinator either.
                if not ("title" in fm or "page_type" in fm):
                    skipped += 1
                    continue
                _stamp_frontmatter_compiled(fm, now_iso, model_name)
                body = extract_body(content)
                md_file.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
                stamped += 1
                stamped_slugs.append(md_file.stem)
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("stamp skip", path=str(md_file), error=str(exc))
                skipped += 1

    # Catalog mirror — keep ``wiki_pages.last_compiled_at`` in lockstep
    # with the on-disk frontmatter so downstream consumers (resolve_page
    # ordering, Langfuse freshness scores) see a live value instead of
    # the NULL #165 flagged. Runs in a single batch UPDATE; failures are
    # logged but never re-raised (catalog staleness < crashed coordinator).
    if stamped_slugs:
        _stamp_catalog_last_compiled(stamped_slugs)

    return stamped, skipped


def _stamp_catalog_last_compiled(slugs: list[str]) -> None:
    """UPDATE ``wiki_pages.last_compiled_at = now()`` for each slug.

    Companion to the frontmatter stamp in ``_stamp_recently_modified_pages``
    — #165 noted the DB column was NULL on every topic page because the
    agent's ``stamp_page_compiled_at`` tool only writes frontmatter.
    Wraps in try/except + logs: the frontmatter is already persisted, a
    DB blip must not crash the compile coordinator (next catalog sync
    reconciles).
    """
    if not slugs:
        return
    # Lazy import so the conftest monkeypatch on ``src.db.connect`` is
    # picked up (same pattern as ``_sync_wiki_catalog``).
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE wiki_pages SET last_compiled_at = now() WHERE slug = ANY(%s)",
                (list(slugs),),
            )
            conn.commit()
    except psycopg.Error as exc:
        logger.warning(
            "stamp_catalog_last_compiled_failed",
            slugs_count=len(slugs),
            error=str(exc),
        )


def _iter_touched_content_pages(batch_start: float, wiki_dir: Path) -> list[Path]:
    """Like ``_iter_touched_pages`` but scans every CONTENT-type directory.

    The catalog write path needs touch rows for any content-type page the
    batch modified — including ``policies/`` and ``decisions/`` which the
    formatter/validator helpers don't walk. Without this, a batch that
    only edits a policy page writes zero touch rows and the message stays
    pending forever (Bug C-shaped: Codex flagged on PR #132).

    Excludes ``entities/`` and ``people/`` — person stubs are not
    compile-state evidence (we tightened the citation check explicitly).
    Excludes ``glossary`` (single root-level file, agent doesn't author).
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return []
    touched: list[Path] = []
    for category in _CONTENT_PAGE_DIRS:
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                if md_file.stat().st_mtime >= batch_start:
                    touched.append(md_file)
            except OSError as exc:
                logger.warning(
                    "post-batch content stat failed",
                    path=str(md_file),
                    error=str(exc),
                )
    return touched


def _iter_touched_pages(batch_start: float, wiki_dir: Path) -> list[Path]:
    """Return every wiki page whose mtime advanced at/after ``batch_start``.

    Scans the same set of categories the formatter operates on
    (topics/entities/people/systems — policies/timelines/conflicts keep their
    own templates). `people/` is accepted alongside `entities/` during the
    C1 migration transition. Used by both the post-batch formatter and
    validator so the validator sees every page the batch touched, not just
    the ones the formatter chose to rewrite. Missing ``wiki_dir`` → empty
    list (matches the stamp helper's contract).

    NOT used for catalog writes — see ``_iter_touched_content_pages`` for
    the wider scope that covers policies/decisions too.
    """
    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return []

    touched: list[Path] = []
    for category in ("topics", "entities", "people", "systems"):
        cat_dir = wiki_path / category
        if not cat_dir.exists():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            try:
                if md_file.stat().st_mtime >= batch_start:
                    touched.append(md_file)
            except OSError as exc:
                # stat() can fail if the page was deleted mid-scan. Log and
                # skip — a missing page is never "touched" for our purposes.
                logger.warning(
                    "post-batch stat failed",
                    path=str(md_file),
                    error=str(exc),
                )
    return touched


def _normalize_touched_pages(pages: list[Path], wiki_dir: Path) -> list[Path]:
    """Run the formatter against each page in ``pages``. Returns the subset
    the formatter actually rewrote.

    Prompt discipline alone doesn't keep the agent from writing the things
    the light-format rule forbids (hand-written ``## Related`` / ``## People``
    / duplicate-heading drift). This hook closes the loop at write time:
    after a batch's emails are marked compiled, run the idempotent formatter
    over every page the batch touched.

    The formatter is a no-op on clean pages and skips malformed ones (both
    return False), so this list is a strict subset of ``pages``. The caller
    feeds ``pages`` (not the returned subset) into the validator so we
    surface corruption on pages the formatter leaves alone. Exceptions from
    the formatter are caught and logged — a buggy formatter should never
    corrupt the successful compile that already happened.
    """
    wiki_path = Path(wiki_dir)
    changed: list[Path] = []
    for md_file in pages:
        try:
            if format_page(md_file, wiki_path, confirm=True):
                changed.append(md_file)
        except Exception as exc:  # noqa: BLE001 — formatter must not crash compile
            logger.warning(
                "post-batch formatter failed",
                path=str(md_file),
                error=str(exc),
            )
    return changed


def _mdlint_autofix_touched_pages(pages: list[Path]) -> int:
    """Best-effort structural hygiene pass via ``pymarkdown fix`` on the
    touched pages. Complements the content formatter:

    - ``_normalize_touched_pages`` handles section-level normalization
      (idempotent frontmatter, section templating)
    - this handles whitespace-class hygiene (final newlines, blank-line
      collapse, list indent, trailing spaces)

    Silently no-ops if pymarkdown is not installed (e.g. production
    images without the dev extra) — the validator's mdlint warn-pass
    will still surface remaining issues. Returns the fix-command exit
    code purely for observability; failures don't affect the compile.

    MD024 (duplicate-heading) is NOT auto-fixable by design — content
    judgement lives in the reviewer subagent.
    """
    import shutil
    import subprocess

    if shutil.which("pymarkdown") is None or not pages:
        return 0
    try:
        result = subprocess.run(
            ["pymarkdown", "fix", *[str(p) for p in pages]],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("mdlint_autofix_failed", error=str(exc))
        return 0
    fixed = sum(1 for line in result.stdout.splitlines() if line.startswith("Fixed:"))
    if fixed:
        logger.info("mdlint_autofix_applied", pages_fixed=fixed, total_touched=len(pages))
    return fixed


def _sync_wiki_catalog(pages: list[Path], wiki_dir: Path) -> int:
    """Upsert each touched page into the `wiki_pages` catalog.

    The catalog is what `resolve_page` queries. Without this sync the
    agent's next run can't find pages this batch just created, and
    `resolve_page` silently misses — see
    docs/reviews/trace-tooling-audit-20260415T121025Z.md for the symptom.

    Entity pages pre-insert their canonical email into `users` to satisfy
    the wiki_pages→users FK (the stub tool already does this; this
    mirror-call keeps the post-batch hook self-contained).

    Per-page errors are isolated via SAVEPOINT so one failing row doesn't
    cascade. Without the savepoint, psycopg3 leaves the connection in an
    aborted-transaction state after any constraint violation, and every
    subsequent upsert on the same connection raises InFailedSqlTransaction
    — silently zeroing out the rest of the batch's sync. Claude review
    on PR #80 caught this; the `test_sync_does_not_cascade_on_bad_row`
    test pins it.

    Paths are stored repo-relative (matching `scripts/backfill_wiki_pages.py`)
    so the two code paths don't fight over the `wiki_pages.path` column on
    ON-CONFLICT updates.

    Failures are logged but never re-raised — catalog staleness is
    strictly less bad than a crashed compile coordinator.

    Returns the count of pages successfully upserted.
    """
    # `connect` is lazy-imported so the test suite's conftest
    # monkeypatch on `src.db.connect` (which pins search_path to the
    # per-test schema) is picked up here. A top-level `from src.db
    # import connect` would bind the original unpatched function and
    # tests would write to the production schema.
    from src.db import connect as _connect

    wiki_dir_abs = wiki_dir.resolve()
    synced = 0
    with _connect() as conn:
        for page in pages:
            page_abs = Path(page).resolve()
            try:
                rel = page_abs.relative_to(wiki_dir_abs)
            except ValueError:
                continue  # page outside the wiki root; skip
            if len(rel.parts) < 2:
                continue  # top-level files (home.md, index.md) aren't catalog entries
            page_type = _CATEGORY_BY_FOLDER.get(rel.parts[0])
            if page_type is None:
                continue
            try:
                content = page_abs.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("catalog_sync_read_failed", path=str(page_abs), error=str(exc))
                continue
            fm = extract_frontmatter(content)
            # Empty-frontmatter guard (#180). `--- / {} / ---` and other
            # missing-field frontmatter produced catalog rows with a
            # stem-derived title and default status — the validator
            # flagged the page as broken but the catalog still reported
            # it as synced. Refuse to upsert when both ``page_type`` and
            # ``title`` are absent; the coordinator can't tell a legit
            # page from a mangled one.
            title_raw = fm.get("title")
            fm_title = title_raw.strip() if isinstance(title_raw, str) else ""
            type_raw = fm.get("page_type")
            fm_type = type_raw.strip() if isinstance(type_raw, str) else ""
            if not fm_title and not fm_type:
                logger.info(
                    "catalog_skipped",
                    path=str(page_abs),
                    reason="empty_frontmatter",
                )
                continue
            title = fm_title or page_abs.stem.replace("-", " ").title()
            status_raw = fm.get("status")
            status = (
                status_raw
                if isinstance(status_raw, str) and status_raw in _VALID_WIKI_STATUS
                else "current"
            )
            canonical_email: str | None = None
            # Both 'entity' and 'person' carry a canonical email during the
            # C1 transition — neither gets dropped here.
            if page_type in ("entity", "person"):
                email_raw = fm.get("email")
                if isinstance(email_raw, str) and email_raw.strip():
                    canonical_email = email_raw.strip()

            # Match backfill_wiki_pages.py: prefer repo-relative, fall back
            # to absolute if the page lives outside the repo tree. Without
            # this, on-conflict updates overwrite backfilled relative paths
            # with absolute ones and downstream consumers break.
            try:
                path_to_store = str(page_abs.relative_to(REPO_ROOT))
            except ValueError:
                path_to_store = str(page_abs)

            # Per-page SAVEPOINT: a bad row rolls back its own sub-txn and
            # the outer loop keeps going. Otherwise a single constraint
            # violation aborts the whole batch's sync silently.
            try:
                conn.execute("SAVEPOINT sync_page")
                if canonical_email:
                    upsert_user(conn, email=canonical_email, display_name=title)
                upsert_wiki_page(
                    conn,
                    slug=page_abs.stem,
                    path=path_to_store,
                    title=title,
                    page_type=page_type,
                    status=status,
                    canonical_user_email=canonical_email,
                )
                conn.execute("RELEASE SAVEPOINT sync_page")
                synced += 1
            except Exception as exc:  # noqa: BLE001
                conn.execute("ROLLBACK TO SAVEPOINT sync_page")
                logger.warning(
                    "catalog_sync_page_failed",
                    path=str(page_abs),
                    error=str(exc),
                )
        conn.commit()
    return synced


def _sync_and_stamp_landing_surfaces(wiki_dir: str, model_name: str) -> tuple[int, int]:
    """Stamp ``last_compiled`` + upsert ``wiki_pages`` rows for the North-Star
    landing surfaces that ``_regenerate_landing_surfaces`` rewrote.

    Covers:
    - top-level ``home.md`` / ``glossary.md`` / ``changes.md`` — all stamped
      as ``page_type='glossary'`` (generators emit mixed 'index'/'glossary'
      frontmatter; we normalize to one page_type the CHECK constraint
      accepts so the catalog has one consistent entry per file).
    - ``wiki/domains/*.md`` — stamped as ``page_type='domain'``.
    - ``wiki/decisions/*.md`` — stamped as ``page_type='decision'``.

    Runs AFTER ``_regenerate_landing_surfaces``; stamping earlier would
    be wiped because the generators write without ``last_compiled``.
    The batch-loop ``_sync_wiki_catalog`` misses these because
    ``_iter_touched_pages`` only walks topics/entities/systems and the
    top-level files aren't under any category folder.

    Uses the same per-page SAVEPOINT pattern as ``_sync_wiki_catalog``:
    a single constraint violation doesn't cascade, DB errors log but
    never re-raise.

    Returns ``(stamped_count, synced_count)``.
    """
    from src.db import connect as _connect

    wiki_path = Path(wiki_dir)
    if not wiki_path.exists():
        return 0, 0

    candidates: list[tuple[Path, str]] = []
    for name, page_type in _TOP_LEVEL_LANDING_PAGE_TYPES.items():
        path = wiki_path / name
        if path.exists():
            candidates.append((path, page_type))
    for folder in ("domains", "decisions"):
        folder_path = wiki_path / folder
        if not folder_path.exists():
            continue
        page_type = _CATEGORY_BY_FOLDER[folder]
        for md_file in sorted(folder_path.glob("*.md")):
            candidates.append((md_file, page_type))

    if not candidates:
        return 0, 0

    now_iso = datetime.now(IST).isoformat(timespec="seconds")
    stamped = 0
    synced = 0

    with _connect() as conn:
        for path, page_type in candidates:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("landing_read_failed", path=str(path), error=str(exc))
                continue
            fm = extract_frontmatter(content)

            # Stamp iff frontmatter looks sane. The catalog upsert below
            # still runs on mangled files (using stem fallback for title)
            # so resolve_page can still find them.
            if "title" in fm or "page_type" in fm:
                _stamp_frontmatter_compiled(fm, now_iso, model_name)
                body = extract_body(content)
                try:
                    path.write_text(render_with_frontmatter(fm, body), encoding="utf-8")
                    stamped += 1
                except OSError as exc:
                    logger.warning("landing_stamp_write_failed", path=str(path), error=str(exc))

            title_raw = fm.get("title")
            title = (
                title_raw.strip()
                if isinstance(title_raw, str) and title_raw.strip()
                else path.stem.replace("-", " ").title()
            )
            status_raw = fm.get("status")
            status = (
                status_raw
                if isinstance(status_raw, str) and status_raw in _VALID_WIKI_STATUS
                else "active"
            )
            path_abs = path.resolve()
            try:
                path_to_store = str(path_abs.relative_to(REPO_ROOT))
            except ValueError:
                path_to_store = str(path_abs)

            try:
                conn.execute("SAVEPOINT sync_landing")
                upsert_wiki_page(
                    conn,
                    slug=path.stem,
                    path=path_to_store,
                    title=title,
                    page_type=page_type,
                    status=status,
                    canonical_user_email=None,
                )
                conn.execute("RELEASE SAVEPOINT sync_landing")
                synced += 1
            except Exception as exc:  # noqa: BLE001
                conn.execute("ROLLBACK TO SAVEPOINT sync_landing")
                logger.warning(
                    "landing_sync_page_failed",
                    path=str(path),
                    page_type=page_type,
                    error=str(exc),
                )
        conn.commit()

    return stamped, synced


def _validate_touched_pages(pages: list[Path], wiki_dir: Path) -> dict[Path, list[ValidationError]]:
    """Run the per-page validator against each touched page. Returns a
    page→errors map for pages that have at least one error.

    Companion to ``_normalize_touched_pages``. Visibility lever, not a
    retry mechanism — the emails are already compiled, and the caller
    logs the error set + surfaces it in the batch notes so an operator
    can decide whether to re-compile via
    ``scripts/reconcile_compile_state.py``.

    ``wiki_dir`` is accepted for future parity with cross-page validators
    (duplicate-body, broken-wikilinks) if we decide to promote any of them
    to the per-batch hook; currently we only call ``validate_page`` which
    inspects a single file in isolation.
    """
    _ = wiki_dir  # currently unused; see docstring
    errors_by_page: dict[Path, list[ValidationError]] = {}
    for page in pages:
        try:
            errs = validate_page(page)
        except Exception as exc:  # noqa: BLE001 — validator must not crash compile
            logger.warning(
                "post-batch validator failed",
                path=str(page),
                error=str(exc),
            )
            continue
        if errs:
            errors_by_page[page] = errs
    return errors_by_page


def _write_touch_catalog(touched_pages: list[Path], batch_message_ids: list[str]) -> int:
    """Insert ``(message_id, page_id)`` rows into ``message_touched_pages``
    for every (batch message, content-type touched page) pair.

    Returns the count of rows actually inserted (ON-CONFLICT no-ops via
    ``insert_touch``'s RETURNING check don't count).

    Uses **batch message_ids**, NOT the subset that ended up marked
    compiled — the catalog must be authoritative on "which messages
    contributed to which page", which is independent of the state the
    claim loop ends up flipping. Under ``--batch-size 1`` there's only
    one message per batch; with larger batch sizes every message in the
    batch gets a row for each touched content page.

    Content filter uses ``CONTENT_PAGE_TYPES`` — entity / person stubs
    are excluded because name-dropping a message in a stub is not
    evidence the agent did compile work.

    Each insert runs inside its own SAVEPOINT so a single bad pair
    (e.g. the page catalog row vanished between sync and here) rolls
    back only that row. Without the savepoint, psycopg3 leaves the
    connection in an aborted-transaction state after the first
    constraint violation and every subsequent insert on the same
    connection silently no-ops — mirrors the ``_sync_wiki_catalog``
    pattern.

    Catalog staleness is strictly less bad than a crashed coordinator,
    so all failures log ``touch_insert_failed`` and continue.
    """
    if not touched_pages or not batch_message_ids:
        return 0

    # Lazy import so the conftest monkeypatch on ``src.db.connect`` (which
    # pins search_path to the per-test schema) is picked up here. A
    # top-level ``from src.db import connect`` would bind the unpatched
    # function and tests would write to the production schema.
    from src.db import connect as _connect

    inserted = 0
    with _connect() as conn:
        for page_path in touched_pages:
            slug = page_path.stem
            try:
                page_row = find_by_slug(slug)
            except Exception as exc:  # noqa: BLE001 — catalog read is best-effort
                logger.warning("touch_insert_lookup_failed", slug=slug, error=str(exc))
                continue
            if page_row is None:
                continue
            if page_row.get("page_type") not in CONTENT_PAGE_TYPES:
                continue
            page_id = page_row["page_id"]
            for mid in batch_message_ids:
                try:
                    conn.execute("SAVEPOINT touch_insert")
                    if insert_touch(conn, message_id=mid, page_id=page_id):
                        inserted += 1
                    conn.execute("RELEASE SAVEPOINT touch_insert")
                except Exception as exc:  # noqa: BLE001
                    conn.execute("ROLLBACK TO SAVEPOINT touch_insert")
                    logger.warning(
                        "touch_insert_failed",
                        message_id=mid,
                        page_id=page_id,
                        slug=slug,
                        error=str(exc),
                    )
        conn.commit()
    return inserted
