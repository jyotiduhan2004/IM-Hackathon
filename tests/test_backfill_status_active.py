"""Tests for scripts/backfill_status_active.py — the C2 one-shot migration.

We never touch the real wiki/ or docs/audits/ — every test uses tmp_path
and the pytest-scoped test DB schema for wiki_pages. We invoke the CLI
via CliRunner with `--repo-root tmp_path` so plan files land under the
tmp tree too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest
from click.testing import CliRunner


def _load_script_module():
    """Import scripts/backfill_status_active.py as a standalone module."""
    path = Path(__file__).parent.parent / "scripts" / "backfill_status_active.py"
    spec = importlib.util.spec_from_file_location("_backfill_status_active_for_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_backfill_status_active_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def script_mod():
    return _load_script_module()


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """Fake wiki tree + docs/audits/ dir under tmp_path."""
    (tmp_path / "wiki" / "topics").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    (tmp_path / "wiki" / "policies").mkdir(parents=True)
    (tmp_path / "docs" / "audits").mkdir(parents=True)
    return tmp_path


def _write_page(path: Path, *, status: str, title: str | None = None) -> None:
    """Drop a minimal frontmatter + body page at `path`."""
    title = title or path.stem.replace("-", " ").title()
    body = f"""---
title: "{title}"
page_type: topic
status: {status}
sources:
  - "raw/2026-01-01_example.md"
related: []
last_compiled: "2026-01-01T00:00:00+00:00"
---

# {title}

Body text for {path.stem}.
"""
    path.write_text(body, encoding="utf-8")


def _seed_wiki_page(conn: psycopg.Connection, *, slug: str, path: str, status: str) -> None:
    """Insert a wiki_pages row directly — bypasses the higher-level
    upsert_wiki_page helper so we can stash legacy statuses the helper
    would refuse to produce today."""
    conn.execute(
        """
        INSERT INTO wiki_pages (slug, path, title, page_type, status)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (slug, path, slug.replace("-", " ").title(), "topic", status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Plan phase (--dry-run)
# ---------------------------------------------------------------------------


def test_dry_run_counts_legacy_rows_only(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """Mixed DB rows (current + active + contested + superseded + archived) →
    dry-run plans only the two legacy statuses."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "alpha.md", status="current")
    _write_page(topics / "beta.md", status="contested")
    _write_page(topics / "gamma.md", status="active")
    _write_page(topics / "delta.md", status="superseded")
    _write_page(topics / "epsilon.md", status="archived")

    _seed_wiki_page(db_conn, slug="alpha", path="wiki/topics/alpha.md", status="current")
    _seed_wiki_page(db_conn, slug="beta", path="wiki/topics/beta.md", status="contested")
    _seed_wiki_page(db_conn, slug="gamma", path="wiki/topics/gamma.md", status="active")
    _seed_wiki_page(db_conn, slug="delta", path="wiki/topics/delta.md", status="superseded")
    _seed_wiki_page(db_conn, slug="epsilon", path="wiki/topics/epsilon.md", status="archived")

    result = CliRunner().invoke(
        script_mod.main,
        ["--dry-run", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert (
        "1 rows would flip current→active · 1 rows would flip contested→archived · "
        "2 .md files would be rewritten"
    ) in result.output

    # Modern rows untouched in DB.
    cur = db_conn.execute("SELECT slug, status FROM wiki_pages ORDER BY slug")
    statuses = {row["slug"]: row["status"] for row in cur.fetchall()}
    assert statuses == {
        "alpha": "current",  # dry-run MUST NOT mutate
        "beta": "contested",
        "gamma": "active",
        "delta": "superseded",
        "epsilon": "archived",
    }

    # Modern files untouched on disk.
    gamma = (wiki_root / "wiki" / "topics" / "gamma.md").read_text(encoding="utf-8")
    assert "status: active" in gamma
    delta = (wiki_root / "wiki" / "topics" / "delta.md").read_text(encoding="utf-8")
    assert "status: superseded" in delta


def test_dry_run_writes_plan_file(script_mod, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Plan file ends up under docs/audits/ with a filename containing ISO."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "alpha.md", status="current")
    _seed_wiki_page(db_conn, slug="alpha", path="wiki/topics/alpha.md", status="current")

    result = CliRunner().invoke(
        script_mod.main,
        ["--dry-run", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    plans = list((wiki_root / "docs" / "audits").glob("status-backfill-plan-*.md"))
    assert len(plans) == 1
    content = plans[0].read_text(encoding="utf-8")
    assert "# Status backfill plan" in content
    assert "current → active: 1" in content
    assert "alpha.md" in content


def test_dry_run_plan_mentions_contested_branch(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """`contested → archived` branch must show up verbatim in the plan file, not
    just in the CLI counts."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "beta.md", status="contested")
    _seed_wiki_page(db_conn, slug="beta", path="wiki/topics/beta.md", status="contested")

    result = CliRunner().invoke(
        script_mod.main,
        ["--dry-run", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    plans = list((wiki_root / "docs" / "audits").glob("status-backfill-plan-*.md"))
    assert len(plans) == 1
    content = plans[0].read_text(encoding="utf-8")
    assert "contested → archived: 1" in content
    assert "beta.md" in content
    # And the file-rewrites table row shows the explicit flip direction.
    assert "| contested | archived |" in content


# ---------------------------------------------------------------------------
# Execute phase (--commit)
# ---------------------------------------------------------------------------


def test_commit_flips_db_and_rewrites_files(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "alpha.md", status="current")
    _write_page(topics / "beta.md", status="contested")
    _write_page(topics / "gamma.md", status="active")  # untouched
    _write_page(topics / "delta.md", status="superseded")  # untouched

    _seed_wiki_page(db_conn, slug="alpha", path="wiki/topics/alpha.md", status="current")
    _seed_wiki_page(db_conn, slug="beta", path="wiki/topics/beta.md", status="contested")
    _seed_wiki_page(db_conn, slug="gamma", path="wiki/topics/gamma.md", status="active")
    _seed_wiki_page(db_conn, slug="delta", path="wiki/topics/delta.md", status="superseded")

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    cur = db_conn.execute("SELECT slug, status FROM wiki_pages ORDER BY slug")
    statuses = {row["slug"]: row["status"] for row in cur.fetchall()}
    assert statuses == {
        "alpha": "active",
        "beta": "archived",
        "gamma": "active",
        "delta": "superseded",
    }

    assert "status: active" in (topics / "alpha.md").read_text(encoding="utf-8")
    assert "status: archived" in (topics / "beta.md").read_text(encoding="utf-8")
    assert "status: active" in (topics / "gamma.md").read_text(encoding="utf-8")
    assert "status: superseded" in (topics / "delta.md").read_text(encoding="utf-8")


def test_commit_preserves_other_frontmatter_fields(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """yaml.safe_load/safe_dump round-trip must keep title, sources, etc."""
    topics = wiki_root / "wiki" / "topics"
    alpha = topics / "alpha.md"
    alpha.write_text(
        """---
title: "Alpha Page"
page_type: topic
status: current
sources:
  - "raw/2026-01-01_foo.md"
  - "raw/2026-01-02_bar.md"
related:
  - "[[beta]]"
last_compiled: "2026-01-01T00:00:00+00:00"
domain: "buyer-experience"
---

# Alpha Page

The body is preserved untouched.

## Another heading

More body content.
""",
        encoding="utf-8",
    )
    _seed_wiki_page(db_conn, slug="alpha", path="wiki/topics/alpha.md", status="current")

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    rewritten = alpha.read_text(encoding="utf-8")
    # Frontmatter preserved.
    assert "title: Alpha Page" in rewritten or 'title: "Alpha Page"' in rewritten
    assert "status: active" in rewritten
    assert "raw/2026-01-01_foo.md" in rewritten
    assert "raw/2026-01-02_bar.md" in rewritten
    assert "[[beta]]" in rewritten
    assert "domain: buyer-experience" in rewritten
    # Body preserved.
    assert "# Alpha Page" in rewritten
    assert "The body is preserved untouched." in rewritten
    assert "## Another heading" in rewritten


def test_commit_is_idempotent(script_mod, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "alpha.md", status="current")
    _seed_wiki_page(db_conn, slug="alpha", path="wiki/topics/alpha.md", status="current")

    # First run flips.
    r1 = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert r1.exit_code == 0, r1.output

    # Grab file bytes so we can prove the second run is a true no-op.
    after_first = (topics / "alpha.md").read_text(encoding="utf-8")

    # Second run — nothing left on legacy values, so planner finds 0 rows.
    r2 = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert r2.exit_code == 0, r2.output
    assert (
        "0 rows would flip current→active · 0 rows would flip contested→archived · "
        "0 .md files would be rewritten"
    ) in r2.output

    # Row stayed active; file content identical.
    cur = db_conn.execute("SELECT status FROM wiki_pages WHERE slug='alpha'")
    row = cur.fetchone()
    assert row is not None
    assert row["status"] == "active"
    assert (topics / "alpha.md").read_text(encoding="utf-8") == after_first


# ---------------------------------------------------------------------------
# Edge cases: DB-only flips and file-only rewrites stay independent.
# ---------------------------------------------------------------------------


def test_commit_handles_db_row_without_file(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """DB row points at a path that doesn't exist — still flip the DB."""
    _seed_wiki_page(
        db_conn,
        slug="orphan",
        path="wiki/topics/orphan.md",  # does NOT exist on disk
        status="current",
    )

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    cur = db_conn.execute("SELECT status FROM wiki_pages WHERE slug='orphan'")
    row = cur.fetchone()
    assert row is not None
    assert row["status"] == "active"
    # No file was created.
    assert not (wiki_root / "wiki" / "topics" / "orphan.md").exists()


def test_commit_rewrites_file_without_db_row(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """Orphan file — legacy `status:` frontmatter but no wiki_pages row —
    must still be rewritten. The filesystem scan finds it regardless of
    DB state, which is what keeps disk and DB converging when they drift."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "lonely.md", status="current")
    # No _seed_wiki_page call — DB has no row for this file.

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "status: active" in (topics / "lonely.md").read_text(encoding="utf-8")
    assert "status: current" not in (topics / "lonely.md").read_text(encoding="utf-8")


def test_commit_retries_stuck_file_after_db_already_migrated(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """Regression for P1: simulate a previous partial run where the DB row
    flipped to `active` but the file write never landed. The file still has
    `status: current` in frontmatter. `--dry-run` must plan it, and
    `--commit` must rewrite it."""
    topics = wiki_root / "wiki" / "topics"
    stuck = topics / "alpha.md"
    _write_page(stuck, status="current")
    # Seed the DB row in the already-migrated state — what a previous
    # partial run would have left behind.
    _seed_wiki_page(db_conn, slug="alpha", path="wiki/topics/alpha.md", status="active")

    # Dry-run must surface the stuck file in the file-rewrite plan.
    dry = CliRunner().invoke(
        script_mod.main,
        ["--dry-run", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert dry.exit_code == 0, dry.output
    assert (
        "0 rows would flip current→active · 0 rows would flip contested→archived · "
        "1 .md files would be rewritten"
    ) in dry.output
    plan = next((wiki_root / "docs" / "audits").glob("status-backfill-plan-*.md"))
    plan_text = plan.read_text(encoding="utf-8")
    assert "alpha.md" in plan_text
    assert "| current | active |" in plan_text

    # Commit must rewrite the stuck file and leave the DB alone.
    commit = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert commit.exit_code == 0, commit.output
    assert "status: active" in stuck.read_text(encoding="utf-8")
    cur = db_conn.execute("SELECT status FROM wiki_pages WHERE slug='alpha'")
    row = cur.fetchone()
    assert row is not None
    assert row["status"] == "active"
