"""Tests for scripts/backfill_source_threads_and_touches.py.

End-to-end tests against a synthetic wiki under ``tmp_path`` and the
shared test DB schema fixture from ``tests/conftest.py``. We invoke the
CLI via ``CliRunner`` with ``--repo-root tmp_path`` so plan files land
under the tmp tree and never pollute the real ``docs/audits/``.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import psycopg
import pytest
from click.testing import CliRunner
from src.db import messages as messages_repo
from src.db import wiki_pages as wiki_repo
from src.utils import extract_frontmatter


def _load_script_module():
    """Import scripts/backfill_source_threads_and_touches.py as a module."""
    path = Path(__file__).parent.parent / "scripts" / "backfill_source_threads_and_touches.py"
    spec = importlib.util.spec_from_file_location("_backfill_st_for_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_backfill_st_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def script_mod():
    return _load_script_module()


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """Synthetic wiki tree under tmp_path."""
    for sub in ("topics", "entities", "systems", "policies"):
        (tmp_path / "wiki" / sub).mkdir(parents=True)
    (tmp_path / "docs" / "audits").mkdir(parents=True)
    return tmp_path


def _write_page(path: Path, *, sources: list[str], page_type: str = "topic") -> None:
    """Drop a minimal frontmatter + body wiki page at ``path``."""
    title = path.stem.replace("-", " ").title()
    src_block = "".join(f"  - {s}\n" for s in sources) or "  []\n"
    path.write_text(
        f"""---
title: "{title}"
page_type: {page_type}
status: active
sources:
{src_block}related: []
last_compiled: "2026-01-01T00:00:00+00:00"
---

# {title}

Body for {path.stem}.
""",
        encoding="utf-8",
    )


def _seed_message(
    conn: psycopg.Connection, *, message_id: str, raw_path: str, thread_id: str | None
) -> None:
    """Insert a ``messages`` row via the repo (gets a FK-safe raw_path)."""
    messages_repo.insert_message(
        conn,
        message_id=message_id,
        raw_path=raw_path,
        thread_id=thread_id,
        subject="s",
        from_address="a@b.c",
        date=datetime(2026, 4, 1, tzinfo=UTC),
    )
    conn.commit()


def _seed_wiki_page(conn: psycopg.Connection, *, slug: str, page_type: str = "topic") -> int:
    """Insert a ``wiki_pages`` row and return its ``page_id``."""
    page_id = wiki_repo.upsert_wiki_page(
        conn,
        slug=slug,
        path=f"wiki/{page_type}s/{slug}.md",
        title=slug.replace("-", " ").title(),
        page_type=page_type,
    )
    conn.commit()
    return page_id


def _read_frontmatter(path: Path) -> dict:
    """Load YAML frontmatter from a wiki page on disk."""
    return extract_frontmatter(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Happy path — 2 pages, 5 messages across 3 threads
# ---------------------------------------------------------------------------


def test_commit_writes_source_threads_and_touches(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """Canonical synthetic run — verify both sides of the write land."""
    topics = wiki_root / "wiki" / "topics"
    # alpha: messages m1 + m2 (thread t-A), m3 (thread t-B) → 2 distinct threads
    _write_page(
        topics / "alpha.md",
        sources=[
            "raw/2026-01-01_alpha_m1.md",
            "raw/2026-01-02_alpha_m2.md",
            "raw/2026-01-03_alpha_m3.md",
        ],
    )
    # beta: messages m4 (thread t-B), m5 (thread t-C) → 2 distinct threads
    _write_page(
        topics / "beta.md",
        sources=[
            "raw/2026-01-04_beta_m4.md",
            "raw/2026-01-05_beta_m5.md",
        ],
    )

    # Seed messages.
    _seed_message(db_conn, message_id="m1", raw_path="raw/2026-01-01_alpha_m1.md", thread_id="t-A")
    _seed_message(db_conn, message_id="m2", raw_path="raw/2026-01-02_alpha_m2.md", thread_id="t-A")
    _seed_message(db_conn, message_id="m3", raw_path="raw/2026-01-03_alpha_m3.md", thread_id="t-B")
    _seed_message(db_conn, message_id="m4", raw_path="raw/2026-01-04_beta_m4.md", thread_id="t-B")
    _seed_message(db_conn, message_id="m5", raw_path="raw/2026-01-05_beta_m5.md", thread_id="t-C")

    # Seed wiki_pages so find_by_slug resolves.
    alpha_id = _seed_wiki_page(db_conn, slug="alpha")
    beta_id = _seed_wiki_page(db_conn, slug="beta")

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    # source_threads on each page — sorted, deduped.
    alpha_fm = _read_frontmatter(topics / "alpha.md")
    assert alpha_fm["source_threads"] == ["t-A", "t-B"]
    beta_fm = _read_frontmatter(topics / "beta.md")
    assert beta_fm["source_threads"] == ["t-B", "t-C"]

    # message_touched_pages — 5 distinct touch rows (one per message, each
    # paired with the right page_id).
    cur = db_conn.execute(
        "SELECT message_id, page_id FROM message_touched_pages ORDER BY message_id, page_id"
    )
    touches = [(r["message_id"], r["page_id"]) for r in cur.fetchall()]
    assert touches == [
        ("m1", alpha_id),
        ("m2", alpha_id),
        ("m3", alpha_id),
        ("m4", beta_id),
        ("m5", beta_id),
    ]


# ---------------------------------------------------------------------------
# Idempotency — re-running --commit is a no-op
# ---------------------------------------------------------------------------


def test_commit_is_idempotent(script_mod, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Second --commit inserts zero new touches and leaves files byte-identical."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(
        topics / "alpha.md",
        sources=["raw/2026-01-01_alpha_m1.md", "raw/2026-01-02_alpha_m2.md"],
    )
    _seed_message(db_conn, message_id="m1", raw_path="raw/2026-01-01_alpha_m1.md", thread_id="t-A")
    _seed_message(db_conn, message_id="m2", raw_path="raw/2026-01-02_alpha_m2.md", thread_id="t-A")
    _seed_wiki_page(db_conn, slug="alpha")

    r1 = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert r1.exit_code == 0, r1.output

    after_first = (topics / "alpha.md").read_text(encoding="utf-8")
    row_count_first = db_conn.execute(
        "SELECT count(*)::int AS n FROM message_touched_pages"
    ).fetchone()
    assert row_count_first is not None
    n_first = row_count_first["n"]
    assert n_first == 2

    # Second run — everything already in place.
    r2 = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert r2.exit_code == 0, r2.output
    assert "touches_inserted=0" in r2.output
    # idempotent=1 because alpha is already fully up to date.
    assert "idempotent=1" in r2.output
    assert "updated=0" in r2.output

    # File bytes unchanged.
    assert (topics / "alpha.md").read_text(encoding="utf-8") == after_first
    # DB row count unchanged.
    row_count_second = db_conn.execute(
        "SELECT count(*)::int AS n FROM message_touched_pages"
    ).fetchone()
    assert row_count_second is not None
    assert row_count_second["n"] == n_first


# ---------------------------------------------------------------------------
# Drift — source raw_path missing from messages table
# ---------------------------------------------------------------------------


def test_drift_logged_and_skipped(script_mod, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Unresolvable raw_paths are counted + surfaced; the rest still back-fills."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(
        topics / "alpha.md",
        sources=[
            "raw/2026-01-01_alpha_m1.md",  # resolves
            "raw/2026-01-02_ghost_nope.md",  # drift
        ],
    )
    _seed_message(db_conn, message_id="m1", raw_path="raw/2026-01-01_alpha_m1.md", thread_id="t-A")
    alpha_id = _seed_wiki_page(db_conn, slug="alpha")

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "drift=1" in result.output
    assert "touches_inserted=1" in result.output

    # Resolved half still writes: source_threads has the one thread.
    alpha_fm = _read_frontmatter(topics / "alpha.md")
    assert alpha_fm["source_threads"] == ["t-A"]
    # Only the resolvable message was touched.
    cur = db_conn.execute(
        "SELECT message_id FROM message_touched_pages WHERE page_id = %s ORDER BY message_id",
        (alpha_id,),
    )
    assert [r["message_id"] for r in cur.fetchall()] == ["m1"]

    # Plan file surfaces the drift sample.
    plans = list((wiki_root / "docs" / "audits").glob("source-threads-backfill-plan-*.md"))
    assert len(plans) == 1
    plan_text = plans[0].read_text(encoding="utf-8")
    assert "raw/2026-01-02_ghost_nope.md" in plan_text


# ---------------------------------------------------------------------------
# --limit N
# ---------------------------------------------------------------------------


def test_limit_honored(script_mod, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """--limit 2 processes exactly two pages (sorted deterministic walk)."""
    topics = wiki_root / "wiki" / "topics"
    # Four pages — first two (sorted) should be 'alpha' and 'beta'.
    for slug in ("alpha", "beta", "gamma", "delta"):
        _write_page(topics / f"{slug}.md", sources=[f"raw/2026-01-01_{slug}_x.md"])
        _seed_message(
            db_conn, message_id=slug, raw_path=f"raw/2026-01-01_{slug}_x.md", thread_id="t-X"
        )
        _seed_wiki_page(db_conn, slug=slug)

    result = CliRunner().invoke(
        script_mod.main,
        ["--limit", "2", "--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "scanned=2" in result.output

    # Only the first two (sorted) pages got a source_threads write.
    alpha_fm = _read_frontmatter(topics / "alpha.md")
    beta_fm = _read_frontmatter(topics / "beta.md")
    gamma_fm = _read_frontmatter(topics / "gamma.md")
    delta_fm = _read_frontmatter(topics / "delta.md")
    assert alpha_fm.get("source_threads") == ["t-X"]
    assert beta_fm.get("source_threads") == ["t-X"]
    assert "source_threads" not in gamma_fm
    assert "source_threads" not in delta_fm


# ---------------------------------------------------------------------------
# Dry-run never writes
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_changes(script_mod, wiki_root: Path, db_conn: psycopg.Connection) -> None:
    """Default dry-run must leave disk + DB untouched, but still emit a plan."""
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "alpha.md", sources=["raw/2026-01-01_alpha_m1.md"])
    _seed_message(db_conn, message_id="m1", raw_path="raw/2026-01-01_alpha_m1.md", thread_id="t-A")
    _seed_wiki_page(db_conn, slug="alpha")

    before = (topics / "alpha.md").read_text(encoding="utf-8")

    result = CliRunner().invoke(
        script_mod.main,
        ["--dry-run", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "would insert touches:" in result.output

    # File unchanged.
    assert (topics / "alpha.md").read_text(encoding="utf-8") == before
    # DB catalog still empty.
    row = db_conn.execute("SELECT count(*)::int AS n FROM message_touched_pages").fetchone()
    assert row is not None
    assert row["n"] == 0
    # Plan file written.
    plans = list((wiki_root / "docs" / "audits").glob("source-threads-backfill-plan-*.md"))
    assert len(plans) == 1


# ---------------------------------------------------------------------------
# Page missing from wiki_pages catalog
# ---------------------------------------------------------------------------


def test_page_without_catalog_row_is_skipped(
    script_mod, wiki_root: Path, db_conn: psycopg.Connection
) -> None:
    """A page with sources but no ``wiki_pages`` row is skipped entirely.

    Per the spec: ``find_by_slug(slug)`` is a hard prerequisite for the
    backfill — if the page isn't in the catalog, we can't attribute
    touches to it, so we log + skip both the touch insert and the
    frontmatter rewrite.
    """
    topics = wiki_root / "wiki" / "topics"
    _write_page(topics / "alpha.md", sources=["raw/2026-01-01_alpha_m1.md"])
    _seed_message(db_conn, message_id="m1", raw_path="raw/2026-01-01_alpha_m1.md", thread_id="t-A")
    # NOTE: no _seed_wiki_page — catalog miss.

    result = CliRunner().invoke(
        script_mod.main,
        ["--commit", "--repo-root", str(wiki_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # No touches — page_id couldn't be resolved.
    row = db_conn.execute("SELECT count(*)::int AS n FROM message_touched_pages").fetchone()
    assert row is not None
    assert row["n"] == 0
    # And source_threads NOT written — page skipped whole.
    alpha_fm = _read_frontmatter(topics / "alpha.md")
    assert "source_threads" not in alpha_fm
