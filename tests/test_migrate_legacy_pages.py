"""Tests for scripts/migrate_legacy_pages.py — nightly legacy-debt migration.

Uses the shared test schema (see conftest.py) so DB state is isolated;
wiki filesystem state uses tmp_path per test.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from click.testing import CliRunner
from src.db import wiki_pages as wiki_repo


def _load_module() -> Any:
    """Import scripts/migrate_legacy_pages.py as a module.

    Matches the pattern test_cleanup_auto_stubs uses — scripts aren't a
    Python package, so we spec_from_file_location.
    """
    path = Path(__file__).parent.parent / "scripts" / "migrate_legacy_pages.py"
    spec = importlib.util.spec_from_file_location("_migrate_legacy_pages_for_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_migrate_legacy_pages_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod() -> Any:
    return _load_module()


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """Fake wiki with empty category folders + pinned REPO_ROOT override.

    The script resolves paths relative to REPO_ROOT (the real repo) so we
    monkeypatch that to tmp_path for test-scope isolation.
    """
    for folder in ("topics", "entities", "systems", "policies", "people", "domains"):
        (tmp_path / folder).mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _redirect_repo_root(
    mod: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Pin REPO_ROOT to tmp_path so paths resolve against the fake wiki."""
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    yield


# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------

_LEGACY_ENTITY = """---
title: "Amit Agarwal"
page_type: entity
status: current
email: amit@indiamart.com
sources: []
related: []
---

Email: amit@indiamart.com
"""

_LEGACY_TOPIC_NO_DOMAIN = """---
title: "Buyer Lead Pipeline"
page_type: topic
status: current
sources:
  - "raw/2026-04-01_foo.md"
related: []
---

# Buyer Lead Pipeline

Real content about the pipeline.
"""

_CLEAN_TOPIC = """---
title: "Seller KYC"
page_type: topic
status: active
domain: trust-safety
sources:
  - "raw/2026-04-01_bar.md"
related: []
---

# Seller KYC

Clean page — has domain, already at active.
"""


def _write_page(wiki_root: Path, folder: str, slug: str, body: str) -> Path:
    """Write a page + return its absolute path."""
    p = wiki_root / "wiki" / folder / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _seed_wiki_row(
    conn: psycopg.Connection,
    *,
    slug: str,
    page_type: str,
    status: str,
    rel_path: str,
) -> int:
    return wiki_repo.upsert_wiki_page(
        conn,
        slug=slug,
        path=rel_path,
        title=slug.replace("-", " ").title(),
        page_type=page_type,
        status=status,
    )


# ---------------------------------------------------------------------------
# scan_legacy_debt — classification
# ---------------------------------------------------------------------------


def test_scan_detects_legacy_entity(mod: Any, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Entity page with status=current triggers both flags."""
    rel = "wiki/entities/amit-indiamart-com.md"
    _write_page(wiki_root, "entities", "amit-indiamart-com", _LEGACY_ENTITY)
    _seed_wiki_row(
        db_conn,
        slug="amit-indiamart-com",
        page_type="entity",
        status="current",
        rel_path=rel,
    )
    db_conn.commit()

    legacy = mod.scan_legacy_debt()
    assert len(legacy) == 1
    page = legacy[0]
    assert page.slug == "amit-indiamart-com"
    assert "status_current" in page.reasons
    assert "page_type_entity" in page.reasons


def test_scan_detects_topic_without_domain(
    mod: Any, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """Topic page at status=active but missing `domain:` → flagged."""
    rel = "wiki/topics/buyer-lead-pipeline.md"
    _write_page(
        wiki_root,
        "topics",
        "buyer-lead-pipeline",
        _LEGACY_TOPIC_NO_DOMAIN.replace("status: current", "status: active"),
    )
    _seed_wiki_row(
        db_conn,
        slug="buyer-lead-pipeline",
        page_type="topic",
        status="active",
        rel_path=rel,
    )
    db_conn.commit()

    legacy = mod.scan_legacy_debt()
    assert len(legacy) == 1
    assert legacy[0].reasons == ["missing_domain"]


def test_scan_ignores_clean_page(mod: Any, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Clean page (domain present + status=active) → not flagged."""
    rel = "wiki/topics/seller-kyc.md"
    _write_page(wiki_root, "topics", "seller-kyc", _CLEAN_TOPIC)
    _seed_wiki_row(
        db_conn,
        slug="seller-kyc",
        page_type="topic",
        status="active",
        rel_path=rel,
    )
    db_conn.commit()

    assert mod.scan_legacy_debt() == []


def test_scan_mixed_wiki(mod: Any, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """One clean + one legacy + one entity → detects exactly two."""
    _write_page(wiki_root, "topics", "seller-kyc", _CLEAN_TOPIC)
    _seed_wiki_row(
        db_conn,
        slug="seller-kyc",
        page_type="topic",
        status="active",
        rel_path="wiki/topics/seller-kyc.md",
    )
    _write_page(
        wiki_root,
        "topics",
        "buyer-lead-pipeline",
        _LEGACY_TOPIC_NO_DOMAIN,
    )
    _seed_wiki_row(
        db_conn,
        slug="buyer-lead-pipeline",
        page_type="topic",
        status="current",
        rel_path="wiki/topics/buyer-lead-pipeline.md",
    )
    _write_page(wiki_root, "entities", "amit-indiamart-com", _LEGACY_ENTITY)
    _seed_wiki_row(
        db_conn,
        slug="amit-indiamart-com",
        page_type="entity",
        status="current",
        rel_path="wiki/entities/amit-indiamart-com.md",
    )
    db_conn.commit()

    legacy = mod.scan_legacy_debt()
    slugs = {p.slug for p in legacy}
    assert slugs == {"buyer-lead-pipeline", "amit-indiamart-com"}


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def _run_cli(mod: Any, wiki_root: Path, *args: str) -> Any:
    """Invoke the click command with --wiki-dir pointing at tmp_path."""
    return CliRunner().invoke(
        mod.main,
        ["--wiki-dir", str(wiki_root / "wiki"), *args],
        catch_exceptions=False,
    )


def test_dry_run_writes_nothing(mod: Any, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Default --dry-run → prints summary; no report, no log, no DB change."""
    _write_page(wiki_root, "entities", "amit-indiamart-com", _LEGACY_ENTITY)
    _seed_wiki_row(
        db_conn,
        slug="amit-indiamart-com",
        page_type="entity",
        status="current",
        rel_path="wiki/entities/amit-indiamart-com.md",
    )
    db_conn.commit()

    result = _run_cli(mod, wiki_root, "--dry-run", "--limit", "5")
    assert result.exit_code == 0, result.output
    assert "Legacy debt found: 1" in result.output

    # No audit file, no log.md line, DB status unchanged.
    assert not (wiki_root / "docs" / "audits").exists()
    assert not (wiki_root / "wiki" / "log.md").exists()
    row = wiki_repo.find_by_slug("amit-indiamart-com")
    assert row is not None
    assert row["status"] == "current"
    assert row["page_type"] == "entity"


def test_report_only_writes_audit_and_single_log_row(
    mod: Any, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """--report-only → writes the audit file + one log row, no mutation."""
    _write_page(wiki_root, "entities", "amit-indiamart-com", _LEGACY_ENTITY)
    _seed_wiki_row(
        db_conn,
        slug="amit-indiamart-com",
        page_type="entity",
        status="current",
        rel_path="wiki/entities/amit-indiamart-com.md",
    )
    _write_page(wiki_root, "topics", "buyer-lead-pipeline", _LEGACY_TOPIC_NO_DOMAIN)
    _seed_wiki_row(
        db_conn,
        slug="buyer-lead-pipeline",
        page_type="topic",
        status="current",
        rel_path="wiki/topics/buyer-lead-pipeline.md",
    )
    db_conn.commit()

    result = _run_cli(mod, wiki_root, "--report-only", "--limit", "5")
    assert result.exit_code == 0, result.output

    audits = list((wiki_root / "docs" / "audits").glob("legacy-debt-*.md"))
    assert len(audits) == 1, f"expected one audit file, got {audits}"
    report = audits[0].read_text(encoding="utf-8")
    assert "Legacy-debt nightly migration report" in report
    assert "Legacy debt found: 2" in report
    assert "amit-indiamart-com" in report

    # log.md appended — count total non-header body lines (exactly 1).
    log = (wiki_root / "wiki" / "log.md").read_text(encoding="utf-8")
    body_lines = [
        ln
        for ln in log.splitlines()
        if ln.startswith("| ") and "Timestamp" not in ln and "---" not in ln
    ]
    assert len(body_lines) == 1
    # Row uses the shared 6-column compile_all schema, with outcome=report-only.
    assert "| migrate |" in body_lines[0]
    assert "| report-only |" in body_lines[0]
    assert "legacy-debt migration (report-only)" in body_lines[0]

    # DB unchanged — report-only never mutates.
    row = wiki_repo.find_by_slug("amit-indiamart-com")
    assert row is not None
    assert row["status"] == "current"
    assert row["page_type"] == "entity"


def test_commit_limit_applies_exactly_two_fixes(
    mod: Any, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """--commit --limit 2 → three legacy pages seeded, only two migrated."""
    # Three legacy topics missing `domain:`. Limit=2 should migrate the
    # first two (ordering in scan_legacy_debt is page_type, slug).
    for slug in ("apple-topic", "banana-topic", "cherry-topic"):
        _write_page(wiki_root, "topics", slug, _LEGACY_TOPIC_NO_DOMAIN)
        _seed_wiki_row(
            db_conn,
            slug=slug,
            page_type="topic",
            status="active",
            rel_path=f"wiki/topics/{slug}.md",
        )
    db_conn.commit()

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "2")
    assert result.exit_code == 0, result.output

    # Exactly two pages got `domain: unassigned` appended.
    migrated = 0
    for slug in ("apple-topic", "banana-topic", "cherry-topic"):
        body = (wiki_root / "wiki" / "topics" / f"{slug}.md").read_text(encoding="utf-8")
        if "domain: unassigned" in body:
            migrated += 1
    assert migrated == 2

    # Audit file + log row still written even under --commit.
    audits = list((wiki_root / "docs" / "audits").glob("legacy-debt-*.md"))
    assert len(audits) == 1


def test_commit_current_to_active_flip(
    mod: Any, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """--commit flips status=current to active in DB + frontmatter."""
    _write_page(wiki_root, "topics", "buyer-lead-pipeline", _LEGACY_TOPIC_NO_DOMAIN)
    _seed_wiki_row(
        db_conn,
        slug="buyer-lead-pipeline",
        page_type="topic",
        status="current",
        rel_path="wiki/topics/buyer-lead-pipeline.md",
    )
    db_conn.commit()

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "5")
    assert result.exit_code == 0, result.output

    row = wiki_repo.find_by_slug("buyer-lead-pipeline")
    assert row is not None
    assert row["status"] == "active"
    body = (wiki_root / "wiki" / "topics" / "buyer-lead-pipeline.md").read_text(encoding="utf-8")
    assert "status: active" in body
    assert "status: current" not in body


def test_commit_entity_rename(mod: Any, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """--commit moves entities/<slug>.md → people/<slug>.md + DB page_type flip."""
    _write_page(wiki_root, "entities", "amit-indiamart-com", _LEGACY_ENTITY)
    # An incoming wikilink using the `entities/` prefix form — should
    # get rewritten to `people/`.
    _write_page(
        wiki_root,
        "topics",
        "mentions",
        (
            "---\n"
            "title: Mentions\n"
            "page_type: topic\n"
            "status: active\n"
            "domain: unassigned\n"
            "sources: []\n"
            "related: []\n"
            "---\n\n"
            "See [[entities/amit-indiamart-com]] for details.\n"
        ),
    )
    _seed_wiki_row(
        db_conn,
        slug="amit-indiamart-com",
        page_type="entity",
        status="current",
        rel_path="wiki/entities/amit-indiamart-com.md",
    )
    _seed_wiki_row(
        db_conn,
        slug="mentions",
        page_type="topic",
        status="active",
        rel_path="wiki/topics/mentions.md",
    )
    db_conn.commit()

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "5")
    assert result.exit_code == 0, result.output

    # File moved.
    assert not (wiki_root / "wiki" / "entities" / "amit-indiamart-com.md").exists()
    moved = wiki_root / "wiki" / "people" / "amit-indiamart-com.md"
    assert moved.exists()

    # DB updated to person + new path.
    row = wiki_repo.find_by_slug("amit-indiamart-com")
    assert row is not None
    assert row["page_type"] == "person"
    assert row["path"].endswith("wiki/people/amit-indiamart-com.md")

    # Moved file's frontmatter flipped to `page_type: person` —
    # validate_wiki.py's directory→type check expects the on-disk
    # frontmatter to agree with the directory the file lives in.
    body = moved.read_text(encoding="utf-8")
    assert "page_type: person" in body
    assert "page_type: entity" not in body

    # Incoming wikilink rewritten.
    mentions = (wiki_root / "wiki" / "topics" / "mentions.md").read_text(encoding="utf-8")
    assert "[[people/amit-indiamart-com]]" in mentions
    assert "[[entities/amit-indiamart-com]]" not in mentions


def test_commit_entity_rename_rolls_back_file_on_db_failure(
    mod: Any,
    wiki_root: Path,
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB UPDATE failure during entity→person rename must revert the file move.

    Without rollback the file lives in `wiki/people/<slug>.md` while the
    DB row still points at `wiki/entities/<slug>.md` — split-brain after
    the SAVEPOINT rollback in `apply_fixes` drops the DB change.
    """
    _write_page(wiki_root, "entities", "amit-indiamart-com", _LEGACY_ENTITY)
    _seed_wiki_row(
        db_conn,
        slug="amit-indiamart-com",
        page_type="entity",
        status="current",
        rel_path="wiki/entities/amit-indiamart-com.md",
    )
    db_conn.commit()

    # Force the DB UPDATE inside _rename_entity_to_people to blow up.
    # We do that by swapping out psycopg.Connection.execute for a
    # wrapper that raises on the wiki_pages UPDATE but passes SAVEPOINT
    # management through unchanged.
    real_execute = psycopg.Connection.execute

    def flaky_execute(self: psycopg.Connection, query: Any, *args: Any, **kwargs: Any) -> Any:
        q = str(query)
        if "UPDATE wiki_pages" in q and "page_type" in q:
            # Run a broken statement to put the real connection into
            # InFailedSqlTransaction — matches the real-world failure.
            return real_execute(self, "SELECT * FROM table_that_does_not_exist")
        return real_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", flaky_execute)

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "5")
    assert result.exit_code == 0, result.output
    assert "errors: 1" in result.output

    # File rolled back — still at entities/, NOT orphaned at people/.
    assert (wiki_root / "wiki" / "entities" / "amit-indiamart-com.md").exists()
    assert not (wiki_root / "wiki" / "people" / "amit-indiamart-com.md").exists()

    # DB row unchanged by the SAVEPOINT rollback.
    row = wiki_repo.find_by_slug("amit-indiamart-com")
    assert row is not None
    assert row["page_type"] == "entity"
    assert row["path"].endswith("wiki/entities/amit-indiamart-com.md")


def test_commit_on_clean_state_is_noop(
    mod: Any, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """Clean wiki + clean DB → exit 0, no audit file, no log row."""
    _write_page(wiki_root, "topics", "seller-kyc", _CLEAN_TOPIC)
    _seed_wiki_row(
        db_conn,
        slug="seller-kyc",
        page_type="topic",
        status="active",
        rel_path="wiki/topics/seller-kyc.md",
    )
    db_conn.commit()

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "5")
    assert result.exit_code == 0
    assert "Nothing to migrate" in result.output
    assert not (wiki_root / "docs" / "audits").exists()
    assert not (wiki_root / "wiki" / "log.md").exists()


def test_mutually_exclusive_modes(mod: Any, wiki_root: Path) -> None:
    """--dry-run + --commit at once → exit 2, not silently ignored."""
    result = _run_cli(mod, wiki_root, "--dry-run", "--commit")
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------


def test_commit_isolates_per_page_errors(
    mod: Any,
    wiki_root: Path,
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failing page raises OSError; the sibling page still migrates."""
    _write_page(wiki_root, "topics", "good-topic", _LEGACY_TOPIC_NO_DOMAIN)
    _write_page(wiki_root, "topics", "bad-topic", _LEGACY_TOPIC_NO_DOMAIN)
    _seed_wiki_row(
        db_conn,
        slug="good-topic",
        page_type="topic",
        status="active",
        rel_path="wiki/topics/good-topic.md",
    )
    _seed_wiki_row(
        db_conn,
        slug="bad-topic",
        page_type="topic",
        status="active",
        rel_path="wiki/topics/bad-topic.md",
    )
    db_conn.commit()

    original = mod._add_domain_placeholder

    def flaky(page_path: Path) -> bool:
        if page_path.name == "bad-topic.md":
            raise OSError("simulated IO failure")
        return bool(original(page_path))

    monkeypatch.setattr(mod, "_add_domain_placeholder", flaky)

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "5")
    assert result.exit_code == 0, result.output
    assert "errors: 1" in result.output
    good = (wiki_root / "wiki" / "topics" / "good-topic.md").read_text(encoding="utf-8")
    assert "domain: unassigned" in good


def test_commit_isolates_per_page_psycopg_errors(
    mod: Any,
    wiki_root: Path,
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A psycopg.Error on page N must not poison the rest of the batch.

    Without the per-page SAVEPOINT, a single SQL failure puts the
    psycopg3 connection into ``InFailedSqlTransaction``, every
    subsequent DB call raises, AND ``conn.commit()`` at the end of
    ``apply_fixes`` raises and propagates up — crashing the CLI after
    files have already been mutated. The SAVEPOINT around each page
    keeps the connection valid so the batch keeps going AND the final
    commit succeeds.
    """
    # Two status-flip candidates. The status-flip path runs both a
    # frontmatter rewrite (on disk) and a DB UPDATE — we'll inject the
    # psycopg failure at the DB step for one of them.
    _write_page(
        wiki_root,
        "topics",
        "good-topic",
        _LEGACY_TOPIC_NO_DOMAIN.replace("status: current", "status: current"),
    )
    _write_page(
        wiki_root,
        "topics",
        "bad-topic",
        _LEGACY_TOPIC_NO_DOMAIN.replace("status: current", "status: current"),
    )
    _seed_wiki_row(
        db_conn,
        slug="good-topic",
        page_type="topic",
        status="current",
        rel_path="wiki/topics/good-topic.md",
    )
    _seed_wiki_row(
        db_conn,
        slug="bad-topic",
        page_type="topic",
        status="current",
        rel_path="wiki/topics/bad-topic.md",
    )
    db_conn.commit()

    original_flip = mod._flip_db_status

    def flaky_flip(conn: psycopg.Connection, slug: str) -> int:
        if slug == "bad-topic":
            # Trigger a real psycopg error so the connection's
            # transaction state gets exercised (a bare Python exception
            # wouldn't reproduce InFailedSqlTransaction).
            conn.execute("SELECT * FROM table_that_does_not_exist")
        return original_flip(conn, slug)

    monkeypatch.setattr(mod, "_flip_db_status", flaky_flip)

    result = _run_cli(mod, wiki_root, "--commit", "--limit", "5")
    # CLI must NOT crash — exit 0 with errors counted in the tally.
    assert result.exit_code == 0, result.output
    assert "errors: 1" in result.output

    # The good page's DB row was flipped to active; the bad one was
    # rolled back at the savepoint.
    good = wiki_repo.find_by_slug("good-topic")
    bad = wiki_repo.find_by_slug("bad-topic")
    assert good is not None and good["status"] == "active"
    assert bad is not None and bad["status"] == "current"
