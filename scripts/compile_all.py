"""Compile all unprocessed raw emails into wiki pages.

Usage:
    uv run python scripts/compile_all.py
    uv run python scripts/compile_all.py --dry-run
    uv run python scripts/compile_all.py --batch-size 10
"""

from __future__ import annotations

import concurrent.futures
import os
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast
from uuid import UUID

import click
import httpx
import psycopg
import structlog

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.format_wiki import format_page  # noqa: E402
from scripts.validate_wiki import Error as ValidationError  # noqa: E402
from scripts.validate_wiki import validate_page  # noqa: E402
from src.budget import fetch_budget  # noqa: E402
from src.compile.cache_stats import BatchStatsCallback  # noqa: E402
from src.compile.categories import WIKI_CATEGORIES as _WIKI_CATEGORIES  # noqa: E402
from src.compile.compiler import _extract_merge_candidates  # noqa: E402
from src.compile.compiler import _generate_changes  # noqa: E402
from src.compile.compiler import _generate_home  # noqa: E402
from src.compile.compiler import _regenerate_decision_stubs  # noqa: E402
from src.compile.compiler import _regenerate_domain_hubs  # noqa: E402
from src.compile.compiler import list_uncompiled_emails  # noqa: E402
from src.compile.compiler import run_compilation  # noqa: E402
from src.compile.compiler import update_wiki_index  # noqa: E402
from src.compile.tool_call_log import ToolCallLogHandler  # noqa: E402
from src.config import settings  # noqa: E402
from src.db import compile_attempts as compile_attempts_repo  # noqa: E402
from src.db import connect  # noqa: E402
from src.db.compile_runs import finish_run  # noqa: E402
from src.db.compile_runs import start_run  # noqa: E402
from src.db.insights import list_for_run as list_insights_for_run  # noqa: E402
from src.db.insights import max_id_for_run as _insights_max_id  # noqa: E402
from src.db.messages import fail_message_compile  # noqa: E402
from src.db.messages import find_by_raw_path  # noqa: E402
from src.db.messages import finish_message_compile  # noqa: E402
from src.db.messages import mark_skipped  # noqa: E402
from src.db.messages import model_health_stats  # noqa: E402
from src.db.tool_call_log import fallback_to_jsonl as tool_log_fallback_to_jsonl  # noqa: E402
from src.db.tool_call_log import insert_many as tool_log_insert_many  # noqa: E402
from src.db.tool_call_log import summarize as tool_log_summarize  # noqa: E402
from src.db.touched_pages import insert_touch  # noqa: E402
from src.db.users import upsert_user  # noqa: E402
from src.db.wiki_pages import find_by_slug  # noqa: E402
from src.db.wiki_pages import upsert_wiki_page  # noqa: E402
from src.utils import extract_body  # noqa: E402
from src.utils import extract_frontmatter  # noqa: E402
from src.utils import render_with_frontmatter  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)


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

    now_iso = datetime.now(UTC).isoformat()
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


_CONTENT_PAGE_DIRS: tuple[str, ...] = (
    "topics",
    "systems",
    "policies",
    "decisions",
    "timelines",
    "conflicts",
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

# Page types that count as "the agent did real compile work". Entity /
# person stubs name-drop a message without extracting content, so citing
# an email only in a stub is NOT evidence of compile success. The
# catalog-truth compile-state check filters `message_touched_pages`
# joins against this set; anything else (entity, person, home, changes,
# domain) keeps the message in `pending` so the next claim cycle retries.
CONTENT_PAGE_TYPES = frozenset(
    {"topic", "system", "policy", "decision", "glossary", "timeline", "conflict"}
)


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

    now_iso = datetime.now(UTC).isoformat()
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


def _run_with_timeout[T](fn: Callable[[], T], timeout_s: float | None) -> T:
    """Run ``fn()`` in a worker thread, raising
    ``concurrent.futures.TimeoutError`` after ``timeout_s`` seconds.
    ``timeout_s`` of ``0`` or ``None`` runs ``fn`` inline.

    Caveat — Python threads are cooperative. On timeout the worker is
    orphaned (``shutdown(wait=False)``) and may linger until process
    exit if it's wedged in C code or a blocking socket. Acceptable
    trade-off: the outer batch loop progresses instead of freezing the
    whole run. For the same reason we avoid ``with ThreadPoolExecutor``
    — its ``__exit__`` would block on ``shutdown(wait=True)``.
    """
    if timeout_s is None or timeout_s == 0:
        return fn()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    finally:
        # ``wait=False`` only; no ``cancel_futures=True``. The orphaned
        # worker keeps running regardless — Python can't forcibly stop
        # a thread, so a hung HTTP socket continues until the process
        # exits. This just avoids blocking ``shutdown()`` on it.
        pool.shutdown(wait=False)


_LOG_HEADER = (
    "# Compilation Log\n\n"
    "| Timestamp | Batch | N Emails | Thread ID | Outcome | Notes |\n"
    "|---|---|---|---|---|---|\n"
)


def _preflight_mount_sanity(raw_dir: Path, wiki_dir: Path) -> int:
    """Verify raw_dir + wiki_dir look like a populated corpus before compiling.

    Returns the raw ``.md`` file count so the caller can log it. Aborts via
    ``click.ClickException`` on:

    - ``raw_dir`` missing on disk
    - ``raw_dir`` has zero ``.md`` files (the 2026-04-16 failure mode where a
      codex worktree pointed at an empty mount with only ``.gitkeep`` +
      attachments)
    - ``wiki_dir`` missing or lacking a ``topics/`` subdir (not a real wiki)

    Runs BEFORE any LLM or DB work so a bad mount costs nothing. The
    ``attachments/`` subtree is automatically excluded because ``glob("*.md")``
    only matches top-level ``raw_dir`` and attachments are always binaries.
    """
    if not raw_dir.exists():
        raise click.ClickException(f"raw_dir={raw_dir} does not exist; check cwd and --raw-dir")
    md_count = sum(1 for _ in raw_dir.glob("*.md"))
    if md_count == 0:
        raise click.ClickException(f"raw_dir={raw_dir} has 0 .md files; check cwd and --raw-dir")
    if not wiki_dir.exists():
        raise click.ClickException(f"wiki_dir={wiki_dir} does not exist; check cwd and --wiki-dir")
    if not (wiki_dir / "topics").exists():
        raise click.ClickException(
            f"wiki_dir={wiki_dir} has no topics/ subdir; is it a real wiki tree?"
        )
    return md_count


BatchOutcome = Literal["compiled", "failed", "partial"]


def _format_top_tools(pairs: list[tuple[str, int]]) -> str:
    """Render a `top_tools=name:count,…` suffix for the batch log, or ''."""
    if not pairs:
        return ""
    return "top_tools=" + ",".join(f"{n}:{c}" for n, c in pairs)


def _flush_tool_calls(run_id: UUID, tool_cb: ToolCallLogHandler) -> str:
    """Persist buffered tool-call records for this batch and return a log suffix.

    Writes to Postgres via `src.db.tool_call_log.insert_many`; on DB failure
    falls back to `docs/audits/tool_calls-<run_id>.jsonl` so telemetry isn't
    dropped silently. Returns a `top_tools=name:count,…` string for the
    batch-log `Notes` column, or empty string if no calls were captured.

    Uses `flush_all()` so in-flight tool calls (agent crashed mid-call) are
    captured with `status='abandoned'` instead of silently dropped — those
    are the most diagnostic records.
    """
    records: list[dict[str, Any]] = [dict(r) for r in tool_cb.flush_all()]
    if not records:
        return ""

    try:
        tool_log_insert_many(run_id, records)
    except psycopg.Error as exc:
        logger.warning(
            "tool-call DB insert failed; falling back to JSONL",
            run_id=run_id,
            error=str(exc),
        )
        try:
            tool_log_fallback_to_jsonl(run_id, records)
        except OSError as fs_exc:
            logger.warning("tool-call JSONL fallback failed", run_id=run_id, error=str(fs_exc))
        # Without DB, we can't summarize across this run's prior batches —
        # compute a local top-5 from the just-flushed records instead.
        counts: dict[str, int] = {}
        for r in records:
            counts[r["tool_name"]] = counts.get(r["tool_name"], 0) + 1
        return _format_top_tools(sorted(counts.items(), key=lambda kv: -kv[1])[:5])

    try:
        summary = tool_log_summarize(run_id)
    except psycopg.Error as exc:
        logger.warning("tool-call summarize failed", run_id=run_id, error=str(exc))
        return ""
    return _format_top_tools(summary.get("top_by_count") or [])


def _append_batch_log(
    batch_idx: int,
    batch: list[Any],
    outcome: BatchOutcome,
    wiki_dir: str,
    notes: str = "",
) -> None:
    """Append one structured row to wiki/log.md for an end-of-batch event.

    The coordinator owns the audit trail. Previously the LLM agent was
    instructed to call `append_to_log` at the end of each batch, but it
    forgot often enough to leave gaps in the log. Writing here guarantees
    every batch — success, failure, or partial — gets a row.

    Args:
        batch_idx: 1-based batch index in this run.
        batch: List of batch members (dicts with `path`/`thread_id` keys, or
            bare path strings).
        outcome: One of `compiled`, `failed`, `partial`.
        wiki_dir: Root wiki directory.
        notes: Optional human-readable detail (e.g., error message tail).
    """
    wiki_path = Path(wiki_dir)
    wiki_path.mkdir(parents=True, exist_ok=True)
    log_path = wiki_path / "log.md"

    timestamp = datetime.now(UTC).isoformat()
    n_emails = len(batch)
    thread_id = ""
    if batch:
        first = batch[0]
        if isinstance(first, dict):
            thread_id = str(first.get("thread_id", ""))

    # Pipes in notes would break markdown table parsing — escape them.
    safe_notes = notes.replace("|", r"\|").replace("\n", " ").strip()

    if not log_path.exists():
        log_path.write_text(_LOG_HEADER, encoding="utf-8")

    row = f"| {timestamp} | {batch_idx} | {n_emails} | {thread_id} | {outcome} | {safe_notes} |\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(row)


_MERGE_CANDIDATES_HEADER = (
    "---\n"
    'title: "Merge candidates"\n'
    "page_type: coordinator_notes\n"
    "status: active\n"
    "---\n\n"
    "# Merge candidates\n\n"
    "Append-only queue populated by the reviewer subagent when it flags two\n"
    "pages as duplicates. Each block is one batch. Apply with:\n\n"
    "    uv run python scripts/apply_merge_candidate.py \\\n"
    "        --pair slug-a,slug-b --keep slug-a --dry-run\n\n"
    "Then re-run with ``--commit`` when the diff looks right.\n\n"
)


def _ensure_merge_candidates_frontmatter(queue_path: Path) -> None:
    """Prepend the canonical header (with YAML frontmatter) to an existing
    ``merge_candidates.md`` that lacks frontmatter.

    Critique's ``find_touched_pages`` treats any recently-modified page
    with no parseable frontmatter as a "broken" touched page and pulls
    it into unrelated batch reviews — the Codex-flagged "poisoned input
    set" that caused #179. Prepending the header is idempotent: once
    the file has `page_type: coordinator_notes`, subsequent appends
    re-check and no-op.
    """
    try:
        existing = queue_path.read_text(encoding="utf-8")
    except OSError:
        return  # caller will re-create with the full header
    if existing.lstrip().startswith("---"):
        return  # already has frontmatter; leave alone
    # Pre-existing content without frontmatter: prepend the header so
    # the file becomes critique-safe without losing the backlog.
    queue_path.write_text(_MERGE_CANDIDATES_HEADER + existing, encoding="utf-8")


def _append_merge_candidates(
    pairs: list[dict[str, Any]],
    wiki_dir: str,
    *,
    trace_id: str,
) -> int:
    """Append reviewer-flagged merge candidates to ``wiki/merge_candidates.md``.

    The queue is append-only: humans (or a future Claude session) scan the
    file, pick pairs worth merging, and run
    ``scripts/apply_merge_candidate.py``. Returns the number of entries
    written (0 when ``pairs`` is empty). Filesystem failures are logged
    and swallowed — the queue is observational, never load-bearing.

    Args:
        pairs: parsed ``[{"slug_a", "slug_b", "note"}, ...]`` from
            :func:`src.compile.compiler._extract_merge_candidates`.
        wiki_dir: root wiki directory.
        trace_id: ``run_id:batch_index`` so the reader can grep back to
            the originating compile batch.
    """
    if not pairs:
        return 0

    wiki_path = Path(wiki_dir)
    try:
        wiki_path.mkdir(parents=True, exist_ok=True)
        queue_path = wiki_path / "merge_candidates.md"

        if not queue_path.exists():
            queue_path.write_text(_MERGE_CANDIDATES_HEADER, encoding="utf-8")
        else:
            # Legacy files without frontmatter are "broken" to the
            # critique touched-pages scan — backfill the header so the
            # file isn't pulled into unrelated batch reviews.
            _ensure_merge_candidates_frontmatter(queue_path)

        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        lines = [f"## {timestamp} — trace `{trace_id}`", ""]
        for pair in pairs:
            slug_a = str(pair.get("slug_a") or "?")
            slug_b = str(pair.get("slug_b") or "?")
            note = str(pair.get("note") or "")[:200].replace("\n", " ").strip()
            lines.append(f"- [{slug_a}] vs [{slug_b}]: {note}")
        lines.append("")
        with queue_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return len(pairs)
    except OSError as exc:
        logger.warning(
            "merge_candidates_append_failed",
            trace_id=trace_id,
            error=str(exc)[:200],
        )
        return 0


def _max_insight_id_safe(run_id: UUID) -> int:
    """Best-effort fetch of the current max insight id. Returns 0 on DB error
    so a DB blip doesn't crash the compile loop — we just lose the
    "since-last-batch" filter for this one batch's digest."""
    try:
        return _insights_max_id(run_id)
    except Exception as exc:  # noqa: BLE001 — insights are best-effort
        logger.warning("insights cursor fetch failed", run_id=run_id, error=str(exc))
        return 0


def _insights_suffix(run_id: UUID, since_id: int, limit: int = 3) -> str:
    """Return a short ``insights=N: <preview>`` fragment for the log notes.

    Pulls rows newer than `since_id` for this `run_id` so the digest reflects
    only the insights logged during the just-completed batch — not every
    insight accumulated in earlier batches of the same run. Fails open with
    an empty string if the DB is unreachable.
    """
    try:
        rows = list_insights_for_run(run_id, limit=limit, since_id=since_id)
    except Exception as exc:  # noqa: BLE001 — insights are best-effort
        logger.warning("insights fetch failed", run_id=run_id, error=str(exc))
        return ""
    if not rows:
        return ""
    preview = (rows[0].get("message") or "").replace("\n", " ")[:40]
    return f"insights={len(rows)}: {preview}"


def _batch_paths(batch: list) -> list[str]:
    """Extract raw_path strings from a batch list (dicts or bare strings)."""
    return [item["path"] if isinstance(item, dict) else str(item) for item in batch]


def _collect_content_cited_message_ids(message_ids: list[str]) -> set[str]:
    """Return the subset of `message_ids` with >=1 touch in a content-type page.

    Replaces the older frontmatter scan with a catalog query against
    `message_touched_pages` joined to `wiki_pages` filtered by
    ``CONTENT_PAGE_TYPES``. Entity/person stubs don't count — name-dropping
    a message in a stub is not evidence the agent did compile work.

    Empty input → empty set (no DB round-trip).
    """
    if not message_ids:
        return set()
    # Lazy import so the conftest monkeypatch on ``src.db.connect`` (which
    # pins search_path to the per-test schema) is picked up. A top-level
    # ``from src.db import connect`` would bind the unpatched function and
    # tests would read from the production schema.
    from src.db import connect as _connect

    with _connect() as conn:
        # ``src.db.connect`` uses ``dict_row`` so fetchall() yields dicts,
        # but mypy can't see that through the generic ``psycopg.Connection``
        # return type — cast the result explicitly.
        rows = cast(
            list[dict[str, Any]],
            conn.execute(
                """
                SELECT DISTINCT mtp.message_id
                  FROM message_touched_pages mtp
                  JOIN wiki_pages wp ON wp.page_id = mtp.page_id
                 WHERE mtp.message_id = ANY(%s)
                   AND wp.page_type IN (
                     'topic','system','policy','decision','glossary',
                     'timeline','conflict'
                   )
                """,
                (list(message_ids),),
            ).fetchall(),
        )
    return {r["message_id"] for r in rows}


# Insight categories that mean "don't compile this email, but don't
# leave it pending either". `trivial_skip` is currently accepted by the
# ``compile_insights`` CHECK (migration 202604160000); `already_captured`
# lands with U3 once the CHECK is widened. Keeping both names here is
# cheap and future-proofs the lookup: the DB never produces
# ``already_captured`` yet, so querying for it is a harmless no-op.
_SKIP_INSIGHT_CATEGORIES = frozenset({"trivial_skip", "already_captured"})


def _collect_attempts_compiled_message_ids(run_id: UUID, message_ids: list[str]) -> set[str]:
    """Secondary compile evidence (#174): return message_ids that already
    have ``compile_attempts.outcome='compiled'`` stamped in the current run.

    WHY — the primary signal is a content-page touch
    (``_collect_content_cited_message_ids``). That misses the legitimate
    edge case where the agent did real work (``write_draft_page``,
    ``edit_file``) but the only citation landed on a people/entity stub
    — the page_type filter excludes those by design, so the message
    wrongly stays ``pending`` and the next run's kimi batch re-processes
    it and often lands on ``skipped``. That was the ~5% waste #174
    identified.

    Scoping the lookup to ``run_id`` keeps the signal tight: older
    compile-attempts (from prior runs) don't reach across and flip
    messages the current run never processed.

    Fails open — a DB blip returns an empty set so the coordinator
    leaves the message pending rather than falsely promoting it.
    """
    if not message_ids:
        return set()
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            rows = cast(
                list[dict[str, Any]],
                conn.execute(
                    """
                    SELECT DISTINCT message_id
                      FROM compile_attempts
                     WHERE run_id = %s
                       AND outcome = 'compiled'
                       AND message_id = ANY(%s)
                    """,
                    (run_id, list(message_ids)),
                ).fetchall(),
            )
    except Exception as exc:  # noqa: BLE001 — attempts lookup is best-effort
        logger.warning(
            "attempts_compiled_fetch_failed",
            run_id=str(run_id),
            error=str(exc),
        )
        return set()
    return {r["message_id"] for r in rows}


def _insights_skip_paths(run_id: UUID) -> set[str]:
    """Return `email_path`s the agent flagged as skip-worthy this run.

    Joined against batch raw paths to flip matching messages to
    ``skipped`` instead of leaving them pending. Failures are logged and
    return an empty set — absence of a skip signal is the safe default
    (leave ``pending``; the claim loop will retry).
    """
    from src.db import connect as _connect

    try:
        with _connect() as conn:
            rows = cast(
                list[dict[str, Any]],
                conn.execute(
                    """
                    SELECT DISTINCT email_path
                      FROM compile_insights
                     WHERE run_id = %s
                       AND category = ANY(%s)
                       AND email_path IS NOT NULL
                    """,
                    (run_id, list(_SKIP_INSIGHT_CATEGORIES)),
                ).fetchall(),
            )
    except Exception as exc:  # noqa: BLE001 — insights are best-effort
        logger.warning("skip_insights_fetch_failed", run_id=run_id, error=str(exc))
        return set()
    return {r["email_path"] for r in rows if r.get("email_path")}


def _mark_batch_compiled(
    batch: list,
    wiki_dir: Path,
    compile_model: str | None = None,
    *,
    run_id: UUID | None = None,
) -> tuple[list[str], list[str], list[str], int]:
    """Flip batch emails to `compiled` / `skipped` / keep-pending using
    the catalog as source of truth.

    Returns ``(compiled_ids, skipped_ids, not_cited_paths, missing)``.
      - ``compiled_ids``: message_ids with >=1 touch in a content-type
        page (``CONTENT_PAGE_TYPES``) → flipped to ``compiled``.
      - ``skipped_ids``: message_ids the agent declared trivial /
        already-captured via ``log_insight`` this run → flipped to
        ``skipped`` (terminal; never re-claimed).
      - ``not_cited_paths``: raw_paths of emails without a content
        touch AND without a skip insight — agent likely didn't finish
        them; kept ``pending`` for the next claim cycle. Returned as
        paths (not counts) so the caller can selectively flip the
        terminal-decision-guard-exhausted subset to ``skipped``.
      - ``missing``: count of emails whose raw_path has no ``messages``
        row at all — indicates backfill drift; logged as a warning.

    `compile_model` records which A/B-pool model produced this batch so
    we can later join model → outcome. `run_id` scopes the skip-insight
    lookup to the current run — insights from earlier runs don't reach
    back and flip emails this run didn't touch. When ``run_id`` is None
    the skip path is a no-op (callers can still get the compiled/pending
    split).

    Catalog-truth successor to the frontmatter scan: entity/person stubs
    name-dropping the email no longer count as "compiled" because the
    ``page_type`` join filters them out. ``wiki_dir`` is retained for
    call-site compatibility; no longer read.
    """
    _ = wiki_dir  # retained for back-compat; content-truth now comes from the catalog
    # Resolve (path → messages row) once so we can batch the catalog
    # query by message_id instead of running one query per path.
    row_by_path: dict[str, Any] = {}
    missing = 0
    for path in _batch_paths(batch):
        row = find_by_raw_path(path)
        if row is None:
            logger.warning("no messages row for batch path", path=path)
            missing += 1
            continue
        row_by_path[path] = row

    message_ids = [str(row["message_id"]) for row in row_by_path.values()]
    content_cited = _collect_content_cited_message_ids(message_ids)
    skip_paths = _insights_skip_paths(run_id) if run_id is not None else set()
    # Secondary compile signal (#174): message_ids that already have an
    # attempts-row outcome='compiled' in this run. Catches the edge case
    # where the agent's only citation was to a people/entity stub —
    # content-page filter drops those but the attempts row says the
    # batch did real work.
    attempts_compiled: set[str] = (
        _collect_attempts_compiled_message_ids(run_id, message_ids) if run_id is not None else set()
    )

    compiled_ids: list[str] = []
    skipped_ids: list[str] = []
    not_cited_paths: list[str] = []
    for path, row in row_by_path.items():
        mid = str(row["message_id"])
        cited_in_content = mid in content_cited
        outcome_in_attempts = mid in attempts_compiled
        if cited_in_content or outcome_in_attempts:
            finish_message_compile(mid, compile_model=compile_model)
            compiled_ids.append(mid)
            decision = "compiled"
        elif path in skip_paths:
            # ``mark_skipped`` is a no-op on already-compiled/claimed
            # rows (state guard inside the repo function), so ordering
            # vs. the ``content_cited`` branch is safe either way.
            mark_skipped(mid, "insight:trivial_or_already_captured")
            skipped_ids.append(mid)
            decision = "skipped"
        else:
            logger.warning(
                "batch email not cited in content page; leaving pending",
                path=path,
                message_id=mid,
            )
            not_cited_paths.append(path)
            decision = "kept_pending"
        logger.info(
            "state_flip_decision",
            message_id=mid,
            outcome_in_attempts=outcome_in_attempts,
            cited_in_content_page=cited_in_content,
            decision=decision,
        )
    return compiled_ids, skipped_ids, not_cited_paths, missing


def _mark_batch_failed(batch: list, error: str, compile_model: str | None = None) -> int:
    """Mark every email in a crashed batch as failed. Returns marked count.

    Records the model that failed so per-model failure rates are
    recoverable from the catalog later.
    """
    trimmed = error[:500]
    marked = 0
    for path in _batch_paths(batch):
        row = find_by_raw_path(path)
        if row is None:
            continue
        fail_message_compile(row["message_id"], trimmed, compile_model=compile_model)
        marked += 1
    return marked


# Reason string stamped on ``last_error`` when the terminal-decision
# guard ran out of nudges. Matched verbatim by operator greps; change
# only in tandem with ``docs/audits/v12-50-compile-deep-audit-...``.
TERMINAL_GUARD_EXHAUSTED_REASON = "agent_exited_without_terminal_decision"


# Canonical sentinel substring for nudge detection. Must appear
# verbatim in ``TERMINAL_NUDGE_MESSAGE`` — enforced by
# ``test_terminal_guard_exhausted_detects_injected_nudge``. Kept as a
# short phrase (not the full message) so typo fixes or indentation
# tweaks to the nudge body don't silently break detection.
_TERMINAL_NUDGE_SENTINEL = "batch is about to exit without a terminal"


def _terminal_guard_exhausted(batch_result: dict[str, Any] | None) -> bool:
    """True when the terminal-decision guard ran but didn't secure a commit.

    The middleware (``TerminalDecisionGuardMiddleware``) injects the
    ``TERMINAL_NUDGE_MESSAGE`` into agent state each time the agent
    tries to exit without a content write or terminal ``log_insight``.
    Presence of that message in the final batch result means the
    guard fired at least once; combined with a downstream ``not_cited``
    classification for the same email, it's the load-bearing signal
    that the agent genuinely refused to decide.
    """
    if not isinstance(batch_result, dict):
        return False
    messages = batch_result.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and _TERMINAL_NUDGE_SENTINEL in content:
            return True
    return False


def _mark_terminal_guard_exhausted_paths(
    not_cited_paths: list[str],
) -> list[str]:
    """Flip ``not_cited`` paths to ``skipped`` with the guard-exhausted reason.

    Returns the list of ``message_id`` strings that were actually
    flipped (``mark_skipped`` rowcount 1). The rest — already
    compiled/claimed, or without a ``messages`` row — are silently
    dropped; those cases are handled by the caller's existing
    branches.

    Marking ``skipped`` (rather than ``failed``) is deliberate: the
    claim loop filters to ``pending`` + ``failed`` and re-queues
    anything matching. ``skipped`` is terminal, which matches the
    spec's "investigate but do NOT auto-requeue" requirement — the
    agent made a decision-shaped non-decision that won't resolve by
    retry. ``last_error`` carries the distinct reason so humans can
    grep for guard-exhausted messages separately from the
    trivial/already-captured skips.
    """
    flipped: list[str] = []
    for path in not_cited_paths:
        row = find_by_raw_path(path)
        if row is None:
            continue
        mid = str(row["message_id"])
        rowcount = mark_skipped(mid, TERMINAL_GUARD_EXHAUSTED_REASON)
        if rowcount:
            flipped.append(mid)
    return flipped


def _group_by_thread(
    emails: list[dict[str, str]], max_per_group: int
) -> list[list[dict[str, str]]]:
    """Group emails by thread_id, chronological within thread, threads ordered
    by earliest message date.

    Threads longer than `max_per_group` are split into sub-groups (still in
    order). Emails without a thread_id become singleton groups. The whole
    return list is sorted by each group's earliest date, so callers can
    process batches in chronological order across threads.
    """
    by_thread: dict[str, list[dict[str, str]]] = defaultdict(list)
    standalone: list[list[dict[str, str]]] = []

    for email in emails:
        tid = email.get("thread_id") or ""
        if tid:
            by_thread[tid].append(email)
        else:
            standalone.append([email])

    groups: list[list[dict[str, str]]] = []
    for members in by_thread.values():
        members.sort(key=lambda e: e.get("date", ""))
        # Split huge threads for safety; most will be one group
        for i in range(0, len(members), max_per_group):
            groups.append(members[i : i + max_per_group])
    groups.extend(standalone)

    # Threads processed in order of their earliest message — strict
    # chronological for supersession detection across topics.
    groups.sort(key=lambda g: min(e.get("date", "") for e in g) if g else "")
    return groups


# Auto-exclusion thresholds. If you tune these, update the matching
# comment block in src/config.py::llm_model_pool so future reviewers can
# reason about "why did my model get dropped?" without grepping.
#
# Two windows, both gated by _HEALTH_MIN_ATTEMPTS:
# - 24h window: the historical guard — catches persistent offenders with
#   a moderate threshold (>50% fail OR >=10 absolute failures).
# - 4h window: aggressive short-window quarantine — catches "hot" breakage
#   (LiteLLM proxy starts 400-ing a model mid-day) before the 24h window
#   dilutes the signal with earlier successes. Threshold is higher (>80%)
#   because 4h is noisy; we only pull the trigger when the model is
#   clearly broken *right now*.
_HEALTH_WINDOW_HOURS = 24
_HEALTH_SHORT_WINDOW_HOURS = 4
_HEALTH_MIN_ATTEMPTS = 5
_HEALTH_FAIL_RATE_THRESHOLD = 0.5
_HEALTH_SHORT_WINDOW_FAIL_RATE_THRESHOLD = 0.80
_HEALTH_ABS_FAILURE_CAP = 10


def _fetch_available_models() -> set[str] | None:
    """Return the LiteLLM proxy's advertised model ids, or None on failure."""
    if not settings.litellm_base_url or not settings.openai_api_key:
        return None

    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    url = settings.litellm_base_url.rstrip("/") + "/models"
    try:
        response = httpx.get(url, headers=headers, timeout=5)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("model_catalog_fetch_failed", url=url, error=str(exc))
        return None

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("model_catalog_invalid_json", url=url, error=str(exc))
        return None

    data = payload.get("data")
    if not isinstance(data, list):
        logger.warning("model_catalog_unexpected_shape", url=url, payload_type=type(data).__name__)
        return None

    return {
        model_id.strip()
        for item in data
        if isinstance(item, dict)
        and isinstance(model_id := item.get("id"), str)
        and model_id.strip()
    }


def _filter_pool_to_available_models(
    pool: list[str], available_models: set[str]
) -> tuple[list[str], list[str]]:
    """Drop pool entries the proxy does not currently advertise."""
    kept = [model for model in pool if model in available_models]
    dropped = [model for model in pool if model not in available_models]
    return kept, dropped


def _healthy_pool(pool: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Filter ``pool`` by recent ``compile_attempts`` outcomes.

    Two-window quarantine:
    - 24h window (persistent): drops models where
      ``(fail_rate > 0.5 AND total >= 5) OR failed_hard >= 10``.
      Catches models that have drifted broken over the day.
      ``failed_hard`` (real failures, excluding timeouts) gates the
      absolute cap so a burst of proxy stalls doesn't nuke an otherwise
      healthy model (#194: 24 grok timeouts had the cap excluding our
      best performer). Timeouts still feed ``fail_rate``, so a model
      that's consistently slow still gets caught by the rate guard.
    - 4h window (short): drops models where
      ``fail_rate > 0.80 AND total >= 5``. Catches "hot" breakage that
      hasn't accumulated enough 24h signal yet (e.g. LiteLLM proxy starts
      400-ing a model an hour ago). Higher threshold because 4h is noisy.

    Fails open — on DB errors, missing stats, or a filter that would
    empty the pool, returns the unfiltered pool (with a warning when all
    models would be excluded, so we never deadlock).

    Returns ``(kept_models, exclusion_records)``. Each exclusion record
    is a ``model_health_stats`` dict augmented with ``reason`` and
    ``window_hours`` so the caller can tell the operator which guard
    fired. If a model trips both windows, the 24h record wins (persistent
    offenders are the more damning signal).
    """
    long_stats: dict[str, dict[str, Any]] = {}
    short_stats: dict[str, dict[str, Any]] = {}
    try:
        long_stats = {
            s["compile_model"]: s for s in model_health_stats(since_hours=_HEALTH_WINDOW_HOURS)
        }
        short_stats = {
            s["compile_model"]: s
            for s in model_health_stats(since_hours=_HEALTH_SHORT_WINDOW_HOURS)
        }
    except psycopg.Error as exc:
        logger.warning("healthy_pool_db_error", error=str(exc))
        return pool, []

    kept: list[str] = []
    excluded: list[dict[str, Any]] = []
    for m in pool:
        long = long_stats.get(m)
        short = short_stats.get(m)
        long_drop = long is not None and (
            (
                long["fail_rate"] > _HEALTH_FAIL_RATE_THRESHOLD
                and long["total"] >= _HEALTH_MIN_ATTEMPTS
            )
            or long["failed_hard"] >= _HEALTH_ABS_FAILURE_CAP
        )
        short_drop = (
            short is not None
            and short["fail_rate"] > _HEALTH_SHORT_WINDOW_FAIL_RATE_THRESHOLD
            and short["total"] >= _HEALTH_MIN_ATTEMPTS
        )
        # Narrowing note: mypy can't propagate the `is not None` guard from
        # `*_drop` (compound-bool local) into the branch body, so we re-assert.
        if long_drop:
            assert long is not None
            excluded.append({**long, "reason": "quarantined (24h)", "window_hours": 24})
        elif short_drop:
            assert short is not None
            excluded.append({**short, "reason": "quarantined (4h)", "window_hours": 4})
        else:
            kept.append(m)

    if not kept:
        logger.warning("healthy_pool_would_empty_pool", excluded=excluded)
        return pool, excluded
    return kept, excluded


def _is_model_unavailable_error(exc: BaseException) -> bool:
    """True if ``exc`` indicates the LiteLLM proxy refuses this model for
    this team key OR the upstream provider is transiently dead. Covers:

    - 401 ``team not allowed to access model`` — original LiteLLM shape
    - 400 ``Invalid model name`` — original LiteLLM shape
    - 401 ``Authentication Error`` — bare auth fail (Bug K, Cycle 6 glm-5
      at 162s died with ``Error code: 401 - {'error': {'message':
      'Authentication Error'...`` and slipped past the original string
      match)
    - 403 ``Forbidden`` — bare forbidden (Bug K)
    - ``SilentModelFailError`` — HTTP 200 with empty payload (Bug J,
      docs/audits/cycle-5-case-bug-j-minimax-silent-fail.md)
    - 5xx HTML error pages from the proxy front-end. 2026-04-24 smoke:
      glm-5.1 hit 5/29 ``<title>502 Server Error</title>`` HTML responses
      (each at ~570 s) — these are upstream provider gateway failures
      raised as opaque text bodies that don't follow the
      ``Error code: NNN`` pattern. Treat as infra so the batch retries
      with another pool model instead of burning the round.

    All are infrastructure failures, not agent failures; the batch
    should retry with a different pool model instead of being marked
    failed. The 24h ``_healthy_pool`` guard can't help here because the
    failure must accumulate over time and every batch in between burns
    latency + telemetry rows.

    False-positive hedge: ``Error code: 401`` / ``Error code: 403`` is
    LiteLLM's structured error prefix — it doesn't appear in normal
    tool output or model text. The HTML matchers anchor on the
    ``<title>...</title>`` tag, which a wiki page body wouldn't carry
    in an exception string.
    """
    from src.compile.compiler import SilentModelFailError

    if isinstance(exc, SilentModelFailError):
        return True
    msg = str(exc)
    if "team not allowed to access model" in msg or "Invalid model name" in msg:
        return True
    if "Error code: 401" in msg or "Error code: 403" in msg:
        return True
    # Structured 5xx (LiteLLM occasionally surfaces gateway errors this way)
    if "Error code: 502" in msg or "Error code: 503" in msg or "Error code: 504" in msg:
        return True
    # HTML 5xx pages bubbled up as the exception body — Google Frontend /
    # nginx / Cloudflare all use the same ``<title>NNN ...</title>`` shape.
    # Note ``Gateway Time-out`` (with hyphen): nginx's stock 504 page uses
    # the hyphenated form, distinct from Cloudflare/Google's ``Timeout``.
    return (
        "<title>502 Server Error</title>" in msg
        or "<title>503 Service Unavailable</title>" in msg
        or "<title>504 Gateway Timeout</title>" in msg
        or "<title>504 Gateway Time-out</title>" in msg
        or "<title>502 Bad Gateway</title>" in msg
    )


def _record_attempts_start(
    batch: list[Any],
    *,
    run_id: UUID,
    compile_model: str,
) -> dict[str, int]:
    """Insert one in-flight ``compile_attempts`` row per batch message.

    Returns a map of ``message_id → attempt_id`` so the caller can stamp
    the outcome on batch completion. Uses a single connection and commits
    immediately — if the batch crashes mid-compile, the in-flight rows
    are still visible for the next run's ``_healthy_pool`` pass (they just
    lack an outcome; ``model_health_stats`` filters them via
    ``finished_at IS NOT NULL``).

    Fails open on DB errors: logs a warning and returns an empty map so
    the caller proceeds without attempt tracking rather than aborting the
    batch. Rows that can't map a path → message_id (backfill drift) are
    skipped with a warning — same behavior as ``_mark_batch_compiled``.
    """
    attempts: dict[str, int] = {}
    try:
        with connect() as conn:
            for path in _batch_paths(batch):
                row = find_by_raw_path(path)
                if row is None:
                    logger.warning(
                        "attempt_start: no messages row for batch path",
                        path=path,
                    )
                    continue
                message_id = str(row["message_id"])
                attempt_id = compile_attempts_repo.record_start(
                    conn,
                    message_id=message_id,
                    run_id=run_id,
                    compile_model=compile_model,
                )
                attempts[message_id] = attempt_id
            conn.commit()
    except psycopg.Error as exc:
        logger.warning("attempt_start_db_error", error=str(exc))
        return {}
    return attempts


def _record_attempts_outcome(
    attempts: dict[str, int],
    message_ids: list[str],
    *,
    outcome: str,
    error: str | None = None,
) -> None:
    """Stamp ``outcome`` + ``finished_at`` on the given attempt rows.

    ``message_ids`` scopes which attempts to update (so success path only
    stamps the actually-marked messages). Fails open on DB errors — the
    next run's guard just sees stale in-flight rows, which are filtered
    out of ``model_health_stats``.
    """
    if not attempts or not message_ids:
        return
    try:
        with connect() as conn:
            for mid in message_ids:
                attempt_id = attempts.get(mid)
                if attempt_id is None:
                    continue
                compile_attempts_repo.record_outcome(
                    conn,
                    attempt_id=attempt_id,
                    outcome=outcome,
                    error=error,
                )
            conn.commit()
    except psycopg.Error as exc:
        logger.warning("attempt_outcome_db_error", error=str(exc))


def _emit_langfuse_scores_for_run(run_id: UUID) -> None:
    """Push per-trace Langfuse Scores for the headline north-star metrics.

    Best-effort end-to-end: any failure (Langfuse 524, missing keys,
    SDK import error, DB blip) logs a warning and returns. Observability
    must never break the compile coordinator.
    """
    try:
        from src.observability.langfuse_scores import emit_scores_for_run

        emit_scores_for_run(run_id)
    except Exception as exc:  # noqa: BLE001 — observability is best-effort
        logger.warning(
            "langfuse_scores_emit_failed",
            run_id=str(run_id),
            error=str(exc)[:200],
        )


def _setup_model_pool(model_pool: str | None, resolved_model: str) -> list[str]:
    """Parse --model-pool, tracking source for diagnosis.

    CLI flag overrides settings.model_pool. Empty final list = no pool (use
    `resolved_model` for every batch); a list = sample one at random per batch.

    `pool_source` is load-bearing for diagnosis: the last-4h trace showed a
    known-broken model appearing via env/CLI override even though src/config.py
    had dropped it from the default. The run-start log line lets us see
    immediately whether the pool came from the default list, LLM_MODEL_POOL
    env var, or a CLI flag.
    """
    pool: list[str]
    pool_source: str
    if model_pool is not None:
        pool = [m.strip() for m in model_pool.split(",") if m.strip()]
        pool_source = "cli:--model-pool"
    else:
        pool = settings.model_pool if len(settings.model_pool) > 1 else []
        # pydantic-settings fills `llm_model_pool` from env when set, otherwise
        # from the class default. Presence of LLM_MODEL_POOL in os.environ is
        # the cheapest, most reliable distinguisher.
        pool_source = "env:LLM_MODEL_POOL" if "LLM_MODEL_POOL" in os.environ else "default"
    logger.info(
        "effective_model_pool", pool=pool, source=pool_source, resolved_model=resolved_model
    )
    return pool


def _prepare_model_pool(
    pool: list[str], available: set[str] | None, base_url: str | None, resolved_model: str
) -> list[str]:
    """Filter pool by provider-catalog availability, then drop quarantined models.

    Echoes diagnostics via click.echo so operators see what got dropped and why.
    Returns the (possibly shorter) pool ready for per-batch sampling.
    """
    if pool and available is not None:
        pool, unavailable = _filter_pool_to_available_models(pool, available)
        if unavailable:
            click.echo("Provider catalog dropped " + ", ".join(unavailable) + " (not in /models)")
    if not pool and available is not None and base_url and resolved_model not in available:
        click.echo(
            f"WARNING: selected model {resolved_model} is not advertised by "
            f"{base_url.rstrip('/')}/models"
        )

    # Drop chronically-failing models from the pool at run-start so we don't
    # spend a run rediscovering the same 401/400/recursion-loop failure. Fails
    # open on DB errors so a Postgres blip can't block compile. See
    # `_healthy_pool` for the rule.
    #
    # Note: this is short-window *quarantine*, not permanent removal. Permanent
    # removal from the default pool lives in `src/config.py`. A quarantined
    # model re-appears next run if the window has cleared — a config-removed
    # model does not.
    if pool:
        pool, excluded = _healthy_pool(pool)
        if excluded:
            click.echo(
                "Auto-exclusion dropped "
                + ", ".join(
                    f"{s['compile_model']} [{s.get('reason', 'quarantined')}] "
                    f"({s['failed']}/{s['total']} failed)"
                    for s in excluded
                )
            )
    if pool:
        click.echo(f"Model pool: {pool} (random pick per batch)")
    return pool


def _preview_dry_run(uncompiled: list[dict[str, str]], batch_size: int) -> None:
    """Log what the batch loop WOULD do, without invoking the agent."""
    total = len(uncompiled)
    preview_groups = _group_by_thread(uncompiled, max_per_group=batch_size)
    click.echo(
        f"Thread-grouped into {len(preview_groups)} batches "
        f"(avg {total / max(len(preview_groups), 1):.1f} emails/batch)"
    )
    sizes = [len(g) for g in preview_groups]
    if sizes:
        click.echo(
            f"Group size distribution: min={min(sizes)} median={sorted(sizes)[len(sizes) // 2]} "
            f"max={max(sizes)} singletons={sizes.count(1)}"
        )
    click.echo("\nFirst 10 batches:")
    for i, g in enumerate(preview_groups[:10], 1):
        tid = g[0].get("thread_id", "")[:12]
        earliest = g[0].get("date", "")[:10]
        subj = g[0].get("subject", "")[:50]
        click.echo(f"  batch {i}: thread={tid} earliest={earliest} n={len(g)} subj={subj!r}")
    if len(preview_groups) > 10:
        click.echo(f"  ... and {len(preview_groups) - 10} more batches")
    click.echo("\nDry run complete.")


@click.command()
@click.option(
    "--batch-size",
    default=20,
    help="Max emails to compile per agent invocation (default 20)",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help=(
        "Max number of THREADS to process this run (oldest-first by earliest "
        "message date). All pending emails in those threads are pulled. "
        "Default: all pending. Standalone (no-thread) emails each count as "
        "one thread."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List uncompiled emails without compiling",
)
@click.option(
    "--model",
    default=None,
    help="Override LLM model (default from .env LLM_MODEL)",
)
@click.option(
    "--model-pool",
    default=None,
    help=(
        "Comma-separated model IDs. If set, picks one at random per batch "
        "(sticky for that batch) so per-batch cache stats compare models. "
        "Overrides --model. Example: "
        "'z-ai/glm-4.6,minimax/minimax-m2.7'"
    ),
)
@click.option(
    "--recursion-limit",
    type=int,
    default=250,
    help=(
        "Max LangGraph super-steps per batch (each parent turn costs ~5 "
        "super-steps: model + ToolNode + 3 after_model middlewares). "
        "Default 250 (was 150) per smoke-99a267f4 audit — covers legitimate "
        "5-email batches with multi-page writes + reviewer subagents that "
        "share the parent budget. Lower it (e.g. 60) to fail fast on "
        "pathological threads."
    ),
)
@click.option(
    "--batch-timeout",
    type=click.IntRange(min=0),
    default=900,
    help=(
        "Per-batch wall-clock timeout in seconds (default 900 = 15 min, "
        "matching scripts/compile_overnight.sh). Pass 0 to disable. "
        "Guards interactive runs against a single hung batch "
        "(slow OTel export, stuck LLM provider, rare deadlock) freezing "
        "the whole compile loop."
    ),
)
@click.option(
    "--deploy",
    is_flag=True,
    default=False,
    help=(
        "After a successful compile, run the publish-gate + rsync wiki to GCS "
        "+ redeploy Cloud Run viewer (equiv of `make publish`). No-op if the "
        "run did not complete successfully (killed / failed)."
    ),
)
@click.option(
    "--deploy-force",
    is_flag=True,
    default=False,
    help=(
        "After a successful compile, deploy EVEN IF validate_wiki has errors "
        "(equiv of `make publish-force`). Use only when you know the errors "
        "are pre-existing and not introduced by this run."
    ),
)
def main(
    batch_size: int,
    limit: int | None,
    dry_run: bool,
    model: str | None,
    model_pool: str | None,
    recursion_limit: int,
    batch_timeout: int,
    deploy: bool,
    deploy_force: bool,
) -> None:
    """Compile uncompiled raw emails into wiki pages using Deep Agents.

    Pass ``--deploy`` (or ``--deploy-force``) to run ``make publish`` /
    ``make publish-force`` after the compile loop. The deploy step only
    fires when ``run_status == "completed"`` — a killed/failed run will
    skip it so operators don't publish a partially-compiled wiki.
    """
    import random
    import time

    # Capture the run start before any wiki work so we can stamp every page
    # whose mtime advances during the batch loop. See
    # `_stamp_recently_modified_pages` for the why.
    run_start = time.time()
    raw_dir = str(settings.raw_dir)
    wiki_dir = str(settings.wiki_dir)
    resolved_model = model or settings.llm_model

    # Preflight: fail fast if the raw/wiki mounts look wrong. Catches the
    # 2026-04-16 failure mode where a codex worktree had an empty /raw
    # mount (just .gitkeep + attachments), read_file silently failed,
    # and traces looked like synthesis failures. See F3 in the plan.
    raw_dir_path = Path(raw_dir).resolve()
    wiki_dir_path = Path(wiki_dir).resolve()
    raw_md_count = _preflight_mount_sanity(raw_dir_path, wiki_dir_path)
    # topics/ is guaranteed to exist post-preflight.
    topics_count = sum(1 for _ in (wiki_dir_path / "topics").glob("*.md"))
    click.echo(
        f"Preflight OK: cwd={Path.cwd()} raw_dir={raw_dir_path} "
        f"wiki_dir={wiki_dir_path} raw_md={raw_md_count} topics={topics_count}"
    )
    logger.info(
        "preflight_mount_ok",
        cwd=str(Path.cwd()),
        raw_dir=str(raw_dir_path),
        wiki_dir=str(wiki_dir_path),
        raw_dir_md_count=raw_md_count,
        wiki_dir_topics_count=topics_count,
    )

    pool = _setup_model_pool(model_pool, resolved_model)

    if not dry_run:
        try:
            compile_attempts_repo.ensure_schema()
        except psycopg.Error as exc:
            logger.warning("compile_attempts_schema_ensure_failed", error=str(exc))

    pool = _prepare_model_pool(
        pool, _fetch_available_models(), settings.litellm_base_url, resolved_model
    )

    # Use the tool directly for listing, not through the agent.
    # When --limit is set, `list_uncompiled_by_thread` pulls all pending
    # emails from the oldest N THREADS (not N emails) so batch_size can
    # actually matter; without --limit we fall back to the full pool.
    if limit:
        from src.db.messages import list_uncompiled_by_thread

        rows = list_uncompiled_by_thread(limit_threads=limit)
        uncompiled = [
            {
                "path": str(row["raw_path"]),
                "date": row["date"].isoformat() if row["date"] else "",
                "subject": str(row["subject"] or ""),
                "from": str(row["from_address"] or ""),
                "thread_id": str(row["thread_id"] or ""),
            }
            for row in rows
        ]
        total = len(uncompiled)
        click.echo(f"Processing oldest {limit} thread(s): {total} emails total.")
    else:
        all_uncompiled = list_uncompiled_emails.invoke({"raw_dir": raw_dir})
        uncompiled = all_uncompiled
        total = len(uncompiled)
        click.echo(f"Found {total} uncompiled emails total (no --limit; processing all).")
    if total == 0:
        click.echo("Nothing to compile.")
        # Still regenerate index in case wiki changed
        click.echo("Regenerating wiki index...")
        click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
        _regenerate_landing_surfaces(wiki_dir)
        landing_stamped, landing_synced = _sync_and_stamp_landing_surfaces(wiki_dir, resolved_model)
        if landing_stamped or landing_synced:
            click.echo(
                f"Landing surfaces: stamped {landing_stamped}, catalog synced {landing_synced}"
            )
        return

    # Auto-snapshot before compiling so we can roll back if the run corrupts
    # wiki pages. Snapshots are cheap (local copy) and have saved us pain.
    if not dry_run:
        label = f"pre-compile-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        snapshot_path = REPO_ROOT / ".snapshots" / label
        if (REPO_ROOT / wiki_dir).exists():
            import shutil

            snapshot_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(REPO_ROOT / wiki_dir, snapshot_path / "wiki")
            click.echo(f"Pre-compile snapshot: .snapshots/{label}/wiki")

    if dry_run:
        _preview_dry_run(uncompiled, batch_size)
        return

    click.echo(f"Compiling in batches of {batch_size}...")
    click.echo(f"Model: {resolved_model}")
    click.echo(f"Wiki dir: {wiki_dir}")
    budget_before = fetch_budget()
    if budget_before:
        click.echo(f"Budget (pre-run): {budget_before}")
    click.echo()

    # Group uncompiled emails into thread batches. One compile invocation per
    # thread so the agent sees full conversation context at once (3-5x cheaper
    # than recompiling the same page for each reply).
    groups = _group_by_thread(uncompiled, max_per_group=batch_size)
    click.echo(
        f"Thread-grouped into {len(groups)} batches "
        f"(avg {total / max(len(groups), 1):.1f} emails/batch)"
    )

    # Start a compile_runs row so we get per-invocation observability even if
    # the loop crashes. finish_run() below runs in `finally:` → always written.
    run_id = start_run(
        model=resolved_model,
        notes=f"limit={limit} batch_size={batch_size} recursion_limit={recursion_limit}",
    )
    click.echo(f"Run id: {run_id}")

    # Expose the run id to in-process tools (see
    # `src/compile/compiler.py::log_insight`) so every insight the agent
    # records can be joined back to this run.
    os.environ["COMPILE_RUN_ID"] = str(run_id)

    processed = 0
    failed = 0
    # Pessimistic default — overwritten to 'completed' on clean loop exit or
    # 'killed' on KeyboardInterrupt. Any other exception leaves it 'failed'.
    run_status = "failed"
    budget_after = None
    try:
        for batch_idx, batch in enumerate(groups, start=1):
            batch_paths = [b["path"] if isinstance(b, dict) else b for b in batch]
            batch_files = "\n".join(f"- {p}" for p in batch_paths)
            thread_id = batch[0].get("thread_id", "") if batch else ""
            earliest = batch[0].get("date", "")[:10] if batch else ""

            instruction = (
                f"Your job is to update or create the CONCEPT page that best "
                f"describes the subject of these {len(batch)} emails from one "
                f"thread (thread_id={thread_id}, earliest={earliest}). The emails "
                f"are EVIDENCE — what was announced, what was built, what was "
                f"tested, what was decided, what went wrong, who asked what. "
                f"The wiki page is a CONCEPT — a durable description of the "
                f"feature / initiative / decision / system itself, independent "
                f"of the thread that surfaced it. Ask yourself before writing: "
                f'"If the emails went away tomorrow, would this page still '
                f'stand as a useful description of the thing?" If the Summary '
                f"reads like a thread intro (`This thread discusses...`, `We "
                f"announced...`, `The team decided...`), rewrite it as a "
                f"concept definition (`<Thing> is <description>. It <does X> "
                f"for <who>`). When the concept page already exists, UPDATE it: "
                f"absorb new evidence into the current-truth Summary, append a "
                f"`Recent changes` bullet, and add `Open questions` if the "
                f"emails raise unresolved decisions. Never dump the email "
                f"thread verbatim. Never add `## Launch Announcement` / "
                f"`## Bug Report` / `## Testing Results` / `## Final Decision` "
                f"as H2 headings — those describe one email, not a concept. "
                f"Process the emails chronologically as a conversation; when "
                f"multiple replies build on the same concept, merge them into "
                f"a single coherent page rather than one page per message.\n\n"
                f"Files to compile:\n{batch_files}"
            )

            batch_model = random.choice(pool) if pool else resolved_model
            click.echo(
                f"\n=== Batch {batch_idx}/{len(groups)} "
                f"({len(batch)} emails, thread={thread_id[:12]}, "
                f"earliest={earliest}, model={batch_model}) ==="
            )
            cache_cb = BatchStatsCallback(model=batch_model)
            tool_cb = ToolCallLogHandler()
            # Snapshot the insight-id cursor BEFORE the batch so we can show
            # only the insights logged during this batch in the digest.
            insights_cursor = _max_insight_id_safe(run_id)
            # Snapshot wall-clock BEFORE run_compilation so the post-batch
            # formatter hook can find every page the agent touched during
            # the batch (mtime >= batch_start).
            batch_start = time.time()
            # Record in-flight compile_attempts rows BEFORE dispatch so a
            # mid-batch crash still leaves the attempt visible (with a
            # NULL outcome) for post-mortem and for the next run's
            # `_healthy_pool` pass to filter out.
            attempts = _record_attempts_start(batch, run_id=run_id, compile_model=batch_model)
            try:
                # ``concurrent.futures.TimeoutError`` is a subclass of
                # ``Exception``, so the outer ``except`` below handles it
                # via ``_mark_batch_failed`` + ``_append_batch_log``.
                #
                # Inner retry loop: when LiteLLM rejects the picked model
                # (401 ``team not allowed`` / 400 ``Invalid model name``),
                # drop the model from ``pool`` for this run and retry the
                # same batch with another. Avoids burning every batch on
                # an unprovisioned model while waiting for ``_healthy_pool``
                # to accumulate enough failures cross-run.
                while True:
                    # Cumulative deadline across model retries — the
                    # documented per-batch wall-clock cap is enforced
                    # against batch_start, not per-attempt. A pathological
                    # 401-after-N-seconds attempt won't get a fresh full
                    # budget for its retry.
                    remaining_budget = batch_timeout - (time.time() - batch_start)
                    if remaining_budget <= 0:
                        raise concurrent.futures.TimeoutError(
                            f"batch budget ({batch_timeout}s) exhausted across model "
                            f"retries (thread={thread_id[:12]})"
                        )
                    try:
                        batch_result = _run_with_timeout(
                            partial(
                                run_compilation,
                                instruction=instruction,
                                model_name=batch_model,
                                raw_dir=raw_dir,
                                wiki_dir=wiki_dir,
                                recursion_limit=recursion_limit,
                                cache_stats=cache_cb,
                                tool_log=tool_cb,
                                run_name=f"compile:{batch_model}:{thread_id[:12] or 'no-thread'}",
                                trace_metadata={
                                    "compile_run_id": str(run_id),
                                    "compile_batch_index": batch_idx,
                                    "compile_email_count": len(batch),
                                    "compile_model": batch_model,
                                    "compile_thread_id": thread_id,
                                },
                                trace_tags=[
                                    "email-kb",
                                    "compile",
                                    f"model:{batch_model}",
                                    f"batch:{batch_idx}",
                                ],
                            ),
                            timeout_s=remaining_budget,
                        )
                        break
                    except Exception as exc:
                        if not _is_model_unavailable_error(exc):
                            raise
                        eligible = [m for m in pool if m != batch_model]
                        if not eligible:
                            raise
                        click.echo(
                            f"  model {batch_model} unavailable (LiteLLM 401/403/400/5xx) — "
                            f"dropping for this run; retrying batch with another"
                        )
                        _record_attempts_outcome(
                            attempts,
                            list(attempts.keys()),
                            outcome="failed",
                            error=str(exc)[:500],
                        )
                        _flush_tool_calls(run_id, tool_cb)
                        # Propagate the prune to subsequent batches too:
                        # if this team key can't access the model now, no
                        # other batch in this run will either.
                        pool[:] = eligible
                        batch_model = random.choice(eligible)
                        cache_cb = BatchStatsCallback(model=batch_model)
                        tool_cb = ToolCallLogHandler()
                        attempts = _record_attempts_start(
                            batch, run_id=run_id, compile_model=batch_model
                        )
                        click.echo(f"  retry: model={batch_model}")
                # Post-batch normalization + validation. Catches agent-
                # introduced format drift (duplicate ## Related headings,
                # broken wikilinks, malformed frontmatter) right after the
                # batch commits, so the next batch sees a clean wiki. Both
                # helpers are best-effort: they log warnings but never roll
                # back a successful compile.
                #
                # Validator input is the full touched-page set (not just
                # formatter-normalized pages): pages the formatter skips as
                # malformed or leaves alone as already-clean must still be
                # validated, otherwise newly-introduced corruption slips
                # through silently.
                #
                # Order is load-bearing: the touch-catalog write + mark
                # step below depend on ``_sync_wiki_catalog`` having
                # upserted the ``wiki_pages`` rows they join against.
                # Running either earlier leaves ``message_touched_pages``
                # empty and every email stays ``pending``.
                touched_pages = _iter_touched_pages(batch_start, Path(wiki_dir))
                normalized = _normalize_touched_pages(touched_pages, Path(wiki_dir))
                _mdlint_autofix_touched_pages(touched_pages)
                catalog_synced = _sync_wiki_catalog(touched_pages, Path(wiki_dir))
                errors_by_page = _validate_touched_pages(touched_pages, Path(wiki_dir))
                if errors_by_page:
                    logger.warning(
                        "batch touched pages have validator errors",
                        batch_index=batch_idx,
                        errors=[
                            {"page": str(p), "reasons": [e.reason for e in errs]}
                            for p, errs in errors_by_page.items()
                        ],
                    )
                # Catalog-truth write: one ``(message_id, page_id)`` row
                # per (batch message, touched content-type page). Must
                # run AFTER ``_sync_wiki_catalog`` (so the join target
                # has rows) and BEFORE ``_mark_batch_compiled`` (which
                # reads the catalog). ``attempts`` is keyed by
                # ``message_id`` so we reuse its keys instead of a
                # second ``find_by_raw_path`` pass over the batch.
                #
                # Use ``_iter_touched_content_pages`` (wider scope than
                # ``_iter_touched_pages``) so policy/decision/timeline/
                # conflict edits also write touch rows. Without this, a
                # batch that only modifies a policy page writes zero
                # rows → message stays pending → re-claimed forever.
                touched_content_pages = _iter_touched_content_pages(batch_start, Path(wiki_dir))
                touches_inserted = _write_touch_catalog(touched_content_pages, list(attempts))
                compiled_ids, skipped_ids, not_cited_paths, missing = _mark_batch_compiled(
                    batch,
                    Path(wiki_dir),
                    compile_model=batch_model,
                    run_id=run_id,
                )
                processed += len(compiled_ids)
                _record_attempts_outcome(attempts, compiled_ids, outcome="compiled")
                if skipped_ids:
                    _record_attempts_outcome(
                        attempts,
                        skipped_ids,
                        outcome="skipped",
                        error="insight:trivial_or_already_captured",
                    )
                # Terminal-decision guard fallback: when the guard fired
                # (nudge present in the batch result) AND the email is
                # still uncited, the agent refused to decide even after
                # the bounded retry budget. Flip those to ``skipped``
                # with a distinguished reason — preserves investigation
                # (``last_error`` grep-able) and prevents the claim loop
                # from re-queueing a model that already said "no". See
                # ``docs/audits/v12-50-compile-deep-audit-2026-04-23.md``
                # §7 Tier 2 #5 + §3 batch-45 finding.
                guard_skipped_ids: list[str] = []
                if not_cited_paths and _terminal_guard_exhausted(batch_result):
                    guard_skipped_ids = _mark_terminal_guard_exhausted_paths(not_cited_paths)
                    if guard_skipped_ids:
                        _record_attempts_outcome(
                            attempts,
                            guard_skipped_ids,
                            outcome="skipped",
                            error=TERMINAL_GUARD_EXHAUSTED_REASON,
                        )
                not_cited = len(not_cited_paths) - len(guard_skipped_ids)
                # Stamp the un-accounted-for attempts as failures so they
                # don't sit in-flight forever. ``not_cited`` (agent didn't
                # cite the email) and ``missing`` (backfill drift) both
                # count as model-level failures for that batch —
                # ``model_health_stats`` will see them.
                accounted_for = set(compiled_ids) | set(skipped_ids) | set(guard_skipped_ids)
                unfinished_ids = [mid for mid in attempts if mid not in accounted_for]
                if unfinished_ids:
                    _record_attempts_outcome(
                        attempts,
                        unfinished_ids,
                        outcome="failed",
                        error="not cited in wiki",
                    )
                # Drain reviewer-flagged merge candidates into the queue
                # (``wiki/merge_candidates.md``) so a human can apply them
                # via ``scripts/apply_merge_candidate.py``. Best-effort:
                # parse errors are swallowed, and an empty reviewer output
                # writes zero lines. Queue growth shows up in log_notes
                # so operators see the signal without opening the file.
                merge_pairs = _extract_merge_candidates(batch_result)
                merge_written = _append_merge_candidates(
                    merge_pairs,
                    wiki_dir,
                    trace_id=f"{run_id}:{batch_idx}",
                )
                suffix_parts = []
                if merge_written:
                    suffix_parts.append(f"merge_candidates+={merge_written}")
                if not_cited:
                    suffix_parts.append(f"{not_cited} not-yet-cited (kept pending)")
                if skipped_ids:
                    suffix_parts.append(f"{len(skipped_ids)} skipped (trivial/already-captured)")
                if guard_skipped_ids:
                    suffix_parts.append(
                        f"{len(guard_skipped_ids)} skipped (terminal-guard exhausted)"
                    )
                if missing:
                    suffix_parts.append(f"{missing} missing from catalog")
                suffix_parts.append(
                    f"normalized {len(normalized)} pages, "
                    f"{len(errors_by_page)} with validator errors, "
                    f"catalog_synced {catalog_synced}, "
                    f"touches_inserted {touches_inserted}"
                )
                cache = cache_cb.snapshot()
                served = ",".join(cache["served_models"]) or cache["requested_model"]
                suffix_parts.append(
                    f"model={served} cache={cache['cached_tokens']}/"
                    f"{cache['prompt_tokens']} ({cache['cache_pct']}%) "
                    f"writes={cache['cache_creation_tokens']} "
                    f"turns={cache['turns']} tools={cache['tool_calls']} "
                    f"tools/turn={cache['tools_per_turn']} "
                    f"total_tok={cache['total_tokens']}"
                )
                tool_suffix = _flush_tool_calls(run_id, tool_cb)
                if tool_suffix:
                    suffix_parts.append(tool_suffix)
                suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
                click.echo(f"Batch complete. Progress: {processed}/{total}{suffix}")
                log_outcome: BatchOutcome = "partial" if (not_cited or missing) else "compiled"
                log_notes_parts = list(suffix_parts)
                insights_tail = _insights_suffix(run_id, since_id=insights_cursor)
                if insights_tail:
                    log_notes_parts.append(insights_tail)
                log_notes = "; ".join(log_notes_parts) if log_notes_parts else ""
                _append_batch_log(batch_idx, batch, log_outcome, wiki_dir, notes=log_notes)
            except KeyboardInterrupt:
                # Ctrl+C: flush any in-flight tool records before letting the
                # outer handler mark the run 'killed'. Without this, the
                # Postgres compile_tool_calls table loses every tool call
                # since the last batch boundary. Same for the in-flight
                # compile_attempts rows — best-effort stamp so we don't
                # carry a bunch of orphaned NULL-outcome rows forward.
                _flush_tool_calls(run_id, tool_cb)
                _record_attempts_outcome(
                    attempts,
                    list(attempts.keys()),
                    outcome="failed",
                    error="KeyboardInterrupt",
                )
                raise
            except Exception as e:  # noqa: BLE001
                # concurrent.futures.TimeoutError has an empty str(),
                # so fall back to a synthesized message that names the
                # timeout budget — otherwise wiki/log.md gets a blank
                # notes column and the run looks silently broken.
                if isinstance(e, concurrent.futures.TimeoutError):
                    err_msg = (
                        f"TimeoutError: batch exceeded {batch_timeout}s (thread={thread_id[:12]})"
                    )
                    attempt_outcome = "timeout"
                else:
                    # str(e) is empty for some zero-message exception types;
                    # repr(e) keeps the type name so the log row stays useful.
                    err_msg = str(e) or repr(e)
                    attempt_outcome = "failed"
                logger.error("batch compilation failed", batch_index=batch_idx, error=err_msg)
                # Flush tool-call records BEFORE _mark_batch_failed so a secondary
                # DB failure on the mark step doesn't swallow the primary telemetry.
                # In-flight records get `status='abandoned'` via flush_all.
                fail_notes = err_msg[:200]
                tool_suffix = _flush_tool_calls(run_id, tool_cb)
                if tool_suffix:
                    fail_notes = f"{fail_notes}; {tool_suffix}"
                failed_marked = _mark_batch_failed(batch, err_msg, compile_model=batch_model)
                failed += failed_marked
                _record_attempts_outcome(
                    attempts,
                    list(attempts.keys()),
                    outcome=attempt_outcome,
                    error=err_msg[:500],
                )
                click.echo(f"ERROR in batch ({failed_marked} marked failed): {err_msg}")
                click.echo("Continuing with next batch...")
                _append_batch_log(batch_idx, batch, "failed", wiki_dir, notes=fail_notes)
        run_status = "completed"
    except KeyboardInterrupt:
        run_status = "killed"
        click.echo("\nInterrupted — marking run as killed.")
        raise
    finally:
        # Cost delta in int cents. Skip if either budget fetch failed (e.g.
        # LiteLLM proxy down) — cost_cents stays NULL in that case.
        budget_after = fetch_budget()
        cost_cents: int | None = None
        if budget_before is not None and budget_after is not None:
            cost_cents = round((budget_after.spend - budget_before.spend) * 100)
        finish_run(
            run_id,
            status=run_status,
            emails_processed=processed,
            emails_failed=failed,
            cost_cents=cost_cents,
        )
        click.echo(
            f"Recorded compile run {run_id}: status={run_status} "
            f"processed={processed} failed={failed} cost_cents={cost_cents}"
        )

    # Stamp every wiki page touched during this run before regenerating the
    # index. The agent has a `stamp_page_compiled_at` tool but routinely
    # forgets pages; `update_wiki_index`'s fallback only stamps pages whose
    # `last_compiled` is missing entirely, so re-edits of older pages slip
    # through with stale timestamps. Coordinator owns this now.
    click.echo("\nStamping recently modified wiki pages...")
    stamped, skipped = _stamp_recently_modified_pages(wiki_dir, run_start, resolved_model)
    click.echo(
        f"Stamped {stamped} pages with last_compiled"
        + (f" ({skipped} skipped — corrupt frontmatter)" if skipped else "")
    )

    # Regenerate index once after all batches complete — authoritative, not stale
    click.echo("\nRegenerating wiki index (post-compile)...")
    click.echo(update_wiki_index.invoke({"wiki_dir": wiki_dir}))
    _regenerate_landing_surfaces(wiki_dir)

    # Stamp + catalog the landing surfaces we just regenerated. Must run
    # AFTER `_regenerate_landing_surfaces` because the generators rewrite
    # those files without `last_compiled` — stamping earlier would be wiped.
    landing_stamped, landing_synced = _sync_and_stamp_landing_surfaces(wiki_dir, resolved_model)
    if landing_stamped or landing_synced:
        click.echo(
            f"Landing surfaces: stamped {landing_stamped}, "
            f"catalog synced {landing_synced} (home/glossary/changes + domains + decisions)"
        )

    # Push per-trace Langfuse Scores for the headline north-star metrics
    # so they show up in dashboards without re-running trace_scorecard.py.
    # Best-effort: any Langfuse failure logs a warning and the compile
    # finishes normally — observability never blocks the writer.
    _emit_langfuse_scores_for_run(run_id)

    # Run validator and warn (but don't fail) if integrity is broken. Pre-compile
    # snapshot is already captured above for rollback.
    click.echo("\nValidating wiki integrity...")
    import subprocess

    result = subprocess.run(
        ["uv", "run", "python", "scripts/validate_wiki.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    click.echo(result.stdout)
    if result.returncode != 0:
        click.echo(result.stderr)
        click.echo(
            "\n⚠ Validation failed. Pre-compile snapshot is saved. "
            "Restore with: uv run python scripts/snapshot_wiki.py restore <label>"
        )

    # Reuse the post-run budget snapshot captured in `finally:` above.
    if budget_after:
        click.echo(f"Budget (post-run): {budget_after}")
        if budget_before:
            delta = budget_after.spend - budget_before.spend
            click.echo(f"This run cost: ${delta:.4f}")

    click.echo(f"\nDone. Processed {processed}/{total} emails.")

    # Optional: publish the freshly-compiled wiki to Cloud Run. Only fires
    # on a clean 'completed' run. KeyboardInterrupt re-raises through the
    # `finally:` above so deploy never runs on Ctrl+C. Uncaught exceptions
    # that escape the loop likewise bubble past this block. The elif below
    # is defensive — reached only if a future refactor surfaces a non-
    # 'completed' run_status into this code path.
    if (deploy or deploy_force) and run_status == "completed":
        import subprocess

        target = "publish-force" if deploy_force else "publish"
        click.echo(f"\n=== Deploy: running `make {target}` ===")
        try:
            result = subprocess.run(
                ["make", target],
                cwd=REPO_ROOT,
                check=False,  # we want to handle failure, not raise
            )
            if result.returncode == 0:
                click.echo("Deploy succeeded.")
            else:
                click.echo(
                    f"Deploy failed (make {target} exited {result.returncode}). "
                    "Wiki is NOT updated on Cloud Run."
                )
                sys.exit(result.returncode)
        except FileNotFoundError:
            # Operator explicitly asked to deploy but the toolchain is missing.
            # Exit non-zero so CI/operators notice instead of silently leaving
            # Cloud Run stale.
            logger.error("deploy_toolchain_missing", target=target)
            click.echo(
                f"Deploy FAILED: `make` not found on PATH (cannot run `make {target}`). "
                "Install `make` or run the deploy step manually. "
                "Wiki is NOT updated on Cloud Run.",
                err=True,
            )
            sys.exit(1)
    elif (deploy or deploy_force) and run_status != "completed":
        click.echo(
            f"\nDeploy skipped — run_status={run_status!r} (deploy runs only "
            "after a clean 'completed' run)."
        )


if __name__ == "__main__":
    main()
